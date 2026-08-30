import logging
import os
import subprocess
import tempfile

from sqlalchemy.orm import Session

from app.backup_models import BackupChapters, BackupEpisode, BackupLibrary, BackupShow
from app.models import Episode, Library, Show
from app.scanner import sync_episodes, sync_shows

logger = logging.getLogger("mkv_backup")

TIMEOUT = 300


def _find_backup_chapter_xml(backup_db: Session, episode: Episode) -> str | None:
    library_name = episode.show.library.name
    show_name = episode.show.name
    row = (
        backup_db.query(BackupChapters)
        .join(BackupEpisode, BackupChapters.episode_id == BackupEpisode.id)
        .join(BackupShow, BackupEpisode.show_id == BackupShow.id)
        .join(BackupLibrary, BackupShow.library_id == BackupLibrary.id)
        .filter(
            BackupLibrary.name == library_name,
            BackupShow.name == show_name,
            BackupEpisode.filename == episode.filename,
        )
        .first()
    )
    return row.chapter_xml if row else None


def restore_chapters_for_episode(scan_db: Session, backup_db: Session, episode: Episode) -> dict:
    result = {"episode_id": episode.id, "filename": episode.filename}

    chapter_xml = _find_backup_chapter_xml(backup_db, episode)
    if chapter_xml is None:
        return {**result, "ok": False, "error": "No stored chapters for this episode"}

    if not os.path.isfile(episode.path):
        logger.warning("Restore skipped, file not found: %s", episode.path)
        return {**result, "ok": False, "error": "File not found on disk"}

    episode_dir = os.path.dirname(episode.path)
    chapters_fd, chapters_path = tempfile.mkstemp(suffix=".xml", dir=episode_dir)
    out_fd, out_path = tempfile.mkstemp(suffix=".mkv", dir=episode_dir)
    os.close(chapters_fd)
    os.close(out_fd)

    try:
        with open(chapters_path, "w", encoding="utf-8") as f:
            f.write(chapter_xml)

        proc = subprocess.run(
            ["mkvmerge", "-o", out_path, "--no-chapters", "--chapters", chapters_path, episode.path],
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
        if proc.returncode != 0:
            logger.error("Restore failed for %s: %s", episode.path, proc.stderr.strip())
            return {**result, "ok": False, "error": f"mkvmerge failed: {proc.stderr.strip()}"}

        os.replace(out_path, episode.path)
        logger.info("Restored chapters for %s", episode.path)
        return {**result, "ok": True}
    except FileNotFoundError:
        return {**result, "ok": False, "error": "mkvmerge is not installed"}
    finally:
        for p in (chapters_path, out_path):
            if os.path.exists(p):
                os.remove(p)


def restore_show(scan_db: Session, backup_db: Session, show: Show, on_result=None) -> list[dict]:
    episodes = sync_episodes(scan_db, show)
    results = []
    for ep in episodes:
        result = restore_chapters_for_episode(scan_db, backup_db, ep)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def restore_library(scan_db: Session, backup_db: Session, library: Library, on_result=None) -> list[dict]:
    shows = sync_shows(scan_db, library)
    results = []
    for show in shows:
        results.extend(restore_show(scan_db, backup_db, show, on_result=on_result))
    return results
