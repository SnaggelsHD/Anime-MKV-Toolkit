import json
import subprocess

TIMEOUT = 120


class MkvToolError(RuntimeError):
    pass


def extract_chapters(path: str) -> str | None:
    """Return chapter XML for the file, or None if it has no chapters."""
    try:
        proc = subprocess.run(
            ["mkvextract", "chapters", path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise MkvToolError("mkvextract is not installed") from exc

    if proc.returncode != 0:
        raise MkvToolError(f"mkvextract failed: {proc.stderr.strip()}")

    xml = proc.stdout.strip().lstrip("﻿")
    return xml or None


def extract_track_metadata(path: str) -> str:
    """Return the complete mediainfo report for the file as a JSON string."""
    try:
        proc = subprocess.run(
            ["mediainfo", "--Output=JSON", path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise MkvToolError("mediainfo is not installed") from exc

    if proc.returncode != 0:
        raise MkvToolError(f"mediainfo failed: {proc.stderr.strip()}")

    report = proc.stdout.strip()
    if not report:
        raise MkvToolError("mediainfo returned no output")
    try:
        json.loads(report)
    except json.JSONDecodeError as exc:
        raise MkvToolError(f"mediainfo returned invalid JSON: {exc}") from exc
    return report
