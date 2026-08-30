import os

from sqlalchemy.orm import Session

from app.mkvtools import MkvToolError, extract_chapters, extract_track_metadata
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.scanner import sync_episodes, sync_shows


def backup_episode(db: Session, episode: Episode) -> dict:
    if not os.path.isfile(episode.path):
        return {"episode_id": episode.id, "filename": episode.filename, "ok": False, "error": "File not found on disk"}

    try:
        chapter_xml = extract_chapters(episode.path)
        tracks_json = extract_track_metadata(episode.path)
    except MkvToolError as exc:
        return {"episode_id": episode.id, "filename": episode.filename, "ok": False, "error": str(exc)}

    if chapter_xml is not None:
        chapters_row = db.query(Chapters).filter(Chapters.episode_id == episode.id).first()
        if chapters_row is None:
            db.add(Chapters(episode_id=episode.id, chapter_xml=chapter_xml))
        else:
            chapters_row.chapter_xml = chapter_xml

    track_row = db.query(TrackMetadata).filter(TrackMetadata.episode_id == episode.id).first()
    if track_row is None:
        db.add(TrackMetadata(episode_id=episode.id, tracks_json=tracks_json))
    else:
        track_row.tracks_json = tracks_json

    db.commit()
    return {
        "episode_id": episode.id,
        "filename": episode.filename,
        "ok": True,
        "has_chapters": chapter_xml is not None,
    }


def backup_show(db: Session, show: Show) -> list[dict]:
    episodes = sync_episodes(db, show)
    return [backup_episode(db, ep) for ep in episodes]


def backup_library(db: Session, library: Library) -> list[dict]:
    shows = sync_shows(db, library)
    results = []
    for show in shows:
        results.extend(backup_show(db, show))
    return results
