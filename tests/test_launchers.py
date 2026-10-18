"""Black-box launcher contracts, including Windows PowerShell 5.1 quoting."""
import base64
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[1]


BLOCKED_SHELLS = {}


@lru_cache(maxsize=1)
def launchers():
    found = []
    if os.name == 'nt':
        ps = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        if ps.is_file():
            policy_environment = os.environ.copy()
            # Python does not perform pwsh's special Windows PowerShell launch
            # handling. Let 5.1 build its own module path, not inherit PS7 modules.
            policy_environment.pop('PSModulePath', None)
            with tempfile.TemporaryDirectory(prefix='persona script probe ') as directory:
                probe_script = Path(directory)/'probe.ps1'
                probe_script.write_text('exit 0\n', encoding='ascii')
                probe = subprocess.run([str(ps), '-NoLogo', '-NoProfile', '-File', str(probe_script)],
                                       env=policy_environment, capture_output=True, timeout=20)
            if probe.returncode:
                BLOCKED_SHELLS['powershell5'] = 'Host refused the harmless script probe; no execution-policy change attempted'
            else:
                found.append(('powershell5', str(ps)))
        pwsh = os.environ.get('PERSONA_REVIEW_TEST_PWSH') or shutil.which('pwsh')
        if pwsh:
            found.append(('powershell7', pwsh))
        shell = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Git/bin/sh.exe'
        if shell.is_file():
            found.append(('sh', str(shell)))
    else:
        shell = shutil.which('sh')
        if shell:
            found.append(('sh', shell))
    return found


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='persona launcher ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.plugin = self.base/'plugin with spaces'
        for directory in ('scripts', 'schemas'):
            shutil.copytree(ROOT/directory, self.plugin/directory)
        self.cwd = self.base/'unrelated working directory'
        self.cwd.mkdir()
        self.backends = launchers()
        self.assertTrue(self.backends, 'No supported shell available for launcher tests')
        if os.environ.get('PERSONA_REVIEW_REQUIRE_SHELL_TESTS'):
            expected = {'sh', 'powershell5', 'powershell7'} if os.name == 'nt' else {'sh'}
            self.assertTrue(expected <= {name for name, _ in self.backends}, self.backends)

    def run_launcher(self, backend, arguments, environment=None, ps_location=None):
        name, executable = backend
        env = os.environ.copy()
        env['PERSONA_REVIEW_PYTHON'] = Path(sys.executable).as_posix()
        env['PERSONA_REVIEW_HOME'] = str(self.base/'never default data')
        if name == 'powershell5':
            env.pop('PSModulePath', None)
        for key, value in (environment or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        if name == 'sh':
            # MSYS startup does not use Windows CRT argument parsing. Send only
            # the script path across that boundary, and form the argv in sh.
            script = self.base/'invoke.sh'
            words = [Path(executable).as_posix(), (self.plugin/'scripts/persona-review.sh').as_posix(), *arguments]
            script.write_text('exec '+' '.join(shlex.quote(word) for word in words)+'\n',
                              encoding='utf-8', newline='\n')
            command = [executable, script.as_posix()]
        else:
            # Use typed PowerShell splatting so outer -File legacy parsing cannot
            # consume empty strings or quotes before the launcher receives them.
            encoded = base64.b64encode(json.dumps(arguments).encode('utf-8')).decode('ascii')
            script = self.base/'invoke.ps1'
            launcher = str(self.plugin/'scripts/persona-review.ps1').replace("'", "''")
            change_location = ("Set-Location -LiteralPath '"+str(ps_location).replace("'", "''")+"'\n") if ps_location else ''
            script.write_text(
                change_location+
                "$values = @([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"')) | ConvertFrom-Json)\n"
                "& '"+launcher+"' @values\nexit $LASTEXITCODE\n", encoding='ascii')
            command = [executable, '-NoLogo', '-NoProfile', '-File', str(script)]
        return subprocess.run(command, cwd=self.cwd, env=env, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=40)

    def stub_helper(self, exit_code=0):
        (self.plugin/'scripts/persona_review.py').write_text(
            'import json, os, sys\n'
            'sys.stdout.reconfigure(encoding="utf-8")\n'
            'print(json.dumps({"argv":sys.argv[1:],"cwd":os.getcwd(),"isolated":sys.flags.isolated,'
            '"automatic_install":os.environ.get("PYTHON_MANAGER_AUTOMATIC_INSTALL"),'
            '"allow_install":os.environ.get("PYLAUNCHER_ALLOW_INSTALL"),'
            '"always_install":os.environ.get("PYLAUNCHER_ALWAYS_INSTALL")}))\n'
            f'raise SystemExit({exit_code})\n', encoding='utf-8')

    def test_preserves_arguments_cwd_and_disables_auto_install(self):
        self.stub_helper()
        values = ['', 'two words', 'quote"inside', 'backslash\\', 'before\\"after',
                  'literal & $(not-a-command);', '日本語', 'line\nbreak', '--', '-x']
        for backend in self.backends:
            with self.subTest(backend=backend[0]):
                result = self.run_launcher(backend, values, {'PYLAUNCHER_ALLOW_INSTALL':'1',
                                                          'PYLAUNCHER_ALWAYS_INSTALL':'1',
                                                          'PYTHON_MANAGER_AUTOMATIC_INSTALL':'true'})
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = json.loads(result.stdout)
                self.assertEqual(actual['argv'], values)
                self.assertEqual(Path(actual['cwd']), self.cwd)
                self.assertEqual(actual['isolated'], 1)
                self.assertEqual(actual['automatic_install'], 'false')
                self.assertIsNone(actual['allow_install'])
                self.assertIsNone(actual['always_install'])
        # Verify the .NET Framework quoting branch with the real Windows Python
        # process even if local policy disallows running Windows PowerShell 5.1.
        pwsh = next((backend for backend in self.backends if backend[0] == 'powershell7'), None)
        if pwsh:
            launcher = self.plugin/'scripts/persona-review.ps1'
            launcher.write_text(launcher.read_text().replace(
                "if ($null -ne $info.PSObject.Properties['ArgumentList'])", 'if ($false)'))
            result = self.run_launcher(pwsh, values)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['argv'], values)

    def test_real_helper_relative_paths_and_empty_focus(self):
        for index, backend in enumerate(self.backends):
            with self.subTest(backend=backend[0]):
                directory = f'data {index} & literal'
                result = self.run_launcher(backend, ['--data-dir', directory, 'set', 'focus', ''])
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.run_launcher(backend, ['--data-dir', directory, 'resolve'])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['settings']['focus'], [])
                self.assertTrue((self.cwd/directory/'config.json').is_file())
                self.assertFalse((self.plugin/directory).exists())

    def test_missing_runtime_is_clear_and_does_not_write(self):
        for backend in self.backends:
            with self.subTest(backend=backend[0]):
                result = self.run_launcher(backend, ['set', 'scope', 'file'],
                                           {'PERSONA_REVIEW_PYTHON':None, 'PATH':''})
                self.assertEqual(result.returncode, 127, result.stderr)
                self.assertIn('Python 3.10+', result.stderr)
                self.assertIn('python.org', result.stderr)
                self.assertFalse((self.base/'never default data').exists())

    def test_invalid_override_does_not_fall_back(self):
        for backend in self.backends:
            with self.subTest(backend=backend[0]):
                result = self.run_launcher(backend, ['show'],
                                           {'PERSONA_REVIEW_PYTHON':str(self.base/'missing interpreter')})
                self.assertEqual(result.returncode, 127, result.stderr)
                self.assertEqual(result.stdout, '')

    def test_helper_failure_is_returned_without_retry(self):
        self.stub_helper(19)
        for backend in self.backends:
            with self.subTest(backend=backend[0]):
                result = self.run_launcher(backend, ['operation'])
                self.assertEqual(result.returncode, 19, result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 1)
        # Also exercise the real helper's schema/argument failure.
        shutil.copyfile(ROOT/'scripts/persona_review.py', self.plugin/'scripts/persona_review.py')
        for backend in self.backends:
            result = self.run_launcher(backend, ['--data-dir', 'bad settings', 'set', 'image_mode', 'invalid'])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse((self.cwd/'bad settings/config.json').exists())

    def test_pythonpath_cannot_inject_startup_code(self):
        self.stub_helper()
        injected = self.base/'injected module'
        injected.mkdir()
        marker = self.base/'injection executed'
        (injected/'sitecustomize.py').write_text('open('+repr(str(marker))+',"w").write("bad")\n')
        for backend in self.backends:
            result = self.run_launcher(backend, [], {'PYTHONPATH':str(injected)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())

    def test_default_runtime_discovery(self):
        for backend in self.backends:
            with self.subTest(backend=backend[0]):
                result = self.run_launcher(backend, ['--data-dir', 'untouched', 'show'],
                                           {'PERSONA_REVIEW_PYTHON':None})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['version'], 1)
                self.assertFalse((self.cwd/'untouched').exists())

    def test_shell_rejects_incompatible_probe_before_running_helper(self):
        shell = next((backend for backend in self.backends if backend[0] == 'sh'), None)
        if not shell:
            self.skipTest('POSIX shell not available')
        fake = self.base/'incompatible python'
        fake.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8', newline='\n')
        fake.chmod(0o755)
        result = self.run_launcher(shell, ['show'], {'PERSONA_REVIEW_PYTHON':fake.as_posix()})
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_relative_python_override_after_set_location(self):
        powershells = [backend for backend in self.backends if backend[0].startswith('powershell')]
        if not powershells:
            self.skipTest('PowerShell unavailable on this host')
        selected = self.base/'selected location with spaces'
        selected.mkdir()
        # The host Python and TEMP may be on different Windows drives. Build a
        # real interpreter fixture beside the selected location so a relative
        # override is always meaningful; no pip, downloads, or symlinks needed.
        runtime = selected/'python fixture with spaces'
        venv.EnvBuilder(with_pip=False, symlinks=False).create(runtime)
        executable = runtime/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        relative_python = os.path.relpath(executable, selected)
        self.stub_helper()
        for backend in powershells:
            with self.subTest(backend=backend[0]):
                relative = self.run_launcher(backend, ['relative'],
                                             {'PERSONA_REVIEW_PYTHON':relative_python}, ps_location=selected)
                absolute = self.run_launcher(backend, ['absolute'],
                                             {'PERSONA_REVIEW_PYTHON':str(executable)}, ps_location=selected)
                self.assertEqual(relative.returncode, 0, relative.stderr)
                self.assertEqual(absolute.returncode, 0, absolute.stderr)
                self.assertEqual(json.loads(relative.stdout)['argv'], ['relative'])
                self.assertEqual(Path(json.loads(relative.stdout)['cwd']), selected)
                self.assertEqual(json.loads(relative.stdout)['cwd'], json.loads(absolute.stdout)['cwd'])


if __name__ == '__main__':
    unittest.main()
