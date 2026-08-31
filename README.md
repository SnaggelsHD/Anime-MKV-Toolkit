# Anime MKV Toolkit

A Dockerized web app for managing an anime MKV library: back up and restore
chapter data and track metadata (mediainfo) using a two-phase
**scan → backup** workflow so backed-up data survives even if the source
files are later moved, renamed, or temporarily missing — and clean up noisy
track/container metadata on the files themselves, all from one web UI.

Matching is done by `(library, show, filename)` only — files are not hashed,
so this assumes stable filenames (e.g. as produced by TinyMediaManager).

## Status

Feature-complete for the core workflow: scan extracts chapters and mediainfo
from disk into a scan database, backup copies already-scanned data into a
separate backup database, and restore writes backed-up chapters back into the
MKV files. All three (plus clearing backup data) work at the episode, season,
show, library, or all-libraries level, through a web UI with live progress and
dark mode. Track metadata restore (re-applying track names/languages/flags) is
not implemented — only chapter restore, per the original scope.

## Requirements

- Docker and Docker Compose

## Build & Run

```bash
docker compose build
docker compose up -d
```

The UI will be available at [http://localhost:8077](http://localhost:8077),
and the health check at [http://localhost:8077/api/health](http://localhost:8077/api/health).

## Running the prebuilt image (e.g. on a NAS)

Every push to `main` and every `vX.Y.Z` tag is built and published to GHCR by
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
as `ghcr.io/snaggelshd/anime-mkv-toolkit` (tagged `latest`, `vX.Y.Z`, `vX.Y`,
and a `sha-<short-sha>` for traceability) — amd64 only. No source checkout or
build step is needed on the machine that runs it: copy
[`docker-compose.ghcr.yml`](docker-compose.ghcr.yml) to your NAS, point the
`/libraries` volume at your actual media root, then:

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

Re-run `pull` followed by `up -d` to update to the latest published image.

The image is public — `docker compose pull` on the NAS works with no login.
(If GitHub ever defaults a future package to private, fix it once from
`github.com/SnaggelsHD?tab=packages` → `anime-mkv-toolkit` → **Package
settings** → visibility → **Public**.)

## Mounting your libraries

Edit `docker-compose.yml` and point the `/libraries` volume at your actual
media root:

```yaml
volumes:
  - ./data:/data
  - /path/to/your/libraries:/libraries
```

Each top-level folder under `/libraries` is treated as a **library**
(e.g. `/libraries/Anime`), each subfolder as a **show**
(e.g. `/libraries/Anime/Some Anime Show`), and each `.mkv` file inside a show
folder (optionally under season subfolders) as an **episode**.

Two separate SQLite databases live under `./data` on the host (mounted to
`/data` in the container), so both persist across container restarts:

- `chapters.db` — the **scan database**: library/show/episode structure plus
  whatever chapters/mediainfo were last scanned from disk, each episode's last
  scan timestamp, and `missing` flags for anything that used to be found but
  no longer is.
- `backup.db` — the **backup database**: a durable archive of scanned data
  you've explicitly chosen to keep, independent of the scan database. This is
  what restore reads from, and what "Export backup database" in Settings
  downloads. Override its path with the `BACKUP_DB_PATH` environment variable
  if needed (defaults to `/data/backup.db`).
- `cleanup.db` — tracks metadata cleanup results (see below), completely
  independent of the other two. Override with `CLEANUP_DB_PATH` (defaults to
  `/data/cleanup.db`).

## Using the UI

The workflow is **scan, then backup, then (optionally, later) restore**:

1. **Scan** a library, show, season, or episode (or "Scan all libraries" in
   Settings) to extract chapters and the full mediainfo report from the MKV
   files currently on disk into the scan database. This also records a
   per-episode "last scanned" timestamp and detects anything that's gone
   missing since the last scan.
2. **Backup** copies already-scanned data into the separate backup database.
   You can only back up an episode after it's been scanned at least once —
   backing up an unscanned episode fails with a clear error, and the "Backup
   this episode" button in the episode detail view is disabled until it's
   been scanned.
3. **Restore** writes the chapters from the **backup** database back into the
   MKV file on disk. This works even if the file was temporarily missing and
   has since reappeared — the backup database isn't affected by files
   disappearing or reappearing on disk, only by explicit backup/clear actions.

Additional UI notes:

- Each library, show, season, and episode shows how many of its episodes are
  scanned and backed up. A show or episode that used to exist on disk but
  doesn't anymore is flagged **MISSING** rather than removed — rescan it once
  the files are back to clear the flag.
- Clicking an episode row shows its chapters and track metadata (each toggles
  between a parsed table and the raw stored data — chapter XML, or the
  complete mediainfo JSON report), along with its last-scanned and backed-up
  timestamps.
- **Clearing** data (per episode/season/show, or the whole database, from
  Settings) only removes it from the **backup** database — the scan database
  is never touched, so you can immediately re-backup from what's already been
  scanned.
- **Settings → Export** downloads the backup database file (`backup.db`)
  directly, for safekeeping outside the container.
- Long-running scan/backup/restore operations show live progress in a task
  queue widget in the bottom-right corner rather than blocking the UI.
- Show and season rows display the poster artwork already sitting in those
  folders — `poster.<ext>` directly inside the show folder, and `poster.<ext>`
  (or `season<NN>-poster.<ext>`) inside each season folder
  (`.jpg`/`.jpeg`/`.png`/`.webp` all work). Served straight off disk, nothing
  is stored in a database; a placeholder icon is shown wherever no matching
  file is found.

Matching between what's on disk, the scan database, and the backup database is
done by `(library name, show name, filename)` only — files are not hashed or
checksummed.

**Note on restore:** restoring rewrites the MKV file in place (remux to a
temp file in the same folder, then atomic replace). Track metadata restore
(re-applying languages/track names/flags) is not implemented yet — only
chapters are restored.

## Metadata cleanup

A separate feature from scan/backup/restore, with its own database
(`cleanup.db`), surfaced through **Clean**/**Re-clean** buttons alongside the
Scan/Backup/Restore buttons at every level of the Library tab (library, show,
season, episode). It normalizes track languages/names and strips noisy
container metadata from anime MKV rips — the same rules a prior standalone
`mkv_cleanup.py` script applied, now built into the app:

- Sets the container title to the filename, and clears the `date`,
  `writing-application`, and `muxing-application` tags.
- Forces the **first track** in the file to `language=jpn` and clears its
  name (matches the original script's behavior for typical Japanese-audio-
  first anime rips — this applies regardless of that track's actual type).
- Sets every video track's default flag.
- Renames every audio track to `"<Language> [Commentary] <Codec> <Channels>"`
  (e.g. `"Japanese AAC 2.0"`, `"English Commentary Dolby Digital 5.1"`) and
  every subtitle track to `"<Language> [Commentary][ Forced]"`.
- Flags unrecognized audio codecs as warnings without failing the cleanup.

Each of the steps above can be turned on or off independently from
**Settings → Cleanup — Steps**. A disabled step contributes no edit and no
line in the Dry Run preview — for example, turning off "Force the first
track to Japanese and clear its name" stops that quirky first-track rule
without affecting audio/subtitle renaming, and turning off "Rename audio
tracks" leaves audio track names untouched while everything else still runs.

The audio codec display names and the subtitle "Forced"/"Commentary" suffixes
are configurable from **Settings → Cleanup**. Built-in codec entries (the ones
from the original script) can have their display name edited but not their
codec identifier, and can't be deleted; entries you add yourself are fully
editable and deletable. Changes (including the step toggles) take effect on
the next cleanup run — no restart needed.

Cleanup works directly on the MKV file via `mkvpropedit` (fast in-place
metadata edit, no remux) and doesn't require the episode to have been scanned
first, though it does trigger an automatic rescan immediately afterward so
the scan database (and the episode detail view's Track Metadata table) stays
in sync with the file's new track names/languages — no manual rescan needed.
It never touches the backup database. Run it per episode, season, show, or
library; each already-cleaned scope shows a **Re-clean** button that confirms
before overwriting. Every scope also has a **Dry Run** button that runs the
same inspection but skips the `mkvpropedit` step entirely, so no file is
touched — it opens a popup listing exactly what each field would change to
(and any warnings), for previewing before committing to a real cleanup. Dry
runs never need confirmation and never affect the cleaned/scanned indicators
or timestamps, since nothing on disk or in either database actually changes.
Episode rows show a third **cleaned** indicator alongside **scanned** and
**backed up**, and the episode detail view shows the
last-cleaned timestamp plus any error inline.

## Notes

- No file hashing/checksums — matching is filename-based, so it assumes
  stable filenames (e.g. from TinyMediaManager).
- Scan/backup/restore operations run as background jobs (see the task queue
  in the UI); a large library scan or backup may take a while to finish.
- Logs (including per-episode scan/backup/restore success and failure) go to
  stdout — view them with `docker compose logs -f`.
