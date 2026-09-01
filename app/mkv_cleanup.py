"""Embedded rules from the standalone mkv_cleanup.py script: normalizes track
languages/names and container metadata for anime MKV rips. Logic (including
the track:1 forced-to-Japanese quirk) is kept identical to that script;
this module returns structured results instead of logging to the console.
"""

import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
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

# The MediaInfo fields Encoded_Library / Encoded_Library_Name /
# Encoded_Library_Version / Encoded_Library_Settings are all derived from a
# single Matroska SimpleTag - conventionally named ENCODER (e.g. embedded by
# ffmpeg/libavformat) - that lives in the file's *global* tags, separate from
# the writing-application/muxing-application segment-info fields mkvpropedit
# edits directly. Some tools may write it under one of the other names below.
ENCODER_TAG_NAMES = {
    "ENCODER",
    "ENCODED_LIBRARY",
    "ENCODED_LIBRARY_NAME",
    "ENCODED_LIBRARY_VERSION",
    "ENCODED_LIBRARY_SETTINGS",
}

# <Targets> child elements that mark a <Tag> block as scoped to something
# other than the whole file (a specific track/edition/chapter/attachment).
# A <Tag> with none of these is a *global* tag block.
TARGET_UID_TAGS = {"TrackUID", "EditionUID", "ChapterUID", "AttachmentUID"}


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
    has_tags_edit: bool = False
    # None + has_tags_edit=True means "delete the file's global tags entirely";
    # a string means "replace the global tags with this XML".
    tags_xml: str | None = None


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


def _extract_tags_root(path: str) -> ET.Element | None:
    """Run mkvextract to pull the file's Matroska tags as XML and parse it.
    Returns the <Tags> root element, or None if the file has no tags, or
    mkvextract/parsing fails - treated as "nothing to clean" rather than an
    error, since not every file has tags."""
    fd, tmp_path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        proc = subprocess.run(
            ["mkvextract", path, "tags", tmp_path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        try:
            return ET.parse(tmp_path).getroot()
        except ET.ParseError:
            return None
    except FileNotFoundError:
        return None
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _is_global_tag(tag_el: ET.Element) -> bool:
    targets = tag_el.find("Targets")
    if targets is None:
        return True
    return not any(child.tag in TARGET_UID_TAGS for child in targets)


def plan_encoder_tag_removal(path: str) -> tuple[list[str], str | None, bool]:
    """Inspect the file's global Matroska tags for encoder-library entries.
    Returns (summary_lines, replacement_xml, changed). `changed` is False if
    there was nothing to remove (skip the tags edit entirely).
    `replacement_xml` is the XML to hand to mkvpropedit's --tags global:<file>,
    or None if the global tags should be deleted outright (--tags global: with
    no file) because nothing but encoder tags was left in them. Per-track tag
    blocks (mkvmerge's BPS/DURATION/... statistics) are never touched."""
    root = _extract_tags_root(path)
    if root is None:
        return [], None, False

    removed_names: list[str] = []
    surviving_global_tags: list[ET.Element] = []
    changed = False

    for tag_el in root.findall("Tag"):
        if not _is_global_tag(tag_el):
            continue
        kept_simples = []
        for simple in tag_el.findall("Simple"):
            name_el = simple.find("Name")
            name = (name_el.text or "").strip().upper() if name_el is not None else ""
            if name in ENCODER_TAG_NAMES:
                removed_names.append(name)
                changed = True
            else:
                kept_simples.append(simple)
        if kept_simples:
            new_tag = ET.Element("Tag")
            # Preserve the original Targets content (e.g. TargetTypeValue)
            # rather than dropping it - only the matched Simple entries and
            # the file's per-track tag blocks are meant to change.
            original_targets = tag_el.find("Targets")
            new_tag.append(original_targets if original_targets is not None else ET.Element("Targets"))
            for simple in kept_simples:
                new_tag.append(simple)
            surviving_global_tags.append(new_tag)

    if not changed:
        return [], None, False

    summary = [f"tags -> cleared {name}" for name in sorted(set(removed_names))]

    if not surviving_global_tags:
        return summary, None, True

    new_root = ET.Element("Tags")
    for tag_el in surviving_global_tags:
        new_root.append(tag_el)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(new_root, encoding="unicode")
    return summary, xml_str, True


DEFAULT_STEPS: dict[str, bool] = {
    "set_title": True,
    "clear_date": True,
    "clear_writing_app": True,
    "clear_muxing_app": True,
    "clear_encoder_tags": True,
    "force_first_track_japanese": True,
    "set_video_default": True,
    "rename_audio_tracks": True,
    "rename_subtitle_tracks": True,
}


def inspect_file(
    path: str,
    codec_map: dict[str, str],
    forced_suffix: str = "Forced",
    commentary_suffix: str = "Commentary",
    steps: dict[str, bool] | None = None,
) -> FilePlan:
    steps = {**DEFAULT_STEPS, **(steps or {})}
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
    if steps["clear_encoder_tags"]:
        tag_summary, tags_xml, tags_changed = plan_encoder_tag_removal(path)
        if tags_changed:
            plan.has_tags_edit = True
            plan.tags_xml = tags_xml
            plan.summaries.extend(tag_summary)

    tracks = data.get("tracks", [])

    if steps["force_first_track_japanese"] and tracks:
        plan.edits.append(PlannedEdit("track:1", "set", "language=jpn"))
        plan.edits.append(PlannedEdit("track:1", "delete", "name"))
        plan.summaries.append("track:1 language -> jpn")
        plan.summaries.append("track:1 name -> <deleted>")

    for overall_index, track in enumerate(tracks, start=1):
        track_type = track.get("type")
        props = track.get("properties", {})
        language = props.get("language") or "und"

        if track_type == "video" and steps["set_video_default"]:
            selector = f"track:{overall_index}"
            plan.edits.append(PlannedEdit(selector, "set", "flag-default=1"))
            plan.summaries.append(f"{selector} video -> flag-default=1")

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

    tags_tmp_path = None
    if plan.has_tags_edit:
        if plan.tags_xml is None:
            cmd.extend(["--tags", "global:"])
        else:
            fd, tags_tmp_path = tempfile.mkstemp(suffix=".xml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(plan.tags_xml)
            cmd.extend(["--tags", f"global:{tags_tmp_path}"])

    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=TIMEOUT)
    except FileNotFoundError as exc:
        raise MkvCleanupError("mkvpropedit is not installed") from exc
    finally:
        if tags_tmp_path is not None:
            try:
                os.remove(tags_tmp_path)
            except OSError:
                pass
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def clean_file(
    path: str,
    codec_map: dict[str, str],
    forced_suffix: str = "Forced",
    commentary_suffix: str = "Commentary",
    dry_run: bool = False,
    steps: dict[str, bool] | None = None,
) -> dict:
    """Inspect and clean up one MKV file's metadata in place. Returns a
    structured result: {ok, summary, warnings, error, edits_count}.

    With dry_run=True, only inspect_file() (read-only, via mkvmerge -J) runs -
    apply_plan() (which invokes mkvpropedit) is skipped, so the file on disk
    is never touched. `steps` turns individual cleanup steps on/off (see
    DEFAULT_STEPS); a step that's off contributes no edits and no summary
    lines at all."""
    if not os.path.isfile(path):
        return {"ok": False, "error": "File not found on disk", "summary": [], "warnings": [], "edits_count": 0}

    try:
        plan = inspect_file(path, codec_map, forced_suffix, commentary_suffix, steps=steps)
    except MkvCleanupError as exc:
        return {"ok": False, "error": str(exc), "summary": [], "warnings": [], "edits_count": 0}

    edits_count = len(plan.edits) + (1 if plan.has_tags_edit else 0)

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
