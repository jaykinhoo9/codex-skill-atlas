import json
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"skills/codex-skill-atlas/app"))
from server import Atlas, Catalog, make_handler
from skill_trash import SkillTrash, TrashError, RETENTION_MS


class SkillTrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home, self.agents = self.base/'codex', self.base/'agents'
        self.skill = self.write_skill(self.home/'skills/demo', 'demo')
        (self.skill.parent/'resource.txt').write_text('keep the resource')
        self.catalog = Catalog(self.home, self.agents).scan()
        self.trash = SkillTrash(self.base/'recycle')

    def tearDown(self):
        self.temp.cleanup()

    def write_skill(self, folder, name):
        folder.mkdir(parents=True, exist_ok=True)
        skill = folder/'SKILL.md'
        skill.write_text(f'---\nname: {name}\n---\n# {name}\n')
        return skill

    def id_for(self, name):
        return next(s['id'] for s in self.catalog.skills.values() if s['name'] == name)

    def delete(self, name='demo'):
        sid = self.id_for(name)
        plan = self.trash.preview(self.catalog, sid)
        return self.trash.delete(self.catalog, sid, plan['token'])

    def test_directory_roundtrip_including_resources(self):
        result = self.delete()
        self.assertFalse(self.skill.parent.exists())
        self.assertEqual(self.catalog.scan().skills, {})
        self.trash.restore(result['trashId'])
        self.assertTrue(self.skill.exists())
        self.assertEqual((self.skill.parent/'resource.txt').read_text(), 'keep the resource')
        self.assertEqual(len(self.catalog.scan().skills), 1)
        with self.assertRaises(TrashError):
            self.trash.restore(result['trashId'])

    def test_running_dashboard_cannot_recycle_itself(self):
        trash = SkillTrash(self.base/'protected-recycle', protected_paths=[self.skill.parent])
        with self.assertRaisesRegex(TrashError, '看板自身'):
            trash.preview(self.catalog, self.id_for('demo'))
        self.assertTrue(self.skill.exists())

    def test_batch_roundtrip_is_one_recoverable_entry(self):
        second = self.write_skill(self.home/'skills/second', 'second')
        keep = self.write_skill(self.home/'skills/keep', 'keep')
        self.catalog.scan()
        ids = [self.id_for('demo'), self.id_for('second')]
        plan = self.trash.preview_batch(self.catalog, ids + ids)
        self.assertEqual(len(plan['selectedSkills']), 2)
        result = self.trash.delete_batch(self.catalog, ids[::-1], plan['token'])
        self.assertEqual(set(result['removedSkillIds']), set(ids))
        self.assertFalse(self.skill.exists());self.assertFalse(second.exists())
        self.assertTrue(keep.exists())
        inventory = self.trash.maintain()['entries']
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]['skillCount'], 2)
        self.trash.restore(result['trashId'])
        self.assertTrue(self.skill.exists());self.assertTrue(second.exists())
        self.assertEqual((self.skill.parent/'resource.txt').read_text(), 'keep the resource')

    def test_batch_parent_child_moves_parent_once_and_discloses_other_children(self):
        child = self.write_skill(self.skill.parent/'skills/child', 'child')
        other = self.write_skill(self.skill.parent/'skills/other', 'other')
        self.catalog.scan()
        ids = [self.id_for('demo'), self.id_for('child')]
        plan = self.trash.preview_batch(self.catalog, ids)
        self.assertEqual(len(plan['paths']), 1)
        self.assertEqual(len(plan['selectedSkills']), 2)
        self.assertEqual({s['name'] for s in plan['affectedSkills']}, {'demo', 'child', 'other'})
        result = self.trash.delete_batch(self.catalog, ids, plan['token'])
        self.trash.restore(result['trashId'])
        self.assertTrue(child.exists());self.assertTrue(other.exists())

    def test_batch_rejects_sessions_unknown_ids_and_invalid_shapes(self):
        sid = self.id_for('demo')
        for ids in [None, [], sid, [None], [sid, {}], [sid, 'session-id'], [sid, '../outside'], [sid]*1001]:
            with self.subTest(ids=str(ids)[:60]), self.assertRaises(TrashError):
                self.trash.preview_batch(self.catalog, ids)
        self.assertTrue(self.skill.exists())
        self.assertEqual(self.trash.plans, {})

    def test_batch_confirmation_binds_ids_files_expiry_and_operation_type(self):
        second = self.write_skill(self.home/'skills/second', 'second')
        self.catalog.scan()
        ids = [self.id_for('demo'), self.id_for('second')]
        plan = self.trash.preview_batch(self.catalog, ids)
        with self.assertRaises(TrashError):
            self.trash.delete_batch(self.catalog, ids[:1], plan['token'])
        plan = self.trash.preview_batch(self.catalog, ids)
        second.write_text('changed after confirmation preview')
        with self.assertRaisesRegex(TrashError, '已变化'):
            self.trash.delete_batch(self.catalog, ids, plan['token'])
        plan = self.trash.preview_batch(self.catalog, ids)
        self.trash.plans[plan['token']]['expires'] = 0
        with self.assertRaises(TrashError):
            self.trash.delete_batch(self.catalog, ids, plan['token'])
        plan = self.trash.preview(self.catalog, ids[0])
        with self.assertRaises(TrashError):
            self.trash.delete_batch(self.catalog, ids, plan['token'])
        plan = self.trash.preview_batch(self.catalog, ids)
        with self.assertRaises(TrashError):
            self.trash.delete(self.catalog, ids[0], plan['token'])
        self.assertTrue(self.skill.exists());self.assertTrue(second.exists())

    def test_batch_failure_rolls_back_all_skills(self):
        second = self.write_skill(self.home/'skills/second', 'second')
        self.catalog.scan()
        ids = [self.id_for('demo'), self.id_for('second')]
        plan = self.trash.preview_batch(self.catalog, ids)
        original_rename = os.rename
        calls = 0
        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected batch failure')
            original_rename(source, destination)
        with patch('skill_trash.os.rename', side_effect=fail_second):
            with self.assertRaisesRegex(TrashError, '已还原'):
                self.trash.delete_batch(self.catalog, ids, plan['token'])
        self.assertTrue(self.skill.exists());self.assertTrue(second.exists())
        self.assertEqual(self.trash.maintain()['entries'], [])

    def test_large_batch_needs_only_one_confirmation_token(self):
        for number in range(70):
            self.write_skill(self.home/f'skills/demo-{number}', f'demo-{number}')
        self.catalog.scan()
        ids = list(self.catalog.skills)
        plan = self.trash.preview_batch(self.catalog, ids)
        self.assertEqual(len(self.trash.plans), 1)
        result = self.trash.delete_batch(self.catalog, ids, plan['token'])
        self.assertEqual(len(result['removedSkillIds']), 71)
        self.assertFalse(self.catalog.scan().skills)
        self.trash.restore(result['trashId'])
        self.assertEqual(len(self.catalog.scan().skills), 71)

    def test_stale_or_unconfirmed_plan_cannot_delete(self):
        sid = self.id_for('demo')
        with self.assertRaises(TrashError):
            self.trash.delete(self.catalog, sid, 'not-a-token')
        plan = self.trash.preview(self.catalog, sid)
        self.skill.write_text('changed after preview')
        with self.assertRaisesRegex(TrashError, '已变化'):
            self.trash.delete(self.catalog, sid, plan['token'])
        self.assertTrue(self.skill.exists())

    def test_parent_skill_preview_lists_nested_skills(self):
        child = self.write_skill(self.skill.parent/'skills/child', 'child')
        neighbor = self.write_skill(self.home/'skills/neighbor', 'neighbor')
        self.catalog.scan()
        plan = self.trash.preview(self.catalog, self.id_for('demo'))
        self.assertEqual({s['name'] for s in plan['affectedSkills']}, {'demo', 'child'})
        result = self.trash.delete(self.catalog, self.id_for('demo'), plan['token'])
        self.assertFalse(child.exists())
        self.assertTrue(neighbor.exists())
        self.trash.restore(result['trashId'])
        self.assertTrue(child.exists())

    def test_plugin_all_versions_only_selected_skill(self):
        for version in ['1.0', '2.0']:
            base = self.home/'plugins/cache/personal/test'/version
            (base/'.codex-plugin').mkdir(parents=True)
            (base/'.codex-plugin/plugin.json').write_text(json.dumps({'skills': './skills'}))
            self.write_skill(base/'skills/chosen', 'chosen')
            self.write_skill(base/'skills/other', 'other')
        self.catalog.scan()
        plan = self.trash.preview(self.catalog, self.id_for('chosen'))
        self.assertEqual(len(plan['paths']), 2)
        result = self.trash.delete(self.catalog, self.id_for('chosen'), plan['token'])
        for version in ['1.0', '2.0']:
            base = self.home/'plugins/cache/personal/test'/version
            self.assertFalse((base/'skills/chosen').exists())
            self.assertTrue((base/'skills/other/SKILL.md').exists())
            self.assertTrue((base/'.codex-plugin/plugin.json').exists())
        self.trash.restore(result['trashId'])
        self.assertTrue((base/'skills/chosen/SKILL.md').exists())

    def test_root_skill_keeps_shared_directory(self):
        root = self.write_skill(self.home/'skills', 'root')
        self.catalog.scan()
        result = self.delete('root')
        self.assertFalse(root.exists())
        self.assertTrue(self.skill.exists())
        self.trash.restore(result['trashId'])
        self.assertTrue(root.exists())

    def test_symlink_installation_keeps_external_target(self):
        external = self.write_skill(self.base/'external', 'external')
        alias = self.home/'skills/external-link'
        alias.symlink_to(external.parent, target_is_directory=True)
        self.catalog.scan()
        result = self.delete('external')
        self.assertFalse(os.path.lexists(alias))
        self.assertTrue(external.exists())
        self.trash.restore(result['trashId'])
        self.assertTrue(alias.is_symlink())

    def test_deduplicated_aliases_all_removed_and_restored(self):
        alias = self.agents/'skills/alias'
        alias.parent.mkdir(parents=True)
        alias.symlink_to(self.skill.parent, target_is_directory=True)
        self.catalog.scan()
        self.assertEqual(len(next(iter(self.catalog.skills.values()))['copies']), 2)
        result = self.delete()
        self.assertFalse(os.path.lexists(alias))
        self.assertFalse(self.skill.exists())
        self.trash.restore(result['trashId'])
        self.assertTrue(alias.is_symlink())
        self.assertTrue(self.skill.exists())

    def test_shared_intermediate_symlink_rejected(self):
        external = self.write_skill(self.base/'shared/sub', 'sub')
        (self.home/'skills/shared').symlink_to(external.parent.parent, target_is_directory=True)
        self.catalog.scan()
        with self.assertRaisesRegex(TrashError, '共享符号链接'):
            self.trash.preview(self.catalog, self.id_for('sub'))
        self.assertTrue(external.exists())

    def test_restore_refuses_to_overwrite_new_install(self):
        result = self.delete()
        self.write_skill(self.skill.parent, 'new-demo')
        with self.assertRaisesRegex(TrashError, '同名文件'):
            self.trash.restore(result['trashId'])
        self.assertIn('new-demo', self.skill.read_text())

    def test_partial_move_failure_rolls_back(self):
        alias = self.agents/'skills/alias';alias.parent.mkdir(parents=True)
        alias.symlink_to(self.skill.parent, target_is_directory=True)
        self.catalog.scan()
        original_rename = os.rename
        calls = 0
        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected failure')
            original_rename(source, destination)
        with patch('skill_trash.os.rename', side_effect=fail_second):
            with self.assertRaisesRegex(TrashError, '已还原'):
                self.delete()
        self.assertTrue(self.skill.exists())
        self.assertTrue(alias.is_symlink())

    def test_invalid_id_and_restore_path_rejected(self):
        with self.assertRaises(TrashError):
            self.trash.preview(self.catalog, '../../SKILL.md')
        with self.assertRaises(TrashError):
            self.trash.restore('../outside')
        self.assertTrue(self.skill.exists())

    def test_inventory_retention_boundary_and_permanent_cleanup(self):
        result = self.delete()
        entry = self.trash.directory/result['trashId']
        inventory = self.trash.maintain()
        item = inventory['entries'][0]
        self.assertEqual(item['name'], 'demo')
        self.assertTrue(item['canRestore'])
        self.assertEqual(item['expiresAt']-item['createdAt'], RETENTION_MS)
        self.assertEqual(len(self.trash.maintain(item['expiresAt']-1)['entries']), 1)
        self.assertEqual(self.trash.maintain(item['expiresAt'])['entries'], [])
        self.assertFalse(entry.exists())
        self.assertFalse(self.skill.exists())

    def test_restore_before_deadline_and_hide_restored_record(self):
        result = self.delete()
        expires = self.trash.maintain()['entries'][0]['expiresAt']
        with patch('skill_trash.time.time', return_value=(expires-1)/1000):
            self.trash.restore(result['trashId'])
        self.assertEqual(self.trash.maintain()['entries'], [])
        self.assertTrue(self.skill.exists())

    def test_expired_restore_cannot_bypass_cleanup(self):
        result = self.delete()
        expires = self.trash.maintain()['entries'][0]['expiresAt']
        with patch('skill_trash.time.time', return_value=expires/1000):
            with self.assertRaises(TrashError) as error:
                self.trash.restore(result['trashId'])
        self.assertEqual(error.exception.status, 410)
        self.assertFalse((self.trash.directory/result['trashId']).exists())

    def test_cleanup_does_not_follow_payload_symlink(self):
        outside = self.base/'outside';outside.mkdir();(outside/'keep.txt').write_text('safe')
        (self.skill.parent/'external').symlink_to(outside, target_is_directory=True)
        self.delete()
        expires = self.trash.maintain()['entries'][0]['expiresAt']
        self.trash.maintain(expires)
        self.assertEqual((outside/'keep.txt').read_text(), 'safe')

    def test_malformed_partial_and_symlink_entries_preserved(self):
        result = self.delete();entry = self.trash.directory/result['trashId']
        manifest = json.loads((entry/'manifest.json').read_text())
        manifest['createdAt'] = 1
        for state in ['partial', 'prepared']:
            manifest['state'] = state;self.trash._save(entry, manifest)
            item = self.trash.maintain()['entries'][0]
            self.assertFalse(item['canRestore']);self.assertTrue(entry.exists())
        manifest['state'] = 'deleted';manifest['items'][0]['stored'] = '../outside'
        self.trash._save(entry, manifest)
        self.assertFalse(self.trash.maintain()['entries'][0]['canRestore'])
        self.assertTrue(entry.exists())
        with self.assertRaises(TrashError):
            self.trash.restore(result['trashId'])
        alias = self.trash.directory/('a'*32);alias.symlink_to(entry, target_is_directory=True)
        self.assertEqual(len(self.trash.maintain()['entries']), 2)
        with self.assertRaises(TrashError):
            self.trash.restore(alias.name)
        self.assertTrue(entry.exists())

    def test_cleanup_failure_keeps_record_and_retries(self):
        result = self.delete();expires = self.trash.maintain()['entries'][0]['expiresAt']
        with patch('skill_trash.shutil.rmtree', side_effect=PermissionError('test')):
            item = self.trash.maintain(expires)['entries'][0]
            self.assertFalse(item['canRestore']);self.assertIn('稍后重试', item['issue'])
            self.assertTrue((self.trash.directory/result['trashId']/'manifest.json').exists())
        self.assertEqual(self.trash.maintain(expires)['entries'], [])

    def test_startup_and_worker_cleanup_without_codex_databases(self):
        atlas = Atlas(self.home, self.agents, cache_dir=self.base/'cache')
        atlas.catalog = self.catalog
        sid = self.id_for('demo');plan = atlas.deletion_plan(sid)
        result = atlas.delete_skill(sid, plan['token'])
        entry = atlas.trash.directory/result['trashId']
        manifest = json.loads((entry/'manifest.json').read_text())
        manifest['createdAt'] = 1;atlas.trash._save(entry, manifest)
        restarted = Atlas(self.home, self.agents, cache_dir=self.base/'cache')
        self.assertFalse(entry.exists())
        self.assertEqual(restarted.snapshot['trash']['entries'], [])
        self.write_skill(self.skill.parent, 'demo')
        restarted.catalog.scan()
        plan = restarted.deletion_plan(sid);result = restarted.delete_skill(sid, plan['token'])
        entry = restarted.trash.directory/result['trashId']
        manifest = json.loads((entry/'manifest.json').read_text())
        manifest['createdAt'] = 1;restarted.trash._save(entry, manifest)
        worker = threading.Thread(target=restarted.run);worker.start()
        try:
            deadline = time.monotonic()+4
            while entry.exists() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertFalse(entry.exists())
        finally:
            restarted.stop.set();worker.join(5)

    def test_http_confirmation_origin_delete_restore_and_stale_publish(self):
        atlas = Atlas(self.home, self.agents, cache_dir=self.base/'cache')
        atlas.catalog = self.catalog
        atlas.publish(dict(atlas.snapshot))
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(atlas))
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def post(route, body, request_origin=origin):
            conn = HTTPConnection('127.0.0.1', server.server_port)
            headers = {'Content-Type': 'application/json'}
            if request_origin:
                headers['Origin'] = request_origin
            conn.request('POST', '/api/skill/'+route, json.dumps(body), headers)
            response = conn.getresponse();result = (response.status, json.loads(response.read()))
            conn.close();return result
        try:
            sid = self.id_for('demo')
            for bad_origin in [None, 'https://example.com']:
                self.assertEqual(post('deletion-plan', {'id': sid}, bad_origin)[0], 403)
            status, plan = post('deletion-plan', {'id': sid})
            self.assertEqual(status, 200)
            body = {'id': sid, 'token': plan['token']}
            self.assertEqual(post('delete', body)[0], 400)
            self.assertTrue(self.skill.exists())
            old_snapshot = dict(atlas.snapshot)
            status, result = post('delete', body | {'confirmed': True})
            self.assertEqual(status, 200)
            self.assertEqual(result['graph']['skills'], [])
            self.assertEqual(result['graph']['trash']['entries'][0]['id'], result['trashId'])
            atlas.publish(old_snapshot)
            self.assertEqual(atlas.snapshot['skills'], [])
            self.assertEqual(len(atlas.snapshot['trash']['entries']), 1)
            self.assertEqual(post('delete', body | {'confirmed': True})[0], 409)
            status, restored = post('restore', {'trashId': result['trashId']})
            self.assertEqual(status, 200)
            self.assertEqual(len(restored['graph']['skills']), 1)
            self.assertEqual(restored['graph']['trash']['entries'], [])
            self.assertTrue(self.skill.exists())
        finally:
            server.shutdown();server.server_close();worker.join(3)

    def test_http_batch_requires_confirmation_and_preserves_sessions(self):
        second = self.write_skill(self.home/'skills/second', 'second')
        self.catalog.scan()
        atlas = Atlas(self.home, self.agents, cache_dir=self.base/'cache')
        atlas.catalog = self.catalog
        sessions = [{'id': 'session-id', 'title': '保留的会话', 'status': 'completed'}]
        session_file = self.home/'sessions/keep.jsonl'
        session_file.parent.mkdir();session_file.write_text(json.dumps(sessions))
        session_bytes = session_file.read_bytes()
        ids = [self.id_for('demo'), self.id_for('second')]
        links = [{'skillId': sid, 'sessionId': 'session-id', 'count': 1} for sid in ids]
        atlas.publish({**atlas.snapshot, 'sessions': sessions, 'links': links})
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(atlas))
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def post(route, body, request_origin=origin):
            conn = HTTPConnection('127.0.0.1', server.server_port)
            conn.request('POST', '/api/skill/'+route, json.dumps(body),
                         {'Content-Type': 'application/json', 'Origin': request_origin})
            response = conn.getresponse();result = (response.status, json.loads(response.read()))
            conn.close();return result
        try:
            self.assertEqual(post('batch-deletion-plan', {'ids': ids}, 'https://example.com')[0], 403)
            self.assertEqual(post('batch-deletion-plan', {'ids': ids+['session-id']})[0], 404)
            status, plan = post('batch-deletion-plan', {'ids': ids})
            self.assertEqual(status, 200)
            body = {'ids': ids, 'token': plan['token']}
            self.assertEqual(post('batch-delete', body)[0], 400)
            self.assertTrue(self.skill.exists());self.assertTrue(second.exists())
            old_snapshot = dict(atlas.snapshot)
            status, result = post('batch-delete', body | {'confirmed': True})
            self.assertEqual(status, 200)
            self.assertEqual(result['graph']['sessions'], sessions)
            self.assertEqual(result['graph']['skills'], [])
            self.assertEqual(result['graph']['links'], [])
            self.assertEqual(len(result['graph']['trash']['entries']), 1)
            atlas.publish(old_snapshot)
            self.assertEqual(atlas.snapshot['skills'], [])
            self.assertEqual(atlas.snapshot['sessions'], sessions)
            self.assertEqual(post('batch-delete', body | {'confirmed': True})[0], 409)
            status, restored = post('restore', {'trashId': result['trashId']})
            self.assertEqual(status, 200)
            self.assertEqual(len(restored['graph']['skills']), 2)
            self.assertEqual(restored['graph']['sessions'], sessions)
            self.assertEqual(session_file.read_bytes(), session_bytes)
            self.assertTrue(self.skill.exists());self.assertTrue(second.exists())
        finally:
            server.shutdown();server.server_close();worker.join(3)


if __name__ == '__main__':
    unittest.main()
