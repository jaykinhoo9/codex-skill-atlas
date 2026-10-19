"""Confirmed, recoverable removal of catalogued skill directories only."""
import hashlib
from contextlib import contextmanager
import fcntl
from functools import wraps
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import threading
import time

RETENTION_DAYS = 30
RETENTION_MS = RETENTION_DAYS * 86400 * 1000


def exclusive(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._exclusive():
            return method(self, *args, **kwargs)
    return locked


class TrashError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def within(path, root):
    return path == root or root in path.parents


def fingerprint(paths):
    records = []
    for target in paths:
        entries = [target]
        if target.is_dir() and not target.is_symlink():
            for current, dirs, files in os.walk(target, followlinks=False):
                entries.extend(Path(current)/name for name in sorted(dirs+files))
        for entry in entries:
            stat = entry.lstat()
            records.append((str(entry), stat.st_dev, stat.st_ino, stat.st_mode,
                            stat.st_size, stat.st_mtime_ns,
                            os.readlink(entry) if entry.is_symlink() else None))
    return hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()


class SkillTrash:
    def __init__(self, directory, protected_paths=()):
        self.directory = Path(directory).absolute()
        self.protected_paths = [Path(p).resolve() for p in protected_paths]
        self.plans = {}
        self.lock = threading.RLock()

    @contextmanager
    def _exclusive(self):
        # Also serialize CLI restores against the server's automatic cleanup.
        with self.lock:
            if self.directory.is_symlink():
                raise TrashError('回收目录不能是符号链接，请检查回收目录。')
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.directory/'.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as file:
                fcntl.flock(file, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(file, fcntl.LOCK_UN)

    def _read(self, trash_id):
        if not isinstance(trash_id, str) or len(trash_id) != 32 or any(c not in '0123456789abcdef' for c in trash_id):
            raise TrashError('回收记录不存在。', 404)
        entry = self.directory/trash_id
        try:
            if entry.is_symlink() or not entry.is_dir() or (entry/'manifest.json').is_symlink():
                raise ValueError()
            manifest = json.loads((entry/'manifest.json').read_text())
            if not isinstance(manifest, dict) or manifest.get('id') != trash_id or not isinstance(manifest.get('name'), str):
                raise ValueError()
            created = manifest.get('createdAt')
            if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created) or created <= 0:
                raise ValueError()
            items = manifest.get('items')
            if not isinstance(items, list) or not items:
                raise ValueError()
            stored_names = set()
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('original'), str) or not isinstance(item.get('stored'), str):
                    raise ValueError()
                stored = item['stored']
                if not Path(item['original']).is_absolute() or not stored or Path(stored).name != stored or stored in {'.', '..', 'manifest.json', 'manifest.tmp'} or stored in stored_names:
                    raise ValueError()
                stored_names.add(stored)
            return entry, manifest
        except (OSError, ValueError, TypeError) as exc:
            raise TrashError('回收记录不完整，无法自动处理。', 409 if entry.exists() else 404) from exc

    @exclusive
    def maintain(self, now_ms=None):
        """Purge expired, valid deleted entries and return the remaining inventory."""
        now = int(time.time()*1000) if now_ms is None else now_ms
        entries, warnings = [], []
        for entry in sorted(self.directory.iterdir()):
            if len(entry.name) != 32 or any(c not in '0123456789abcdef' for c in entry.name):
                continue
            try:
                _, manifest = self._read(entry.name)
            except TrashError:
                entries.append({'id': entry.name, 'name': '待检查的回收记录', 'createdAt': None,
                                'expiresAt': None, 'canRestore': False, 'paths': [str(entry)],
                                'issue': '记录不完整，已保留文件，请检查回收目录。'})
                continue
            state = manifest.get('state')
            if state in {'restored', 'cancelled'}:
                continue
            expires = manifest['createdAt'] + RETENTION_MS
            issue = ''
            if state == 'deleted' and now >= expires:
                try:
                    # Only remove this validated trash entry, never manifest original paths.
                    # rmtree does not follow symlinks in the recycled payload.
                    self._purge(entry, manifest)
                    continue
                except OSError:
                    issue = '已到期，自动清理未完成，稍后重试。'
            elif state != 'deleted':
                issue = '文件移动未完成，已保留文件，请检查回收目录。'
            elif not all(os.path.lexists(entry/i['stored']) for i in manifest['items']):
                issue = '部分回收文件缺失，请检查回收目录。'
            entries.append({'id': entry.name, 'name': manifest['name'], 'source': manifest.get('source'),
                            'createdAt': manifest['createdAt'], 'expiresAt': expires,
                            'paths': [i['original'] for i in manifest['items']],
                            'skillCount': manifest.get('skillCount', 1),
                            'canRestore': not issue, 'issue': issue})
        entries.sort(key=lambda item: item['createdAt'] or 0, reverse=True)
        return {'entries': entries, 'retentionDays': RETENTION_DAYS, 'warnings': warnings}

    def _purge(self, entry, manifest):
        # Keep the manifest until all payloads are gone, so a partial cleanup can retry.
        for child in entry.iterdir():
            if child.name in {'manifest.json', 'manifest.tmp'}:
                continue
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)
        (entry/'manifest.tmp').unlink(missing_ok=True)
        (entry/'manifest.json').unlink()
        try:
            entry.rmdir()
        except OSError:
            self._save(entry, manifest)
            raise

    def _describe(self, catalog, sid):
        skill = catalog.skills.get(sid)
        if not skill:
            raise TrashError('这项技能已不存在，请刷新后重试。', 404)
        targets = set()
        for copy in skill['copies']:
            file = Path(os.path.abspath(copy['path']))
            root = Path(os.path.abspath(copy['root']))
            if file.name != 'SKILL.md' or not within(file, root) or not file.exists():
                raise TrashError('技能路径已变化，请重新打开详情。')
            # Moving a direct skill symlink removes the installation, not its external target.
            # An intermediate shared symlink cannot be removed as one isolated skill.
            for ancestor in file.parent.parents:
                if ancestor == root:
                    break
                if within(ancestor, root) and ancestor.is_symlink():
                    raise TrashError('这项技能位于共享符号链接目录，无法单独移除该目录。')
            target = file if file.parent == root or file.is_symlink() else file.parent
            if any(within(protected, target.resolve()) for protected in self.protected_paths):
                raise TrashError('不能从正在运行的看板中删除看板自身，请先停止服务后手动卸载。')
            if within(self.directory, target):
                raise TrashError('回收目录位于技能目录内，无法安全移动。')
            targets.add(target)
        # A parent skill may contain other skills; move that directory only once.
        targets = sorted((p for p in targets if not any(p != q and within(p, q) for q in targets)), key=str)
        affected = [s for s in catalog.skills.values()
                    if any(within(Path(os.path.abspath(c['path'])), p) for c in s['copies'] for p in targets)]
        return {
            'skillId': sid, 'name': skill['name'],
            'source': skill['source'],
            'paths': [{'path': str(p), 'kind': 'link' if p.is_symlink() else 'directory' if p.is_dir() else 'file'} for p in targets],
            'affectedSkills': [{'id': s['id'], 'name': s['name']} for s in affected],
            'managed': skill['source'] in {'builtin', 'plugin'},
            'signature': fingerprint(targets),
        }

    def _batch_ids(self, ids):
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 1000
                or any(not isinstance(sid, str) or not 1 <= len(sid) <= 200 for sid in ids)):
            raise TrashError('请选择要删除的技能。', 400)
        return sorted(set(ids))

    def _describe_batch(self, catalog, ids):
        # Catalog membership is required for every ID: sessions can never be targets.
        ids = self._batch_ids(ids)
        plans = [self._describe(catalog, sid) for sid in ids]
        paths = {p['path']: p for plan in plans for p in plan['paths']}
        targets = sorted(p for p in paths if not any(p != q and within(Path(p), Path(q)) for q in paths))
        affected = {s['id']: s for plan in plans for s in plan['affectedSkills']}
        sources = {plan['source'] for plan in plans}
        return {
            'skillIds': ids, 'name': f'{len(affected)} 项技能',
            'source': next(iter(sources)) if len(sources) == 1 else 'mixed',
            'selectedSkills': [{'id': plan['skillId'], 'name': plan['name']} for plan in plans],
            'affectedSkills': [affected[sid] for sid in sorted(affected)],
            'paths': [paths[p] for p in targets],
            'managed': any(plan['managed'] for plan in plans),
            'signature': fingerprint([Path(p) for p in targets]),
        }

    def _remember(self, plan):
        now = time.time()
        self.plans = {k: v for k, v in self.plans.items() if v['expires'] > now}
        if len(self.plans) >= 64:
            self.plans.pop(next(iter(self.plans)))
        token = secrets.token_urlsafe(32)
        self.plans[token] = {**plan, 'expires': now+300}
        return {k: v for k, v in plan.items() if k != 'signature'} | {'token': token}

    def preview(self, catalog, sid):
        return self._remember(self._describe(catalog, sid))

    def preview_batch(self, catalog, ids):
        return self._remember(self._describe_batch(catalog, ids))

    @exclusive
    def delete(self, catalog, sid, token):
        plan = self.plans.pop(token, None)
        if not plan or plan.get('skillId') != sid or plan['expires'] < time.time():
            raise TrashError('删除确认已失效，请重新打开删除窗口。')
        return self._delete_plan(plan, self._describe(catalog, sid))

    @exclusive
    def delete_batch(self, catalog, ids, token):
        ids = self._batch_ids(ids)
        plan = self.plans.pop(token, None)
        if not plan or plan.get('skillIds') != ids or plan['expires'] < time.time():
            raise TrashError('删除确认已失效，请重新打开删除窗口。')
        return self._delete_plan(plan, self._describe_batch(catalog, ids))

    def _delete_plan(self, plan, current):
        if current != {k: v for k, v in plan.items() if k != 'expires'}:
            raise TrashError('技能文件或删除范围已变化，请重新确认后再删除。')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        trash_id = secrets.token_hex(16)
        entry = self.directory/trash_id
        entry.mkdir(mode=0o700)
        manifest = {
            'id': trash_id, 'name': plan['name'], 'createdAt': int(time.time()*1000), 'state': 'prepared',
            'source': plan['source'], 'skillCount': len(plan['affectedSkills']),
            'items': [{'original': p['path'], 'stored': f'{i:03d}-{Path(p["path"]).name}'} for i, p in enumerate(plan['paths'])],
        }
        self._save(entry, manifest)
        moved = []
        try:
            for item in manifest['items']:
                # Rename is atomic, preserves symlinks/permissions, and never copies partially.
                os.rename(item['original'], entry/item['stored'])
                moved.append(item)
            manifest['state'] = 'deleted'
            self._save(entry, manifest)
        except OSError as exc:
            self._rollback(entry, manifest, moved, restoring=False)
            raise TrashError('移入回收区失败，已还原原文件。请检查目录权限以及是否位于同一磁盘。') from exc
        return {'trashId': trash_id, 'name': plan['name'], 'recoveryPath': str(entry),
                'removedSkillIds': [s['id'] for s in plan['affectedSkills']]}

    @exclusive
    def restore(self, trash_id):
        entry, manifest = self._read(trash_id)
        if manifest.get('state') != 'deleted':
            raise TrashError('这项技能已恢复，或回收记录需要手动检查。')
        if time.time()*1000 >= manifest['createdAt'] + RETENTION_MS:
            try:
                self._purge(entry, manifest)
            except OSError as exc:
                raise TrashError('已超过 30 天保留期，无法恢复；清理失败，将自动重试。', 410) from exc
            raise TrashError('已超过 30 天保留期，已永久清理，无法恢复。', 410)
        for item in manifest['items']:
            original, stored = Path(item['original']), entry/item['stored']
            if stored.parent != entry or not original.is_absolute() or not os.path.lexists(stored):
                raise TrashError('回收记录不完整，无法自动恢复。')
            if os.path.lexists(original):
                raise TrashError('原位置已有同名文件，已停止恢复以避免覆盖：'+str(original))
            if not original.parent.is_dir():
                raise TrashError('原目录已不存在，请先恢复上级目录：'+str(original.parent))
        moved = []
        try:
            for item in manifest['items']:
                if os.path.lexists(item['original']):
                    raise OSError('Restore destination already exists')
                os.rename(entry/item['stored'], item['original'])
                moved.append(item)
            manifest['state'] = 'restored'
            self._save(entry, manifest)
        except OSError as exc:
            self._rollback(entry, manifest, moved, restoring=True)
            raise TrashError('恢复失败，技能仍保存在回收区。') from exc
        return {'name': manifest['name'], 'trashId': trash_id}

    @staticmethod
    def _save(entry, manifest):
        temp = entry/'manifest.tmp'
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(temp, entry/'manifest.json')

    def _rollback(self, entry, manifest, moved, restoring):
        try:
            for item in reversed(moved):
                source, target = (Path(item['original']), entry/item['stored']) if restoring else (entry/item['stored'], Path(item['original']))
                if os.path.lexists(target):
                    raise OSError('Rollback destination already exists')
                os.rename(source, target)
        except OSError as exc:
            manifest['state'] = 'partial'
            self._save(entry, manifest)
            raise TrashError('部分文件移动后未能自动还原，请根据回收记录恢复：'+str(entry)) from exc
        manifest['state'] = 'deleted' if restoring else 'cancelled'
        self._save(entry, manifest)
