#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("mkv_cleanup")

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

CODEC_NAMES = {
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


@dataclass
class PlannedEdit:
    selector: str
    action: str
    value: str | None = None


@dataclass
class FilePlan:
    path: Path
    title: str
    edits: list[PlannedEdit] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_cmd(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def require_tool(name: str) -> None:
    result = run_cmd(["bash", "-lc", f"command -v {name}"])
    if result.returncode != 0:
        logger.error("Missing required tool: %s", name)
        sys.exit(2)


def get_mkvmerge_json(path: Path) -> dict[str, Any]:
    result = run_cmd(["mkvmerge", "-J", str(path)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"mkvmerge failed for {path}")
    return json.loads(result.stdout)


def lang_name(code: str | None) -> str:
    code = code or "und"
    return LANGUAGE_NAMES.get(code, code)


def codec_name(codec: str | None) -> tuple[str, bool]:
    if not codec:
        return "unknown", True
    if codec in CODEC_NAMES:
        return CODEC_NAMES[codec], False
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

def inspect_file(path: Path) -> FilePlan:
    data = get_mkvmerge_json(path)
    plan = FilePlan(path=path, title=path.stem)

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
            codec_display, unknown_codec = codec_name(codec)
            ch_layout = channel_layout(props.get("audio_channels"))
            commentary = is_commentary(props)

            if commentary:
                new_name = f"{lang_name(language)} Commentary {codec_display} {ch_layout}"
            else:
                new_name = f"{lang_name(language)} {codec_display} {ch_layout}"

            if unknown_codec:
                plan.warnings.append(f"unknown audio codec on {path.name}: {codec}")

            plan.edits.append(PlannedEdit(selector, "set", f"name={new_name}"))
            plan.summaries.append(f'{selector} audio -> "{new_name}"')

        elif track_type == "subtitles":
            selector = f"track:{overall_index}"
            suffix = " Forced" if is_forced(props) else ""
            commentary = is_commentary(props)
            
            if commentary:
                new_name = f"{lang_name(language)} Commentary{suffix}"
            else:
                new_name = f"{lang_name(language)}{suffix}"

            plan.edits.append(PlannedEdit(selector, "set", f"name={new_name}"))
            plan.summaries.append(f'{selector} subtitles -> "{new_name}"')

    return plan


def apply_plan(plan: FilePlan, dry_run: bool) -> tuple[str, str, int]:
    if dry_run:
        return "", "", 0

    cmd = ["mkvpropedit", str(plan.path)]
    current_selector = None

    for edit in plan.edits:
        if edit.selector != current_selector:
            cmd.extend(["--edit", edit.selector])
            current_selector = edit.selector
        if edit.action == "set" and edit.value is not None:
            cmd.extend(["--set", edit.value])
        elif edit.action == "delete" and edit.value is not None:
            cmd.extend(["--delete", edit.value])

    result = run_cmd(cmd)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def append_unknown_codec_log(plan: FilePlan) -> None:
    if not plan.warnings:
        return
    log_path = plan.path.parent / "unknown_audio_codecs.txt"
    with log_path.open("a", encoding="utf-8") as fh:
        for warning in plan.warnings:
            fh.write(warning + "\n")


def process_file(path: Path, dry_run: bool) -> None:
    started = time.perf_counter()

    logger.info("[INSPECT] %s", path)

    try:
        plan = inspect_file(path)
    except Exception as exc:
        logger.info("[DONE] %s", path)
        logger.error("  status -> error")
        logger.error("  error -> %s", exc)
        return

    for line in plan.summaries:
        logger.info("  %s", line)

    for warning in plan.warnings:
        logger.warning("  warning -> %s", warning)

    append_unknown_codec_log(plan)

    stdout, stderr, code = apply_plan(plan, dry_run=dry_run)
    elapsed = time.perf_counter() - started
    status = "dry-run" if dry_run else ("edited" if code == 0 else "failed")

    logger.info("[DONE] %s", path)
    logger.info("  status -> %s", status)
    logger.info("  edits -> %d", len(plan.edits))
    logger.info("  warnings -> %d", len(plan.warnings))
    logger.info("  elapsed -> %.2fs", elapsed)

    if stdout:
        clean_stdout = " ".join(stdout.splitlines())
        logger.info("  stdout -> %s", clean_stdout)

    if stderr:
        clean_stderr = " ".join(stderr.splitlines())
        logger.warning("  stderr -> %s", clean_stderr)


def iter_mkv_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.mkv"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        help="Root directory to scan; defaults to the folder this script is in",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect only, do not edit",
    )
    args = parser.parse_args()

    require_tool("mkvmerge")
    require_tool("mkvpropedit")

    script_dir = Path(__file__).resolve().parent
    root = Path(args.path).expanduser().resolve() if args.path else script_dir

    if not root.exists():
        logger.error("Path does not exist: %s", root)
        sys.exit(2)

    if not root.is_dir():
        logger.error("Path is not a directory: %s", root)
        sys.exit(2)

    files = iter_mkv_files(root)

    if not files:
        logger.info("No MKV files found under: %s", root)
        return

    logger.info("Found %d MKV file(s) under %s", len(files), root)

    for path in files:
        process_file(path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()