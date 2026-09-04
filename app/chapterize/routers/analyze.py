import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.chapterize import audio_match, jobs, mkv_chapters, video_preview
from app.chapterize.db import get_chapterize_db
from app.chapterize.models import ChapterizeResult
from app.chapterize.models import utcnow as chapterize_utcnow
from app.chapterize.range_response import serve_file_with_ranges
from app.db import get_db
from app.models import Episode
from app.scan import scan_episode

logger = logging.getLogger("chapterize.analyze")

router = APIRouter()


@router.get("/preview/{episode_id}")
def preview(episode_id: int, request: Request, db: Session = Depends(get_db)):
    """A browser-playable mp4 proxy of this episode (built/cached on first
    request), used by the review screen's video scrub bar. Defined as a
    plain (non-async) endpoint so FastAPI runs the potentially slow ffmpeg
    build off the event loop in its worker threadpool."""
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    mkv_path = Path(episode.path)
    try:
        audio_index = audio_match.select_japanese_audio_index(mkv_path)
        preview_path = video_preview.get_or_build_preview(mkv_path, audio_index)
    except video_preview.PreviewError as e:
        logger.exception("Failed to build preview for episode %s", episode_id)
        raise HTTPException(status_code=500, detail=str(e))

    return serve_file_with_ranges(request, preview_path, "video/mp4")


@router.delete("/preview-cache")
def clear_preview_cache():
    removed = video_preview.clear_cache()
    return {"removed": removed}


class AnalyzeRequest(BaseModel):
    episode_ids: list[int]
    anime_slug: str
    anime_name: str | None = None
    theme_slugs: list[str]
    mode: Literal["match_all", "episode_mapped"] = "match_all"
    episode_number_overrides: dict[int, int] = {}


@router.post("")
def start_analysis(req: AnalyzeRequest, db: Session = Depends(get_db)):
    if not req.episode_ids:
        raise HTTPException(status_code=400, detail="No episodes selected")
    if not req.theme_slugs:
        raise HTTPException(status_code=400, detail="No OP/ED themes selected")
    found = db.query(Episode.id).filter(Episode.id.in_(req.episode_ids)).count()
    if found == 0:
        raise HTTPException(status_code=404, detail="None of the selected episodes were found")
    job = jobs.start_job(
        req.episode_ids, req.anime_slug, req.theme_slugs, req.mode, req.episode_number_overrides,
    )
    return {"job_id": job.id}


@router.get("/{job_id}/result")
def get_result(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.snapshot()


@router.get("/{job_id}/events")
async def stream_events(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")

    async def event_generator():
        index = 0
        while True:
            new_logs, index = job.logs_since(index)
            if new_logs:
                yield {"event": "log", "data": json.dumps(new_logs)}
            yield {"event": "status", "data": json.dumps(job.snapshot())}
            if job.status in ("done", "error", "cancelled"):
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.post("/{job_id}/cancel")
def cancel_analysis(job_id: str):
    job = jobs.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.snapshot()


class UpdateChaptersRequest(BaseModel):
    episodes: list[dict]


@router.put("/{job_id}/chapters")
def update_chapters(job_id: str, req: UpdateChaptersRequest):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    job.set_episodes(req.episodes)
    return job.snapshot()


def _record_chapterize_result(chapterize_db: Session, episode_id: int, ok: bool, error: str | None) -> None:
    """Upsert a ChapterizeResult row for an episode a save actually
    attempted (successfully or not) - not called for an episode skipped
    without an attempt (locked show, prior analysis error)."""
    row = chapterize_db.query(ChapterizeResult).filter(ChapterizeResult.episode_id == episode_id).first()
    if row is None:
        row = ChapterizeResult(episode_id=episode_id)
        chapterize_db.add(row)
    row.analyzed_at = chapterize_utcnow()
    row.ok = ok
    row.error = error
    chapterize_db.commit()


@router.post("/{job_id}/save")
def save_chapters(job_id: str, db: Session = Depends(get_db), chapterize_db: Session = Depends(get_chapterize_db)):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    if job.status not in ("done", "cancelled"):
        raise HTTPException(status_code=400, detail="Job hasn't finished analyzing yet")
    if not job.episodes:
        raise HTTPException(status_code=400, detail="No analyzed episodes to save")

    results = []
    for ep in job.episodes:
        if ep.get("error"):
            results.append({"episode_id": ep["episode_id"], "ok": False, "error": "skipped: analysis failed for this episode"})
            continue

        episode = db.get(Episode, ep["episode_id"])
        if episode is None:
            results.append({"episode_id": ep["episode_id"], "ok": False, "error": "Episode no longer exists"})
            continue
        if episode.show.locked:
            results.append({
                "episode_id": episode.id, "ok": False,
                "error": "Show is locked (tvshow.nfo tmm_locked=true) - chapter save disabled",
            })
            continue

        try:
            chapters = sorted(ep.get("chapters", []), key=lambda c: c["start"])
            entries = [
                {"title": c.get("title") or c.get("type", "Chapter").capitalize(), "start": c["start"]}
                for c in chapters
            ]
            mkv_chapters.write_chapters(Path(episode.path), entries)
            _record_chapterize_result(chapterize_db, episode.id, ok=True, error=None)
            # Chapters just changed on disk; rescan immediately so the scan
            # database (and the episode detail view) reflect it right away,
            # the same way a successful cleanup already does.
            try:
                scan_episode(db, episode)
            except Exception:
                logger.exception("Post-save rescan failed for episode %s", episode.id)
            results.append({"episode_id": episode.id, "ok": True, "error": None})
        except Exception as e:
            logger.exception("Failed to save chapters for episode %s", episode.id)
            _record_chapterize_result(chapterize_db, episode.id, ok=False, error=str(e))
            results.append({"episode_id": episode.id, "ok": False, "error": str(e)})

    return {"results": results}
