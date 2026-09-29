#!/usr/bin/env python3
"""Recover a completed image result that the harness did not hand back.

Some harnesses report a successful generation and never deliver the payload: the
result exists only as a Base64 record in the harness's own task log, and the UI
loses it. This is not a generation path and not a second generator — it reads the
completed record and hands it to `save_image.py`, so a recovered image gets the
same container check, decode, dimension check, atomic write and provenance
sidecar as one the harness did hand back.

Usage:
  mark     print or write a UTC marker immediately BEFORE the harness call
  extract  find the completed call after that marker and save it as an artifact

Adapters (--harness):
  codex    $CODEX_HOME/sessions and $CODEX_HOME/archived_sessions, *.jsonl,
           response_item records of type image_generation_call with status
           completed. Grow this list when a new harness appears; do not fork a
           second delivery skill for it.

This tool never prints the payload, never writes to the harness's logs, never
deletes session data, and never makes a network request.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import save_image as image_tools  # the one save/verify path; no duplicated rules

UTC = dt.timezone.utc
MARKER_PREFIX = ".image-gen-recovery-"
MARKER_SUFFIX = ".marker"
ADAPTERS = ("codex",)


def utc_now() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# --------------------------------------------------------------------------
# adapter: codex
# --------------------------------------------------------------------------


def codex_session_files(codex_home: Path) -> list[Path]:
    files: list[Path] = []
    for name in ("sessions", "archived_sessions"):
        root = codex_home / name
        if root.is_dir():
            files.extend(root.rglob("*.jsonl"))
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def codex_iter_calls(codex_home: Path, since: dt.datetime, prompt_contains: str | None) -> Iterable[dict[str, Any]]:
    for path in codex_session_files(codex_home):
        try:
            if dt.datetime.fromtimestamp(path.stat().st_mtime, UTC) < since - dt.timedelta(minutes=2):
                continue
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = record.get("payload") or {}
                    if record.get("type") != "response_item" or not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "image_generation_call" or payload.get("status") != "completed":
                        continue
                    result = payload.get("result")
                    if not isinstance(result, str) or not result:
                        continue
                    timestamp = record.get("timestamp")
                    try:
                        event_time = parse_time(str(timestamp))
                    except ValueError:
                        continue
                    if event_time < since:
                        continue
                    prompt = str(payload.get("revised_prompt") or "")
                    if prompt_contains and prompt_contains.casefold() not in prompt.casefold():
                        continue
                    yield {
                        "timestamp": event_time,
                        "timestamp_text": str(timestamp),
                        "id": str(payload.get("id") or "generated-image"),
                        "prompt": prompt,
                        "result": result,
                        "session": str(path),
                        "line": line_number,
                    }
        except OSError:
            continue


def iter_calls(harness: str, codex_home: Path, since: dt.datetime, prompt_contains: str | None) -> Iterable[dict[str, Any]]:
    if harness == "codex":
        return codex_iter_calls(codex_home, since, prompt_contains)
    raise SystemExit(f"unknown harness '{harness}'; supported: {', '.join(ADAPTERS)}")


# --------------------------------------------------------------------------
# output paths
# --------------------------------------------------------------------------


def safe_stem(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in value).strip("-._")
    return cleaned[:80] or "generated-image"


def canonical_suffix(image_format: str) -> str:
    """The extension written for a format detected by save_image.py.

    `.jpg` for jpeg is deliberate: save_image.py accepts `.jpg` and `.jpeg`, and
    the shorter spelling is what the skill's examples and templates use.
    """
    return {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}[image_format]


def available_path(directory: Path, stem: str, suffix: str, force: bool) -> Path:
    candidate = directory / f"{stem}{suffix}"
    if force:
        return candidate
    index = 2
    while candidate.exists() or candidate.with_name(candidate.name + ".json").exists():
        candidate = directory / f"{stem}-v{index}{suffix}"
        index += 1
    return candidate


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def command_mark(args: argparse.Namespace) -> int:
    marker = utc_now()
    if args.create_in:
        directory = Path(args.create_in).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{MARKER_PREFIX}{uuid.uuid4().hex}{MARKER_SUFFIX}"
        path.write_text(marker + "\n", encoding="utf-8")
        print(path)
    elif args.file:
        path = Path(args.file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(marker + "\n", encoding="utf-8")
        print(path)
    else:
        print(marker)
    return 0


def save_recovered(call: dict[str, Any], output: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Hand one recovered payload to save_image.py and return its report."""
    payload = {"data": [{"b64_json": call["result"], "generation_id": call["id"]}]}
    with tempfile.TemporaryDirectory(prefix="image-gen-recovery-") as scratch:
        payload_path = Path(scratch) / "recovered.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        command = [
            sys.executable,
            str(SCRIPT_DIR / "save_image.py"),
            "--input",
            str(payload_path),
            "--output",
            str(output),
            "--provider",
            args.provider,
        ]
        for flag, value in (
            ("--prompt", call["prompt"]),
            ("--model", args.model),
            ("--generation-id", call["id"]),
        ):
            if value:
                command += [flag, str(value)]
        if args.force:
            command.append("--overwrite")
        completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stdout or completed.stderr).strip().splitlines()
        raise SystemExit(json.dumps({"ok": False, "error": "save_failed", "message": detail[-1] if detail else "save_image.py failed"}))
    return json.loads(completed.stdout)


def command_extract(args: argparse.Namespace) -> int:
    if args.marker:
        since_text = Path(args.marker).expanduser().read_text(encoding="utf-8").strip()
    elif args.since:
        since_text = args.since
    else:
        raise SystemExit("extract requires --since or --marker")
    since = parse_time(since_text)
    if args.wait_seconds < 0:
        raise SystemExit("--wait-seconds must be non-negative")
    codex_home = Path(args.codex_home or os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()

    deadline = time.monotonic() + args.wait_seconds
    while True:
        calls = sorted(iter_calls(args.harness, codex_home, since, args.prompt_contains), key=lambda item: item["timestamp"])
        if calls or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if not calls:
        print(json.dumps({"ok": False, "error": "no_completed_generation", "harness": args.harness, "since": since_text}))
        return 2

    selected = calls if args.all else [calls[-1]]
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    for index, call in enumerate(selected, 1):
        try:
            content = base64.b64decode(call["result"], validate=True)
        except (binascii.Error, ValueError) as error:
            print(json.dumps({"ok": False, "error": "invalid_base64", "message": str(error)}))
            return 3
        try:
            image_format = image_tools.validate_container(content)
        except image_tools.ImageToolError as error:
            print(json.dumps({"ok": False, "error": "unsupported_container", "message": str(error)}))
            return 4
        if args.name and len(selected) > 1:
            stem = f"{safe_stem(args.name)}-{index}"
        elif args.name:
            stem = safe_stem(args.name)
        else:
            stem = safe_stem(call["id"])
        output = available_path(out_dir, stem, canonical_suffix(image_format), args.force)
        report = save_recovered(call, output, args)
        outputs.append(
            {
                "path": report["artifact"]["path"],
                "provenance_path": report["provenance_path"],
                "format": report["artifact"]["format"],
                "width": report["artifact"]["width"],
                "height": report["artifact"]["height"],
                "sha256": report["artifact"]["sha256"],
                "id": call["id"],
                "timestamp": call["timestamp_text"],
                "prompt": call["prompt"],
                "source_session": call["session"],
                "source_line": call["line"],
            }
        )

    marker_removed = False
    if args.marker and not args.keep_marker:
        marker_path = Path(args.marker).expanduser().resolve()
        if marker_path.name.startswith(MARKER_PREFIX) and marker_path.suffix == MARKER_SUFFIX:
            try:
                marker_path.unlink()
                marker_removed = True
            except FileNotFoundError:
                pass
    print(json.dumps({"ok": True, "harness": args.harness, "outputs": outputs, "marker_removed": marker_removed}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    mark = sub.add_parser("mark", help="print or write a UTC marker immediately before the harness call")
    where = mark.add_mutually_exclusive_group()
    where.add_argument("--file", help="write the marker to this exact file and print its absolute path")
    where.add_argument("--create-in", help="create a unique marker file in this directory and print its absolute path")
    mark.set_defaults(func=command_mark)

    extract = sub.add_parser("extract", help="find the completed call and save it as a verified artifact")
    source = extract.add_mutually_exclusive_group(required=True)
    source.add_argument("--since", help="ISO-8601 UTC marker printed by the mark command")
    source.add_argument("--marker", help="file containing an ISO-8601 marker")
    extract.add_argument("--harness", default="codex", choices=ADAPTERS, help="which harness log to read (default: codex)")
    extract.add_argument("--out-dir", required=True, help="destination directory; the format picks the extension")
    extract.add_argument("--name", help="desired filename stem; a versioned name is chosen if it is taken")
    extract.add_argument("--prompt-contains", help="only accept calls whose revised prompt contains this text")
    extract.add_argument("--wait-seconds", type=float, default=5.0, help="wait for the log flush before failing (default: 5)")
    extract.add_argument("--all", action="store_true", help="save every matching call instead of only the latest")
    extract.add_argument("--keep-marker", action="store_true", help="keep a unique marker file after a successful extraction")
    extract.add_argument("--force", action="store_true", help="overwrite the exact target instead of versioning")
    extract.add_argument("--provider", default="codex", help="provider label recorded in provenance (default: codex)")
    extract.add_argument("--model", help="model identifier recorded in provenance, when the log does not carry one")
    extract.add_argument("--codex-home", help="override CODEX_HOME")
    extract.set_defaults(func=command_extract)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
