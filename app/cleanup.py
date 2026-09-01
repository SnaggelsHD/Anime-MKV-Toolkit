import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.cleanup_db import STEP_TOGGLE_COLUMNS
from app.cleanup_models import CleanupCodecMapping, CleanupEpisode, CleanupLibrary, CleanupResult, CleanupShow, CleanupSettings
from app.mkv_cleanup import DEFAULT_AUDIO_PRIORITY, clean_file
from app.models import Episode, Library, Show
from app.scan import scan_episode
from app.scanner import sync_episodes, sync_shows

logger = logging.getLogger("mkv_backup")


def _load_cleanup_config(cleanup_db: Session) -> tuple[dict[str, str], str, str, dict[str, bool], list[str]]:
    codec_map = {row.codec_key: row.display_name for row in cleanup_db.query(CleanupCodecMapping).all()}
    settings = cleanup_db.query(CleanupSettings).first()
    forced_suffix = settings.forced_suffix if settings else "Forced"
    commentary_suffix = settings.commentary_suffix if settings else "Commentary"
    steps = {name: getattr(settings, name) for name in STEP_TOGGLE_COLUMNS} if settings else {}
    audio_priority = json.loads(settings.audio_priority_json) if settings else list(DEFAULT_AUDIO_PRIORITY)
    return codec_map, forced_suffix, commentary_suffix, steps, audio_priority


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


def cleanup_episode(scan_db: Session, cleanup_db: Session, episode: Episode, dry_run: bool = False) -> dict:
    if episode.show.locked:
        error = "Show is locked (tvshow.nfo tmm_locked=true) - cleanup disabled"
        result_dict = {"episode_id": episode.id, "filename": episode.filename, "ok": False, "error": error}
        if dry_run:
            result_dict.update({"summary": [], "warnings": [], "dry_run": True})
        return result_dict

    codec_map, forced_suffix, commentary_suffix, steps, audio_priority = _load_cleanup_config(cleanup_db)
    result = clean_file(
        episode.path,
        codec_map,
        forced_suffix,
        commentary_suffix,
        dry_run=dry_run,
        steps=steps,
        audio_priority=audio_priority,
    )

    if dry_run:
        # Never touches cleanup.db or triggers a rescan - nothing on disk changed.
        return {
            "episode_id": episode.id,
            "filename": episode.filename,
            "ok": result["ok"],
            "error": result["error"],
            "summary": result["summary"],
            "warnings": result["warnings"],
            "dry_run": True,
        }

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
        # Cleanup rewrites track names/languages/flags, which the scan database's
        # stored mediainfo snapshot would otherwise keep showing as stale until an
        # explicit rescan. Re-scan immediately so the UI reflects the change live.
        try:
            scan_episode(scan_db, episode)
        except Exception:
            logger.exception("Post-cleanup rescan failed for %s", episode.path)
    else:
        logger.error("Cleanup failed for %s: %s", episode.path, result["error"])

    return {
        "episode_id": episode.id,
        "filename": episode.filename,
        "ok": result["ok"],
        "error": result["error"],
    }


def cleanup_show(scan_db: Session, cleanup_db: Session, show: Show, on_result=None, dry_run: bool = False) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        result = cleanup_episode(scan_db, cleanup_db, ep, dry_run=dry_run)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def cleanup_season(
    scan_db: Session, cleanup_db: Session, show: Show, season: str | None, on_result=None, dry_run: bool = False
) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        if ep.season != season:
            continue
        result = cleanup_episode(scan_db, cleanup_db, ep, dry_run=dry_run)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def cleanup_library(scan_db: Session, cleanup_db: Session, library: Library, on_result=None, dry_run: bool = False) -> list[dict]:
    shows = sync_shows(scan_db, library)
    results = []
    for show in shows:
        results.extend(cleanup_show(scan_db, cleanup_db, show, on_result=on_result, dry_run=dry_run))
    return results
