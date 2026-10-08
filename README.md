# ab-image-gen

Generate raster images and deliver them as verified files. The skill owns prompt craft, candidate discipline, artifact verification, `ab-image-gen.result.v1` provenance and release QA on top of whatever image-capable model the harness can already dispatch; it does not own model access, credentials, billing or endpoint configuration. `SKILL.md` is the agent-behaviour contract; this README is repository-level orientation only, and no agent rule lives here alone.

| Field | Value |
|---|---|
| Skill id | `ab-image-gen` |
| Version | `2.0.0` (`metadata.version` in `SKILL.md`) |
| License | `Internal` |
| Status | `2.0.0` internal; no release tag and no `CHANGELOG.md` in the repo |
| Repository | `https://github.com/archebase/ab-image-gen.git` (branch `main`) |

## Scope
- **Owns:** prompt compilation, bounded candidate sets, artifact save/verify, `ab-image-gen.result.v1` provenance, deterministic crops, and the delivery verdict (`ready` / `revise` / `blocked`).
- **Delegates:** the organisation's brand-guide skill, when one is installed — official logos, house typography and brand-guide rules stay outside this skill; `references/brand-production.md` says to load that skill and let it own those rules.
- **Refuses:** model choice, credentials, billing and automatic retries; official logos and final copy inside generated pixels; a second generation when a completed result already exists; any assumed model capability (edit, reference image, transparency, streaming) the harness has not demonstrated.

## Modes
| Mode | Use when | Required evidence |
|---|---|---|
| Simple one-image request | A single deliverable; the full brief is skipped (`SKILL.md`, Workflow step 1) | Image path, provenance path, model, actual format and dimensions, SHA-256 |
| Production work | A candidate set, crop family or consistency family; the full brief is used | `templates/brief.md` brief, generation record, `templates/release-report.md` verdict |

## Quick start
1. Install the runtime: `python3 -m pip install -r requirements.txt` (pins `Pillow==12.0.0`).
2. Read `references/generation-methodology.md` and `references/prompt-compiler.md` before compiling the first prompt.
3. Ask the harness's image-capable model for the image — one distinct asset per request.
4. Save the returned payload as a verified artifact; this writes the image plus its provenance sidecar.
5. Inspect with `scripts/inspect_image.py`, derive crops with `scripts/make_variants.py`, and reach for `scripts/recover_harness_image.py` only when the harness reports success but hands back no payload.

```bash
python3 scripts/save_image.py --input response.json --output out/hero.png \
    --prompt "<the prompt used>" --model "<model id>"
```

## Commands
| Command | Purpose |
|---|---|
| `python3 scripts/save_image.py --input <payload> --output <path> [--prompt TEXT] [--model ID] [--provider LABEL] [--request-id ID] [--generation-id ID] [--overwrite] [--max-image-bytes N]` | Verify a payload, write the image and the provenance sidecar; refuses to overwrite an existing artifact or sidecar without `--overwrite` |
| `python3 scripts/recover_harness_image.py mark (--create-in DIR \| --file PATH)` | Print or write the ISO-8601 UTC marker immediately before the harness call |
| `python3 scripts/recover_harness_image.py extract (--since ISO \| --marker PATH) --out-dir DIR [--name STEM] [--harness codex] [--prompt-contains TEXT] [--wait-seconds F] [--all] [--keep-marker] [--force] [--provider LABEL] [--model ID] [--codex-home DIR]` | Find the completed call after the marker and save it through `save_image.py`; non-zero exit when no completed call is found |
| `python3 scripts/inspect_image.py <image> [<image> ...]` | Report format, dimensions, mode, byte size and SHA-256 as JSON |
| `python3 scripts/make_variants.py <master> --variant NAME=WIDTHxHEIGHT [--variant NAME=WIDTHxHEIGHT ...] --output-dir DIR` | Center-crop deterministic variants without generative changes; preserves alpha and refuses overwrite |

## Bundle layout
```text
SKILL.md              agent-behaviour contract: required path, rules, output contract
references/           7 reference files on methodology, prompts, delivery, iteration, consistency, brand, briefing
scripts/              save_image.py, recover_harness_image.py, inspect_image.py, make_variants.py
templates/            brief.md, generation-record.md, release-report.md
tests/                test_save_image.py, test_make_variants.py, test_recover_harness_image.py
evals/evals.json      8 eval cases for this skill
.github/workflows/    validate.yml CI job
requirements.txt      Pillow==12.0.0
```

`SKILL.md` owns the load triggers: `generation-methodology.md` and `prompt-compiler.md` before the first prompt, `artifact-delivery.md` before choosing an output path, `iteration-and-review.md` and `consistency.md` for iteration, and `brand-production.md` for an official logo, house typography or a brand guide.

## Validation
```bash
python3 -m py_compile scripts/*.py tests/*.py
python3 -m json.tool evals/evals.json >/dev/null
python3 -m unittest discover -s tests -v
git diff --check
```

These are the steps in `.github/workflows/validate.yml`. They show that the scripts and tests compile, that `evals/evals.json` is valid JSON, and that the three local test modules pass without network access or a credential. They cannot show model-output quality, live harness integration, or that a given commit's CI run succeeded.

## Install
```bash
git clone https://github.com/archebase/ab-image-gen.git
ln -s "$PWD/ab-image-gen" ~/.agents/skills/ab-image-gen
```

Requires Python 3.10+ and Pillow (`requirements.txt` pins `Pillow==12.0.0`). No credential or endpoint configuration is needed: the harness performs the model call, and `scripts/*.py` never make a network request and never read a credential. The repo has no release tags, so pin a commit (`main` head was `dea64e0`, 2026-10-01) if you need a reproducible checkout.

## Status
Current version is `2.0.0` (`SKILL.md` frontmatter); `main` head is `dea64e0` "refactor(skill): rename image-gen to ab-image-gen". Verified in-repo: three test modules covering save/verify, variant cropping and recovery; `evals/evals.json` with 8 cases; the CI check list. Not verified here: no CI run result is recorded in the repo, no eval outcome is recorded, and the harness recovery path is exercised only against the `codex` adapter (`ADAPTERS = ("codex",)` in `scripts/recover_harness_image.py`).
