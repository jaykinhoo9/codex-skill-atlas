import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from urllib.request import build_opener, ProxyHandler
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT/'skills/codex-skill-atlas'
LAUNCH = SKILL/'scripts/launch.py'


class DistributionTests(unittest.TestCase):
    def test_installed_skill_launch_reuse_port_fallback_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install = base/'installed skills/codex-skill-atlas'
            subprocess.run([sys.executable, str(ROOT/'scripts/install.py'), '--dest', str(install)], check=True, capture_output=True)
            self.assertTrue((install/'SKILL.md').is_file())
            again = subprocess.run([sys.executable, str(ROOT/'scripts/install.py'), '--dest', str(install)], capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            home, agents, data = base/'codex', base/'agents', base/'runtime'
            demo = home/'skills/demo/SKILL.md';demo.parent.mkdir(parents=True)
            demo.write_text('---\nname: demo\ndescription: Synthetic launch fixture\n---\nDemo')
            command = [sys.executable, str(install/'scripts/launch.py'), '--no-open', '--codex-home', str(home),
                       '--agents-home', str(agents), '--data-dir', str(data)]
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1', 0));occupied.listen()
                port = occupied.getsockname()[1]
                command += ['--port', str(port)]
                try:
                    first = json.loads(subprocess.check_output(command, text=True))
                    self.assertEqual(first['status'], 'started')
                    self.assertNotEqual(first['url'], f'http://127.0.0.1:{port}')
                    second = json.loads(subprocess.check_output(command, text=True))
                    self.assertEqual(second['status'], 'reused')
                    self.assertEqual(first['pid'], second['pid'])
                    with build_opener(ProxyHandler({})).open(first['url']+'/api/graph') as response:
                        graph = json.load(response)
                    self.assertEqual([s['name'] for s in graph['skills'] if s['source'] == 'local'], ['demo'])
                    self.assertEqual(graph['sessions'], [])
                    self.assertTrue((data/'observations.sqlite').exists())
                    self.assertFalse(list(install.rglob('*.sqlite')))
                    self.assertFalse(list(install.rglob('*.log')))
                    self.assertFalse(list(install.rglob('__pycache__')))
                    self.assertTrue(demo.exists())
                    # A different profile cannot overwrite a live instance's metadata.
                    conflict = subprocess.run(command+['--codex-home', str(base/'other')], capture_output=True)
                    self.assertNotEqual(conflict.returncode, 0)
                    self.assertIn(b'another running Skill Atlas', conflict.stderr)
                finally:
                    stopped = subprocess.run(command+['--stop'], capture_output=True, text=True)
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertEqual(json.loads(stopped.stdout)['status'], 'stopped')
                self.assertEqual(json.loads(subprocess.check_output(command+['--stop'], text=True))['status'], 'not-running')
                # The occupied service was never touched.
                self.assertEqual(occupied.getsockname()[1], port)

    def test_runtime_cannot_be_stored_inside_installation(self):
        result = subprocess.run([sys.executable, str(LAUNCH), '--no-open', '--data-dir', str(SKILL/'private')], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'outside', result.stderr)
        self.assertFalse((SKILL/'private').exists())

    def test_build_is_allowlisted_reproducible_and_installable(self):
        subprocess.run([sys.executable, str(ROOT/'scripts/build_release.py')], check=True, capture_output=True)
        version = json.loads((ROOT/'release-files.json').read_text())
        package = ROOT/f'dist/codex-skill-atlas-v{version["version"]}.zip'
        first = package.read_bytes()
        subprocess.run([sys.executable, str(ROOT/'scripts/build_release.py')], check=True, capture_output=True)
        self.assertEqual(first, package.read_bytes())
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(set(archive.namelist()), {'codex-skill-atlas/'+p for p in version['skillFiles']})
            with tempfile.TemporaryDirectory() as tmp:
                archive.extractall(tmp)
                launch = Path(tmp)/'codex-skill-atlas/scripts/launch.py'
                result = subprocess.run([sys.executable, str(launch), '--help'], capture_output=True)
                self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
