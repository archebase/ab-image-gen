# ab-image-gen

A provider-neutral skill for generating images and delivering them as verified files.

It does not own model access. Whatever image-capable model your harness can already dispatch is the model it uses. The skill adds the parts that are easy to get wrong: prompt craft, candidate discipline, artifact verification, provenance and release QA.

## Requirements

- Python 3.10+
- Pillow

```sh
python3 -m pip install -r requirements.txt
```

No endpoint or credential configuration. `scripts/*.py` never make a network request and never read a credential.

## Use

1. Ask your harness's image-capable model to produce the image.
2. Save the payload as a verified artifact:

```sh
python3 scripts/save_image.py \
  --input response.json \
  --output out/hero.png \
  --prompt "a blue circle on white" \
  --model "<model id>"
```

`--input` accepts an images API response (`data[0].b64_json`), a payload with `b64_json` / `image_base64` / `b64`, or a raw image file. The tool writes the image plus `<image>.json` provenance and refuses to overwrite unless `--overwrite` is passed.

If the harness reports a successful generation but never hands back the payload — nothing renders, no file appears — recover the completed result instead of generating again. Mark before the call, extract after it:

```sh
python3 scripts/recover_harness_image.py mark --create-in tmp/ab-image-gen
python3 scripts/recover_harness_image.py extract \
  --marker "/absolute/path/printed/by/mark" \
  --out-dir output/ab-image-gen --name "descriptive-name"
```

The recovered payload goes through `save_image.py`, so the artifact and its provenance are identical in kind to a delivered one. The tool reads the harness's local task logs and never modifies them. See `references/artifact-delivery.md`.

## Tools

| Tool | Purpose |
|---|---|
| `scripts/save_image.py` | Verify a payload; write image + provenance |
| `scripts/recover_harness_image.py` | Recover a completed result the harness never handed back (Codex adapter) |
| `scripts/inspect_image.py` | Format, dimensions, mode, size, SHA-256 |
| `scripts/make_variants.py` | Deterministic crops; keeps alpha; refuses overwrite |

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests are local and require no credential and no network.
