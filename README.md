# MKV Chapter & Media Info Backup

A Dockerized web app to back up and restore MKV chapter data and track metadata
(mediainfo) for an anime library. Scans your library folders, extracts embedded
chapters and track metadata for each episode, stores them in SQLite, and can
restore chapters back into the MKV files later.

Matching is done by `(show, filename)` only — files are not hashed, so this
assumes stable filenames (e.g. as produced by TinyMediaManager).

## Status

Project skeleton (Milestone 1): FastAPI backend with a health endpoint,
running in Docker. Library scanning, database, and backup/restore are not
implemented yet.

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

- Browse libraries → shows → episodes.
- Back up chapters and track metadata for a whole library, a show, or a
  single episode.
- View stored chapter data and track metadata per episode.
- Restore chapters from the database back into the MKV files on disk.

(UI and these operations are implemented in later milestones — see `AGENTS.md`
for the full plan.)
