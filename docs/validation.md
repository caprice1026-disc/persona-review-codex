# Validation record and limits

## Baseline v0.3.0 (2026-10-18)

The verified baseline is commit
`5af99873467eb9a260480d66b81afeaae21f0000`. Its
[Windows/Linux CI run](https://github.com/caprice1026-disc/persona-review-codex/actions/runs/39115351955)
passed all 82 tests in each of four Python 3.10/3.13 jobs, with no skips. Those
tests are offline helper and hand-authored quality contracts, not LLM output.

An independent Windows check used Codex CLI 0.163.0-alpha.10.2,
gpt-6.1-sol, and Python 3.10.9. It installed the baseline into two empty Codex
configuration areas, recognized both skills in their catalogs, and matched all
35 installed files in each area to the fixed source. Separately, two fresh
persona-review data areas completed natural-language configuration, new
reference-image generation/import, and a first review with a generated saved
expression. Saved and original image hashes matched; a subsequent plan reused
each saved expression. Images were independently opened and visually inspected.

One six-line synthetic example had a concrete authorization comparison defect;
the review reported one high finding. The corrected example had no findings.
Both retained partial scope and unverified integration/test details. These two
observations are not a general quality benchmark. No reviewed code was executed.

With image generation disabled in a further session, the review explained the
missing tool, retained its finding and uncertainty, and did not change saved
settings or images. The prior implementation also exercised all four image
modes and persona-only cache reuse; that historical result does not substitute
for testing a changed candidate.

## Conditions that limit the baseline result

The empty Codex configurations were not logged in. To avoid copying credentials,
installation/catalog checks and authenticated model runs were separate. Model
runs used existing authentication with unrelated configuration/plugins disabled,
and explicit paths to the newly installed skills, Python executable, isolated
data directory, and output directory. Thus this was a conditional reproduction,
not a fully unaided onboarding test. Authentication files were not copied or
linked, and existing user configuration remained unchanged.

The test prepared new reference images explicitly; it did not validate the
attachment-upload UI. Saved images were opened, and final text contained image
references, but the Desktop final-response view and TUI inline rendering were
not screen-tested. Real Codex installation, authentication, and image generation
on Linux/macOS remain unverified.

## Failures recovered during baseline testing

- `codex` and `python3` were not on that Windows PATH. The test found installed
  executables explicitly. Public instructions must use supported CLI setup and
  runtime selection, not another user's internal application path.
- The old README required constructing the marketplace directory and catalog.
  The new distribution builder prepares those together in the marketplace ZIP.
- `rg` was unavailable; read-only PowerShell search recovered. It is not a
  Python-helper dependency.
- A model placed `--data-dir` after `resolve`, which argparse rejected. Correcting
  the order succeeded. Updated skill examples put it before the subcommand.
- A restricted-network clone failed and succeeded after authorized access. This
  was environment access control, not a helper failure.

No raw authentication diagnostics, model transcripts, personal paths, generated
images, or user configuration from these checks are included in the package.

## Candidate 0.4.0-rc.1

The candidate adds launchers, Apache-2.0 metadata, documentation, allowlisted
reproducible ZIP builds, packaging tests, and a macOS CI matrix entry. The Python
business logic remains unchanged. Bash fallback is not implemented.

Windows local results and archive hashes belong to the private candidate review
record. The expanded six-job CI has not run until the candidate is pushed and
its run is checked. Do not report baseline results as acceptance of this candidate.

A Windows test of the initial candidate ZIP used Codex CLI 0.163.0-alpha.10.2,
gpt-6.1-sol, and Python 3.10.9. Both skills appeared in the available catalog.
Prompts named the skills without absolute skill paths; fresh model sessions read
the installed skills and successfully ran settings display and a review through
the packaged PowerShell launcher. The synthetic comparison defect produced one
high finding, with partial scope and unverified checks retained. Its neutral
review passed helper validation. With `cached_only` and no saved expression, the
review explained that no image was available and generated none. Settings
display created no data; image planning created only an image catalog.

This check manually placed the ZIP's verified plugin files in a new version
cache and supplied temporary marketplace/plugin settings, an isolated data
directory, and an explicit Python override. Existing authentication was used
normally, with no credential-file copying or linking; the existing user config
hash stayed unchanged. It demonstrates catalog recognition and first use with
those setup steps, not an unaided CLI installer or Desktop UI test. The test
predates the relative-Python-path and CRLF-checkout fixes; those fixes have
separate deterministic regression tests, not a repeated model acceptance run.

During the review, an unquoted comma-separated PowerShell argument failed and
succeeded when quoted. Missing `rg` was handled with PowerShell search. Listing
an uncreated data directory also failed without preventing the review. The
runtime and skill instructions now explain the quoting and directory behavior.
The revised candidate still needs independent re-review and exact-commit CI.
