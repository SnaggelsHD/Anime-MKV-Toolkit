import logging

from sqlalchemy.orm import Session

from app.backup import backup_library
from app.config import LIBRARIES_ROOT
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.restore import restore_library
from app.scanner import sync_episodes, sync_libraries, sync_shows

logger = logging.getLogger("mkv_backup")


def backup_all(db: Session, on_result=None) -> list[dict]:
    libraries = sync_libraries(db, LIBRARIES_ROOT)
    results = []
    for library in libraries:
        results.extend(backup_library(db, library, on_result=on_result))
    return results


def restore_all(db: Session, on_result=None) -> list[dict]:
    libraries = sync_libraries(db, LIBRARIES_ROOT)
    results = []
    for library in libraries:
        results.extend(restore_library(db, library, on_result=on_result))
    return results


def count_all_episodes(db: Session) -> int:
    total = 0
    for library in sync_libraries(db, LIBRARIES_ROOT):
        for show in sync_shows(db, library):
            total += len(sync_episodes(db, show))
    return total


def clear_database(db: Session) -> None:
    db.query(Chapters).delete()
    db.query(TrackMetadata).delete()
    db.query(Episode).delete()
    db.query(Show).delete()
    db.query(Library).delete()
    db.commit()
    logger.warning("Cleared entire database")


def delete_show(db: Session, show: Show) -> None:
    logger.info("Clearing show from database: %s", show.name)
    db.delete(show)
    db.commit()


def delete_season(db: Session, show_id: int, season: str | None) -> int:
    episodes = db.query(Episode).filter(Episode.show_id == show_id, Episode.season == season).all()
    for ep in episodes:
        db.delete(ep)
    db.commit()
    logger.info("Cleared season %r for show_id=%s (%d episode(s))", season, show_id, len(episodes))
    return len(episodes)


def delete_episode(db: Session, episode: Episode) -> None:
    logger.info("Clearing episode from database: %s", episode.filename)
    db.delete(episode)
    db.commit()
