import json
import logging
import os
import threading

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware

from app.backup import backup_episode, backup_library, backup_season, backup_show
from app.backup_db import BackupSessionLocal, get_backup_db, init_backup_db
from app.backup_models import BackupEpisode, BackupLibrary, BackupShow
from app.cleanup import cleanup_episode, cleanup_season, cleanup_show
from app.cleanup_db import CleanupSessionLocal, get_cleanup_db, init_cleanup_db
from app.cleanup_models import CleanupCodecMapping, CleanupEpisode, CleanupLibrary, CleanupSettings, CleanupShow
from app.config import BACKUP_DB_PATH, CLEANUP_DB_PATH, DB_PATH, LIBRARIES_ROOT
from app.db import SessionLocal, get_db, init_db
from app.jobs import add_result, create_job, fail_job, finish_job, get_job, list_jobs
from app.maintenance import (
    backup_all,
    clear_database,
    count_all_episodes,
    delete_episode,
    delete_season,
    delete_show,
    export_backup_database,
    restore_all,
    scan_all,
)
from app.models import Chapters, Episode, Library, Show, TrackMetadata
from app.restore import restore_chapters_for_episode, restore_library, restore_show
from app.scan import scan_episode, scan_library, scan_season, scan_show
from app.scanner import sync_episodes, sync_libraries, sync_shows

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mkv_backup")

app = FastAPI(title="MKV Chapter & Media Info Backup")


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Force the browser to always revalidate static assets (index.html/app.js/style.css)
    with the server instead of relying on heuristic caching, so a rebuilt image's updated
    frontend is picked up on the next reload instead of silently serving a stale copy."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.add_middleware(NoCacheStaticMiddleware)


@app.on_event("startup")
def on_startup():
    logger.info(
        "Starting up: db_path=%s backup_db_path=%s cleanup_db_path=%s libraries_root=%s",
        DB_PATH,
        BACKUP_DB_PATH,
        CLEANUP_DB_PATH,
        LIBRARIES_ROOT,
    )
    init_db()
    init_backup_db()
    init_cleanup_db()


def launch_job(label: str, total: int, work) -> str:
    """Run `work(scan_db, backup_db, on_result)` in a background thread, tracked as a job.

    Each job gets its own DB sessions since sessions aren't safe to share across threads.
    """
    job = create_job(label, total)

    def run():
        scan_db = SessionLocal()
        backup_db = BackupSessionLocal()
        try:
            work(scan_db, backup_db, lambda result: add_result(job.id, result))
            finish_job(job.id)
        except Exception as exc:
            logger.exception('Job failed: "%s"', label)
            fail_job(job.id, str(exc))
        finally:
            scan_db.close()
            backup_db.close()

    threading.Thread(target=run, daemon=True).start()
    return job.id


def launch_cleanup_job(label: str, total: int, work) -> str:
    """Like launch_job, but for cleanup jobs: gives `work(scan_db, cleanup_db, on_result)`
    its own scan + cleanup DB sessions. Kept separate from launch_job so the existing
    scan/backup/restore endpoints above are untouched by this feature."""
    job = create_job(label, total)

    def run():
        scan_db = SessionLocal()
        cleanup_db = CleanupSessionLocal()
        try:
            work(scan_db, cleanup_db, lambda result: add_result(job.id, result))
            finish_job(job.id)
        except Exception as exc:
            logger.exception('Job failed: "%s"', label)
            fail_job(job.id, str(exc))
        finally:
            scan_db.close()
            cleanup_db.close()

    threading.Thread(target=run, daemon=True).start()
    return job.id


def _backup_episode_row(backup_db: Session, library_name: str, show_name: str, filename: str) -> BackupEpisode | None:
    return (
        backup_db.query(BackupEpisode)
        .join(BackupShow, BackupEpisode.show_id == BackupShow.id)
        .join(BackupLibrary, BackupShow.library_id == BackupLibrary.id)
        .filter(
            BackupLibrary.name == library_name,
            BackupShow.name == show_name,
            BackupEpisode.filename == filename,
        )
        .first()
    )


def _episode_backup_info(backup_db: Session, episode: Episode) -> dict:
    backup_ep = _backup_episode_row(backup_db, episode.show.library.name, episode.show.name, episode.filename)
    if backup_ep is None:
        return {"has_backup": False, "backed_up_at": None}
    backed_up_at = None
    if backup_ep.chapters is not None:
        backed_up_at = backup_ep.chapters.backed_up_at
    elif backup_ep.track_metadata is not None:
        backed_up_at = backup_ep.track_metadata.backed_up_at
    return {"has_backup": True, "backed_up_at": backed_up_at}


def _episode_backup_content(backup_db: Session, episode: Episode) -> dict:
    """Like _episode_backup_info, but also includes the actual backed-up
    chapter XML / track metadata JSON (only used by the single-episode detail
    endpoint, to avoid fetching this heavier data for every row in a list)."""
    backup_ep = _backup_episode_row(backup_db, episode.show.library.name, episode.show.name, episode.filename)
    if backup_ep is None:
        return {"has_backup": False, "backed_up_at": None, "chapters": None, "track_metadata": None}
    backed_up_at = None
    if backup_ep.chapters is not None:
        backed_up_at = backup_ep.chapters.backed_up_at
    elif backup_ep.track_metadata is not None:
        backed_up_at = backup_ep.track_metadata.backed_up_at
    return {
        "has_backup": True,
        "backed_up_at": backed_up_at,
        "chapters": backup_ep.chapters.chapter_xml if backup_ep.chapters else None,
        "track_metadata": backup_ep.track_metadata.tracks_json if backup_ep.track_metadata else None,
    }


def _cleanup_episode_row(cleanup_db: Session, library_name: str, show_name: str, filename: str) -> CleanupEpisode | None:
    return (
        cleanup_db.query(CleanupEpisode)
        .join(CleanupShow, CleanupEpisode.show_id == CleanupShow.id)
        .join(CleanupLibrary, CleanupShow.library_id == CleanupLibrary.id)
        .filter(
            CleanupLibrary.name == library_name,
            CleanupShow.name == show_name,
            CleanupEpisode.filename == filename,
        )
        .first()
    )


def _episode_cleanup_info(cleanup_db: Session, episode: Episode) -> dict:
    cleanup_ep = _cleanup_episode_row(cleanup_db, episode.show.library.name, episode.show.name, episode.filename)
    if cleanup_ep is None or cleanup_ep.result is None:
        return {"has_cleanup": False, "cleaned_at": None, "cleanup_ok": None}
    return {"has_cleanup": True, "cleaned_at": cleanup_ep.result.cleaned_at, "cleanup_ok": cleanup_ep.result.ok}


def _episode_cleanup_detail(cleanup_db: Session, episode: Episode) -> dict:
    cleanup_ep = _cleanup_episode_row(cleanup_db, episode.show.library.name, episode.show.name, episode.filename)
    if cleanup_ep is None or cleanup_ep.result is None:
        return {
            "has_cleanup": False,
            "cleaned_at": None,
            "cleanup_ok": None,
            "summary": [],
            "warnings": [],
            "error": None,
        }
    result = cleanup_ep.result
    return {
        "has_cleanup": True,
        "cleaned_at": result.cleaned_at,
        "cleanup_ok": result.ok,
        "summary": json.loads(result.summary_json) if result.summary_json else [],
        "warnings": json.loads(result.warnings_json) if result.warnings_json else [],
        "error": result.error,
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "db_path": DB_PATH,
        "backup_db_path": BACKUP_DB_PATH,
        "cleanup_db_path": CLEANUP_DB_PATH,
        "libraries_root": LIBRARIES_ROOT,
    }


@app.get("/api/libraries")
def list_libraries(db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)):
    libraries = sync_libraries(db, LIBRARIES_ROOT)
    result = []
    for lib in libraries:
        shows = sync_shows(db, lib)
        backup_lib = backup_db.query(BackupLibrary).filter(BackupLibrary.name == lib.name).first()
        backed_up_count = sum(len(bs.episodes) for bs in backup_lib.shows) if backup_lib else 0
        result.append(
            {
                "id": lib.id,
                "name": lib.name,
                "path": lib.path,
                "missing": lib.missing,
                "show_count": len(shows),
                "backed_up_count": backed_up_count,
            }
        )
    return result


@app.get("/api/libraries/{library_id}/shows")
def list_shows(library_id: int, db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    backup_lib = backup_db.query(BackupLibrary).filter(BackupLibrary.name == library.name).first()

    result = []
    for show in shows:
        episodes = sync_episodes(db, show)
        scanned_count = sum(1 for ep in episodes if ep.last_scanned_at is not None)
        backed_up_count = 0
        if backup_lib is not None:
            backup_show = (
                backup_db.query(BackupShow)
                .filter(BackupShow.library_id == backup_lib.id, BackupShow.name == show.name)
                .first()
            )
            if backup_show is not None:
                backed_up_count = len(backup_show.episodes)
        result.append(
            {
                "id": show.id,
                "name": show.name,
                "path": show.path,
                "missing": show.missing,
                "episode_count": len(episodes),
                "scanned_count": scanned_count,
                "backed_up_count": backed_up_count,
            }
        )
    return result


@app.get("/api/shows/{show_id}/episodes")
def list_episodes(show_id: int, db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    episodes = sync_episodes(db, show)
    result = []
    for ep in episodes:
        backup_info = _episode_backup_info(backup_db, ep)
        result.append(
            {
                "id": ep.id,
                "filename": ep.filename,
                "path": ep.path,
                "season": ep.season,
                "episode": ep.episode,
                "missing": ep.missing,
                "last_scanned_at": ep.last_scanned_at.isoformat() if ep.last_scanned_at else None,
                "has_scan": ep.last_scanned_at is not None,
                "has_backup": backup_info["has_backup"],
            }
        )
    return result


@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: int, db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)):
    ep = db.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    chapters = db.query(Chapters).filter(Chapters.episode_id == ep.id).first()
    track_metadata = db.query(TrackMetadata).filter(TrackMetadata.episode_id == ep.id).first()
    backup_info = _episode_backup_content(backup_db, ep)
    return {
        "id": ep.id,
        "filename": ep.filename,
        "path": ep.path,
        "season": ep.season,
        "episode": ep.episode,
        "show_id": ep.show_id,
        "missing": ep.missing,
        "last_scanned_at": ep.last_scanned_at.isoformat() if ep.last_scanned_at else None,
        "has_backup": backup_info["has_backup"],
        "backed_up_at": backup_info["backed_up_at"].isoformat() if backup_info["backed_up_at"] else None,
        "chapters": chapters.chapter_xml if chapters else None,
        "track_metadata": track_metadata.tracks_json if track_metadata else None,
        "backup_chapters": backup_info["chapters"],
        "backup_track_metadata": backup_info["track_metadata"],
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


# --- Scan ---------------------------------------------------------------


@app.post("/api/episodes/{episode_id}/scan")
def scan_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    def work(scan_db, backup_db, on_result):
        on_result(scan_episode(scan_db, scan_db.get(Episode, episode_id)))

    job_id = launch_job(f"Scan {episode.filename}", 1, work)
    return {"job_id": job_id}


@app.post("/api/shows/{show_id}/scan")
def scan_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    total = len(sync_episodes(db, show))

    def work(scan_db, backup_db, on_result):
        scan_show(scan_db, scan_db.get(Show, show_id), on_result=on_result)

    job_id = launch_job(f'Scan "{show.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/shows/{show_id}/season/scan")
def scan_season_endpoint(show_id: int, season: str | None = None, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    episodes = sync_episodes(db, show)
    total = sum(1 for ep in episodes if ep.season == season)

    def work(scan_db, backup_db, on_result):
        scan_season(scan_db, scan_db.get(Show, show_id), season, on_result=on_result)

    label = f'Scan Season {season} of "{show.name}"' if season else f'Scan Unsorted episodes of "{show.name}"'
    job_id = launch_job(label, total, work)
    return {"job_id": job_id}


@app.post("/api/libraries/{library_id}/scan")
def scan_library_endpoint(library_id: int, db: Session = Depends(get_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    total = sum(len(sync_episodes(db, show)) for show in shows)

    def work(scan_db, backup_db, on_result):
        scan_library(scan_db, scan_db.get(Library, library_id), on_result=on_result)

    job_id = launch_job(f'Scan library "{library.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/scan/all")
def scan_all_endpoint(db: Session = Depends(get_db)):
    total = count_all_episodes(db)

    def work(scan_db, backup_db, on_result):
        scan_all(scan_db, on_result=on_result)

    job_id = launch_job("Scan all libraries", total, work)
    return {"job_id": job_id}


# --- Backup ---------------------------------------------------------------


@app.post("/api/episodes/{episode_id}/backup")
def backup_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    def work(scan_db, backup_db, on_result):
        on_result(backup_episode(scan_db, backup_db, scan_db.get(Episode, episode_id)))

    job_id = launch_job(f"Backup {episode.filename}", 1, work)
    return {"job_id": job_id}


@app.post("/api/shows/{show_id}/backup")
def backup_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    total = len(sync_episodes(db, show))

    def work(scan_db, backup_db, on_result):
        backup_show(scan_db, backup_db, scan_db.get(Show, show_id), on_result=on_result)

    job_id = launch_job(f'Backup "{show.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/shows/{show_id}/season/backup")
def backup_season_endpoint(show_id: int, season: str | None = None, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    episodes = sync_episodes(db, show)
    total = sum(1 for ep in episodes if ep.season == season)

    def work(scan_db, backup_db, on_result):
        backup_season(scan_db, backup_db, scan_db.get(Show, show_id), season, on_result=on_result)

    label = f'Backup Season {season} of "{show.name}"' if season else f'Backup Unsorted episodes of "{show.name}"'
    job_id = launch_job(label, total, work)
    return {"job_id": job_id}


@app.post("/api/libraries/{library_id}/backup")
def backup_library_endpoint(library_id: int, db: Session = Depends(get_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    total = sum(len(sync_episodes(db, show)) for show in shows)

    def work(scan_db, backup_db, on_result):
        backup_library(scan_db, backup_db, scan_db.get(Library, library_id), on_result=on_result)

    job_id = launch_job(f'Backup library "{library.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/backup/all")
def backup_all_endpoint(db: Session = Depends(get_db)):
    total = count_all_episodes(db)

    def work(scan_db, backup_db, on_result):
        backup_all(scan_db, backup_db, on_result=on_result)

    job_id = launch_job("Backup all libraries", total, work)
    return {"job_id": job_id}


# --- Restore ---------------------------------------------------------------


@app.post("/api/episodes/{episode_id}/restore")
def restore_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    def work(scan_db, backup_db, on_result):
        on_result(restore_chapters_for_episode(scan_db, backup_db, scan_db.get(Episode, episode_id)))

    job_id = launch_job(f"Restore {episode.filename}", 1, work)
    return {"job_id": job_id}


@app.post("/api/shows/{show_id}/restore")
def restore_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    total = len(sync_episodes(db, show))

    def work(scan_db, backup_db, on_result):
        restore_show(scan_db, backup_db, scan_db.get(Show, show_id), on_result=on_result)

    job_id = launch_job(f'Restore "{show.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/libraries/{library_id}/restore")
def restore_library_endpoint(library_id: int, db: Session = Depends(get_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    total = sum(len(sync_episodes(db, show)) for show in shows)

    def work(scan_db, backup_db, on_result):
        restore_library(scan_db, backup_db, scan_db.get(Library, library_id), on_result=on_result)

    job_id = launch_job(f'Restore library "{library.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/restore/all")
def restore_all_endpoint(db: Session = Depends(get_db)):
    total = count_all_episodes(db)

    def work(scan_db, backup_db, on_result):
        restore_all(scan_db, backup_db, on_result=on_result)

    job_id = launch_job("Restore all libraries", total, work)
    return {"job_id": job_id}


# --- Clear backup data -------------------------------------------------


@app.delete("/api/database")
def clear_database_endpoint(backup_db: Session = Depends(get_backup_db)):
    clear_database(backup_db)
    return {"ok": True}


@app.delete("/api/shows/{show_id}")
def delete_show_endpoint(show_id: int, db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    delete_show(backup_db, show)
    return {"ok": True}


@app.delete("/api/shows/{show_id}/season")
def delete_season_endpoint(
    show_id: int, season: str | None = None, db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)
):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    deleted = delete_season(backup_db, show, season)
    return {"ok": True, "deleted": deleted}


@app.delete("/api/episodes/{episode_id}")
def delete_episode_endpoint(
    episode_id: int, db: Session = Depends(get_db), backup_db: Session = Depends(get_backup_db)
):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    delete_episode(backup_db, episode)
    return {"ok": True}


# --- Export --------------------------------------------------------------


@app.get("/api/backup/export")
def export_backup_endpoint():
    tmp_path = export_backup_database()
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename="backup.db",
        background=BackgroundTask(lambda: os.remove(tmp_path)),
    )


# --- Cleanup (metadata normalization) --------------------------------------
#
# Entirely separate read/write paths from scan/backup/restore above: its own
# database (cleanup.db), its own job launcher, and it never touches the scan
# or backup databases. Shares only the read-only library/show/episode
# discovery already used everywhere else (sync_libraries/sync_shows/
# sync_episodes), which has no side effects on backup data.


@app.get("/api/cleanup/libraries")
def list_cleanup_libraries(db: Session = Depends(get_db), cleanup_db: Session = Depends(get_cleanup_db)):
    libraries = sync_libraries(db, LIBRARIES_ROOT)
    result = []
    for lib in libraries:
        shows = sync_shows(db, lib)
        cleanup_lib = cleanup_db.query(CleanupLibrary).filter(CleanupLibrary.name == lib.name).first()
        cleaned_count = 0
        if cleanup_lib is not None:
            for cleanup_show_row in cleanup_lib.shows:
                cleaned_count += sum(1 for ce in cleanup_show_row.episodes if ce.result is not None and ce.result.ok)
        result.append(
            {
                "id": lib.id,
                "name": lib.name,
                "path": lib.path,
                "missing": lib.missing,
                "show_count": len(shows),
                "cleaned_count": cleaned_count,
            }
        )
    return result


@app.get("/api/cleanup/libraries/{library_id}/shows")
def list_cleanup_shows(library_id: int, db: Session = Depends(get_db), cleanup_db: Session = Depends(get_cleanup_db)):
    library = db.get(Library, library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    shows = sync_shows(db, library)
    cleanup_lib = cleanup_db.query(CleanupLibrary).filter(CleanupLibrary.name == library.name).first()

    result = []
    for show in shows:
        episodes = sync_episodes(db, show)
        cleaned_count = 0
        if cleanup_lib is not None:
            cleanup_show_row = (
                cleanup_db.query(CleanupShow)
                .filter(CleanupShow.library_id == cleanup_lib.id, CleanupShow.name == show.name)
                .first()
            )
            if cleanup_show_row is not None:
                cleaned_count = sum(1 for ce in cleanup_show_row.episodes if ce.result is not None and ce.result.ok)
        result.append(
            {
                "id": show.id,
                "name": show.name,
                "path": show.path,
                "missing": show.missing,
                "episode_count": len(episodes),
                "cleaned_count": cleaned_count,
            }
        )
    return result


@app.get("/api/cleanup/shows/{show_id}/episodes")
def list_cleanup_episodes(show_id: int, db: Session = Depends(get_db), cleanup_db: Session = Depends(get_cleanup_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    episodes = sync_episodes(db, show)
    result = []
    for ep in episodes:
        info = _episode_cleanup_info(cleanup_db, ep)
        result.append(
            {
                "id": ep.id,
                "filename": ep.filename,
                "path": ep.path,
                "season": ep.season,
                "episode": ep.episode,
                "missing": ep.missing,
                "has_cleanup": info["has_cleanup"],
                "cleaned_at": info["cleaned_at"].isoformat() if info["cleaned_at"] else None,
                "cleanup_ok": info["cleanup_ok"],
            }
        )
    return result


@app.get("/api/cleanup/episodes/{episode_id}")
def get_cleanup_episode(episode_id: int, db: Session = Depends(get_db), cleanup_db: Session = Depends(get_cleanup_db)):
    ep = db.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    detail = _episode_cleanup_detail(cleanup_db, ep)
    return {
        "id": ep.id,
        "filename": ep.filename,
        "path": ep.path,
        "season": ep.season,
        "episode": ep.episode,
        "show_id": ep.show_id,
        "missing": ep.missing,
        "has_cleanup": detail["has_cleanup"],
        "cleaned_at": detail["cleaned_at"].isoformat() if detail["cleaned_at"] else None,
        "cleanup_ok": detail["cleanup_ok"],
        "summary": detail["summary"],
        "warnings": detail["warnings"],
        "error": detail["error"],
    }


@app.post("/api/cleanup/episodes/{episode_id}/clean")
def clean_episode_endpoint(episode_id: int, db: Session = Depends(get_db)):
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    def work(scan_db, cleanup_db, on_result):
        on_result(cleanup_episode(cleanup_db, scan_db.get(Episode, episode_id)))

    job_id = launch_cleanup_job(f"Clean up {episode.filename}", 1, work)
    return {"job_id": job_id}


@app.post("/api/cleanup/shows/{show_id}/clean")
def clean_show_endpoint(show_id: int, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    total = len(sync_episodes(db, show))

    def work(scan_db, cleanup_db, on_result):
        cleanup_show(scan_db, cleanup_db, scan_db.get(Show, show_id), on_result=on_result)

    job_id = launch_cleanup_job(f'Clean up "{show.name}"', total, work)
    return {"job_id": job_id}


@app.post("/api/cleanup/shows/{show_id}/season/clean")
def clean_season_endpoint(show_id: int, season: str | None = None, db: Session = Depends(get_db)):
    show = db.get(Show, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="Show not found")
    episodes = sync_episodes(db, show)
    total = sum(1 for ep in episodes if ep.season == season)

    def work(scan_db, cleanup_db, on_result):
        cleanup_season(scan_db, cleanup_db, scan_db.get(Show, show_id), season, on_result=on_result)

    label = f'Clean up Season {season} of "{show.name}"' if season else f'Clean up Unsorted episodes of "{show.name}"'
    job_id = launch_cleanup_job(label, total, work)
    return {"job_id": job_id}


# --- Cleanup settings: audio codec name mapping + subtitle suffixes -------


def _codec_mapping_dict(row: CleanupCodecMapping) -> dict:
    return {"id": row.id, "codec_key": row.codec_key, "display_name": row.display_name, "is_builtin": row.is_builtin}


@app.get("/api/cleanup/settings/codecs")
def list_codec_mappings(cleanup_db: Session = Depends(get_cleanup_db)):
    rows = cleanup_db.query(CleanupCodecMapping).order_by(CleanupCodecMapping.codec_key).all()
    return [_codec_mapping_dict(row) for row in rows]


@app.post("/api/cleanup/settings/codecs")
def create_codec_mapping(payload: dict, cleanup_db: Session = Depends(get_cleanup_db)):
    codec_key = (payload.get("codec_key") or "").strip()
    display_name = (payload.get("display_name") or "").strip()
    if not codec_key or not display_name:
        raise HTTPException(status_code=400, detail="codec_key and display_name are required")
    if cleanup_db.query(CleanupCodecMapping).filter(CleanupCodecMapping.codec_key == codec_key).first():
        raise HTTPException(status_code=400, detail=f'A mapping for "{codec_key}" already exists')
    row = CleanupCodecMapping(codec_key=codec_key, display_name=display_name, is_builtin=False)
    cleanup_db.add(row)
    cleanup_db.commit()
    return _codec_mapping_dict(row)


@app.put("/api/cleanup/settings/codecs/{mapping_id}")
def update_codec_mapping(mapping_id: int, payload: dict, cleanup_db: Session = Depends(get_cleanup_db)):
    row = cleanup_db.get(CleanupCodecMapping, mapping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mapping not found")

    display_name = (payload.get("display_name") or "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name is required")

    new_key = payload.get("codec_key")
    if new_key is not None:
        new_key = new_key.strip()
        if new_key != row.codec_key:
            if row.is_builtin:
                raise HTTPException(status_code=400, detail="Built-in codec mappings cannot be renamed")
            if not new_key:
                raise HTTPException(status_code=400, detail="codec_key is required")
            if cleanup_db.query(CleanupCodecMapping).filter(
                CleanupCodecMapping.codec_key == new_key, CleanupCodecMapping.id != mapping_id
            ).first():
                raise HTTPException(status_code=400, detail=f'A mapping for "{new_key}" already exists')
            row.codec_key = new_key

    row.display_name = display_name
    cleanup_db.commit()
    return _codec_mapping_dict(row)


@app.delete("/api/cleanup/settings/codecs/{mapping_id}")
def delete_codec_mapping(mapping_id: int, cleanup_db: Session = Depends(get_cleanup_db)):
    row = cleanup_db.get(CleanupCodecMapping, mapping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    if row.is_builtin:
        raise HTTPException(status_code=400, detail="Built-in codec mappings cannot be deleted")
    cleanup_db.delete(row)
    cleanup_db.commit()
    return {"ok": True}


@app.get("/api/cleanup/settings/subtitles")
def get_subtitle_settings(cleanup_db: Session = Depends(get_cleanup_db)):
    settings = cleanup_db.query(CleanupSettings).first()
    if settings is None:
        return {"forced_suffix": "Forced", "commentary_suffix": "Commentary"}
    return {"forced_suffix": settings.forced_suffix, "commentary_suffix": settings.commentary_suffix}


@app.put("/api/cleanup/settings/subtitles")
def update_subtitle_settings(payload: dict, cleanup_db: Session = Depends(get_cleanup_db)):
    forced_suffix = (payload.get("forced_suffix") or "").strip()
    commentary_suffix = (payload.get("commentary_suffix") or "").strip()
    if not forced_suffix or not commentary_suffix:
        raise HTTPException(status_code=400, detail="Both suffixes are required")
    settings = cleanup_db.query(CleanupSettings).first()
    if settings is None:
        settings = CleanupSettings(id=1)
        cleanup_db.add(settings)
    settings.forced_suffix = forced_suffix
    settings.commentary_suffix = commentary_suffix
    cleanup_db.commit()
    return {"forced_suffix": settings.forced_suffix, "commentary_suffix": settings.commentary_suffix}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
