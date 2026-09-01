"""Aggregate statistics (languages, codecs, resolutions, sizes, durations)
computed on demand from the scan database's stored mediainfo reports. Pure
read-only aggregation - never touches any database beyond querying it."""

import json
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models import Episode, Library, Show, TrackMetadata

# MediaInfo reports languages as short ISO 639-1 codes (e.g. "ja", "de"),
# unlike the 3-letter codes mkvmerge/mkv_cleanup.py use - this is a separate
# table for that reason rather than reusing app.mkv_cleanup.LANGUAGE_NAMES.
LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ko": "Korean",
    "zh": "Chinese",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "tr": "Turkish",
    "ar": "Arabic",
    "cs": "Czech",
    "el": "Greek",
    "hu": "Hungarian",
    "ro": "Romanian",
    "th": "Thai",
    "vi": "Vietnamese",
    "und": "Unknown",
}


def _lang_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return LANGUAGE_NAMES.get(code.lower(), code.upper())


def _resolution_bucket(width: Any, height: Any) -> str:
    try:
        h = int(float(height))
    except (TypeError, ValueError):
        return "Unknown"
    if h >= 2000:
        return "2160p (4K)"
    if h >= 1300:
        return "1440p"
    if h >= 1000:
        return "1080p"
    if h >= 700:
        return "720p"
    if h >= 470:
        return "480p"
    return f"{h}p"


GIB = 1024**3
# (inclusive lower bound in bytes, exclusive upper bound, label) - kept as an
# ordered list (not a Counter) so the distribution renders as a real
# ascending-size histogram rather than sorted by count.
SIZE_BUCKETS = [
    (0, 0.5 * GIB, "< 500 MB"),
    (0.5 * GIB, 1 * GIB, "500 MB - 1 GB"),
    (1 * GIB, 2 * GIB, "1 - 2 GB"),
    (2 * GIB, 4 * GIB, "2 - 4 GB"),
    (4 * GIB, 8 * GIB, "4 - 8 GB"),
    (8 * GIB, float("inf"), "8 GB+"),
]


def _size_bucket_label(size_bytes: int) -> str:
    for lower, upper, label in SIZE_BUCKETS:
        if lower <= size_bytes < upper:
            return label
    return SIZE_BUCKETS[-1][2]


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    return int(_to_float(value))


def _counter_list(counter: Counter) -> list[dict]:
    return [{"name": name, "count": count} for name, count in counter.most_common()]


def compute_statistics(scan_db: Session, library_id: int | None = None) -> dict:
    library_q = scan_db.query(Library)
    if library_id is not None:
        library_q = library_q.filter(Library.id == library_id)
    libraries = library_q.order_by(Library.name).all()
    library_ids = [lib.id for lib in libraries]

    shows = scan_db.query(Show).filter(Show.library_id.in_(library_ids)).all() if library_ids else []
    show_ids = [s.id for s in shows]
    show_by_id = {s.id: s for s in shows}
    library_by_id = {lib.id: lib for lib in libraries}

    episodes = scan_db.query(Episode).filter(Episode.show_id.in_(show_ids)).all() if show_ids else []

    scanned_episode_ids = [ep.id for ep in episodes if ep.last_scanned_at is not None]
    track_rows = (
        scan_db.query(TrackMetadata).filter(TrackMetadata.episode_id.in_(scanned_episode_ids)).all()
        if scanned_episode_ids
        else []
    )
    track_by_episode = {row.episode_id: row for row in track_rows}

    audio_languages: Counter = Counter()
    subtitle_languages: Counter = Counter()
    video_codecs: Counter = Counter()
    audio_codecs: Counter = Counter()
    resolutions: Counter = Counter()

    total_size_bytes = 0
    total_duration_seconds = 0.0
    parsed_episode_count = 0

    per_library_size: Counter = Counter()
    per_library_duration: Counter = Counter()
    per_library_episodes: Counter = Counter()
    size_distribution: Counter = Counter()

    all_episode_sizes: list[dict] = []
    show_totals: dict[int, dict] = {}
    season_totals: dict[tuple[int, str], dict] = {}

    for ep in episodes:
        tm = track_by_episode.get(ep.id)
        if tm is None:
            continue
        try:
            parsed = json.loads(tm.tracks_json)
        except (json.JSONDecodeError, TypeError):
            continue
        raw_tracks = parsed.get("media", {}).get("track") if isinstance(parsed, dict) else None
        if not raw_tracks:
            continue
        tracks = raw_tracks if isinstance(raw_tracks, list) else [raw_tracks]

        show = show_by_id.get(ep.show_id)
        library = library_by_id.get(show.library_id) if show else None

        size_bytes = 0
        duration_seconds = 0.0

        for track in tracks:
            track_type = track.get("@type")
            if track_type == "General":
                size_bytes = _to_int(track.get("FileSize"))
                duration_seconds = _to_float(track.get("Duration"))
            elif track_type == "Video":
                video_codecs[track.get("Format") or "Unknown"] += 1
                resolutions[_resolution_bucket(track.get("Width"), track.get("Height"))] += 1
            elif track_type == "Audio":
                audio_codecs[track.get("Format") or "Unknown"] += 1
                audio_languages[_lang_name(track.get("Language"))] += 1
            elif track_type == "Text":
                subtitle_languages[_lang_name(track.get("Language"))] += 1

        parsed_episode_count += 1
        total_size_bytes += size_bytes
        total_duration_seconds += duration_seconds
        size_distribution[_size_bucket_label(size_bytes)] += 1

        if library:
            per_library_size[library.name] += size_bytes
            per_library_duration[library.name] += duration_seconds
            per_library_episodes[library.name] += 1
            all_episode_sizes.append(
                {
                    "filename": ep.filename,
                    "show": show.name,
                    "library": library.name,
                    "size_bytes": size_bytes,
                    "duration_seconds": duration_seconds,
                }
            )

            show_entry = show_totals.setdefault(
                show.id,
                {"show": show.name, "library": library.name, "episode_count": 0, "size_bytes": 0, "duration_seconds": 0},
            )
            show_entry["episode_count"] += 1
            show_entry["size_bytes"] += size_bytes
            show_entry["duration_seconds"] += duration_seconds

            season_label = ep.season or "Unsorted"
            season_key = (show.id, season_label)
            season_entry = season_totals.setdefault(
                season_key,
                {
                    "season": season_label,
                    "show": show.name,
                    "library": library.name,
                    "episode_count": 0,
                    "size_bytes": 0,
                    "duration_seconds": 0,
                },
            )
            season_entry["episode_count"] += 1
            season_entry["size_bytes"] += size_bytes
            season_entry["duration_seconds"] += duration_seconds

    episodes_by_size_asc = sorted(all_episode_sizes, key=lambda e: e["size_bytes"])
    smallest_episodes = episodes_by_size_asc[:5]
    largest_episodes = list(reversed(episodes_by_size_asc[-10:]))

    largest_shows = sorted(show_totals.values(), key=lambda s: s["size_bytes"], reverse=True)[:10]
    largest_seasons = sorted(season_totals.values(), key=lambda s: s["size_bytes"], reverse=True)[:10]

    size_distribution_list = [
        {"name": label, "count": size_distribution.get(label, 0)} for _, _, label in SIZE_BUCKETS
    ]

    by_library = [
        {
            "library": lib.name,
            "episode_count": per_library_episodes.get(lib.name, 0),
            "size_bytes": per_library_size.get(lib.name, 0),
            "duration_seconds": per_library_duration.get(lib.name, 0),
        }
        for lib in libraries
    ]

    return {
        "overview": {
            "library_count": len(libraries),
            "show_count": len(shows),
            "episode_count": len(episodes),
            "scanned_count": len(scanned_episode_ids),
            "missing_count": sum(1 for ep in episodes if ep.missing),
            "parsed_episode_count": parsed_episode_count,
            "total_size_bytes": total_size_bytes,
            "total_duration_seconds": total_duration_seconds,
            "avg_duration_seconds": (total_duration_seconds / parsed_episode_count) if parsed_episode_count else 0,
            "avg_size_bytes": (total_size_bytes / parsed_episode_count) if parsed_episode_count else 0,
        },
        "audio_languages": _counter_list(audio_languages),
        "subtitle_languages": _counter_list(subtitle_languages),
        "video_codecs": _counter_list(video_codecs),
        "audio_codecs": _counter_list(audio_codecs),
        "resolutions": _counter_list(resolutions),
        "size_distribution": size_distribution_list,
        "by_library": by_library,
        "largest_shows": largest_shows,
        "largest_seasons": largest_seasons,
        "largest_episodes": largest_episodes,
        "smallest_episodes": smallest_episodes,
    }
