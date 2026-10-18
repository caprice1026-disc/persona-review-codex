"""Offline distribution boundaries; no user directories or credentials are read."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_dist', ROOT/'tools/build_dist.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='persona distribution ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'source with spaces'
        self.root.mkdir()
        _, files = build.package_files(ROOT)
        for name in [*files, 'distribution/files.txt', 'distribution/marketplace.json', 'tools/build_dist.py']:
            dest = self.root/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((ROOT/name).read_bytes())

    def cli(self, *args):
        return subprocess.run([sys.executable, str(self.root/'tools/build_dist.py'), *args],
                              cwd=self.temp.name, capture_output=True, text=True, encoding='utf-8')

    def test_reproducible_across_paths_and_line_endings(self):
        expected = build.artifacts(ROOT)
        for path in self.root.rglob('*'):
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        self.assertEqual(expected, build.artifacts(self.root))
        self.assertEqual(expected, build.artifacts(ROOT))

    def test_exact_contents_checksums_and_fixed_zip_metadata(self):
        manifest, files = build.package_files(ROOT)
        outputs = build.artifacts(ROOT)
        checksums = outputs['SHA256SUMS'].decode().splitlines()
        self.assertEqual(checksums, [hashlib.sha256(data).hexdigest()+'  '+name
                                   for name, data in sorted(outputs.items()) if name.endswith('.zip')])
        for name, data in outputs.items():
            if not name.endswith('.zip'):
                continue
            with self.subTest(name=name), zipfile.ZipFile(io.BytesIO(data)) as archive:
                prefix = 'persona-review-marketplace/plugins/persona-review/' if 'marketplace' in name else 'persona-review/'
                expected = {prefix+key for key in files}
                if 'marketplace' in name:
                    expected.add('persona-review-marketplace/.agents/plugins/marketplace.json')
                self.assertEqual(set(archive.namelist()), expected)
                self.assertEqual(archive.namelist(), sorted(expected))
                for entry in archive.infolist():
                    self.assertEqual(entry.date_time, build.STAMP)
                    self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(stat.S_IFMT(entry.external_attr >> 16), stat.S_IFREG)
                    self.assertEqual(stat.S_IMODE(entry.external_attr >> 16), 0o755 if entry.filename.endswith('.sh') else 0o644)
                    self.assertFalse(PurePosixPath(entry.filename).is_absolute())
                    self.assertNotIn('..', PurePosixPath(entry.filename).parts)
                self.assertEqual(json.loads(archive.read(prefix+'plugin.json')), manifest)
                self.assertEqual(archive.read(prefix+'LICENSE'), (ROOT/'LICENSE').read_bytes().replace(b'\r\n', b'\n'))
        self.assertEqual(manifest['license'], 'Apache-2.0')
        self.assertTrue(manifest['extensions']['com.openai']['requires_local_executor'])

    def test_marketplace_resolves_and_extracted_helper_runs_elsewhere(self):
        outputs = build.artifacts(ROOT)
        data = next(value for name, value in outputs.items() if 'marketplace' in name)
        destination = Path(self.temp.name)/'extract with spaces'
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(destination)
        market = destination/'persona-review-marketplace'
        catalog = json.loads((market/'.agents/plugins/marketplace.json').read_text())
        plugin = market/catalog['plugins'][0]['source']['path']
        self.assertEqual(json.loads((plugin/'plugin.json').read_text())['name'], catalog['plugins'][0]['name'])
        result = subprocess.run([sys.executable, '-I', str(plugin/'scripts/persona_review.py'),
                                 '--data-dir', 'untouched data', 'resolve'], cwd=self.temp.name,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['settings']['character_id'], 'guide')
        self.assertFalse((Path(self.temp.name)/'untouched data').exists())
        # Exercise the packaged launcher too, after text/permission normalization.
        from test_launchers import launchers
        shell = next((executable for name, executable in launchers() if name == 'sh'), None)
        self.assertIsNotNone(shell, 'A POSIX shell is required for extracted-launcher checks')
        environment = os.environ.copy()
        environment['PERSONA_REVIEW_PYTHON'] = Path(sys.executable).as_posix()
        result = subprocess.run([shell, (plugin/'scripts/persona-review.sh').as_posix(),
                                 '--data-dir', 'untouched data', 'resolve'], cwd=self.temp.name,
                                env=environment, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['settings']['character_id'], 'guide')
        self.assertFalse((Path(self.temp.name)/'untouched data').exists())

    def test_unlisted_personal_data_never_changes_artifacts(self):
        expected = build.artifacts(self.root)
        for name in ('user-data/config.json', 'private/image.png', '.env', '.codex/auth.json', 'artifacts/output.log'):
            target = self.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('SYNTHETIC EXCLUDED MARKER', encoding='utf-8')
        self.assertEqual(expected, build.artifacts(self.root))

    def test_unsafe_allowlist_entries_fail_closed(self):
        original = (self.root/'distribution/files.txt').read_text()
        for name in ('../outside.md', '/absolute.md', 'C:/outside.md', 'docs\\design.md',
                     'docs/../README.md', 'docs//design.md', '.env', 'private/config.json',
                     'tests/fixture.json', 'README.md'):
            with self.subTest(name=name):
                (self.root/'distribution/files.txt').write_text(original+'\n'+name+'\n')
                with self.assertRaises(ValueError):
                    build.artifacts(self.root)

    def test_binary_oversize_and_symlink_inputs_rejected(self):
        target = self.root/'examples/persona.json'
        for contents in (b'\0binary', b'x'*2_000_001):
            target.write_bytes(contents)
            with self.assertRaises(ValueError):
                build.artifacts(self.root)
        # Mock only the symlink marker: ordinary path/content validation still runs.
        from unittest.mock import patch
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == target or original(p)):
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                build.safe_read(self.root, 'examples/persona.json')

    def test_invalid_marketplace_and_missing_runtime_are_rejected(self):
        path = self.root/'distribution/marketplace.json'
        original = json.loads(path.read_text())
        for entries in ([], [{**original['plugins'][0], 'source':{'source':'local','path':'../outside'}}]):
            path.write_text(json.dumps({**original, 'plugins':entries}))
            with self.assertRaises(ValueError):
                build.artifacts(self.root)
        path.write_text(json.dumps(original))
        allow = self.root/'distribution/files.txt'
        allow.write_text(allow.read_text().replace('scripts/persona_review.py\n', ''))
        with self.assertRaisesRegex(ValueError, 'required runtime'):
            build.artifacts(self.root)

    def test_build_check_and_no_overwrite(self):
        out = Path(self.temp.name)/'output with spaces'
        self.assertEqual(self.cli('--output', str(out), '--check').returncode, 2)
        self.assertFalse(out.exists())
        self.assertEqual(self.cli('--output', str(out)).returncode, 0)
        self.assertEqual(self.cli('--output', str(out), '--check').returncode, 0)
        archive = next(out.glob('*.zip'))
        archive.write_bytes(b'KEEP DIFFERING ARTIFACT')
        before = {p.name:p.read_bytes() for p in out.iterdir()}
        self.assertEqual(self.cli('--output', str(out)).returncode, 2)
        self.assertEqual(self.cli('--output', str(out), '--check').returncode, 2)
        self.assertEqual(before, {p.name:p.read_bytes() for p in out.iterdir()})

    def test_packaged_relative_document_links_resolve(self):
        _, files = build.package_files(ROOT)
        for name, content in files.items():
            if not name.endswith('.md'):
                continue
            for link in re.findall(r'\]\(([^)]+)\)', content.decode()):
                link = unquote(link.split('#',1)[0])
                if not link or '://' in link or link.startswith('mailto:'):
                    continue
                target = (ROOT/Path(name).parent/link).resolve().relative_to(ROOT.resolve()).as_posix()
                self.assertIn(target, files, f'{name}: {link}')

    def test_clean_autocrlf_checkout(self):
        executable = shutil.which('git')
        if not executable:
            if os.environ.get('PERSONA_REVIEW_REQUIRE_SHELL_TESTS'):
                self.fail('Git is required for the CI checkout regression')
            self.skipTest('Git unavailable for checkout regression')
        shutil.copyfile(ROOT/'.gitattributes', self.root/'.gitattributes')
        (self.root/'tests').mkdir()
        for name in ('test_distribution.py', 'test_launchers.py'):
            shutil.copyfile(ROOT/'tests'/name, self.root/'tests'/name)
        environment = os.environ.copy()
        environment['GIT_CONFIG_NOSYSTEM'] = '1'
        environment['GIT_CONFIG_GLOBAL'] = os.devnull
        checkout = Path(self.temp.name)/'autocrlf checkout'
        checkout.mkdir()
        for args in (['init', '--quiet'], ['add', '.'],
                     ['checkout-index', '--all', '--prefix='+checkout.as_posix()+'/']):
            result = subprocess.run([executable, '-c', 'safe.directory='+self.root.as_posix(),
                                     '-c', 'core.autocrlf=true', *args], cwd=self.root,
                                    env=environment, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b'\r\n', (checkout/'LICENSE').read_bytes())
        self.assertEqual(build.artifacts(ROOT), build.artifacts(checkout))
        # Run all other distribution tests from the real Git checkout; exclude
        # this test to avoid recursively creating more checkouts.
        command = ('import unittest, test_distribution; '
                   'suite=unittest.defaultTestLoader.loadTestsFromTestCase(test_distribution.DistributionTests); '
                   'suite=unittest.TestSuite(t for t in suite if not t.id().endswith("test_clean_autocrlf_checkout")); '
                   'result=unittest.TextTestRunner().run(suite); raise SystemExit(not result.wasSuccessful())')
        result = subprocess.run([sys.executable, '-c', command], cwd=checkout/'tests',
                                env=environment, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Also support preexisting CRLF license text, not only new LF checkouts.
        license_file = checkout/'LICENSE'
        license_file.write_bytes(license_file.read_bytes().replace(b'\n', b'\r\n'))
        result = subprocess.run([sys.executable, '-m', 'unittest',
                                'test_distribution.DistributionTests.test_exact_contents_checksums_and_fixed_zip_metadata'],
                               cwd=checkout/'tests', env=environment, capture_output=True,
                               text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
