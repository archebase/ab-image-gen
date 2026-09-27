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

## Provenance contents

`image-gen.result.v1`: schema, creation time, source, decoded artifact metadata (format, dimensions, mode, bytes, SHA-256), returned payload metadata, and any of prompt, model, provider, request ID, generation ID and usage that were available. Never the raw Base64 payload.
