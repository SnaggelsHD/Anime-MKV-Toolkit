# Anime MKV Toolkit

A Dockerized web app for managing an anime MKV library: back up and restore
chapter data and track metadata (mediainfo) using a two-phase
**scan → backup** workflow so backed-up data survives even if the source
files are later moved, renamed, or temporarily missing — clean up noisy
track/container metadata on the files themselves, and detect and write in
Prologue/Opening/Episode/Ending/Epilogue chapters automatically by matching
episode audio against animethemes.moe, all from one web UI.

Matching is done by `(library, show, filename)` only — files are not hashed,
so this assumes stable filenames (e.g. as produced by TinyMediaManager).

## Status

Feature-complete for the core workflow: scan extracts chapters and mediainfo
from disk into a scan database, backup copies already-scanned data into a
separate backup database, and restore writes backed-up chapters back into the
MKV files. All three (plus clearing backup data) work at the episode, season,
show, library, or all-libraries level, through a web UI with live progress and
dark mode. Track metadata restore (re-applying track names/languages/flags) is
not implemented — only chapter restore, per the original scope. The chapter
analyzer (merged in from a companion project, see below) is also
feature-complete: search, theme matching, review/edit, video preview, and
save all work end-to-end.

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
- `chapterize.db` — the chapter analyzer's settings (naming schema, match
  threshold, animethemes.moe cache TTL). Override with `CHAPTERIZE_DB_PATH`
  (defaults to `/data/chapterize.db`). Its animethemes.moe theme-audio cache
  and video-preview cache live under `/data/chapterize/` (override the root
  with `CHAPTERIZE_CACHE_DIR`), safe to delete anytime since both are
  rebuilt on demand.

## Using the UI

The Library tab is a library switcher (top right, next to dark mode) plus a
two-column layout, capped at 80% of the window width so there's a margin on
both sides: a flat list of shows for the selected library on the left, and a
drill-down detail panel on the right. Clicking a show opens its overview
there (poster, counts, its own actions, and its seasons/episodes); clicking
one of those episodes replaces that same panel with the full episode view
(chapters and track metadata side by side, timestamps, actions), with a
"‹ Back to \<show\>" link at the top to return to the show's season/episode
list. Show rows in the left list carry no buttons of their own since
clicking one always opens that action set in the detail panel; season and
library rows keep Scan/Backup visible with everything else (Clean, Dry Run,
Clear) behind a "☰" menu, and every button is labeled with its scope (e.g.
"Scan Show", "Backup Episode") so it's clear what it acts on, and repeats
with "Again" once already done once (e.g. "Scan Season Again", "Clean
Episode Again"). The show detail view (the panel a show row opens) and
the episode detail view are the two exceptions to the "☰" menu pattern:
all of their actions sit in one flat row instead.

The workflow is **scan, then backup, then (optionally, later) restore**:

1. **Scan** a library, show, season, or episode (or "Scan all libraries" in
   Settings) to extract chapters and the full mediainfo report from the MKV
   files currently on disk into the scan database. This also records a
   per-episode "last scanned" timestamp and detects anything that's gone
   missing since the last scan.
2. **Backup** copies already-scanned data into the separate backup database.
   You can only back up an episode after it's been scanned at least once —
   backing up an unscanned episode fails with a clear error, and the "Backup
   Episode" button in the episode detail view is disabled until it's
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
- Clicking a show shows its overview, seasons, and episodes in the
  right-hand detail panel. Clicking an episode there replaces that panel
  with its chapters and track metadata side by side (each toggles between a
  parsed table and the raw stored data — chapter XML, or the complete
  mediainfo JSON report), along with its last-scanned and backed-up
  timestamps, and a back link to return to the show. The "×" in the
  panel's corner clears the selection back to a placeholder.
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
- A show whose `tvshow.nfo` (as written by TinyMediaManager) contains
  `<tmm_locked>true</tmm_locked>` shows a 🔒 next to its name and has its
  **Restore**, **Clean**, and **Dry Run** buttons disabled everywhere they
  appear (show, season, and episode level), so a locally-locked show's
  metadata can't accidentally be touched. Scan, Backup, and Clear backup
  stay available. The lock state is re-read from `tvshow.nfo` on disk every
  time the show list loads, not just on an explicit scan, so toggling it in
  TinyMediaManager is picked up on the next reload. A library where every
  show is locked gets the same 🔒 next to its name, with its own
  Restore/Clean/Dry Run Library buttons (in its "☰" menu) disabled too.
  A library's "☰" menu also has a **Hide Locked Shows**/**Show Locked
  Shows** toggle to filter locked shows out of its show list; the choice
  is remembered in the browser (`localStorage`), not per library.
- An **Analyze Chapters** button (in a season's "☰" menu, and in the flat
  action row of the episode detail view) opens the chapter analyzer
  screen pre-loaded with that season's or episode's file(s) - see
  "Chapter analyzer" below.

Matching between what's on disk, the scan database, and the backup database is
done by `(library name, show name, filename)` only — files are not hashed or
checksummed.

**Note on restore:** restoring rewrites the MKV file in place (remux to a
temp file in the same folder, then atomic replace). Track metadata restore
(re-applying languages/track names/flags) is not implemented yet — only
chapters are restored.

## Metadata cleanup

A separate feature from scan/backup/restore, with its own database
(`cleanup.db`), surfaced through a **Clean** button (**Clean Again** once
already cleaned) alongside the Scan/Backup/Restore buttons at every level
of the Library tab (library, show, season, episode). It normalizes track
languages/names and strips noisy
container metadata from anime MKV rips — the same rules a prior standalone
`mkv_cleanup.py` script applied, now built into the app:

- Sets the container title to the filename, and clears the `date`,
  `writing-application`, and `muxing-application` tags.
- Forces the **first track** in the file to `language=jpn` and clears its
  name (matches the original script's behavior for typical Japanese-audio-
  first anime rips — this applies regardless of that track's actual type).
- Sets every video track's default flag.
- Picks exactly one audio track per file to be the default, chosen by
  language priority (configurable, see below) rather than whatever the
  source rip happened to ship with; every other audio track's default flag
  is explicitly cleared.
- Renames every audio track to `"<Language> [Commentary] <Codec> <Channels>"`
  (e.g. `"Japanese AAC 2.0"`, `"English Commentary Dolby Digital 5.1"`) and
  every subtitle track to `"<Language> [Commentary][ Forced]"`.
- Flags unrecognized audio codecs as warnings without failing the cleanup.

Cleanup (and restore) is refused for any show locked via `tvshow.nfo` (see
"Using the UI" above), and this is enforced at the API level, not just in
the button state, so a library- or all-libraries-wide clean/restore
silently skips a locked show's episodes (reported as a failed result with
an explanatory error) instead of failing the whole run.

Each of the steps above can be turned on or off independently from
**Settings → Cleanup — Steps**. A disabled step contributes no edit and no
line in the Dry Run preview — for example, turning off "Force the first
track to Japanese and clear its name" stops that quirky first-track rule
without affecting audio/subtitle renaming, and turning off "Rename audio
tracks" leaves audio track names untouched while everything else still runs.

The default-audio language priority is configurable from **Settings →
Cleanup Default Audio Track**: an ordered list of language codes (German,
Japanese, English by default) that you can reorder, extend, or shrink. The
first entry with a matching audio track in a given file wins; if nothing
matches (or the list is empty), the first audio track in the file is used.
Changes apply on the next cleanup run, same as every other setting here.

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
library; each already-cleaned scope shows a **Clean Again** button that
confirms before overwriting. Every scope also has a **Dry Run** button
that runs the same inspection but skips the `mkvpropedit` step entirely, so no file is
touched — it opens a popup listing exactly what each field would change to
(and any warnings), for previewing before committing to a real cleanup. Dry
runs never need confirmation and never affect the cleaned/scanned indicators
or timestamps, since nothing on disk or in either database actually changes.
Episode rows show a third **cleaned** indicator alongside **scanned** and
**backed up**, and the episode detail view shows the
last-cleaned timestamp plus any error inline.

## Track flags (default/forced editor)

The episode detail view's scanned Track Metadata table doubles as a manual
editor for each track's **Default** and **Forced** flags, for a quick fix
that doesn't need a full cleanup run. When the show isn't locked (see
"Using the UI" above), those two columns are checkboxes instead of plain
yes/no text; toggle any of them and:

- **Save** writes just those flags to the file on disk via `mkvpropedit`
  (nothing else - no titles, tags, or track names touched) and re-scans the
  episode immediately, the same way Clean does, so the table reflects the
  new state right away.
- **Cancel** resets the checkboxes back to what the table loaded with. This
  is purely client-side - no request is sent.
- **Save for Season** writes the same flag values to every other episode in
  the current season whose tracks are "the same" as this one (same number
  of tracks, same type and language in the same order - the flag values
  themselves aren't part of that comparison, since applying them is the
  whole point). An episode with a different track layout is skipped rather
  than guessed at, and shows up as a failed result (with an explanatory
  reason) in the task queue alongside the episodes it did update.

Like Clean/Restore, editing track flags is refused for a locked show, both
in the button/checkbox state and at the API level.

## Chapter analyzer

Merged in from a companion project (Anime Chapterizer), this detects
Prologue/Opening/Episode/Ending/Epilogue chapters by matching each
episode's audio against the actual OP/ED theme songs from
[animethemes.moe](https://animethemes.moe) (chroma cross-correlation, not
exact audio fingerprinting), and writes them in via `mkvpropedit` - no
re-encoding. A final **End** marker is always added at the file's exact
last timestamp too.

Reached via the **Analyze Chapters** button on a season or an episode
(there's no separate library browser for it - it reuses whatever
show/season/episode you already picked in the Library tab):

1. The pre-selected episode list shows each file with its parsed episode
   number, editable if the parser got it wrong (it drives `{episode}` in
   chapter titles, and per-episode OP/ED assignment in that recognition
   mode).
2. Search animethemes.moe for the show and pick which OP/ED themes to
   match against (a show can have more than one across cours - pick
   whichever apply to the episodes you're analyzing); theme audio is
   downloaded once per show and cached.
3. Pick a recognition mode: **Match all themes** tries every selected
   theme against every episode; **Use per-episode OP/ED assignment**
   looks up which theme animethemes.moe assigns to each episode and tries
   that first, only falling back to the others if nothing matches. Either
   way, a match is classified as an opening or ending by *where* it lands
   in the episode (first half vs. second half), not by which tag
   animethemes.moe gives it - so a theme reused as that episode's ending
   still ends up in the Ending chapter. A theme matching more than once
   (e.g. an OP reused as an insert song) shows all candidates so you can
   pick the right one.
4. **Start analysis** shows live progress and a log as each episode's
   audio is extracted and compared, with a Cancel button; a concurrency
   limit queues extra analyses instead of piling every job's audio work
   onto the CPU at once.
5. Review the results: each episode shows its **existing chapters**
   (read straight from the file, for comparison) next to the newly
   detected ones. Edit any start/end time, title, or type, remove a
   chapter, or add a custom one; a **Preview** button loads a scrubbable
   video for that episode with a jump button per chapter (color-coded by
   type, reflecting your current edits).
6. **Save chapters to MKV files** writes them in. There's no separate
   backup step here - this app's own scan/backup/restore already covers
   undoing a bad save, so back up the episode first if you want that
   safety net.

Chapter naming is configurable per type (Prologue/Opening/Ending/Epilogue/
End) from **Settings → Chapter Analyzer Naming Schema**, with two
placeholders: `{episode}` (the episode number) and `{n}` (the chapter's
occurrence number within its type for that episode). **Episode** isn't
in that list - its title is always plain "Episode", not configurable and
never numbered, since the episode number is already shown everywhere else
(filename, episode list, etc.). The **End** chapter is never detected -
it's always the zero-length marker at the file's exact last timestamp.
**Settings → Chapter Analyzer** also has the match
confidence threshold (results below it are treated as "no match", falling
back to a single Episode chapter spanning the whole file) and the
animethemes.moe cache TTL; **Settings → Chapter Analyzer Cache** can force
a clean re-download of everything.

Like Clean/Restore/track-flag editing, saving chapters is refused for a
locked show (the **Analyze Chapters** button itself is disabled for one);
analysis itself is read-only so it isn't blocked, only the save.

## Notes

- No file hashing/checksums — matching is filename-based, so it assumes
  stable filenames (e.g. from TinyMediaManager).
- Scan/backup/restore operations run as background jobs (see the task queue
  in the UI); a large library scan or backup may take a while to finish.
- Logs (including per-episode scan/backup/restore success and failure) go to
  stdout — view them with `docker compose logs -f`.
- The chapter analyzer adds `ffmpeg` and Python audio-analysis libraries
  (`numpy`/`scipy`/`librosa`/`soundfile`) to the image, so a build takes
  noticeably longer and the image is noticeably larger than before it was
  merged in. Its own analysis jobs (with live log/progress streaming) run
  independently of the scan/backup/restore/cleanup task queue.
