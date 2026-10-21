"""Native-platform regression tests; all files and services are temporary fixtures."""
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]/'skills/codex-skill-atlas/app'
sys.path.insert(0, str(APP))
from platform_support import file_lock, is_link
from server import Atlas, Catalog, LocalHTTPServer, make_handler, observed_reads
from skill_trash import SkillTrash, TrashError
from windows_reads import powershell_reads


class PowerShellTests(unittest.TestCase):
    def test_literal_reads_wrappers_aliases_and_unicode_paths(self):
        path = r'C:\demo skills\测试\SKILL.md'
        commands = [
            f"Get-Content -LiteralPath '{path}' -Raw",
            f'pwsh -NoProfile -Command "Get-Content \'{path}\'"',
            f"powershell.exe -Command \"gc -Path '{path}' -TotalCount 80\"",
            f"pwsh -c \"Get-Date; cat '{path}' | Select-Object -First 30\"",
            f"pwsh -c \"type '{path}'\"",
            f"Get-Date\nGet-Content '{path}'",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(powershell_reads(command), [path])

    def test_environment_expansion_and_single_quoted_literals(self):
        with patch.dict(os.environ, {'CODEX_HOME': r'C:\Codex 数据'}):
            for prefix in ['$env:CODEX_HOME', '${env:CODEX_HOME}']:
                self.assertEqual(powershell_reads(f'Get-Content "{prefix}\\skills\\demo\\SKILL.md"'),
                                 [r'C:\Codex 数据\skills\demo\SKILL.md'])
            self.assertEqual(powershell_reads("Get-Content '$env:CODEX_HOME\\skills\\demo\\SKILL.md'"), [])

    def test_strings_scripts_and_non_reads_are_not_counted(self):
        path = r'C:\demo\SKILL.md'
        commands = [
            f'Write-Output "Get-Content {path}"',
            f'Write-Output "; Get-Content {path}"',
            f'$example="; Get-Content {path}"',
            f"Set-Content '{path}' 'new text'",
            f"Get-ChildItem '{path}'",
            f"Get-Content -Unknown '{path}'",
            f"$example = @'\nGet-Content {path}\n'@",
            f"function Example {{Get-Date; Get-Content '{path}'}}",
            f'pwsh -Command \'Write-Output "Get-Content {path}"\'',
            'pwsh -EncodedCommand ZWNobyBoaQ==',
            'Get-Content "unclosed',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(powershell_reads(command))

    def test_native_paths_resolve_to_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)/'中文 space'
            skill = home/'skills/测试/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('---\nname: 测试技能\n---\n示例', encoding='utf-8')
            catalog = Catalog(home, Path(tmp)/'agents').scan()
            reads = observed_reads(f"Get-Content -LiteralPath '{skill}'", [], str(home), catalog)
            self.assertEqual(set(reads), set(catalog.skills))
            if os.name == 'nt':
                self.assertEqual(catalog.resolve(str(skill).upper().replace('\\', '/')), next(iter(reads)))


class PlatformStorageTests(unittest.TestCase):
    def test_lock_serializes_processes_and_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock, ready, acquired = root/'lock', root/'ready', root/'acquired'
            child = root/'child.py'
            child.write_text(
                "import sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n"
                "from platform_support import file_lock\nPath(sys.argv[3]).touch()\n"
                "with file_lock(sys.argv[2]):\n    Path(sys.argv[4]).touch()\n", encoding='utf-8')
            process = None
            try:
                with self.assertRaisesRegex(RuntimeError, 'release'), file_lock(lock):
                    process = subprocess.Popen([sys.executable, '-B', str(child), str(APP), str(lock), str(ready), str(acquired)])
                    deadline = time.monotonic()+10
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(ready.exists())
                    time.sleep(.15)
                    self.assertIsNone(process.poll())
                    self.assertFalse(acquired.exists())
                    raise RuntimeError('release')
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertTrue(acquired.exists())
            finally:
                if process is not None and process.poll() is None:
                    process.kill();process.wait()

    def test_unicode_recycle_restore_and_builtin_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)/'工作空间'
            skill = home/'skills/.system/测试技能/SKILL.md'
            skill.parent.mkdir(parents=True)
            original = '---\nname: 测试技能\n---\n完整中文说明'.encode('utf-8')
            skill.write_bytes(original)
            catalog = Catalog(home, Path(tmp)/'agents').scan()
            sid = next(iter(catalog.skills))
            self.assertEqual(catalog.skills[sid]['source'], 'builtin')
            trash = SkillTrash(Path(tmp)/'回收区')
            plan = trash.preview(catalog, sid)
            result = trash.delete(catalog, sid, plan['token'])
            self.assertFalse(skill.exists())
            self.assertEqual(trash.maintain()['entries'][0]['name'], '测试技能')
            trash.restore(result['trashId'])
            self.assertEqual(skill.read_bytes(), original)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_junction_recycle_restore_cleanup_and_shared_directory_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, agents = root/'codex', root/'agents'
            (home/'skills').mkdir(parents=True)
            external = root/'outside'
            external.mkdir();(external/'SKILL.md').write_text('---\nname: junction-demo\n---', encoding='utf-8')
            link = home/'skills/alias'
            def junction(alias, target):
                subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)], check=True, capture_output=True)
            junction(link, external)
            self.assertTrue(is_link(link))
            catalog = Catalog(home, agents).scan()
            sid = next(iter(catalog.skills))
            trash = SkillTrash(root/'trash')
            plan = trash.preview(catalog, sid)
            result = trash.delete(catalog, sid, plan['token'])
            self.assertFalse(os.path.lexists(link))
            self.assertTrue((external/'SKILL.md').exists())
            trash.restore(result['trashId'])
            self.assertTrue(is_link(link))
            plan = trash.preview(catalog.scan(), sid)
            trash.delete(catalog, sid, plan['token'])
            expires = trash.maintain()['entries'][0]['expiresAt']
            self.assertEqual(trash.maintain(expires)['entries'], [])
            self.assertTrue((external/'SKILL.md').exists())
            shared = root/'shared'
            nested = shared/'child/SKILL.md';nested.parent.mkdir(parents=True)
            nested.write_text('---\nname: shared-child\n---', encoding='utf-8')
            junction(home/'skills/shared', shared)
            catalog.scan()
            with self.assertRaisesRegex(TrashError, '共享符号链接'):
                trash.preview(catalog, next(iter(catalog.skills)))
            # A junction used as the lock itself must also be rejected.
            with self.assertRaises(OSError), file_lock(home/'skills/shared'):
                self.fail('junction lock accepted')


class ShutdownTests(unittest.TestCase):
    def test_shutdown_requires_local_origin_and_private_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atlas = Atlas(root/'codex', root/'agents', cache_dir=root/'runtime')
            atlas.shutdown_token = 'fixture-private-token'
            server = LocalHTTPServer(('127.0.0.1', 0), make_handler(atlas))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            origin = f'http://127.0.0.1:{server.server_port}'
            def request(method, route, body=None, request_origin=origin):
                conn = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                conn.request(method, route, json.dumps(body) if body is not None else None,
                             {'Content-Type': 'application/json', 'Origin': request_origin})
                response = conn.getresponse()
                result = response.status, json.loads(response.read())
                conn.close()
                return result
            try:
                for route in ['/api/health', '/api/graph']:
                    status, body = request('GET', route)
                    self.assertEqual(status, 200)
                    self.assertNotIn(atlas.shutdown_token, json.dumps(body))
                for token in [None, 'wrong', '错误', 123]:
                    self.assertEqual(request('POST', '/api/shutdown', {'token': token})[0], 403)
                self.assertEqual(request('POST', '/api/shutdown', {'token': atlas.shutdown_token}, 'https://example.com')[0], 403)
                self.assertTrue(worker.is_alive())
                self.assertEqual(request('POST', '/api/shutdown', {'token': atlas.shutdown_token})[0], 200)
                worker.join(3)
                self.assertFalse(worker.is_alive())
            finally:
                server.shutdown();server.server_close();worker.join(3)


if __name__ == '__main__':
    unittest.main()
