# image-gen

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

## Tools

| Tool | Purpose |
|---|---|
| `scripts/save_image.py` | Verify a payload; write image + provenance |
| `scripts/inspect_image.py` | Format, dimensions, mode, size, SHA-256 |
| `scripts/make_variants.py` | Deterministic crops; keeps alpha; refuses overwrite |

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests are local and require no credential and no network.
