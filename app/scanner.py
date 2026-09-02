import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Library, Show, Episode

SEASON_EPISODE_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")
SEASON_DIR_RE = re.compile(r"season\s*0*(\d+)", re.IGNORECASE)

# Non-numbered top-level folders (siblings of the season folders) that a
# library organizer commonly uses for clean OP/ED rips - grouped as their
# own named "season" instead of falling into Unsorted.
_SPECIAL_SEASON_FOLDERS = {
    "openings & endings": "Openings & Endings",
    "openings and endings": "Openings & Endings",
}


@dataclass
class ScannedEpisode:
    filename: str
    path: str
    season: str | None
    episode: str | None


def find_libraries(libraries_root: str) -> list[tuple[str, str]]:
    """Return (name, path) for each top-level directory under libraries_root."""
    if not os.path.isdir(libraries_root):
        return []
    result = []
    for entry in sorted(os.scandir(libraries_root), key=lambda e: e.name):
        if entry.is_dir():
            result.append((entry.name, entry.path))
    return result


def _contains_mkv(dir_path: str) -> bool:
    for _root, _dirs, files in os.walk(dir_path):
        if any(f.lower().endswith(".mkv") for f in files):
            return True
    return False


def find_shows(library_path: str) -> list[tuple[str, str]]:
    """Return (name, path) for each show directory (direct subdir of a library
    that contains .mkv files, directly or recursively)."""
    if not os.path.isdir(library_path):
        return []
    result = []
    for entry in sorted(os.scandir(library_path), key=lambda e: e.name):
        if entry.is_dir() and _contains_mkv(entry.path):
            result.append((entry.name, entry.path))
    return result


def _parse_season_episode(filename: str, rel_dir: str) -> tuple[str | None, str | None]:
    match = SEASON_EPISODE_RE.search(filename)
    if match:
        return str(int(match.group(1))), str(int(match.group(2)))

    # A special-named folder (e.g. "Openings & Endings") takes priority over
    # the numeric season-folder regex below, since its files won't carry an
    # S01E01-style episode number either.
    top_level_dir = rel_dir.replace("\\", "/").strip("/").split("/")[0].lower()
    if top_level_dir in _SPECIAL_SEASON_FOLDERS:
        return _SPECIAL_SEASON_FOLDERS[top_level_dir], None

    season = None
    dir_match = SEASON_DIR_RE.search(rel_dir)
    if dir_match:
        season = str(int(dir_match.group(1)))
    return season, None


def find_episodes(show_path: str) -> list[ScannedEpisode]:
    """Return every .mkv file under show_path (recursively)."""
    result = []
    for root, _dirs, files in os.walk(show_path):
        rel_dir = os.path.relpath(root, show_path)
        for f in sorted(files):
            if not f.lower().endswith(".mkv"):
                continue
            season, episode = _parse_season_episode(f, rel_dir)
            result.append(
                ScannedEpisode(
                    filename=f,
                    path=os.path.join(root, f),
                    season=season,
                    episode=episode,
                )
            )
    return result


def sync_libraries(db: Session, libraries_root: str) -> list[Library]:
    """Upsert libraries found on disk into the DB. Stale rows are kept but
    flagged as missing rather than deleted, and un-flagged if they reappear."""
    existing = {lib.name: lib for lib in db.query(Library).all()}
    found_names = set()
    for name, path in find_libraries(libraries_root):
        found_names.add(name)
        lib = existing.get(name)
        if lib is None:
            lib = Library(name=name, path=path, missing=False)
            db.add(lib)
        else:
            lib.path = path
            lib.missing = False
    for name, lib in existing.items():
        if name not in found_names:
            lib.missing = True
    db.commit()
    return db.query(Library).order_by(Library.name).all()


def _read_tvshow_locked(show_path: str) -> bool:
    """TinyMediaManager writes a tvshow.nfo in the show's root folder; a
    <tmm_locked>true</tmm_locked> child means the show's metadata is locked
    there, which we mirror by locking cleanup/restore for it here too."""
    nfo_path = os.path.join(show_path, "tvshow.nfo")
    if not os.path.isfile(nfo_path):
        return False
    try:
        root = ET.parse(nfo_path).getroot()
    except ET.ParseError:
        return False
    locked_el = root.find("tmm_locked")
    if locked_el is None or locked_el.text is None:
        return False
    return locked_el.text.strip().lower() == "true"


def sync_shows(db: Session, library: Library, backup_db: Optional[Session] = None) -> list[Show]:
    existing = {show.name: show for show in db.query(Show).filter(Show.library_id == library.id).all()}
    found_names = set()
    for name, path in find_shows(library.path):
        found_names.add(name)
        locked = _read_tvshow_locked(path)
        show = existing.get(name)
        if show is None:
            show = Show(library_id=library.id, name=name, path=path, missing=False, locked=locked)
            db.add(show)
        else:
            show.path = path
            show.missing = False
            show.locked = locked

    # Collect show names that exist in the backup DB so we can decide what to
    # do with shows no longer on disk (mark missing vs. purge entirely).
    backed_up_show_names: set[str] = set()
    if backup_db is not None:
        from app.backup_models import BackupLibrary
        backup_lib = backup_db.query(BackupLibrary).filter(BackupLibrary.name == library.name).first()
        if backup_lib:
            backed_up_show_names = {bs.name for bs in backup_lib.shows}

    for name, show in existing.items():
        if name not in found_names:
            has_scanned = (
                db.query(Episode)
                .filter(Episode.show_id == show.id, Episode.last_scanned_at.isnot(None))
                .first()
            ) is not None
            if has_scanned or name in backed_up_show_names:
                show.missing = True
            else:
                db.delete(show)

    db.commit()
    return db.query(Show).filter(Show.library_id == library.id).order_by(Show.name).all()


def sync_episodes(db: Session, show: Show, backup_db: Optional[Session] = None) -> list[Episode]:
    existing = {ep.filename: ep for ep in db.query(Episode).filter(Episode.show_id == show.id).all()}
    found_filenames = set()
    for scanned in find_episodes(show.path):
        found_filenames.add(scanned.filename)
        ep = existing.get(scanned.filename)
        if ep is None:
            ep = Episode(
                show_id=show.id,
                filename=scanned.filename,
                path=scanned.path,
                season=scanned.season,
                episode=scanned.episode,
                missing=False,
            )
            db.add(ep)
        else:
            ep.path = scanned.path
            ep.season = scanned.season
            ep.episode = scanned.episode
            ep.missing = False

    # Collect filenames backed up for this show so we can decide what to do
    # with episodes no longer on disk (mark missing vs. purge entirely).
    backed_up_filenames: set[str] = set()
    if backup_db is not None:
        from app.backup_models import BackupLibrary, BackupShow as BackupShowModel
        library = db.get(Library, show.library_id)
        if library:
            backup_lib = backup_db.query(BackupLibrary).filter(BackupLibrary.name == library.name).first()
            if backup_lib:
                backup_show_row = (
                    backup_db.query(BackupShowModel)
                    .filter(BackupShowModel.library_id == backup_lib.id, BackupShowModel.name == show.name)
                    .first()
                )
                if backup_show_row:
                    backed_up_filenames = {be.filename for be in backup_show_row.episodes}

    for filename, ep in existing.items():
        if filename not in found_filenames:
            if ep.last_scanned_at is not None or filename in backed_up_filenames:
                ep.missing = True
            else:
                db.delete(ep)

    db.commit()
    return db.query(Episode).filter(Episode.show_id == show.id).order_by(Episode.filename).all()
