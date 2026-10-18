# Runtime and installation troubleshooting

The current helper uses Python 3.10+ and its standard library. The shell and
PowerShell launchers select an existing Python, then run the packaged helper.
They are not alternate implementations. Bash/jq fallback remains unimplemented.

## Select an existing Python

Run the launcher from the trusted plugin installation, even when the current
working directory is the project being reviewed. Relative input/data paths stay
relative to your current directory; the helper and schemas resolve from the
plugin's location. Quote paths containing spaces.

macOS/Linux:

```sh
sh ./scripts/persona-review.sh --help
PERSONA_REVIEW_PYTHON="/trusted/path/to/python3" sh ./scripts/persona-review.sh show
```

Windows PowerShell:

```powershell
& .\scripts\persona-review.ps1 --help
$env:PERSONA_REVIEW_PYTHON = 'C:\trusted\path\python.exe'
& .\scripts\persona-review.ps1 show
```

The override is one trusted executable, not a command string or extra arguments.
An invalid explicit override fails without selecting a different interpreter.
Relative executable paths in PowerShell resolve from the current PowerShell
location, including after `Set-Location`.
Automatic Windows selection checks `py.exe -3`, `python3.exe`, then `python.exe`;
the shell launcher checks `python3`, then `python`. Use a trusted PATH; do not
include the reviewed project in it. Version detection happens before the helper
starts. Once started, helper errors propagate without another backend attempt.

Python runs with `-I` to ignore Python-specific environment/path injection.
Runtime probes do not read or create persona-review configuration. The launchers
disable Python install-manager automatic installation in their child environment
and remove legacy launcher auto-install flags. They do not change user settings.

If no compatible runtime is available, the launcher exits 127 with guidance.
You may choose an existing Python using the override or install Python yourself
from [python.org](https://www.python.org/downloads/) or your trusted OS package
manager, then reopen the terminal. No automatic installation occurs. Having
Node.js does not satisfy this preview's helper requirement.

If PowerShell policy blocks a downloaded script, respect that policy. A verified
Python executable can run the helper directly without changing execution policy:

```powershell
& 'C:\trusted\path\python.exe' -I .\scripts\persona_review.py --data-dir '../demo data' resolve
```

Use actual paths from your installation, not the illustrative paths above.
`--data-dir` belongs before `show`, `resolve`, or any other subcommand. An empty
focus value clears the list: `set focus ''` from an interactive shell. When
embedding commands inside another shell, preserve empty arguments and quoting;
prefer calling the launcher directly.

Quote comma-separated values when invoking the PowerShell script, for example
`--focus 'correctness,security'` or `set focus 'security,performance'`.
An unquoted comma is PowerShell array syntax, not one argument. `show` and
`resolve` do not create a missing data directory; check its existence before
listing it. Image planning can create an image catalog even in `cached_only`.

## Plugin discovery

1. Verify that the extracted marketplace contains
   `.agents/plugins/marketplace.json` and `plugins/persona-review/plugin.json`.
   The catalog source is relative to the marketplace root, not its `.agents`
   directory. Do not point the marketplace command at the single-plugin ZIP.
2. Check `codex plugin --help` and `codex plugin list --json` in the same Codex
   installation/profile you will use for the review. Confirm the plugin was
   installed and is enabled. Consult current CLI help before changing settings.
3. Start a new session and check both names in the available skill catalog:
   `persona-review:review` and `persona-review:review-config`. Ask for current
   settings through the latter. If recognition still fails, record the CLI
   version and nonsecret error; do not claim the skill ran by improvising it.
4. Use your normal login. A separate empty Codex configuration does not inherit
   authentication. Do not copy/link credential files to make an isolated test
   appear self-contained. See [the validation distinction](validation.md).

The documented install path is the CLI marketplace flow. A Windows candidate
test recognized and invoked both skills from their catalog names, with manual
cache placement and temporary plugin configuration; see [validation](validation.md).
Desktop installation UI labels and an unaided end-to-end install are unverified.
Do not substitute an absolute skill-path prompt and call that a normal
installation test. For version-specific setup use the
[official CLI documentation](https://developers.openai.com/codex/cli/).

## Failures and data safety

- Missing Python: Codex can explain the limitation and provide a text review
  with session-only preferences. It must not claim helper validation, saved
  settings, image persistence, or cache management succeeded.
- Missing `image_gen`: retain the technical review and explain no new image was
  generated. `cached_only` can still use valid existing images. Never silently
  change `always` into a cached-image mode.
- `rg` missing: use existing read-only file/search tools. Ripgrep is not a helper
  dependency; its absence does not require installing it to use the helper.
- Locked config: another operation may be active. Retry after it finishes. For
  a crash, inspect the process state before manually removing the stale lock.
- Invalid settings or I/O failures: preserve originals, report the actual error,
  and correct its cause. Do not retry through another implementation.
- Power loss and all filesystem failures are not fully covered by atomic file
  replacement. Old images and unreferenced files are retained rather than
  automatically deleted. See [design](design.md).

This release does not guarantee identical image-generation access across Codex
versions, accounts, operating systems, or plans. The helper itself stays offline.
