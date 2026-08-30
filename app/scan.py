import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.mkvtools import MkvToolError, extract_chapters, extract_track_metadata
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.scanner import sync_episodes, sync_shows

logger = logging.getLogger("mkv_backup")


def scan_episode(db: Session, episode: Episode) -> dict:
    if not os.path.isfile(episode.path):
        episode.missing = True
        db.commit()
        logger.warning("Scan skipped, file not found: %s", episode.path)
        return {"episode_id": episode.id, "filename": episode.filename, "ok": False, "error": "File not found on disk"}

    try:
        chapter_xml = extract_chapters(episode.path)
        tracks_json = extract_track_metadata(episode.path)
    except MkvToolError as exc:
        logger.error("Scan failed for %s: %s", episode.path, exc)
        return {"episode_id": episode.id, "filename": episode.filename, "ok": False, "error": str(exc)}

    chapters_row = db.query(Chapters).filter(Chapters.episode_id == episode.id).first()
    if chapter_xml is not None:
        if chapters_row is None:
            db.add(Chapters(episode_id=episode.id, chapter_xml=chapter_xml))
        else:
            chapters_row.chapter_xml = chapter_xml
    elif chapters_row is not None:
        # The file no longer has chapters (e.g. re-downloaded without them);
        # drop the stale scan data instead of leaving the old chapters behind.
        db.delete(chapters_row)

    track_row = db.query(TrackMetadata).filter(TrackMetadata.episode_id == episode.id).first()
    if track_row is None:
        db.add(TrackMetadata(episode_id=episode.id, tracks_json=tracks_json))
    else:
        track_row.tracks_json = tracks_json

    episode.missing = False
    episode.last_scanned_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Scanned %s (has_chapters=%s)", episode.path, chapter_xml is not None)
    return {
        "episode_id": episode.id,
        "filename": episode.filename,
        "ok": True,
        "has_chapters": chapter_xml is not None,
    }


def scan_show(db: Session, show: Show, on_result=None) -> list[dict]:
    episodes = sync_episodes(db, show)
    results = []
    for ep in episodes:
        result = scan_episode(db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def scan_season(db: Session, show: Show, season: str | None, on_result=None) -> list[dict]:
    episodes = sync_episodes(db, show)
    results = []
    for ep in episodes:
        if ep.season != season:
            continue
        result = scan_episode(db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def scan_library(db: Session, library: Library, on_result=None) -> list[dict]:
    shows = sync_shows(db, library)
    results = []
    for show in shows:
        results.extend(scan_show(db, show, on_result=on_result))
    return results
