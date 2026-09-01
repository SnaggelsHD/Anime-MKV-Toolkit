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


# Ordered list of language codes: the first one that matches an audio track's
# language (compared by display name, so "ger"/"deu" both count as German)
# is the one picked as the file's default audio track. If none match, the
# first audio track in the file is used as a fallback ("else").
DEFAULT_AUDIO_PRIORITY: list[str] = ["ger", "jpn", "eng"]


def pick_default_audio_track(tracks: list[dict[str, Any]], priority: list[str]) -> tuple[int | None, str | None]:
    """Return (overall_index, matched_language_name) for the audio track that
    should be marked default, or (None, None) if the file has no audio
    tracks. `overall_index` is 1-based over ALL tracks (matching mkvmerge's
    track numbering), not just audio ones."""
    audio_tracks = [
        (i, t) for i, t in enumerate(tracks, start=1) if t.get("type") == "audio"
    ]
    if not audio_tracks:
        return None, None

    wanted_names = [lang_name(code) for code in priority]
    for wanted in wanted_names:
        for index, track in audio_tracks:
            track_lang = lang_name(track.get("properties", {}).get("language") or "und")
            if track_lang == wanted:
                return index, wanted

    fallback_index, fallback_track = audio_tracks[0]
    fallback_lang = lang_name(fallback_track.get("properties", {}).get("language") or "und")
    return fallback_index, fallback_lang


DEFAULT_STEPS: dict[str, bool] = {
    "set_title": True,
    "clear_date": True,
    "clear_writing_app": True,
    "clear_muxing_app": True,
    "force_first_track_japanese": True,
    "set_video_default": True,
    "select_default_audio": True,
    "rename_audio_tracks": True,
    "rename_subtitle_tracks": True,
}


def inspect_file(
    path: str,
    codec_map: dict[str, str],
    forced_suffix: str = "Forced",
    commentary_suffix: str = "Commentary",
    steps: dict[str, bool] | None = None,
    audio_priority: list[str] | None = None,
) -> FilePlan:
    steps = {**DEFAULT_STEPS, **(steps or {})}
    audio_priority = audio_priority if audio_priority is not None else DEFAULT_AUDIO_PRIORITY
    data = get_mkvmerge_json(path)
    title = os.path.splitext(os.path.basename(path))[0]
    plan = FilePlan(path=path, title=title)

    if steps["set_title"]:
        plan.edits.append(PlannedEdit("info", "set", f"title={plan.title}"))
        plan.summaries.append(f'title -> "{plan.title}"')
    if steps["clear_date"]:
        plan.edits.append(PlannedEdit("info", "delete", "date"))
        plan.summaries.append("date -> ")
    if steps["clear_writing_app"]:
        plan.edits.append(PlannedEdit("info", "set", "writing-application="))
        plan.summaries.append("writing-application -> ")
    if steps["clear_muxing_app"]:
        plan.edits.append(PlannedEdit("info", "set", "muxing-application="))
        plan.summaries.append("muxing-application -> ")

    tracks = data.get("tracks", [])

    if steps["force_first_track_japanese"] and tracks:
        plan.edits.append(PlannedEdit("track:1", "set", "language=jpn"))
        plan.edits.append(PlannedEdit("track:1", "delete", "name"))
        plan.summaries.append("track:1 language -> jpn")
        plan.summaries.append("track:1 name -> <deleted>")

    default_audio_index = None
    default_audio_lang = None
    if steps["select_default_audio"]:
        default_audio_index, default_audio_lang = pick_default_audio_track(tracks, audio_priority)

    for overall_index, track in enumerate(tracks, start=1):
        track_type = track.get("type")
        props = track.get("properties", {})
        language = props.get("language") or "und"

        if track_type == "video" and steps["set_video_default"]:
            selector = f"track:{overall_index}"
            plan.edits.append(PlannedEdit(selector, "set", "flag-default=1"))
            plan.summaries.append(f"{selector} video -> flag-default=1")

        if track_type == "audio" and default_audio_index is not None:
            selector = f"track:{overall_index}"
            if overall_index == default_audio_index:
                plan.edits.append(PlannedEdit(selector, "set", "flag-default=1"))
                plan.summaries.append(f"{selector} audio default -> flag-default=1 ({default_audio_lang})")
            else:
                plan.edits.append(PlannedEdit(selector, "set", "flag-default=0"))
                plan.summaries.append(f"{selector} audio default -> flag-default=0")

        if track_type == "audio" and steps["rename_audio_tracks"]:
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

        elif track_type == "subtitles" and steps["rename_subtitle_tracks"]:
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


def set_track_flags(path: str, flags: list[dict[str, Any]]) -> dict:
    """Directly set flag-default/flag-forced on specific tracks via
    mkvpropedit, independent of the rest of the cleanup pipeline (no title/
    tag/rename edits, no dry-run mode). `flags` is a list of
    {"id": <1-based overall track number>, "default": bool, "forced": bool},
    matching mediainfo's per-track "ID" field, which lines up with
    mkvpropedit's track:N selector. Used by the episode/season track flag
    editor in the UI."""
    if not os.path.isfile(path):
        return {"ok": False, "error": "File not found on disk"}
    if not flags:
        return {"ok": True, "error": None}

    plan = FilePlan(path=path, title=os.path.splitext(os.path.basename(path))[0])
    for f in flags:
        selector = f"track:{f['id']}"
        plan.edits.append(PlannedEdit(selector, "set", f"flag-default={1 if f['default'] else 0}"))
        plan.edits.append(PlannedEdit(selector, "set", f"flag-forced={1 if f['forced'] else 0}"))

    try:
        stdout, stderr, code = apply_plan(plan)
    except MkvCleanupError as exc:
        return {"ok": False, "error": str(exc)}
    if code != 0:
        return {"ok": False, "error": stderr or stdout or f"mkvpropedit exited with code {code}"}
    return {"ok": True, "error": None}


def clean_file(
    path: str,
    codec_map: dict[str, str],
    forced_suffix: str = "Forced",
    commentary_suffix: str = "Commentary",
    dry_run: bool = False,
    steps: dict[str, bool] | None = None,
    audio_priority: list[str] | None = None,
) -> dict:
    """Inspect and clean up one MKV file's metadata in place. Returns a
    structured result: {ok, summary, warnings, error, edits_count}.

    With dry_run=True, only inspect_file() (read-only, via mkvmerge -J) runs -
    apply_plan() (which invokes mkvpropedit) is skipped, so the file on disk
    is never touched. `steps` turns individual cleanup steps on/off (see
    DEFAULT_STEPS); a step that's off contributes no edits and no summary
    lines at all. `audio_priority` controls which language wins the default
    audio track when select_default_audio is on (see DEFAULT_AUDIO_PRIORITY)."""
    if not os.path.isfile(path):
        return {"ok": False, "error": "File not found on disk", "summary": [], "warnings": [], "edits_count": 0}

    try:
        plan = inspect_file(path, codec_map, forced_suffix, commentary_suffix, steps=steps, audio_priority=audio_priority)
    except MkvCleanupError as exc:
        return {"ok": False, "error": str(exc), "summary": [], "warnings": [], "edits_count": 0}

    edits_count = len(plan.edits)

    if dry_run:
        return {
            "ok": True,
            "error": None,
            "summary": plan.summaries,
            "warnings": plan.warnings,
            "edits_count": edits_count,
        }

    stdout, stderr, code = apply_plan(plan)
    if code != 0:
        error = stderr or stdout or f"mkvpropedit exited with code {code}"
        return {
            "ok": False,
            "error": error,
            "summary": plan.summaries,
            "warnings": plan.warnings,
            "edits_count": edits_count,
        }

    return {
        "ok": True,
        "error": None,
        "summary": plan.summaries,
        "warnings": plan.warnings,
        "edits_count": edits_count,
    }
