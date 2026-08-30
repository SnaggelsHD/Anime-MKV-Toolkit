import os
import subprocess
import tempfile

from sqlalchemy.orm import Session

from app.models import Chapters, Episode, Library, Show
from app.scanner import sync_episodes, sync_shows

TIMEOUT = 300


def restore_chapters_for_episode(db: Session, episode: Episode) -> dict:
    result = {"episode_id": episode.id, "filename": episode.filename}

    chapters_row = db.query(Chapters).filter(Chapters.episode_id == episode.id).first()
    if chapters_row is None:
        return {**result, "ok": False, "error": "No stored chapters for this episode"}

    if not os.path.isfile(episode.path):
        return {**result, "ok": False, "error": "File not found on disk"}

    episode_dir = os.path.dirname(episode.path)
    chapters_fd, chapters_path = tempfile.mkstemp(suffix=".xml", dir=episode_dir)
    out_fd, out_path = tempfile.mkstemp(suffix=".mkv", dir=episode_dir)
    os.close(chapters_fd)
    os.close(out_fd)

    try:
        with open(chapters_path, "w", encoding="utf-8") as f:
            f.write(chapters_row.chapter_xml)

        proc = subprocess.run(
            ["mkvmerge", "-o", out_path, "--chapters", chapters_path, episode.path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
        if proc.returncode != 0:
            return {**result, "ok": False, "error": f"mkvmerge failed: {proc.stderr.strip()}"}

        os.replace(out_path, episode.path)
        return {**result, "ok": True}
    except FileNotFoundError:
        return {**result, "ok": False, "error": "mkvmerge is not installed"}
    finally:
        for p in (chapters_path, out_path):
            if os.path.exists(p):
                os.remove(p)


def restore_show(db: Session, show: Show) -> list[dict]:
    episodes = sync_episodes(db, show)
    return [restore_chapters_for_episode(db, ep) for ep in episodes]


def restore_library(db: Session, library: Library) -> list[dict]:
    shows = sync_shows(db, library)
    results = []
    for show in shows:
        results.extend(restore_show(db, show))
    return results
