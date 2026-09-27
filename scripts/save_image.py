#!/usr/bin/env python3
"""Save a generated image as a verified artifact with a provenance sidecar.

Model- and provider-agnostic: this tool performs no network request and reads no
credential. A harness (or any other model dispatcher) produces the image; this
step turns that payload into a checked file plus provenance.

Accepted inputs:
  * an images API response: {"data": [{"b64_json": "...", "generation_id": "..."}], ...}
  * a payload containing "b64_json", "image_base64" or "b64"
  * a raw image file (PNG / JPEG / WebP)

Examples:
  python3 save_image.py --input response.json --output out/hero.png \
      --prompt "a blue circle" --model "<model id>"
  python3 save_image.py --input raw.png --output out/hero.png
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from typing import Any


RESULT_SCHEMA = "image-gen.result.v1"
DEFAULT_MAX_IMAGE_BYTES = 96 * 1024 * 1024
MAX_IMAGE_EDGE = 4096
MAX_IMAGE_PIXELS = 16_777_216
FORMAT_EXTENSIONS = {"png": {".png"}, "jpeg": {".jpg", ".jpeg"}, "webp": {".webp"}}
PIL_FORMATS = {"png": "png", "jpeg": "jpeg", "jpg": "jpeg", "webp": "webp"}
RETURNED_METADATA = ("size", "quality", "background", "output_format")
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")
BASE64_KEYS = ("b64_json", "image_base64", "b64")


class ImageToolError(Exception):
    """A stable, secret-free error."""

    def __init__(self, category: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.details = {key: value for key, value in details.items() if value is not None}

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"category": self.category, "message": self.message}
        if self.details:
            value["details"] = self.details
        return value


# --------------------------------------------------------------------------
# payload extraction
# --------------------------------------------------------------------------


def find_base64(payload: Any) -> tuple[str, dict[str, Any]]:
    """Return (base64 data, surrounding metadata) from a supported payload."""
    if not isinstance(payload, dict):
        raise ImageToolError("input_error", "The payload must be a JSON object")
    data = payload.get("data")
    if isinstance(data, list):
        if len(data) != 1 or not isinstance(data[0], dict):
            raise ImageToolError("input_error", "Expected exactly one image in data[]")
        item = data[0]
        for key in BASE64_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value:
                meta = dict(payload)
                meta.pop("data", None)
                if isinstance(item.get("generation_id"), str):
                    meta["generation_id"] = item["generation_id"]
                return value, meta
        raise ImageToolError("input_error", "data[0] contained no Base64 image field")
    for key in BASE64_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            meta = dict(payload)
            meta.pop(key, None)
            return value, meta
    raise ImageToolError("input_error", "No Base64 image field found in the payload")


def is_image_file(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError as error:
        raise ImageToolError("input_error", f"Could not read input file: {path}") from error
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8")
        or (len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def load_payload(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not path.exists():
        raise ImageToolError("input_error", f"Input file does not exist: {path}")
    if is_image_file(path):
        try:
            return path.read_bytes(), {}
        except OSError as error:
            raise ImageToolError("input_error", f"Could not read input file: {path}") from error
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ImageToolError("input_error", f"Input is neither an image nor valid JSON: {path}") from error
    encoded, meta = find_base64(payload)
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageToolError("input_error", "The payload contained invalid Base64 image data") from error
    return content, meta


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def validate_container(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        position = 8
        while position + 12 <= len(content):
            length = int.from_bytes(content[position : position + 4], "big")
            chunk_type = content[position + 4 : position + 8]
            position += 12 + length
            if chunk_type == b"IEND":
                if length == 0 and position == len(content):
                    return "png"
                break
        raise ImageToolError("invalid_image", "Incomplete PNG container")
    if content.startswith(b"\xff\xd8"):
        if content.endswith(b"\xff\xd9"):
            return "jpeg"
        raise ImageToolError("invalid_image", "Incomplete JPEG container")
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        if int.from_bytes(content[4:8], "little") + 8 == len(content):
            return "webp"
        raise ImageToolError("invalid_image", "Incomplete WebP container")
    raise ImageToolError("invalid_image", "Unsupported image container")


def inspect(content: bytes, max_bytes: int) -> dict[str, Any]:
    if not content:
        raise ImageToolError("invalid_image", "The image payload is empty")
    if len(content) > max_bytes:
        raise ImageToolError("invalid_image", "The image exceeds the configured size limit")
    container = validate_container(content)
    try:
        from PIL import Image, UnidentifiedImageError
        from PIL.Image import DecompressionBombError
    except ImportError as error:
        raise ImageToolError("dependency_missing", "Pillow is required to verify image artifacts") from error
    try:
        with Image.open(BytesIO(content)) as image:
            actual_format = PIL_FORMATS.get((image.format or "").lower())
            if actual_format is None or actual_format != container:
                raise ImageToolError("invalid_image", "The decoded image format is unsupported or inconsistent")
            if image.width <= 0 or image.height <= 0 or image.width > MAX_IMAGE_EDGE or image.height > MAX_IMAGE_EDGE:
                raise ImageToolError("invalid_image", "The decoded image dimensions are outside the supported range")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ImageToolError("invalid_image", "The decoded image exceeds the supported pixel count")
            image.load()
            return {
                "format": actual_format,
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
    except (UnidentifiedImageError, DecompressionBombError, OSError, SyntaxError, ValueError) as error:
        raise ImageToolError("invalid_image", "The image bytes could not be decoded") from error


def require_extension(path: Path, actual_format: str) -> Path:
    if path.suffix.lower() not in FORMAT_EXTENSIONS[actual_format]:
        raise ImageToolError(
            "input_error",
            f"Output extension {path.suffix or '(none)'} does not match the decoded {actual_format} image",
        )
    return path


def safe_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in USAGE_KEYS:
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result[key] = item
    for key in ("input_tokens_details", "output_tokens_details"):
        details = value.get(key)
        if isinstance(details, dict):
            clean = {
                name: item
                for name, item in details.items()
                if isinstance(item, int) and not isinstance(item, bool) and item >= 0
            }
            if clean:
                result[key] = clean
    return result or None


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def artifact_paths(output: Path) -> tuple[Path, Path]:
    output = output.expanduser()
    if output.is_symlink():
        raise ImageToolError("artifact_error", "Refusing to write an image through a symbolic link")
    manifest = output.with_suffix(output.suffix + ".json")
    if manifest.is_symlink():
        raise ImageToolError("artifact_error", "Refusing to write provenance through a symbolic link")
    return output, manifest


def check_writable(output: Path, overwrite: bool) -> tuple[Path, Path]:
    output, manifest = artifact_paths(output)
    if not overwrite and (output.exists() or manifest.exists()):
        raise ImageToolError(
            "artifact_exists",
            "The output image or provenance file already exists; pass --overwrite or choose a new path",
        )
    return output, manifest


def write_temp(parent: Path, prefix: str, content: bytes) -> Path:
    handle = tempfile.NamedTemporaryFile(dir=parent, prefix=prefix, suffix=".tmp", delete=False)
    path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def write_artifacts(output: Path, content: bytes, manifest: dict[str, Any], overwrite: bool) -> tuple[Path, Path]:
    output, manifest_path = check_writable(output, overwrite)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ImageToolError("artifact_error", "Could not create the output directory") from error
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    image_temp = write_temp(output.parent, f".{output.name}.", content)
    manifest_temp = write_temp(output.parent, f".{manifest_path.name}.", manifest_bytes)
    try:
        if overwrite:
            os.replace(image_temp, output)
            image_temp = Path()
            os.replace(manifest_temp, manifest_path)
            manifest_temp = Path()
        else:
            os.link(image_temp, output)
            os.link(manifest_temp, manifest_path)
        return output, manifest_path
    except FileExistsError as error:
        output.unlink(missing_ok=True)
        raise ImageToolError("artifact_exists", "The output image or provenance file already exists") from error
    except OSError as error:
        output.unlink(missing_ok=True)
        raise ImageToolError("artifact_error", "Could not save the image artifact") from error
    finally:
        for temp in (image_temp, manifest_temp):
            if str(temp) not in {"", "."}:
                temp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# command
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path, help="Image file, or JSON payload containing Base64")
    parser.add_argument("--output", required=True, type=Path, help="Destination path (.png/.jpg/.jpeg/.webp)")
    parser.add_argument("--prompt", help="Prompt used to produce the image, for provenance")
    parser.add_argument("--model", help="Model identifier, for provenance")
    parser.add_argument("--provider", help="Free-form provider or harness label, for provenance")
    parser.add_argument("--request-id", help="Upstream request identifier, for provenance")
    parser.add_argument("--generation-id", help="Upstream generation identifier, for provenance")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing artifact and sidecar")
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    content, payload_meta = load_payload(args.input)
    if args.max_image_bytes <= 0:
        raise ImageToolError("input_error", "--max-image-bytes must be a positive integer")
    artifact = inspect(content, args.max_image_bytes)
    output = require_extension(args.output, artifact["format"])

    returned = {key: payload_meta[key] for key in RETURNED_METADATA if isinstance(payload_meta.get(key), str)}
    manifest: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "provided-payload",
        "artifact": artifact,
        "returned": returned,
    }
    for key, value in (
        ("prompt", args.prompt),
        ("model", args.model),
        ("provider", args.provider),
        ("request_id", args.request_id or payload_meta.get("request_id")),
        ("generation_id", args.generation_id or payload_meta.get("generation_id")),
    ):
        if isinstance(value, str) and value.strip():
            manifest[key] = value.strip()
    usage = safe_usage(payload_meta.get("usage"))
    if usage:
        manifest["usage"] = usage

    saved, manifest_path = write_artifacts(output, content, manifest, args.overwrite)
    result = dict(manifest)
    result["ok"] = True
    result["artifact"] = {**artifact, "path": str(saved)}
    result["provenance_path"] = str(manifest_path)
    return result


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except ImageToolError as error:
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
