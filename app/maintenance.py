import logging
import os
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.backup import backup_library
from app.backup_models import BackupChapters, BackupEpisode, BackupLibrary, BackupShow, BackupTrackMetadata
from app.config import BACKUP_DB_PATH, LIBRARIES_ROOT
from app.models import Episode, Library, Show
from app.restore import restore_library
from app.scan import scan_library
from app.scanner import sync_episodes, sync_libraries, sync_shows

logger = logging.getLogger("mkv_backup")


def scan_all(scan_db: Session, on_result=None) -> list[dict]:
    libraries = sync_libraries(scan_db, LIBRARIES_ROOT)
    results = []
    for library in libraries:
        results.extend(scan_library(scan_db, library, on_result=on_result))
    return results


def backup_all(scan_db: Session, backup_db: Session, on_result=None) -> list[dict]:
    libraries = sync_libraries(scan_db, LIBRARIES_ROOT)
    results = []
    for library in libraries:
        results.extend(backup_library(scan_db, backup_db, library, on_result=on_result))
    return results


def restore_all(scan_db: Session, backup_db: Session, on_result=None) -> list[dict]:
    libraries = sync_libraries(scan_db, LIBRARIES_ROOT)
    results = []
    for library in libraries:
        results.extend(restore_library(scan_db, backup_db, library, on_result=on_result))
    return results


def count_all_episodes(scan_db: Session) -> int:
    total = 0
    for library in sync_libraries(scan_db, LIBRARIES_ROOT):
        for show in sync_shows(scan_db, library):
            total += len(sync_episodes(scan_db, show))
    return total


def clear_database(backup_db: Session) -> None:
    backup_db.query(BackupChapters).delete()
    backup_db.query(BackupTrackMetadata).delete()
    backup_db.query(BackupEpisode).delete()
    backup_db.query(BackupShow).delete()
    backup_db.query(BackupLibrary).delete()
    backup_db.commit()
    logger.warning("Cleared entire backup database")


def purge_library(db: Session, backup_db: Session, library: Library) -> None:
    """Remove a library and all its shows/episodes from both databases."""
    backup_lib = backup_db.query(BackupLibrary).filter(BackupLibrary.name == library.name).first()
    if backup_lib is not None:
        backup_db.delete(backup_lib)
        backup_db.commit()
    db.delete(library)
    db.commit()
    logger.info("Purged library from scan and backup databases: %s", library.name)


def purge_show(db: Session, backup_db: Session, show: Show) -> None:
    """Remove this show from both the scan and backup databases entirely."""
    delete_show(backup_db, show)
    db.delete(show)
    db.commit()
    logger.info("Purged show from scan and backup databases: %s", show.name)


def delete_show(backup_db: Session, show: Show) -> None:
    """Remove this show's backup data. The scan database entry is untouched."""
    backup_show = (
        backup_db.query(BackupShow)
        .join(BackupLibrary, BackupShow.library_id == BackupLibrary.id)
        .filter(BackupLibrary.name == show.library.name, BackupShow.name == show.name)
        .first()
    )
    if backup_show is None:
        return
    logger.info("Clearing show from backup database: %s", show.name)
    backup_db.delete(backup_show)
    backup_db.commit()


def delete_season(backup_db: Session, show: Show, season: str | None) -> int:
    """Remove backup data for one season of a show. The scan database is untouched."""
    backup_show = (
        backup_db.query(BackupShow)
        .join(BackupLibrary, BackupShow.library_id == BackupLibrary.id)
        .filter(BackupLibrary.name == show.library.name, BackupShow.name == show.name)
        .first()
    )
    if backup_show is None:
        return 0
    episodes = (
        backup_db.query(BackupEpisode)
        .filter(BackupEpisode.show_id == backup_show.id, BackupEpisode.season == season)
        .all()
    )
    for ep in episodes:
        backup_db.delete(ep)
    backup_db.commit()
    logger.info("Cleared season %r of backup show %s (%d episode(s))", season, show.name, len(episodes))
    return len(episodes)


def delete_episode(backup_db: Session, episode: Episode) -> None:
    """Remove this episode's backup data. The scan database entry is untouched."""
    backup_episode = (
        backup_db.query(BackupEpisode)
        .join(BackupShow, BackupEpisode.show_id == BackupShow.id)
        .join(BackupLibrary, BackupShow.library_id == BackupLibrary.id)
        .filter(
            BackupLibrary.name == episode.show.library.name,
            BackupShow.name == episode.show.name,
            BackupEpisode.filename == episode.filename,
        )
        .first()
    )
    if backup_episode is None:
        return
    logger.info("Clearing episode from backup database: %s", episode.filename)
    backup_db.delete(backup_episode)
    backup_db.commit()


def export_backup_database() -> str:
    """Write a consistent snapshot of the backup database to a temp file and
    return its path. Caller is responsible for deleting it afterwards."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(tmp_path)

    engine = create_engine(f"sqlite:///{BACKUP_DB_PATH}")
    with engine.connect() as conn:
        conn.execute(text("VACUUM INTO :path"), {"path": tmp_path})
    engine.dispose()
    return tmp_path
