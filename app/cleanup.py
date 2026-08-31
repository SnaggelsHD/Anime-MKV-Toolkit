import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.cleanup_models import CleanupEpisode, CleanupLibrary, CleanupResult, CleanupShow
from app.mkv_cleanup import clean_file
from app.models import Episode, Show
from app.scanner import sync_episodes

logger = logging.getLogger("mkv_backup")


def _get_or_create_cleanup_episode(cleanup_db: Session, episode: Episode) -> CleanupEpisode:
    library_name = episode.show.library.name
    show_name = episode.show.name

    cleanup_library = cleanup_db.query(CleanupLibrary).filter(CleanupLibrary.name == library_name).first()
    if cleanup_library is None:
        cleanup_library = CleanupLibrary(name=library_name)
        cleanup_db.add(cleanup_library)
        cleanup_db.flush()

    cleanup_show = (
        cleanup_db.query(CleanupShow)
        .filter(CleanupShow.library_id == cleanup_library.id, CleanupShow.name == show_name)
        .first()
    )
    if cleanup_show is None:
        cleanup_show = CleanupShow(library_id=cleanup_library.id, name=show_name)
        cleanup_db.add(cleanup_show)
        cleanup_db.flush()

    cleanup_episode = (
        cleanup_db.query(CleanupEpisode)
        .filter(CleanupEpisode.show_id == cleanup_show.id, CleanupEpisode.filename == episode.filename)
        .first()
    )
    if cleanup_episode is None:
        cleanup_episode = CleanupEpisode(
            show_id=cleanup_show.id,
            filename=episode.filename,
            season=episode.season,
            episode=episode.episode,
        )
        cleanup_db.add(cleanup_episode)
        cleanup_db.flush()
    else:
        cleanup_episode.season = episode.season
        cleanup_episode.episode = episode.episode

    return cleanup_episode


def cleanup_episode(cleanup_db: Session, episode: Episode) -> dict:
    result = clean_file(episode.path)

    cleanup_ep = _get_or_create_cleanup_episode(cleanup_db, episode)
    existing = cleanup_db.query(CleanupResult).filter(CleanupResult.episode_id == cleanup_ep.id).first()
    now = datetime.now(timezone.utc)
    if existing is None:
        existing = CleanupResult(episode_id=cleanup_ep.id)
        cleanup_db.add(existing)
    existing.cleaned_at = now
    existing.ok = result["ok"]
    existing.summary_json = json.dumps(result["summary"])
    existing.warnings_json = json.dumps(result["warnings"])
    existing.error = result["error"]
    cleanup_db.commit()

    if result["ok"]:
        logger.info("Cleaned up %s (%d edits)", episode.path, result["edits_count"])
    else:
        logger.error("Cleanup failed for %s: %s", episode.path, result["error"])

    return {
        "episode_id": episode.id,
        "filename": episode.filename,
        "ok": result["ok"],
        "error": result["error"],
    }


def cleanup_show(scan_db: Session, cleanup_db: Session, show: Show, on_result=None) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        result = cleanup_episode(cleanup_db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def cleanup_season(scan_db: Session, cleanup_db: Session, show: Show, season: str | None, on_result=None) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        if ep.season != season:
            continue
        result = cleanup_episode(cleanup_db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results
