# MKV Chapter & Media Info Backup

A Dockerized web app to back up and restore MKV chapter data and track metadata
(mediainfo) for an anime library. Scans your library folders, extracts embedded
chapters and track metadata for each episode, stores them in SQLite, and can
restore chapters back into the MKV files later.

Matching is done by `(show, filename)` only — files are not hashed, so this
assumes stable filenames (e.g. as produced by TinyMediaManager).

## Status

Feature-complete for the core workflow: scans libraries/shows/episodes from
disk, backs up chapters and track metadata to SQLite, restores chapters back
into MKV files, and exposes it all through a web UI. Track metadata restore
(re-applying track names/languages/flags) is not implemented — only chapter
restore, per the original scope.

## Requirements

- Docker and Docker Compose

## Build & Run

```bash
docker compose build
docker compose up -d
```

The UI will be available at [http://localhost:8000](http://localhost:8000),
and the health check at [http://localhost:8000/api/health](http://localhost:8000/api/health).

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

The SQLite database is stored under `./data` on the host (mounted to `/data`
in the container), so it persists across container restarts.

## Using the UI

Once running, open the web UI to:

- Browse libraries → shows → episodes (episodes are grouped by season where
  detectable from the filename or folder name).
- Back up chapters and track metadata for a whole library, a show, or a
  single episode, via the "Backup" buttons at each level.
- View stored chapter XML and a parsed track metadata table per episode by
  clicking an episode row.
- Restore chapters from the database back into the MKV files on disk, via
  the "Restore" buttons at each level.

Matching between what's on disk and what's in the database is done by
`(show, filename)` only — files are not hashed or checksummed. Every list
endpoint re-scans the filesystem on read, so newly added episodes show up
without a restart.

**Note on restore:** restoring rewrites the MKV file in place (remux to a
temp file in the same folder, then atomic replace). Track metadata restore
(re-applying languages/track names/flags) is not implemented yet — only
chapters are restored.

## Notes

- No file hashing/checksums — matching is filename-based, so it assumes
  stable filenames (e.g. from TinyMediaManager).
- Backup/restore operations are synchronous HTTP calls; a large library
  backup may take a while to return.
- Logs (including per-episode backup/restore success and failure) go to
  stdout — view them with `docker compose logs -f`.
