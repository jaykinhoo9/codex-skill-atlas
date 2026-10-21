import json
from pathlib import Path
import shlex
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"skills/codex-skill-atlas/app"))
from server import Atlas, Catalog, LocalHTTPServer, metadata, observed_reads


class LocalServerTests(unittest.TestCase):
    def test_loopback_startup_does_not_require_dns(self):
        with patch('socket.getfqdn', side_effect=OSError('DNS unavailable')):
            with LocalHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler) as server:
                self.assertEqual(server.server_name, '127.0.0.1')
                self.assertGreater(server.server_port, 0)


class SkillDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base/'codex'
        self.agents = self.base/'agents'
        self.skill = self.home/'skills/test skill/SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text('---\nname: test-skill\ndescription: >\n  useful first line\n  second line\n---\n# Test\n')
        self.catalog = Catalog(self.home,self.agents).scan()

    def tearDown(self):
        self.temp.cleanup()

    def detect(self, command, actions=None):
        return observed_reads(command,actions or [],str(self.home),self.catalog)

    def test_structured_successful_read(self):
        self.assertEqual(len(self.detect('cat file', [{'type':'read','path':str(self.skill)}])),1)

    def test_shell_wrapped_and_space_path(self):
        command='cat '+shlex.quote(str(self.skill))
        self.assertEqual(len(self.detect('/bin/bash -lc '+shlex.quote(command))),1)

    def test_compound_read(self):
        command = 'pwd && head -80 '+shlex.quote(str(self.skill))
        self.assertEqual(len(self.detect('/bin/bash -lc '+shlex.quote(command))),1)

    def test_relative_path(self):
        self.assertEqual(len(self.detect("cat 'skills/test skill/SKILL.md'")),1)

    def test_read_through_alternate_home_symlink(self):
        alias = self.base/'linked-home'
        alias.symlink_to(self.home, target_is_directory=True)
        self.assertEqual(len(self.detect('cat '+shlex.quote(str(alias/'skills/test skill/SKILL.md')))), 1)

    def test_mention_and_quoted_code_are_not_reads(self):
        for command in [f'echo "cat {self.skill}"', f'echo "; cat {self.skill}"',
                        f'python3 -c "print(\'; cat {self.skill}\')"',
                        f'rg SKILL.md {self.home}', f'ls {self.skill}']:
            self.assertFalse(self.detect(command),command)

    def test_patch_and_heredoc_are_not_reads(self):
        command=f"python3 - <<'PY'\nsource = 'cat {self.skill}'\nPY"
        self.assertFalse(self.detect(command))
        self.assertFalse(self.detect('/bin/bash -lc '+shlex.quote(command)))

    def test_edit_is_not_a_read(self):
        self.assertFalse(self.detect('sed -i s/a/b/ '+shlex.quote(str(self.skill))))

    def test_unknown_path_is_not_invented(self):
        self.assertFalse(self.detect('cat /missing/skill/SKILL.md'))

    def test_multiline_metadata(self):
        skill=next(iter(self.catalog.skills.values()))
        self.assertEqual(skill['description'],'useful first line second line')

    def test_plugin_versions_merge_and_old_reads_resolve(self):
        for version in ['1.0','2.0']:
            root=self.home/'plugins/cache/personal/demo'/version
            (root/'.codex-plugin').mkdir(parents=True)
            (root/'.codex-plugin/plugin.json').write_text(json.dumps({'skills':'./skills'}))
            (root/'skills/hello').mkdir(parents=True)
            (root/'skills/hello/SKILL.md').write_text('---\nname: hello\n---\nHello')
        self.catalog.scan()
        skills=[s for s in self.catalog.skills.values() if s['plugin']=='demo']
        self.assertEqual(len(skills),1)
        self.assertEqual(len(skills[0]['copies']),2)
        old=self.home/'plugins/cache/personal/demo/0.5/skills/hello/SKILL.md'
        self.assertEqual(self.catalog.resolve(str(old)),skills[0]['id'])

    def test_symlink_is_deduplicated(self):
        (self.agents/'skills').mkdir(parents=True)
        (self.agents/'skills/alias').symlink_to(self.skill.parent,target_is_directory=True)
        self.catalog.scan()
        self.assertEqual(len(self.catalog.skills),1)


class RealtimeIntegrationTests(unittest.TestCase):
    def test_pending_to_completed_to_finished_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'codex';home.mkdir()
            skill=home/'skills/demo/SKILL.md';skill.parent.mkdir(parents=True)
            skill.write_text('---\nname: demo\n---\n# Demo')
            state=sqlite3.connect(home/'state_5.sqlite')
            state.executescript('CREATE TABLE threads(id,name,title,cwd,updated_at,archived);')
            state.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)',('t','Test session','prompt',str(home),int(time.time()),0));state.commit();state.close()
            history=sqlite3.connect(home/'thread_history_1.sqlite')
            history.executescript('CREATE TABLE thread_turns(thread_id,turn_id,status,started_at,completed_at,rollout_ordinal); CREATE TABLE thread_items(thread_id,item_id,turn_id,created_at_ms,item_json,item_type,rollout_ordinal,updated_at_ordinal);')
            history.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?,?)',('t','turn','inProgress',int(time.time()),None,1))
            command={'command':'cat '+str(skill),'commandActions':[{'type':'read','path':str(skill)}],'cwd':str(home),'status':'inProgress','exitCode':None}
            history.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',('t','item','turn',int(time.time()*1000),json.dumps(command),'commandExecution',2,2));history.commit()
            atlas=Atlas(home,Path(temp)/'agents',cache_dir=Path(temp)/'index')
            worker=threading.Thread(target=atlas.run,daemon=True);worker.start()
            def wait(predicate):
                deadline=time.time()+12
                while time.time()<deadline:
                    with atlas.lock:
                        if predicate(atlas.snapshot):return
                    time.sleep(.05)
                self.fail('State did not update: '+str(atlas.snapshot['status']))
            try:
                wait(lambda d:len(d['sessions'])==1)
                self.assertEqual(atlas.snapshot['links'],[])
                command.update(status='completed',exitCode=0)
                history.execute('UPDATE thread_items SET item_json=?,updated_at_ordinal=3',(json.dumps(command),));history.commit()
                wait(lambda d:len(d['links'])==1)
                self.assertTrue(atlas.snapshot['links'][0]['active'])
                self.assertEqual(atlas.snapshot['links'][0]['count'],1)
                history.execute("UPDATE thread_turns SET status='completed',completed_at=?",(int(time.time()),));history.commit()
                wait(lambda d:d['links'] and not d['links'][0]['active'])
                self.assertEqual(atlas.snapshot['links'][0]['count'],1)
                # A failed read must not add another observation.
                command.update(status='completed',exitCode=1)
                history.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)',('t','failed','turn',int(time.time()*1000),json.dumps(command),'commandExecution',4,4));history.commit()
                previous=atlas.snapshot['status']['updatedAt']
                wait(lambda d:d['status']['updatedAt']>previous)
                self.assertEqual(atlas.snapshot['links'][0]['count'],1)
            finally:
                atlas.stop.set();worker.join(5);history.close()


if __name__=='__main__':unittest.main()
