import logging
import threading

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.backup import backup_episode, backup_library, backup_show
from app.config import DB_PATH, LIBRARIES_ROOT
from app.db import SessionLocal, get_db, init_db
from app.jobs import add_result, create_job, fail_job, finish_job, get_job, list_jobs
from app.maintenance import (
    backup_all,
    clear_database,
    count_all_episodes,
    delete_episode,
    delete_season,
    delete_show,
    restore_all,
)
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.restore import restore_chapters_for_episode, restore_library, restore_show
from app.scanner import sync_episodes, sync_libraries, sync_shows

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mkv_backup")

app = FastAPI(title="MKV Chapter & Media Info Backup")


@app.on_event("startup")
def on_startup():
    logger.info("Starting up: db_path=%s libraries_root=%s", DB_PATH, LIBRARIES_ROOT)
    init_db()


def launch_job(label: str, total: int, work) -> str:
    """Run `work(thread_db, on_result)` in a background thread, tracked as a job.

    `work` gets its own DB session since sessions aren't safe to share across threads.
    """
    job = create_job(label, total)

    def run():
        thread_db = SessionLocal()
        try:
            work(thread_db, lambda result: add_result(job.id, result))
            finish_job(job.id)
        except Exception as exc:
            logger.exception('Job failed: "%s"', label)
            fail_job(job.id, str(exc))
        finally:
            thread_db.close()

    threading.Thread(target=run, daemon=True).start()
    return job.id


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
    result = []
    for lib in libraries:
        shows = sync_shows(db, lib)
        result.append({"id": lib.id, "name": lib.name, "path": lib.path, "show_count": len(shows)})
    return result


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


@app.get("/api/jobs")
def list_jobs_endpoint():
    return [job.to_dict() for job in list_jobs()]


@app.get("/api/jobs/{job_id}")
def get_job_endpoint(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.post("/api/shows/{show_id}/backup")
def backup_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    total = len(sync_episodes(db, show))

    def work(thread_db, on_result):
        backup_show(thread_db, thread_db.get(Show, show_id), on_result=on_result)

    job_id = launch_job(f'Backup "{show.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/libraries/{library_id}/backup")
def backup_library_endpoint(library_id: int, db: Session = Depends(get_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    total = sum(len(sync_episodes(db, show)) for show in shows)

    def work(thread_db, on_result):
        backup_library(thread_db, thread_db.get(Library, library_id), on_result=on_result)

    job_id = launch_job(f'Backup library "{library.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/episodes/{episode_id}/backup")
def backup_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    def work(thread_db, on_result):
        on_result(backup_episode(thread_db, thread_db.get(Episode, episode_id)))

    job_id = launch_job(f"Backup {episode.filename}", 1, work)
    return {"job_id": job_id}


@app.post("/api/episodes/{episode_id}/restore")
def restore_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    def work(thread_db, on_result):
        on_result(restore_chapters_for_episode(thread_db, thread_db.get(Episode, episode_id)))

    job_id = launch_job(f"Restore {episode.filename}", 1, work)
    return {"job_id": job_id}


@app.post("/api/shows/{show_id}/restore")
def restore_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    total = len(sync_episodes(db, show))

    def work(thread_db, on_result):
        restore_show(thread_db, thread_db.get(Show, show_id), on_result=on_result)

    job_id = launch_job(f'Restore "{show.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/libraries/{library_id}/restore")
def restore_library_endpoint(library_id: int, db: Session = Depends(get_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    total = sum(len(sync_episodes(db, show)) for show in shows)

    def work(thread_db, on_result):
        restore_library(thread_db, thread_db.get(Library, library_id), on_result=on_result)

    job_id = launch_job(f'Restore library "{library.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/backup/all")
def backup_all_endpoint(db: Session = Depends(get_db)):
    total = count_all_episodes(db)

    def work(thread_db, on_result):
        backup_all(thread_db, on_result=on_result)

    job_id = launch_job("Backup all libraries", total, work)
    return {"job_id": job_id}


@app.post("/api/restore/all")
def restore_all_endpoint(db: Session = Depends(get_db)):
    total = count_all_episodes(db)

    def work(thread_db, on_result):
        restore_all(thread_db, on_result=on_result)

    job_id = launch_job("Restore all libraries", total, work)
    return {"job_id": job_id}


@app.delete("/api/database")
def clear_database_endpoint(db: Session = Depends(get_db)):
    clear_database(db)
    return {"ok": True}


@app.delete("/api/shows/{show_id}")
def delete_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    delete_show(db, show)
    return {"ok": True}


@app.delete("/api/shows/{show_id}/season")
def delete_season_endpoint(show_id: int, season: str | None = None, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    deleted = delete_season(db, show_id, season)
    return {"ok": True, "deleted": deleted}


@app.delete("/api/episodes/{episode_id}")
def delete_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    delete_episode(db, episode)
    return {"ok": True}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
