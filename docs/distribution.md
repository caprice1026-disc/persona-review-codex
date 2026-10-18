# Building and reviewing a distribution

This is a local build procedure. It does not upload files, create a GitHub
release, change repository visibility, or submit a public-directory listing.
Version `0.4.0-rc.1` is an unreleased candidate.

## Build

From a source checkout, using an existing Python 3.10+ executable:

```text
python tools/build_dist.py --output dist
python tools/build_dist.py --output dist --check
```

The output directory is ignored by Git. Existing identical artifacts are reused;
an existing differing artifact is an error. Choose a new output directory after
editing sources. The build needs no network, model access, API keys, Git CLI,
third-party Python packages, or preexisting user data.

| Output | Purpose |
|---|---|
| `persona-review-<version>.zip` | One `persona-review/` plugin directory with manifest, skills, helper, schemas, examples, docs, and license |
| `persona-review-marketplace-<version>.zip` | Ready-to-extract local marketplace with catalog and the same plugin files |
| `SHA256SUMS` | SHA-256 of both ZIPs, sorted by filename |

The marketplace ZIP expands to:

```text
persona-review-marketplace/
  .agents/plugins/marketplace.json
  plugins/persona-review/
    plugin.json
    LICENSE
    scripts/
    skills/
    schemas/
```

`distribution/files.txt` is the explicit allowlist. The builder does not scan a
home directory or package everything under the checkout. It rejects traversal,
symlinks, oversized/non-UTF-8/binary source files, and forbidden data directories.
It normalizes CRLF to LF, orders entries, fixes timestamps and permissions, and
uses uncompressed ZIP entries to avoid compression-library differences. This
produces the same bytes from the same allowlisted text on each OS. ZIP size is
slightly larger in exchange for deterministic output.

Tests verify byte reproducibility, allowed contents, license metadata, catalog
resolution, checksum consistency, fixed metadata, rejected unsafe inputs,
no-overwrite behavior, documentation links, and an extracted helper launched
from another working directory. Neither generated archives nor test logs should
be committed. Record their hashes alongside the reviewed source commit.

## Specification checks

Checked on 2026-10-18: local Codex CLI 0.163.0-alpha.10.2 exposes
`plugin marketplace add`, `plugin add`, and `plugin list`. Its help accepts a
local marketplace source and `PLUGIN@MARKETPLACE`. Manifest fields were then
checked against official OpenAI documentation.

- [Package your plugin](https://developers.openai.com/plugins/build/plugins)
  documents root `plugin.json`, discovery under `skills/`, portable license
  metadata, and relative onboarding paths. The marketplace source
  `./plugins/persona-review` resolves from its root. The checked catalog uses
  `AVAILABLE`, `ON_INSTALL`, and `Developer Tools`.
- [Desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only)
  documents `extensions.com.openai.requires_local_executor: true` for skills
  requiring local execution. This package has no MCP server or hook.
- [Public submission](https://developers.openai.com/plugins/deploy/submission)
  is a separate review and publisher workflow. The local ZIP has not been
  submitted or accepted by that service. Do not invent publisher identity,
  privacy-policy URLs, registration status, or review approval.

The single-plugin ZIP is for local handoff/review; suitability for a portal's
current upload requirements must be checked when submission is authorized.

## Release preparation

Use `docs/releases/0.4.0-rc.1.md` in the source checkout as the draft description.
Attach the two ZIPs and checksums only after the exact candidate has passed
review. Keep a GitHub release as a draft unless publication is explicitly
authorized. The current work prepares local files only; it does not create even
a remote draft, tag, or commit.

Before public distribution, confirm the actual publisher identity and listing
assets, inspect relevant Git history/commit identity metadata, and run the
candidate's CI plus normal skill-discovery acceptance. Apache-2.0 has been
selected; the official license text is included. A future Bash implementation
must be reviewed and tested separately before claiming Python-free operation.

User settings, images, code under review, credentials, raw live-model logs, and
machine-specific audit artifacts are excluded. Heuristic secret scans cannot
prove all sensitive information is absent. Keep the audit scope and exclusions
in the private review record, rather than embedding private paths in this package.
