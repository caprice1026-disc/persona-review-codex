# Changelog

## 0.4.0-rc.1 — Unreleased

- Prepare a portable local distribution with English and Japanese installation
  instructions, ready-made marketplace layout, and explicit runtime requirements.
- Add POSIX shell and PowerShell launchers for existing Python 3.10+ runtimes.
  Preserve arguments and helper failure status without automatic installation.
- Add deterministic allowlisted ZIP builds, checksum output, packaging tests,
  and macOS to the existing Windows/Linux offline CI matrix.
- Add Apache-2.0 license metadata and mark the plugin as requiring local execution.
- Record first-install recovery steps and distinguish offline checks, conditional
  live Windows acceptance, and still-unverified onboarding/platform behavior.

The Python helper's configuration/image behavior is unchanged. A Python-free
Bash backend is not included. This candidate has not been published.

## 0.3.0

- Add `cached_only` alongside always, hybrid, and pre_generate image modes.
- Separate character/appearance from shared persona/voice and scope from focus.
- Add v1 configuration migration with backups and preserve shared-persona
  bindings during legacy registration edits.
- Add offline review-quality contracts and Windows/Linux CI. Preserve findings,
  severity, evidence, and unverified scope across persona wording changes.
