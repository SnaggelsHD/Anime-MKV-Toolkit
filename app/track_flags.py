"""Manual per-track default/forced flag editing, separate from the broader
metadata cleanup feature: lets the UI toggle just those two flags on an
already-scanned episode (or propagate the same toggles across a season) via
mkvpropedit, without touching titles, tags, or track names."""

import json
import logging

from sqlalchemy.orm import Session

from app.mkv_cleanup import set_track_flags
from app.models import Episode, Show, TrackMetadata
from app.scan import scan_episode
from app.scanner import sync_episodes

logger = logging.getLogger("mkv_backup")

TRACK_TYPES = ("Video", "Audio", "Text")


def _av_text_tracks(tracks_json: str) -> list[dict]:
    parsed = json.loads(tracks_json)
    raw = parsed.get("media", {}).get("track") if isinstance(parsed, dict) else None
    if not raw:
        return []
    raw = raw if isinstance(raw, list) else [raw]
    return [t for t in raw if t.get("@type") in TRACK_TYPES]


def track_layout_signature(tracks_json: str) -> list[tuple[str, str]]:
    """A (type, language) fingerprint per track, in file order - used to
    decide whether two episodes' tracks are "the same" for a season-wide
    flag apply. Flags aren't part of the signature: applying the same flags
    to matching layouts is exactly the point."""
    return [(t.get("@type"), t.get("Language") or "") for t in _av_text_tracks(tracks_json)]


def apply_episode_track_flags(scan_db: Session, episode: Episode, flags: list[dict]) -> dict:
    result = {"episode_id": episode.id, "filename": episode.filename}
    if episode.show.locked:
        return {
            **result,
            "ok": False,
            "error": "Show is locked (tvshow.nfo tmm_locked=true) - editing track flags disabled",
        }

    outcome = set_track_flags(episode.path, flags)
    if not outcome["ok"]:
        return {**result, "ok": False, "error": outcome["error"]}

    try:
        scan_episode(scan_db, episode)
    except Exception:
        logger.exception("Post-edit rescan failed for %s", episode.path)

    return {**result, "ok": True, "error": None}


def apply_season_track_flags(
    scan_db: Session,
    show: Show,
    season: str | None,
    flags: list[dict],
    reference_signature: list[tuple[str, str]],
    on_result=None,
) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        if ep.season != season:
            continue
        tm = scan_db.query(TrackMetadata).filter(TrackMetadata.episode_id == ep.id).first()
        if tm is None:
            result = {"episode_id": ep.id, "filename": ep.filename, "ok": False, "error": "Not scanned yet - skipped"}
        elif track_layout_signature(tm.tracks_json) != reference_signature:
            result = {
                "episode_id": ep.id,
                "filename": ep.filename,
                "ok": False,
                "error": "Track layout differs from the edited episode - skipped",
            }
        else:
            result = apply_episode_track_flags(scan_db, ep, flags)
        results.append(result)
        if on_result:
            on_result(result)
    return results
