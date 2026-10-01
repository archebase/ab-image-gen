---
name: ab-image-gen
description: "Generate and deliver raster images as verifiable files. Use whenever an image must be produced and saved — one-shot generation, candidate sets, controlled iteration, consistent visual families, campaign masters, deterministic crops, artifact recovery or brand-sensitive graphics. Provider-neutral: it works with whatever image-capable model the harness can already dispatch, and adds prompt craft, candidate discipline, artifact verification, provenance and release QA on top."
license: Internal
metadata:
  version: "2.0.0"
  result_schema: "ab-image-gen.result.v1"
compatibility: "Requires Python 3.10+ and Pillow. No credential or endpoint configuration: the harness performs the model call."
---

# ab-image-gen

This skill is about producing images well and delivering files that can be trusted. It does not own model access: whatever image-capable model the harness can already dispatch is the model to use. The skill contributes prompt craft, candidate discipline, verification and delivery.

## Required path

1. Read `references/generation-methodology.md` and `references/prompt-compiler.md` before compiling the first prompt.
2. Ask the harness's image-capable model for the image, following the methodology. One distinct asset per request.
3. The moment the harness returns image data — a Base64 payload or a written file — save it as a verified artifact:

   ```bash
   python3 scripts/save_image.py --input response.json --output out/hero.png \
       --prompt "<the prompt used>" --model "<model id>"
   ```

   If the harness reports success but never hands back the payload (nothing
   renders, no file appears), recover the completed result instead of generating
   again: `scripts/recover_harness_image.py`, documented in
   `references/artifact-delivery.md`.

4. Read `references/artifact-delivery.md` before choosing an output path.
5. Read `references/iteration-and-review.md` before selecting or revising candidates; read `references/consistency.md` when a subject must stay stable across images.
6. Read `references/brand-production.md` when an official logo, house typography or a brand guide applies.

## Workflow

1. Resolve deliverable, audience, placement, dimensions and rights. Skip the full brief for a simple one-image request; use `templates/brief.md` for production work.
2. Compile a concrete prompt. Keep official logos, final typography and exact figures out of generated pixels.
3. Generate, then save. A model response is not delivered until `save_image.py` has written and verified the file.
4. Inspect every saved artifact against the brief.
5. Iterate with one stated hypothesis at a time, and restate the invariants you are holding fixed.
6. Derive crops with `scripts/make_variants.py`, re-check any file with `scripts/inspect_image.py`, and add official assets and typography afterwards.
7. Before delivery confirm: image and provenance exist, hashes and dimensions match, variants open, provenance records the prompt and model, and a human accepted the release candidate.

## Bundled tools

| Tool | Purpose |
|---|---|
| `scripts/save_image.py` | Verify a payload and write the image plus a provenance sidecar |
| `scripts/recover_harness_image.py` | Recover a completed result the harness never handed back; saves through `save_image.py` |
| `scripts/inspect_image.py` | Report format, dimensions, mode, size and SHA-256 for existing files |
| `scripts/make_variants.py` | Deterministic crops from a master; preserves alpha, refuses overwrite |

These tools never make a network request and never read a credential.

## Rules

- The harness owns model choice, credentials and billing. Never ask for or handle a credential inside this skill.
- One image per request. A candidate set is several requests with distinct output paths.
- Never claim a capability the harness did not demonstrate — no assumed edit, reference-image, transparency or streaming support.
- Never retry automatically. If a call failed, re-decide with the user; an unknown billing state is not a retry signal.
- A saved artifact is not regenerated because a UI failed to display it. If the harness lost the result, recover the completed call (`scripts/recover_harness_image.py`); a second generation is never the remedy.
- Keep generated pixels free of official logos and final copy; composite those deterministically.

## Output contract

Return the image path, provenance path, model used, actual format and dimensions, SHA-256, plus any warnings or blockers. For production work also return the requested variants and a verdict: `ready`, `revise` or `blocked`.
