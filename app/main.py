from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import DB_PATH, LIBRARIES_ROOT
from app.db import get_db, init_db
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.scanner import sync_episodes, sync_libraries, sync_shows

app = FastAPI(title="MKV Chapter & Media Info Backup")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "db_path": DB_PATH,
        "libraries_root": LIBRARIES_ROOT,
    }


@app.get("/api/libraries")
def list_libraries(db: Session = Depends(get_db)):
    libraries = sync_libraries(db, LIBRARIES_ROOT)
    return [{"id": lib.id, "name": lib.name, "path": lib.path} for lib in libraries]


@app.get("/api/libraries/{library_id}/shows")
def list_shows(library_id: int, db: Session = Depends(get_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    result = []
    for show in shows:
        episodes = sync_episodes(db, show)
        result.append(
            {
                "id": show.id,
                "name": show.name,
                "path": show.path,
                "episode_count": len(episodes),
            }
        )
    return result


@app.get("/api/shows/{show_id}/episodes")
def list_episodes(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    episodes = sync_episodes(db, show)
    result = []
    for ep in episodes:
        has_chapters = db.query(Chapters).filter(Chapters.episode_id == ep.id).first() is not None
        has_track_metadata = (
            db.query(TrackMetadata).filter(TrackMetadata.episode_id == ep.id).first() is not None
        )
        result.append(
            {
                "id": ep.id,
                "filename": ep.filename,
                "path": ep.path,
                "season": ep.season,
                "episode": ep.episode,
                "has_chapters": has_chapters,
                "has_track_metadata": has_track_metadata,
            }
        )
    return result


@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: int, db: Session = Depends(get_db)):
    ep = db.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    chapters = db.query(Chapters).filter(Chapters.episode_id == ep.id).first()
    track_metadata = db.query(TrackMetadata).filter(TrackMetadata.episode_id == ep.id).first()
    return {
        "id": ep.id,
        "filename": ep.filename,
        "path": ep.path,
        "season": ep.season,
        "episode": ep.episode,
        "show_id": ep.show_id,
        "chapters": chapters.chapter_xml if chapters else None,
        "track_metadata": track_metadata.tracks_json if track_metadata else None,
    }


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
