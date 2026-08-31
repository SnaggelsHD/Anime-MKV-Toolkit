"""Embedded rules from the standalone mkv_cleanup.py script: normalizes track
languages/names and container metadata for anime MKV rips. Logic (including
the track:1 forced-to-Japanese quirk) is kept identical to that script;
this module returns structured results instead of logging to the console.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

TIMEOUT = 120

LANGUAGE_NAMES = {
    "ger": "German",
    "deu": "German",
    "jpn": "Japanese",
    "eng": "English",
    "fre": "French",
    "fra": "French",
    "pol": "Polish",
    "chi": "Chinese",
    "zho": "Chinese",
    "zh-CN": "Chinese",
    "und": "und",
}

DEFAULT_CODEC_NAMES = {
    "DTS": "DTS",
    "A_DTS": "DTS",
    "DTS-HD Master Audio": "DTS-HD MA",
    "DTS-HD High Resolution Audio": "DTS-HD HR",
    "AC-3": "Dolby Digital",
    "AC-3 Dolby Surround EX": "Dolby Digital Surround EX",
    "AAC": "AAC",
    "TrueHD": "Dolby TrueHD",
    "TrueHD Atmos": "Dolby Atmos TrueHD",
    "FLAC": "FLAC",
    "PCM": "PCM",
    "A_MS/ACM": "PCM",
    "Opus": "Opus",
    "E-AC-3": "Dolby Digital Plus",
    "unknown, format tag 0x0161": "WMA",
}

CHANNEL_LAYOUTS = {
    1: "1.0",
    2: "2.0",
    6: "5.1",
    8: "7.1",
}


class MkvCleanupError(RuntimeError):
    pass


@dataclass
class PlannedEdit:
    selector: str
    action: str
    value: str | None = None


@dataclass
class FilePlan:
    path: str
    title: str
    edits: list[PlannedEdit] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def get_mkvmerge_json(path: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["mkvmerge", "-J", path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise MkvCleanupError("mkvmerge is not installed") from exc

    if proc.returncode != 0:
        raise MkvCleanupError(proc.stderr.strip() or f"mkvmerge failed for {path}")
    return json.loads(proc.stdout)


def lang_name(code: str | None) -> str:
    code = code or "und"
    return LANGUAGE_NAMES.get(code, code)


def codec_name(codec: str | None, codec_map: dict[str, str]) -> tuple[str, bool]:
    if not codec:
        return "unknown", True
    if codec in codec_map:
        return codec_map[codec], False
    return codec, True


def channel_layout(channels: Any) -> str:
    if channels is None:
        return "unknown"
    try:
        ch = int(channels)
    except (TypeError, ValueError):
        return str(channels)
    return CHANNEL_LAYOUTS.get(ch, f"{ch}.0")


def is_forced(props: dict[str, Any]) -> bool:
    value = props.get("flag_forced_track", props.get("forced_track"))
    return value in (True, 1, "1", "true", "True")


def is_commentary(props: dict[str, Any]) -> bool:
    value = props.get("flag_commentary", props.get("commentary"))
    return value in (True, 1, "1", "true", "True")


def inspect_file(
    path: str,
    codec_map: dict[str, str],
    forced_suffix: str = "Forced",
    commentary_suffix: str = "Commentary",
) -> FilePlan:
    data = get_mkvmerge_json(path)
    title = os.path.splitext(os.path.basename(path))[0]
    plan = FilePlan(path=path, title=title)

    plan.edits.append(PlannedEdit("info", "set", f"title={plan.title}"))
    plan.edits.append(PlannedEdit("info", "delete", "date"))
    plan.edits.append(PlannedEdit("info", "set", "writing-application="))
    plan.edits.append(PlannedEdit("info", "set", "muxing-application="))
    plan.summaries.append(f'title -> "{plan.title}"')
    plan.summaries.append("date -> ")
    plan.summaries.append("writing-application -> ")
    plan.summaries.append("muxing-application -> ")

    tracks = data.get("tracks", [])

    if tracks:
        plan.edits.append(PlannedEdit("track:1", "set", "language=jpn"))
        plan.edits.append(PlannedEdit("track:1", "delete", "name"))
        plan.summaries.append("track:1 language -> jpn")
        plan.summaries.append("track:1 name -> <deleted>")

    for overall_index, track in enumerate(tracks, start=1):
        track_type = track.get("type")
        props = track.get("properties", {})
        language = props.get("language") or "und"

        if track_type == "video":
            selector = f"track:{overall_index}"
            plan.edits.append(PlannedEdit(selector, "set", "flag-default=1"))
            plan.summaries.append(f"{selector} video -> flag-default=1")

        if track_type == "audio":
            selector = f"track:{overall_index}"
            codec = track.get("codec")
            codec_display, unknown_codec = codec_name(codec, codec_map)
            ch_layout = channel_layout(props.get("audio_channels"))
            commentary = is_commentary(props)

            if commentary:
                new_name = f"{lang_name(language)} Commentary {codec_display} {ch_layout}"
            else:
                new_name = f"{lang_name(language)} {codec_display} {ch_layout}"

            if unknown_codec:
                plan.warnings.append(f"unknown audio codec on {os.path.basename(path)}: {codec}")

            plan.edits.append(PlannedEdit(selector, "set", f"name={new_name}"))
            plan.summaries.append(f'{selector} audio -> "{new_name}"')

        elif track_type == "subtitles":
            selector = f"track:{overall_index}"
            suffix = f" {forced_suffix}" if is_forced(props) else ""
            commentary = is_commentary(props)

            if commentary:
                new_name = f"{lang_name(language)} {commentary_suffix}{suffix}"
            else:
                new_name = f"{lang_name(language)}{suffix}"

            plan.edits.append(PlannedEdit(selector, "set", f"name={new_name}"))
            plan.summaries.append(f'{selector} subtitles -> "{new_name}"')

    return plan


def apply_plan(plan: FilePlan) -> tuple[str, str, int]:
    cmd = ["mkvpropedit", plan.path]
    current_selector = None

    for edit in plan.edits:
        if edit.selector != current_selector:
            cmd.extend(["--edit", edit.selector])
            current_selector = edit.selector
        if edit.action == "set" and edit.value is not None:
            cmd.extend(["--set", edit.value])
        elif edit.action == "delete" and edit.value is not None:
            cmd.extend(["--delete", edit.value])

    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=TIMEOUT)
    except FileNotFoundError as exc:
        raise MkvCleanupError("mkvpropedit is not installed") from exc
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def clean_file(
    path: str,
    codec_map: dict[str, str],
    forced_suffix: str = "Forced",
    commentary_suffix: str = "Commentary",
    dry_run: bool = False,
) -> dict:
    """Inspect and clean up one MKV file's metadata in place. Returns a
    structured result: {ok, summary, warnings, error, edits_count}.

    With dry_run=True, only inspect_file() (read-only, via mkvmerge -J) runs -
    apply_plan() (which invokes mkvpropedit) is skipped, so the file on disk
    is never touched."""
    if not os.path.isfile(path):
        return {"ok": False, "error": "File not found on disk", "summary": [], "warnings": [], "edits_count": 0}

    try:
        plan = inspect_file(path, codec_map, forced_suffix, commentary_suffix)
    except MkvCleanupError as exc:
        return {"ok": False, "error": str(exc), "summary": [], "warnings": [], "edits_count": 0}

    if dry_run:
        return {
            "ok": True,
            "error": None,
            "summary": plan.summaries,
            "warnings": plan.warnings,
            "edits_count": len(plan.edits),
        }

    stdout, stderr, code = apply_plan(plan)
    if code != 0:
        error = stderr or stdout or f"mkvpropedit exited with code {code}"
        return {
            "ok": False,
            "error": error,
            "summary": plan.summaries,
            "warnings": plan.warnings,
            "edits_count": len(plan.edits),
        }

    return {
        "ok": True,
        "error": None,
        "summary": plan.summaries,
        "warnings": plan.warnings,
        "edits_count": len(plan.edits),
    }
