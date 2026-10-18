# Bash fallback proposal (not implemented)

The user requested Python first, then Bash when a compatible Python runtime is
unavailable. This is a design checkpoint, not a claim that Bash support ships.
No dependency installation or remote publication is authorized by this document.

## Current implementation

`scripts/persona_review.py` implements 13 commands: `show`, `migrate`, `set`,
`set-project`, `register`, `register-persona`, `import-image`, `save-generated`,
`invalidate-image`, `resolve`, `plan`, `plan-set`, and `validate-review`.

The shared contract is larger than launching a script: bounded UTF-8 JSON with
duplicate-key rejection; strict schema types; v1-to-v2 migration and backup;
character/persona separation; a common exclusive lock; atomic file replacement;
flushing file data; binary image signatures and size limits; SHA-256 provenance;
managed-path containment; reusable image history; and idempotent generated-image
saves. Both `plan` and `plan-set` can migrate the image catalog, so they cannot be
treated as read-only commands.

## Proposed selection

1. Probe a trusted Python executable for version 3.10+ before invoking any command.
2. Only if the runtime probe fails, probe Bash and its required utilities. Do not
   use the reviewed repository as an executable search path.
3. Select one backend before reading or changing user state. Preserve its exit
   status. A schema error, lock conflict, permission error, or partial operation
   must never trigger a second backend attempt.
4. If the required dependencies are missing, report them accurately. Codex may
   still provide a text review with session-only preferences, but must not claim
   persistent configuration, image-cache management, or schema validation worked.

An explicit runtime override should fail closed when invalid. Tests may select a
backend explicitly; normal use should not need a backend option. Node.js is not
the proposed fallback.

## Dependency and compatibility limits

| Environment | Full Python backend | Candidate Bash fallback |
|---|---|---|
| Windows | Existing Python 3.10+ | Existing Git Bash plus jq and tested utilities; Windows alone does not supply this bundle |
| macOS | Existing Python 3.10+ | Bash plus jq; hash adapter can use `shasum -a 256`; durable-write support must be verified separately |
| Linux | Existing Python 3.10+ | Bash plus jq, a SHA-256 utility, and tested file utilities; minimal images may lack them |

Target Bash 3.2-compatible syntax where possible, but test the actual oldest
supported version. Require a verified jq version/capability profile rather than
assuming every installed jq behaves identically. Other likely utilities are
`mktemp`, `mv`, `ln`, `od`, `wc`, `date`, and a tested path-resolution adapter.
Windows paths and MSYS argument conversion need explicit tests. Do not silently
route to the Windows WSL launcher or install/enable WSL.

The inspected Windows machine has Git Bash 5.2, `sha256sum`, `mktemp`, GNU `sync`,
and binary/path utilities, but jq is not on the tested Windows or Git Bash PATH.
No missing dependency was installed.

## Safety work required before write parity

- JSON must be parsed and constructed with jq programs and data arguments, never
  sed/grep substitutions or eval. Normal jq parsing is not sufficient evidence
  of Python's duplicate-key and exact integer-type behavior. Add raw-input or
  streaming validation and reject unsupported cases before writing.
- Compare canonical UTF-8 serialization byte-for-byte for all fields used in
  hashes, including control characters, Unicode, and line endings. Pretty JSON
  equality alone does not protect existing cache keys.
- Keep the same lock path and cross-backend exclusion semantics. A separate
  Bash-only mkdir lock would allow simultaneous Python writes. Evaluate atomic
  hard-link creation or a verified common lock protocol, including symlink cases
  and ownership-aware cleanup; do not simply replace it with `flock`.
- Use temporary files in the destination directory, restrictive creation modes,
  validation before replacement, and checked renames. An ordinary rename gives
  atomic visibility but does not by itself match Python's file `fsync` step.
  GNU `sync FILE` offers file synchronization, but must not be assumed available
  or equivalent on every platform. A portable native helper would add another
  shipped component and build/security maintenance.
- Do not hold image bytes in Bash variables. Use bounded files, binary header
  checks, content hashing, and safe path handling. Keep old images and migration
  backups; do not add automatic cleanup.

## Implementation choices and tradeoff

**Full Bash+jq backend:** keeps all 13 commands available without Python when the
dependency set is already installed. It duplicates substantial validation and
storage logic. On machines without jq or a supported durable-write primitive it
adds installation requirements, contrary to the goal of avoiding setup burden.

**Restricted Bash+jq backend:** first implement `show`, `resolve`, and
`validate-review`, with the same strict parsing/validation contract. Other
commands explicitly report unavailable rather than change state. This reduces
storage risk but does not provide configuration changes, image history, or the
four image modes without Python. With no jq, only Codex's text-only workflow
remains; that is a declared degraded workflow, not equivalent plugin execution.

Before implementation, select the acceptable dependency set and whether full
storage parity or the restricted initial scope is required. The current goal
does not authorize silently switching to Node or weakening JSON/storage rules.

## Reviewable diff plan after that decision

1. Add the capability probe and selection contract to both launchers.
2. Add isolated Bash and jq modules sharing the existing schemas and constants.
3. Add command-level fixtures that run against both backends: schema errors,
   duplicate keys and numeric types, aliases, v1 migrations/backups, persona
   sharing, atomic-write failures, concurrent cross-backend locks, image hashes,
   cached-image corruption, plan identity, and idempotent save/retry.
4. Add launcher tests for missing/old runtimes, missing dependencies, spaces,
   quotes, empty arguments, Unicode, relative paths, and no fallback after a
   business-operation failure. Compare error categories/exit status; do not
   require identical OS-specific error wording or random identifiers.
5. Run Windows/macOS/Linux CI and ZIP reproducibility/content checks. Keep CI
   deterministic and separate from real Codex/image-generation acceptance.
6. Update English/Japanese requirements, supported/degraded capability matrix,
   release-note draft, and allowlisted ZIPs. Keep the release private and
   unpublished pending the independent review and publication authorization.

## References checked 2026-10-18

- [jq manual](https://jqlang.org/manual/): streaming, sorted keys, data arguments,
  Windows binary output, and numeric-representation caveats.
- [Bash redirections](https://www.gnu.org/software/bash/manual/html_node/Redirections.html):
  the limited documented behavior of `noclobber`.
- [GNU sync](https://www.gnu.org/software/coreutils/manual/html_node/sync-invocation.html):
  file-specific synchronization and system-dependent guarantees.
- [OpenAI packaging](https://developers.openai.com/plugins/build/plugins) and
  [desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only):
  root manifest, license metadata, and `requires_local_executor`.

The sources inform the design; they do not prove backend compatibility. The
command inventory and Windows dependency observations come from the local code
and machine checks.
