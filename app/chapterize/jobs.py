"""In-memory background job manager for analysis runs. Each job runs on
its own thread (the work is CPU/subprocess-bound: ffmpeg + chroma feature
extraction), with progress and log lines polled by the SSE endpoint. Kept
separate from the Toolkit's own app/jobs.py (a different, simpler polling
job system used by the task-queue widget for scan/backup/restore/cleanup)
since this one needs live log streaming and a cancel button, matching the
original chapter analyzer's UI."""
import asyncio
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from app.chapterize import animethemes, audio_match, mkv_chapters, naming
from app.chapterize.config import TMP_DIR
from app.chapterize.db import load_settings
from app.db import SessionLocal
from app.models import Episode

logger = logging.getLogger("chapterize.jobs")

_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()

# Caps how many analyses run their actual audio work at once; extra jobs sit
# in "queued" status until a slot frees up, so firing off several seasons in
# a row doesn't pile every thread onto the CPU simultaneously.
MAX_CONCURRENT_JOBS = int(os.environ.get("CHAPTERIZE_MAX_CONCURRENT_JOBS", "2"))
_run_slots = threading.Semaphore(MAX_CONCURRENT_JOBS)

# Finished jobs are pruned so a long-lived container doesn't accumulate
# them forever; a running/queued job is never pruned.
_FINISHED_JOB_MAX_AGE = 6 * 3600
_FINISHED_JOB_MAX_COUNT = 20


class Job:
    def __init__(self, job_id: str, episode_ids: list[int]):
        self.id = job_id
        self.episode_ids = episode_ids
        self.season_label = ""
        self.status = "pending"  # pending | queued | running | done | error | cancelled
        self.progress = 0.0
        self.logs: list[dict] = []
        self.episodes: list[dict] = []
        self.error: str | None = None
        self.created_at = time.time()
        self.updated_at = time.time()
        self.cancel_requested = False
        self._lock = threading.Lock()

    def log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.logs.append({"ts": time.time(), "level": level, "message": message})
            self.updated_at = time.time()

    def set_progress(self, fraction: float) -> None:
        with self._lock:
            self.progress = max(0.0, min(100.0, fraction * 100))
            self.updated_at = time.time()

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "progress": self.progress,
                "season_label": self.season_label,
                "episodes": self.episodes,
                "error": self.error,
                "log_count": len(self.logs),
                "cancel_requested": self.cancel_requested,
            }

    def logs_since(self, index: int) -> tuple[list[dict], int]:
        with self._lock:
            new_logs = self.logs[index:]
            return new_logs, len(self.logs)

    def set_episodes(self, episodes: list[dict]) -> None:
        with self._lock:
            self.episodes = episodes

    def append_episode(self, episode_result: dict) -> None:
        with self._lock:
            self.episodes.append(episode_result)


def get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def cancel_job(job_id: str) -> Job | None:
    job = get_job(job_id)
    if job is None:
        return None
    job.request_cancel()
    return job


def _prune_finished_jobs() -> None:
    """Drop old/excess finished jobs so _jobs doesn't grow forever across a
    long-running container. Running/queued/pending jobs are never touched."""
    now = time.time()
    with _jobs_lock:
        finished = sorted(
            (j for j in _jobs.values() if j.status in ("done", "error", "cancelled")),
            key=lambda j: j.updated_at, reverse=True,
        )
        keep_ids = set()
        for i, j in enumerate(finished):
            if i < _FINISHED_JOB_MAX_COUNT and (now - j.updated_at) < _FINISHED_JOB_MAX_AGE:
                keep_ids.add(j.id)
        for j in finished:
            if j.id not in keep_ids:
                del _jobs[j.id]


MODE_MATCH_ALL = "match_all"
MODE_EPISODE_MAPPED = "episode_mapped"


def start_job(episode_ids: list[int], anime_slug: str, theme_slugs: list[str],
              mode: str = MODE_MATCH_ALL, episode_number_overrides: dict[int, int] | None = None) -> Job:
    _prune_finished_jobs()
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, episode_ids)
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, anime_slug, theme_slugs, mode, episode_number_overrides or {}),
        daemon=True,
    )
    thread.start()
    return job


def _run_job(job: Job, anime_slug: str, theme_slugs: list[str], mode: str,
             episode_number_overrides: dict[int, int]) -> None:
    job.status = "queued"
    with _run_slots:
        _run_job_locked(job, anime_slug, theme_slugs, mode, episode_number_overrides)


def _run_job_locked(job: Job, anime_slug: str, theme_slugs: list[str], mode: str,
                     episode_number_overrides: dict[int, int]) -> None:
    scan_db = SessionLocal()
    try:
        job.status = "running"
        settings = load_settings()
        schema = settings.get("naming_schema", {})
        threshold = settings.get("match_threshold", 0.8)

        episodes = (
            scan_db.query(Episode)
            .filter(Episode.id.in_(job.episode_ids))
            .order_by(Episode.filename)
            .all()
        )
        if episodes:
            show = episodes[0].show
            season_bit = f"Season {episodes[0].season}" if episodes[0].season else "Unsorted"
            job.season_label = f"{show.name} - {season_bit}" if len(episodes) > 1 else f"{show.name} - {episodes[0].filename}"

        job.log(f"Fetching theme list for '{anime_slug}'")
        themes = asyncio.run(animethemes.get_themes(anime_slug))
        by_slug = {t.slug: t for t in themes}

        theme_chroma: dict[str, tuple] = {}  # slug -> (chroma, type, song_title)
        for slug in theme_slugs:
            theme = by_slug.get(slug)
            if not theme:
                job.log(f"Theme {slug} not found, skipping", level="warn")
                continue
            try:
                path = asyncio.run(animethemes.download_and_cache_theme(anime_slug, theme, log=job.log))
                y = audio_match.load_mono(path)
                chroma = audio_match.chroma_features(y)
                theme_chroma[slug] = (chroma, theme.type, theme.song_title)
            except Exception as e:
                job.log(f"Could not prepare theme {slug}: {e}", level="err")

        if not theme_chroma:
            job.log("No usable OP/ED themes were cached; episodes will get a single Episode chapter.", level="warn")

        mode_label = "matching every selected theme" if mode == MODE_MATCH_ALL else "using per-episode OP/ED assignment first"
        job.log(f"Recognition mode: {mode_label}")

        total = len(episodes)
        for i, ep in enumerate(episodes):
            if job.cancel_requested:
                job.log(f"Cancelled after {i}/{total} episodes", level="warn")
                job.status = "cancelled"
                return

            episode_number = episode_number_overrides.get(ep.id)
            if episode_number is None:
                episode_number = naming.parse_episode_number(ep.filename, fallback=i + 1)
            job.log(f"[{i + 1}/{total}] Processing {ep.filename}")
            episode_result = {
                "episode_id": ep.id,
                "path": ep.path,
                "name": ep.filename,
                "episode_number": episode_number,
                "duration": None,
                "show_locked": bool(ep.show.locked),
                "old_chapters_xml": None,
                "chapters": [],
                "opening_candidates": [],
                "ending_candidates": [],
                "error": None,
            }
            try:
                mkv_path = Path(ep.path)

                try:
                    episode_result["old_chapters_xml"] = mkv_chapters.extract_chapters_xml(mkv_path)
                except mkv_chapters.ChapterToolError as e:
                    job.log(f"Could not read existing chapters: {e}", level="warn")

                duration = audio_match.probe_duration(mkv_path)
                episode_result["duration"] = duration

                audio_streams = audio_match.probe_audio_streams(mkv_path)
                audio_index = audio_match.select_japanese_audio_index_from(audio_streams)
                if audio_index is not None:
                    picked = audio_streams[audio_index]
                    job.log(f"Using audio track {audio_index} for analysis "
                            f"(language={picked.language or 'unknown'}, default={picked.is_default})")
                else:
                    job.log("Could not detect any audio streams; letting ffmpeg pick automatically", level="warn")

                job.log("Extracting and analyzing audio (this can take a bit)")
                TMP_DIR.mkdir(parents=True, exist_ok=True)
                tmp_wav = TMP_DIR / f"{job.id}-{i}.wav"
                audio_match.extract_audio(mkv_path, tmp_wav, audio_index)
                try:
                    ep_audio = audio_match.load_mono(tmp_wav)
                    ep_chroma = audio_match.chroma_features(ep_audio)
                finally:
                    tmp_wav.unlink(missing_ok=True)

                if mode == MODE_EPISODE_MAPPED:
                    primary = [s for s in theme_chroma if by_slug[s].applies_to_episode(episode_number)]
                    fallback = [s for s in theme_chroma if s not in primary]
                else:
                    primary = list(theme_chroma.keys())
                    fallback = []

                opening_cands, ending_cands = _gather_zone_candidates(
                    ep_chroma, theme_chroma, primary, threshold, duration,
                )
                if fallback and (not opening_cands or not ending_cands):
                    fb_open, fb_end = _gather_zone_candidates(
                        ep_chroma, theme_chroma, fallback, threshold, duration,
                    )
                    if not opening_cands and fb_open:
                        job.log("No opening match among episode-assigned themes; falling back to the rest", level="warn")
                        opening_cands = fb_open
                    if not ending_cands and fb_end:
                        job.log("No ending match among episode-assigned themes; falling back to the rest", level="warn")
                        ending_cands = fb_end

                op_chapter, op_candidates = _resolve_zone(opening_cands, "opening", schema, episode_number)
                ed_chapter, ed_candidates = _resolve_zone(ending_cands, "ending", schema, episode_number)
                episode_result["opening_candidates"] = op_candidates
                episode_result["ending_candidates"] = ed_candidates

                chapters = _assemble_chapters(duration, op_chapter, ed_chapter, schema, episode_number)
                episode_result["chapters"] = chapters

                if op_chapter:
                    job.log(f"Opening found at {op_chapter['start']:.1f}s-{op_chapter['end']:.1f}s "
                            f"(score {op_chapter['confidence']:.2f}, theme {op_chapter['theme_slug']})")
                    if op_chapter["needs_review"]:
                        job.log(f"{len(op_candidates)} candidate openings found - please review", level="warn")
                else:
                    job.log("No opening match above threshold", level="warn")
                if ed_chapter:
                    job.log(f"Ending found at {ed_chapter['start']:.1f}s-{ed_chapter['end']:.1f}s "
                            f"(score {ed_chapter['confidence']:.2f}, theme {ed_chapter['theme_slug']})")
                    if ed_chapter["needs_review"]:
                        job.log(f"{len(ed_candidates)} candidate endings found - please review", level="warn")
                else:
                    job.log("No ending match above threshold", level="warn")

            except Exception as e:
                episode_result["error"] = str(e)
                job.log(f"Failed to analyze {ep.filename}: {e}", level="err")

            job.append_episode(episode_result)
            job.set_progress((i + 1) / total)

        job.status = "done"
        job.log("Analysis complete", level="ok")
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.log(f"Job failed: {e}", level="err")
    finally:
        scan_db.close()


def _gather_zone_candidates(ep_chroma, theme_chroma: dict, slugs: list[str], threshold: float,
                             duration: float) -> tuple[list[dict], list[dict]]:
    """Match every theme in slugs against the full episode, then split the
    hits into an opening-zone (first half) and ending-zone (second half)
    bucket by where each hit actually landed - not by the OP/ED tag the
    theme carries on animethemes.moe. This is what lets a theme that's
    tagged OP but reused as that episode's ending end up in the Ending
    chapter instead of Opening."""
    half = duration / 2
    opening, ending = [], []
    for slug in slugs:
        chroma, tag_type, song_title = theme_chroma[slug]
        for m in audio_match.find_all_matches(ep_chroma, chroma, threshold=threshold, max_matches=3):
            candidate = {
                "theme_slug": slug, "song_title": song_title, "tag_type": tag_type,
                "start": m.start, "end": m.end, "score": m.score,
            }
            (opening if m.start < half else ending).append(candidate)
    return _dedup_candidates(opening), _dedup_candidates(ending)


def _overlap_ratio(a: dict, b: dict) -> float:
    lo = max(a["start"], b["start"])
    hi = min(a["end"], b["end"])
    inter = max(0.0, hi - lo)
    union = (a["end"] - a["start"]) + (b["end"] - b["start"]) - inter
    return inter / union if union > 0 else 0.0


def _dedup_candidates(candidates: list[dict], overlap_thresh: float = 0.3) -> list[dict]:
    """Collapse near-duplicate detections of the same underlying moment
    (e.g. two different theme slugs both aligning to the same real OP)
    down to the highest-scoring one, keeping genuinely distinct hits."""
    ordered = sorted(candidates, key=lambda c: -c["score"])
    kept: list[dict] = []
    for c in ordered:
        if any(_overlap_ratio(c, k) > overlap_thresh for k in kept):
            continue
        kept.append(c)
    return kept


def _resolve_zone(candidates: list[dict], ctype: str, schema: dict, episode_number: int) -> tuple[dict | None, list[dict]]:
    if not candidates:
        return None, []
    ordered = sorted(candidates, key=lambda c: -c["score"])
    best = ordered[0]
    chapter = _chapter(ctype, best["start"], best["end"], schema, episode_number, best["score"])
    chapter["theme_slug"] = best["theme_slug"]
    chapter["needs_review"] = len(ordered) > 1

    candidates_out = [
        {
            "theme_slug": c["theme_slug"],
            "song_title": c["song_title"],
            "start": c["start"],
            "end": c["end"],
            "score": round(c["score"], 3),
        }
        for c in ordered
    ]
    return chapter, candidates_out


def _assemble_chapters(duration: float, op_chapter: dict | None, ed_chapter: dict | None,
                        schema: dict, episode_number: int) -> list[dict]:
    chapters = []
    episode_start = 0.0
    episode_end = duration

    if op_chapter and op_chapter["start"] > 1.0:
        chapters.append(_chapter("prologue", 0.0, op_chapter["start"], schema, episode_number))
    if op_chapter:
        chapters.append(op_chapter)
        episode_start = op_chapter["end"]

    if ed_chapter:
        episode_end = max(ed_chapter["start"], episode_start)

    chapters.append(_chapter("episode", episode_start, episode_end, schema, episode_number))

    if ed_chapter:
        chapters.append(ed_chapter)
        if duration - ed_chapter["end"] > 1.0:
            chapters.append(_chapter("epilogue", ed_chapter["end"], duration, schema, episode_number))

    # Always-present marker at the very last timestamp of the file,
    # regardless of what else was found.
    chapters.append(_chapter("end", duration, duration, schema, episode_number))

    return chapters


def _chapter(ctype: str, start: float, end: float, schema: dict, episode_number: int,
             confidence: float | None = None, n: int = 1) -> dict:
    template = schema.get(ctype, ctype.capitalize())
    title = naming.render_title(template, n, episode_number)
    return {"type": ctype, "start": start, "end": end, "title": title, "confidence": confidence}
