from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import recover_harness_image as recovery  # noqa: E402


def png_bytes(width: int = 48, height: int = 32) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (18, 33, 36)).save(buffer, format="PNG")
    return buffer.getvalue()


def call_record(timestamp: str, *, status: str = "completed", prompt: str = "a calm hero", data: bytes | None = None) -> dict:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "image_generation_call",
            "status": status,
            "id": "ig_test",
            "revised_prompt": prompt,
            "result": base64.b64encode(png_bytes() if data is None else data).decode(),
        },
    }


def session_file(codex_home: Path, records: list[dict]) -> Path:
    path = codex_home / "sessions" / "2026" / "01" / "01" / "rollout-test.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


class LogSelectionTests(unittest.TestCase):
    def test_selects_only_completed_generation_calls(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch)
            session_file(
                codex_home,
                [
                    {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item", "payload": {"type": "message"}},
                    call_record("2026-01-01T00:00:02Z", status="in_progress"),
                    call_record("2026-01-01T00:00:03Z"),
                ],
            )
            calls = list(recovery.codex_iter_calls(codex_home, recovery.parse_time("2026-01-01T00:00:00Z"), None))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["prompt"], "a calm hero")
            self.assertEqual(calls[0]["line"], 3)

    def test_respects_the_marker_time(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch)
            session_file(codex_home, [call_record("2026-01-01T00:00:00Z")])
            before = list(recovery.codex_iter_calls(codex_home, recovery.parse_time("2025-12-31T00:00:00Z"), None))
            after = list(recovery.codex_iter_calls(codex_home, recovery.parse_time("2026-02-01T00:00:00Z"), None))
            self.assertEqual(len(before), 1)
            self.assertEqual(after, [])

    def test_prompt_filter_is_case_insensitive_and_required(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch)
            session_file(codex_home, [call_record("2026-01-01T00:00:00Z", prompt="A CALM Hero, landscape")])
            since = recovery.parse_time("2025-12-31T00:00:00Z")
            self.assertEqual(len(list(recovery.codex_iter_calls(codex_home, since, "calm hero"))), 1)
            self.assertEqual(list(recovery.codex_iter_calls(codex_home, since, "seascape")), [])

    def test_unreadable_lines_are_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch)
            path = session_file(codex_home, [call_record("2026-01-01T00:00:00Z")])
            path.write_text("{not json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
            calls = list(recovery.codex_iter_calls(codex_home, recovery.parse_time("2025-12-31T00:00:00Z"), None))
            self.assertEqual(len(calls), 1)

    def test_unknown_harness_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            list(recovery.iter_calls("nope", Path("/nonexistent"), recovery.parse_time("2026-01-01T00:00:00Z"), None))


class OutputPathTests(unittest.TestCase):
    def test_suffix_follows_the_detected_format(self) -> None:
        self.assertEqual(recovery.canonical_suffix("png"), ".png")
        self.assertEqual(recovery.canonical_suffix("jpeg"), ".jpg")
        self.assertEqual(recovery.canonical_suffix("webp"), ".webp")

    def test_a_taken_name_is_versioned_rather_than_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            (directory / "hero.png").write_bytes(b"taken")
            (directory / "hero-v2.png").write_bytes(b"taken")
            chosen = recovery.available_path(directory, "hero", ".png", force=False)
            self.assertEqual(chosen.name, "hero-v3.png")

    def test_a_lone_provenance_sidecar_also_blocks_the_name(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            (directory / "hero.png.json").write_text("{}", encoding="utf-8")
            self.assertEqual(recovery.available_path(directory, "hero", ".png", force=False).name, "hero-v2.png")

    def test_force_targets_the_exact_path(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            (directory / "hero.png").write_bytes(b"taken")
            self.assertEqual(recovery.available_path(directory, "hero", ".png", force=True).name, "hero.png")

    def test_stem_is_sanitised(self) -> None:
        self.assertEqual(recovery.safe_stem("../../etc/passwd"), "etc-passwd")
        self.assertEqual(recovery.safe_stem("///"), "generated-image")


class ExtractCommandTests(unittest.TestCase):
    def run_extract(self, argv: list[str]) -> int:
        args = recovery.build_parser().parse_args(argv)
        return args.func(args)

    def test_recovered_artifact_matches_the_logged_bytes_and_carries_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch) / "codex"
            out_dir = Path(scratch) / "out"
            session_file(codex_home, [call_record("2026-01-01T00:00:00Z", prompt="poster background")])
            code = self.run_extract(
                [
                    "extract",
                    "--since",
                    "2025-12-31T00:00:00Z",
                    "--out-dir",
                    str(out_dir),
                    "--name",
                    "recovered",
                    "--wait-seconds",
                    "0",
                    "--codex-home",
                    str(codex_home),
                ]
            )
            self.assertEqual(code, 0)
            artifact = out_dir / "recovered.png"
            self.assertEqual(artifact.read_bytes(), png_bytes())
            manifest = json.loads((out_dir / "recovered.png.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "ab-image-gen.result.v1")
            self.assertEqual(manifest["provider"], "codex")
            self.assertEqual(manifest["prompt"], "poster background")
            self.assertEqual(manifest["generation_id"], "ig_test")
            self.assertEqual(manifest["artifact"]["sha256"], hashlib.sha256(png_bytes()).hexdigest())
            self.assertEqual(manifest["artifact"]["width"], 48)

    def test_no_completed_call_exits_2_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch) / "codex"
            out_dir = Path(scratch) / "out"
            session_file(codex_home, [call_record("2026-01-01T00:00:00Z", status="in_progress")])
            code = self.run_extract(
                [
                    "extract",
                    "--since",
                    "2025-12-31T00:00:00Z",
                    "--out-dir",
                    str(out_dir),
                    "--wait-seconds",
                    "0",
                    "--codex-home",
                    str(codex_home),
                ]
            )
            self.assertEqual(code, 2)
            self.assertFalse(out_dir.exists())

    def test_all_saves_every_matching_call_with_distinct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch) / "codex"
            out_dir = Path(scratch) / "out"
            session_file(
                codex_home,
                [call_record("2026-01-01T00:00:01Z"), call_record("2026-01-01T00:00:02Z")],
            )
            code = self.run_extract(
                [
                    "extract",
                    "--since",
                    "2025-12-31T00:00:00Z",
                    "--out-dir",
                    str(out_dir),
                    "--name",
                    "batch",
                    "--all",
                    "--wait-seconds",
                    "0",
                    "--codex-home",
                    str(codex_home),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(sorted(path.name for path in out_dir.glob("*.png")), ["batch-1.png", "batch-2.png"])

    def test_a_unique_marker_is_removed_after_a_successful_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch) / "codex"
            out_dir = Path(scratch) / "out"
            session_file(codex_home, [call_record("2026-01-01T00:00:00Z")])
            marker = Path(scratch) / f"{recovery.MARKER_PREFIX}test{recovery.MARKER_SUFFIX}"
            marker.write_text("2025-12-31T00:00:00Z\n", encoding="utf-8")
            code = self.run_extract(
                [
                    "extract",
                    "--marker",
                    str(marker),
                    "--out-dir",
                    str(out_dir),
                    "--wait-seconds",
                    "0",
                    "--codex-home",
                    str(codex_home),
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(marker.exists())

    def test_a_marker_the_tool_did_not_create_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            codex_home = Path(scratch) / "codex"
            out_dir = Path(scratch) / "out"
            session_file(codex_home, [call_record("2026-01-01T00:00:00Z")])
            marker = Path(scratch) / "handmade.marker"
            marker.write_text("2025-12-31T00:00:00Z\n", encoding="utf-8")
            code = self.run_extract(
                [
                    "extract",
                    "--marker",
                    str(marker),
                    "--out-dir",
                    str(out_dir),
                    "--wait-seconds",
                    "0",
                    "--codex-home",
                    str(codex_home),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
