# Persona Review

[日本語](README.ja.md)

Evidence-based code reviews in a character's voice, with optional expressions.
Codex records the technical assessment first, then changes its wording while
preserving findings, severity, evidence, and uncertainty. A blunt persona must
not invent defects; clean code does not need a criticism.

**Development preview: 0.4.0-rc.1 is not released.** The helper currently requires
Python 3.10+. The shell and PowerShell scripts launch Python. A Bash alternative
is under discussion and is **not implemented**.

## Requirements

- A signed-in Codex environment supporting local plugins, skills, and local file
  execution. The manifest declares `requires_local_executor`; this is not a
  cloud-only plugin. Availability depends on the Codex version and account.
- Python 3.10+ for configuration, validation, and image management. Its standard
  library is sufficient: no pip packages, Node.js, jq, HTTP/MCP server, or separate
  helper API key. JavaScript development tools do not imply Python is installed.
- Codex's `image_gen` and a registered reference image for new expressions. If
  generation, saving, or display fails, the review finishes with an explanation.
  No images are bundled.
- A connected GitHub reader for GitHub URLs, or local files to review.

| Platform | Helper | Optional launcher |
|---|---|---|
| Windows | Python 3.10+ | PowerShell 5.1+; checks `py -3`, `python3`, then `python` |
| macOS | Python 3.10+ | POSIX `sh`; checks `python3`, then `python` |
| Linux | Python 3.10+ | POSIX `sh`; checks `python3`, then `python` |

Launchers never install a runtime or change execution policy. See [runtime setup](docs/runtime.md).

## Install a reviewed local package

Obtain a reviewed `persona-review-marketplace-<version>.zip` from the maintainer.
This preview has no published release. Private source access requires permission;
maintainers can [build the archive](docs/distribution.md).

1. Expand the ZIP into a new directory. It includes the catalog and correct
   plugin layout, so no JSON editing is needed.
2. Use your normal signed-in Codex. Check `codex --version`,
   `codex plugin --help`, and `codex login status`. If the command is missing,
   follow [official CLI setup](https://developers.openai.com/codex/cli/) or your
   installed CLI's launch instructions. Do not hard-code internal desktop app
   paths or copy credentials from another user.
3. From the extracted `persona-review-marketplace` directory, run:

   ```text
   codex plugin marketplace add .
   codex plugin add persona-review@persona-review-local
   codex plugin list --json
   ```

4. Start a new Codex session. Confirm `persona-review:review` and
   `persona-review:review-config` are available in its skill catalog, then ask:

   > Use persona-review:review-config to show my current settings.

   Normal use should not require an absolute skill path. If the skills are
   missing, follow [plugin discovery checks](docs/runtime.md#plugin-discovery).

Windows extraction example, from the folder containing the ZIP:

```powershell
Expand-Archive -LiteralPath .\persona-review-marketplace-0.4.0-rc.1.zip -DestinationPath .\persona-review-preview
Set-Location .\persona-review-preview\persona-review-marketplace
```

macOS/Linux, using an existing `unzip` command or a graphical archive tool:

```sh
unzip persona-review-marketplace-0.4.0-rc.1.zip -d persona-review-preview
cd persona-review-preview/persona-review-marketplace
```

These CLI commands were available in 0.163.0-alpha.10.2; check local help for other
versions. [Local marketplaces](https://developers.openai.com/plugins/build/plugins)
are separate from public directory submission. The single-plugin ZIP is not a
marketplace directory. Installing a local package does not publish it.

## Use it

> Use persona-review:review-config to register Mina with a calm, direct voice.
> Use my attached image as the reference and choose hybrid image mode.

> Ask Mina to review this project's changes, focusing on security and correctness.

> Keep Mina's appearance but use the registered direct persona for this review.

Appearance and voice are independent. Scope is `auto / diff / full / file`.
Focus accepts correctness, security, performance, architecture, maintainability,
and testing. Focus never hides serious findings outside it. Unread code and
tests that were not run stay unverified.

| Image mode | Behavior |
|---|---|
| `always` | Generate and save a new expression for each review. |
| `cached_only` | Use valid saved images only; never generate missing ones. |
| `hybrid` | Reuse a matching expression; generate and save it when missing. |
| `pre_generate` | Prepare happy, neutral, concerned, and stern; reuse valid images and retry missing ones. |

Image identity includes the character, appearance, and reference-image hash.
Changing only the voice reuses images. Images and provenance are saved outside
the plugin; old images remain. `cached_only` can use valid saved images without
image generation. When no usable image exists, the output explains why and
includes the technical review.

## Try the helper

From the plugin directory, use `sh scripts/persona-review.sh` on macOS/Linux or
`& .\scripts\persona-review.ps1` in Windows PowerShell. A verified Python executable
can also run `scripts/persona_review.py` directly.

```sh
sh scripts/persona-review.sh --data-dir "../demo data" show
sh scripts/persona-review.sh --data-dir "../demo data" register-persona calm examples/persona.json
sh scripts/persona-review.sh --data-dir "../demo data" register mina examples/character-v2.json
sh scripts/persona-review.sh --data-dir "../demo data" set character_id mina
sh scripts/persona-review.sh --data-dir "../demo data" resolve
sh scripts/persona-review.sh validate-review examples/review.json
```

**Put `--data-dir` before the subcommand**: `--data-dir PATH resolve`, not
`resolve --data-dir PATH`. Use a new temporary directory for experiments. Saving
commands write to the selected location.

Defaults are `%LOCALAPPDATA%/persona-review` on Windows, or
`$XDG_DATA_HOME/persona-review` / `~/.local/share/persona-review` on macOS/Linux.
`PERSONA_REVIEW_HOME` and `--data-dir` can select another trusted location.
Project overrides are disabled by default. See [configuration and migration](docs/configuration-v2.md)
and [image workflow](docs/image-workflow.md).

Reading v1 configuration is nonmutating. A write or `migrate` makes an exact
backup before saving v2. Old A means `always`; old B means `pre_generate` and can
now generate missing expressions. Choose `cached_only` to forbid generation.
Do not write migrated configuration with an older helper.

## Validation and limits

Offline CI requires no model/API credentials. The matrix is configured for
Windows/macOS/Linux with Python 3.10 and 3.13, including launcher and archive
tests. Check results for the specific commit; adding a matrix is not evidence
that it ran. Baseline v0.3.0 passed 82 tests in each Windows/Linux job.

Independent Windows checks of v0.3.0 confirmed two clean plugin installations
and fresh-data reviews with generated images. Authenticated model execution
was separate and used explicit installed skill paths. This does **not** prove
fully unaided installation, Desktop final-image rendering, or non-Windows Codex
integration. See [recorded validation scope](docs/validation.md).

From a source checkout (tests and build tools are not in the runtime ZIP):

```text
python -m unittest discover -s tests -v
python tests/review_quality.py check tests/fixtures/review_quality/reference-results.json
python tools/build_dist.py --output dist
python tools/build_dist.py --output dist --check
```

Quality fixtures are hand-authored contracts, not real LLM results. Neither
schema validation nor the small live evaluation establishes general accuracy.
Image checks cover headers, size, hashes, and provenance; the skill must inspect
image contents. The helper never runs reviewed code or calls an LLM/image API.
See [quality evaluation](docs/review-quality.md) and [design](docs/design.md).

## License

[Apache-2.0](LICENSE). User images, reviewed code, settings, credentials, and
generated review data are not bundled. Do not commit them.
