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
    """Return a JSON string: array of track objects (track_id, track_type,
    language, name, default, forced)."""
    try:
        proc = subprocess.run(
            ["mkvmerge", "-J", path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise MkvToolError("mkvmerge is not installed") from exc

    if proc.returncode != 0:
        raise MkvToolError(f"mkvmerge failed: {proc.stderr.strip()}")

    info = json.loads(proc.stdout)
    tracks = []
    for track in info.get("tracks", []):
        props = track.get("properties", {})
        tracks.append(
            {
                "track_id": track.get("id"),
                "track_type": track.get("type"),
                "language": props.get("language"),
                "name": props.get("track_name"),
                "default": bool(props.get("default_track", False)),
                "forced": bool(props.get("forced_track", False)),
                "codec": track.get("codec"),
            }
        )
    return json.dumps(tracks)
