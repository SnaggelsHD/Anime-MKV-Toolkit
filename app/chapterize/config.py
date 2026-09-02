import os
from pathlib import Path

CHAPTERIZE_DB_PATH = os.environ.get("CHAPTERIZE_DB_PATH", "/data/chapterize.db")

CACHE_ROOT = Path(os.environ.get("CHAPTERIZE_CACHE_DIR", "/data/chapterize"))
CACHE_DIR = CACHE_ROOT / "cache" / "animethemes"
TMP_DIR = CACHE_ROOT / "tmp"
PREVIEW_DIR = CACHE_ROOT / "previews"


def ensure_dirs() -> None:
    for d in (CACHE_ROOT, CACHE_DIR, TMP_DIR, PREVIEW_DIR):
        d.mkdir(parents=True, exist_ok=True)


def cleanup_tmp_dir() -> int:
    """Wipe TMP_DIR on startup. It only ever holds per-episode audio
    extracted during an analysis run (deleted right after use), so
    anything found here on startup is debris from a hard kill mid-run.
    Returns the number of files removed."""
    ensure_dirs()
    removed = 0
    for f in TMP_DIR.iterdir():
        try:
            if f.is_file():
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed
