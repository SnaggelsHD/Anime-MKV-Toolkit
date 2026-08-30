import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.backup_models import BackupChapters, BackupEpisode, BackupLibrary, BackupShow, BackupTrackMetadata
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.scanner import sync_episodes, sync_shows

logger = logging.getLogger("mkv_backup")


def _get_or_create_backup_episode(backup_db: Session, episode: Episode) -> BackupEpisode:
    library_name = episode.show.library.name
    show_name = episode.show.name

    backup_library = backup_db.query(BackupLibrary).filter(BackupLibrary.name == library_name).first()
    if backup_library is None:
        backup_library = BackupLibrary(name=library_name)
        backup_db.add(backup_library)
        backup_db.flush()

    backup_show = (
        backup_db.query(BackupShow)
        .filter(BackupShow.library_id == backup_library.id, BackupShow.name == show_name)
        .first()
    )
    if backup_show is None:
        backup_show = BackupShow(library_id=backup_library.id, name=show_name)
        backup_db.add(backup_show)
        backup_db.flush()

    backup_episode = (
        backup_db.query(BackupEpisode)
        .filter(BackupEpisode.show_id == backup_show.id, BackupEpisode.filename == episode.filename)
        .first()
    )
    if backup_episode is None:
        backup_episode = BackupEpisode(
            show_id=backup_show.id,
            filename=episode.filename,
            season=episode.season,
            episode=episode.episode,
        )
        backup_db.add(backup_episode)
        backup_db.flush()
    else:
        backup_episode.season = episode.season
        backup_episode.episode = episode.episode

    return backup_episode


def backup_episode(scan_db: Session, backup_db: Session, episode: Episode) -> dict:
    chapters_row = scan_db.query(Chapters).filter(Chapters.episode_id == episode.id).first()
    track_row = scan_db.query(TrackMetadata).filter(TrackMetadata.episode_id == episode.id).first()

    if chapters_row is None and track_row is None:
        return {
            "episode_id": episode.id,
            "filename": episode.filename,
            "ok": False,
            "error": "Episode has not been scanned yet",
        }

    backup_ep = _get_or_create_backup_episode(backup_db, episode)
    now = datetime.now(timezone.utc)

    if chapters_row is not None:
        existing = backup_db.query(BackupChapters).filter(BackupChapters.episode_id == backup_ep.id).first()
        if existing is None:
            backup_db.add(BackupChapters(episode_id=backup_ep.id, chapter_xml=chapters_row.chapter_xml, backed_up_at=now))
        else:
            existing.chapter_xml = chapters_row.chapter_xml
            existing.backed_up_at = now

    if track_row is not None:
        existing = backup_db.query(BackupTrackMetadata).filter(BackupTrackMetadata.episode_id == backup_ep.id).first()
        if existing is None:
            backup_db.add(BackupTrackMetadata(episode_id=backup_ep.id, tracks_json=track_row.tracks_json, backed_up_at=now))
        else:
            existing.tracks_json = track_row.tracks_json
            existing.backed_up_at = now

    backup_db.commit()
    logger.info("Backed up %s to backup database", episode.path)
    return {
        "episode_id": episode.id,
        "filename": episode.filename,
        "ok": True,
        "has_chapters": chapters_row is not None,
    }


def backup_show(scan_db: Session, backup_db: Session, show: Show, on_result=None) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        result = backup_episode(scan_db, backup_db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def backup_season(scan_db: Session, backup_db: Session, show: Show, season: str | None, on_result=None) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        if ep.season != season:
            continue
        result = backup_episode(scan_db, backup_db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def backup_library(scan_db: Session, backup_db: Session, library: Library, on_result=None) -> list[dict]:
    shows = sync_shows(scan_db, library)
    results = []
    for show in shows:
        results.extend(backup_show(scan_db, backup_db, show, on_result=on_result))
    return results
