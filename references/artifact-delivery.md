# Artifact delivery and recovery

Generation is incomplete until the result exists as a verified file. A model response in a transcript is not a deliverable.

## Saving step

`scripts/save_image.py --input <payload> --output <path>`:

1. loads the payload — an images API response, a Base64 field, or a raw image file;
2. validates the container (PNG / JPEG / WebP) and rejects truncated data;
3. fully decodes the bitmap with Pillow and rejects unsupported or oversized images;
4. refuses an output whose extension disagrees with the decoded format;
5. writes the image plus `<image-path>.json` provenance;
6. returns absolute paths, actual metadata, SHA-256 and any captured request/generation IDs.

An existing image or sidecar stops the write unless `--overwrite` is passed. Prefer a new versioned path so pixels and provenance can never disagree.

## Delivery is the file, not the UI

The saved file plus provenance is the source of truth. A harness that fails to display the image has a display problem — not a reason to generate (and pay for) another one. If neither an artifact nor a completed result exists, report the failure instead of fabricating a placeholder or a path.

## When the harness never hands back the payload

Some harnesses report a successful generation and lose the result: nothing renders, and no file appears. The completed result usually still exists in the harness's own task log, and that is what `scripts/recover_harness_image.py` reads. It is a recovery path, not a second generator, and it is not a second delivery path either: it hands the recovered payload to `save_image.py`, so a recovered image gets the same container check, decode, dimension check, atomic write and `image-gen.result.v1` provenance as one the harness handed back.

Mark immediately before the call, recover immediately after it:

```sh
python3 scripts/recover_harness_image.py mark --create-in tmp/imagegen
# ... the harness call ... use the marker path it printed; shell variables do not
# survive across tool calls, so carry the absolute path
python3 scripts/recover_harness_image.py extract \
    --marker "/absolute/path/printed/by/mark" \
    --out-dir output/imagegen --name "descriptive-name"
```

Rules:

- **Recover, never regenerate.** The recovered bytes are the completed result; re-calling the model costs money for a file that already exists.
- **A recovered image needs its provenance.** The artifact on its own is not a deliverable; the sidecar is what makes it verifiable and citable later.
- **One asset per call.** `--all` suffixes the names (`name-1`, `name-2`); use `--prompt-contains` when a concurrent generation could be picked up instead.
- **Read-only on the harness.** The tool reads local logs and never modifies, moves or deletes them, and never prints the payload into the transcript.
- **Markers.** A unique marker created by `mark --create-in` is removed after a successful extraction; a marker passed with `--file` is left alone. A failed extraction removes nothing.
- **No completed call found is a non-zero exit.** Report the failure; do not substitute a placeholder, and do not silently regenerate.

Supported harnesses: `codex` — `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions`, `*.jsonl`, `response_item` records of type `image_generation_call` with status `completed`. Add an adapter to this script when a new harness appears; do not fork a second delivery skill for it.

## Provenance contents

`image-gen.result.v1`: schema, creation time, source, decoded artifact metadata (format, dimensions, mode, bytes, SHA-256), returned payload metadata, and any of prompt, model, provider, request ID, generation ID and usage that were available. Never the raw Base64 payload.
