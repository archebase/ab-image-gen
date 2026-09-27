from __future__ import annotations

import base64
import contextlib
import io
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image
from PIL.Image import DecompressionBombError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import save_image as tool  # noqa: E402


def png_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (22, 82, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (22, 82, 140)).save(buffer, format="JPEG")
    return buffer.getvalue()


def response_payload(*, body: bytes | None = None) -> dict[str, object]:
    return {
        "created": 1790364360,
        "data": [
            {
                "b64_json": base64.b64encode(body if body is not None else png_bytes()).decode("ascii"),
                "generation_id": "gen_test",
            }
        ],
        "output_format": "png",
        "quality": "low",
        "background": "opaque",
        "size": "64x64",
        "usage": {"input_tokens": 16, "output_tokens": 515, "total_tokens": 531},
        "request_id": "req_from_payload",
    }


class PayloadExtractionTests(unittest.TestCase):
    def test_extracts_images_api_response(self) -> None:
        encoded, meta = tool.find_base64(response_payload())
        self.assertTrue(encoded)
        self.assertEqual(meta["generation_id"], "gen_test")
        self.assertEqual(meta["request_id"], "req_from_payload")
        self.assertNotIn("data", meta)

    def test_extracts_plain_base64_fields(self) -> None:
        for key in ("b64_json", "image_base64", "b64"):
            encoded, meta = tool.find_base64({key: "AAAA", "model": "m"})
            self.assertEqual(encoded, "AAAA")
            self.assertEqual(meta["model"], "m")

    def test_rejects_payload_without_image(self) -> None:
        with self.assertRaises(tool.ImageToolError) as caught:
            tool.find_base64({"unexpected": True})
        self.assertEqual(caught.exception.category, "input_error")

    def test_rejects_multi_image_payload(self) -> None:
        with self.assertRaises(tool.ImageToolError):
            tool.find_base64({"data": [{"b64_json": "AAAA"}, {"b64_json": "BBBB"}]})


class VerificationTests(unittest.TestCase):
    def test_accepts_valid_png(self) -> None:
        artifact = tool.inspect(png_bytes(), tool.DEFAULT_MAX_IMAGE_BYTES)
        self.assertEqual(artifact["format"], "png")
        self.assertEqual(artifact["width"], 64)
        self.assertEqual(len(artifact["sha256"]), 64)

    def test_rejects_truncated_container(self) -> None:
        with self.assertRaises(tool.ImageToolError) as caught:
            tool.inspect(png_bytes()[:-1], tool.DEFAULT_MAX_IMAGE_BYTES)
        self.assertEqual(caught.exception.category, "invalid_image")

    def test_rejects_non_image_bytes(self) -> None:
        with self.assertRaises(tool.ImageToolError):
            tool.inspect(b"not an image at all", tool.DEFAULT_MAX_IMAGE_BYTES)

    def test_decompression_bomb_is_structured(self) -> None:
        with mock.patch("PIL.Image.open", side_effect=DecompressionBombError("too large")):
            with self.assertRaises(tool.ImageToolError) as caught:
                tool.inspect(png_bytes(), tool.DEFAULT_MAX_IMAGE_BYTES)
        self.assertEqual(caught.exception.category, "invalid_image")

    def test_pixel_limit_is_enforced(self) -> None:
        with mock.patch.object(tool, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(tool.ImageToolError):
                tool.inspect(png_bytes(64, 64), tool.DEFAULT_MAX_IMAGE_BYTES)

    def test_size_limit_is_enforced(self) -> None:
        with self.assertRaises(tool.ImageToolError):
            tool.inspect(png_bytes(), 10)

    def test_extension_must_match_format(self) -> None:
        with self.assertRaises(tool.ImageToolError) as caught:
            tool.require_extension(Path("out.jpg"), "png")
        self.assertEqual(caught.exception.category, "input_error")
        self.assertEqual(tool.require_extension(Path("out.jpeg"), "jpeg").suffix, ".jpeg")


class WriteTests(unittest.TestCase):
    def run_tool(self, root: Path, *, body: bytes | None = None, overwrite: bool = False, output: Path | None = None) -> dict:
        payload = root / "response.json"
        payload.write_text(json.dumps(response_payload(body=body)), encoding="utf-8")
        destination = output or (root / "out.png")
        argv = ["save_image.py", "--input", str(payload), "--output", str(destination), "--prompt", "a blue circle"]
        if overwrite:
            argv.append("--overwrite")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with mock.patch.object(sys, "argv", argv):
                code = tool.main()
        self.assertEqual(code, 0, buffer.getvalue())
        return json.loads(buffer.getvalue())

    def test_writes_verified_artifact_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_tool(root)
            output = root / "out.png"
            self.assertTrue(result["ok"])
            self.assertEqual(output.read_bytes(), png_bytes())
            self.assertEqual(result["artifact"]["sha256"], tool.inspect(png_bytes(), tool.DEFAULT_MAX_IMAGE_BYTES)["sha256"])
            self.assertEqual(result["generation_id"], "gen_test")
            self.assertEqual(result["request_id"], "req_from_payload")
            self.assertEqual(result["prompt"], "a blue circle")
            sidecar = json.loads(Path(result["provenance_path"]).read_text(encoding="utf-8"))
            self.assertEqual(sidecar["schema"], tool.RESULT_SCHEMA)
            self.assertEqual(sidecar["artifact"]["sha256"], result["artifact"]["sha256"])
            self.assertNotIn(base64.b64encode(png_bytes()).decode("ascii"), json.dumps(sidecar))

    def test_raw_image_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "raw.png"
            source.write_bytes(png_bytes())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                with mock.patch.object(
                    sys, "argv", ["save_image.py", "--input", str(source), "--output", str(root / "out.png")]
                ):
                    code = tool.main()
            self.assertEqual(code, 0, buffer.getvalue())
            self.assertTrue((root / "out.png").exists())

    def test_refuses_to_overwrite_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_tool(root)
            payload = root / "response.json"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                with mock.patch.object(
                    sys, "argv", ["save_image.py", "--input", str(payload), "--output", str(root / "out.png")]
                ):
                    code = tool.main()
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(buffer.getvalue())["error"]["category"], "artifact_exists")

    def test_overwrite_replaces_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out.png"
            output.write_bytes(b"stale")
            result = self.run_tool(root, overwrite=True)
            self.assertTrue(result["ok"])
            self.assertEqual(output.read_bytes(), png_bytes())

    def test_symlink_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.png"
            link = root / "link.png"
            link.symlink_to(target)
            payload = root / "response.json"
            payload.write_text(json.dumps(response_payload()), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                with mock.patch.object(
                    sys, "argv", ["save_image.py", "--input", str(payload), "--output", str(link)]
                ):
                    code = tool.main()
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(buffer.getvalue())["error"]["category"], "artifact_error")
            self.assertFalse(target.exists())

    def test_jpeg_payload_requires_jpeg_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "response.json"
            data = response_payload(body=jpeg_bytes())
            data["output_format"] = "jpeg"
            payload.write_text(json.dumps(data), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                with mock.patch.object(
                    sys, "argv", ["save_image.py", "--input", str(payload), "--output", str(root / "out.jpg")]
                ):
                    code = tool.main()
            self.assertEqual(code, 0, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
