"""Client for animethemes.moe: search anime, list OP/ED themes, and cache
their theme audio locally so it can be matched against episode audio.

The AnimeThemes API is a JSON:API variant that wraps top-level resources
under a key named after the resource (e.g. {"anime": [...]}) rather than
the standard "data" key, and single-resource responses use the singular
form (e.g. {"anime": {...}}). Responses are parsed defensively (falling
back to "data") since this client can't be exercised against the live API
from this sandbox (its domains are not reachable here).
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.chapterize.config import CACHE_DIR
from app.chapterize.db import load_settings

logger = logging.getLogger("chapterize.animethemes")

API_BASE = "https://api.animethemes.moe"
AUDIO_HOST = "https://a.animethemes.moe"
VIDEO_HOST = "https://v.animethemes.moe"

_TIMEOUT = httpx.Timeout(20.0, read=60.0)


def parse_episode_ranges(text: str | None) -> list[tuple[int, int]]:
    """Parse an animethemeentries "episodes" string like "1-12", "5", or
    "1-12,15" into a list of inclusive (start, end) integer ranges. Any
    non-numeric fragments (e.g. "NC", "unknown") are ignored."""
    if not text:
        return []
    ranges = []
    for part in re.split(r"[,;]", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            ranges.append((min(a, b), max(a, b)))
            continue
        m = re.match(r"^(\d+)$", part)
        if m:
            n = int(m.group(1))
            ranges.append((n, n))
    return ranges


def _format_episode_ranges(ranges: list[tuple[int, int]]) -> str | None:
    if not ranges:
        return None
    parts = [f"{a}-{b}" if a != b else str(a) for a, b in ranges]
    return ",".join(parts)


def _slugify(text: str) -> str:
    keep = [c if c.isalnum() else "-" for c in text.lower()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "item"


def _unwrap_list(payload: dict, *keys: str) -> list:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, list):
            return val
    data = payload.get("data")
    if isinstance(data, list):
        return data
    return []


def _unwrap_obj(payload: dict, *keys: str) -> dict:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, dict):
            return val
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return {}


@dataclass
class AnimeResult:
    id: int
    name: str
    slug: str
    year: int | None
    season: str | None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "slug": self.slug, "year": self.year, "season": self.season}


@dataclass
class ThemeVideo:
    basename: str
    audio_basename: str | None

    def audio_url(self) -> str:
        if self.audio_basename:
            return f"{AUDIO_HOST}/{self.audio_basename}"
        return f"{VIDEO_HOST}/{self.basename}"


@dataclass
class Theme:
    id: int
    slug: str  # e.g. "OP1", "ED2"
    type: str  # "OP" or "ED"
    sequence: int | None
    song_title: str | None
    episode_ranges: list[tuple[int, int]] = field(default_factory=list)
    video: ThemeVideo | None = None

    def applies_to_episode(self, episode_number: int) -> bool:
        """True if this theme is known to cover the given episode. An
        unknown/unspecified range (no entries carried an episodes string)
        is treated as applying to every episode, since that's how
        single-entry themes without a restriction are normally used."""
        if not self.episode_ranges:
            return True
        return any(a <= episode_number <= b for a, b in self.episode_ranges)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "type": self.type,
            "sequence": self.sequence,
            "song_title": self.song_title,
            "episodes": _format_episode_ranges(self.episode_ranges),
            "has_video": self.video is not None,
        }


async def search_anime(query: str, limit: int = 12) -> list[AnimeResult]:
    """Uses the global /search endpoint (built for free-text lookup) rather
    than filtering the /anime index: it takes a plain query string instead
    of a filter/sort combination that has to match the API's validation
    exactly, and ranks by relevance instead of alphabetically."""
    query = query.strip()
    if not query:
        return []
    params = {"q": query, "page[limit]": str(limit)}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}/search", params=params)
        resp.raise_for_status()
        payload = resp.json()

    search_results = _unwrap_obj(payload, "search")
    items = search_results.get("anime") or _unwrap_list(payload, "anime")
    results = []
    for item in items:
        results.append(
            AnimeResult(
                id=item.get("id"),
                name=item.get("name", "?"),
                slug=item.get("slug", ""),
                year=item.get("year"),
                season=item.get("season"),
            )
        )
    return results


async def get_themes(anime_slug: str) -> list[Theme]:
    params = {"include": "animethemes.song,animethemes.animethemeentries.videos.audio"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{API_BASE}/anime/{anime_slug}", params=params)
        resp.raise_for_status()
        payload = resp.json()

    anime = _unwrap_obj(payload, "anime")
    raw_themes = anime.get("animethemes", []) or []

    themes = []
    for rt in raw_themes:
        song = rt.get("song") or {}
        entries = rt.get("animethemeentries", []) or []
        video = None
        episode_ranges: list[tuple[int, int]] = []
        for entry in entries:
            episode_ranges.extend(parse_episode_ranges(entry.get("episodes")))
            if video is None:
                videos = entry.get("videos", []) or []
                if videos:
                    v = videos[0]
                    audio = v.get("audio") or {}
                    video = ThemeVideo(basename=v.get("basename", ""), audio_basename=audio.get("basename"))
        themes.append(
            Theme(
                id=rt.get("id"),
                slug=rt.get("slug", ""),
                type=rt.get("type", "OP"),
                sequence=rt.get("sequence"),
                song_title=song.get("title"),
                episode_ranges=episode_ranges,
                video=video,
            )
        )
    return themes


def _cache_paths(anime_slug: str, theme_slug: str) -> tuple[Path, Path]:
    d = CACHE_DIR / _slugify(anime_slug)
    d.mkdir(parents=True, exist_ok=True)
    audio_path = d / f"{_slugify(theme_slug)}.wav"
    meta_path = d / f"{_slugify(theme_slug)}.json"
    return audio_path, meta_path


def get_cached_theme_audio(anime_slug: str, theme_slug: str) -> Path | None:
    """Return the cached wav path if present and not expired, else None."""
    audio_path, meta_path = _cache_paths(anime_slug, theme_slug)
    if not audio_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    ttl_days = load_settings().get("animethemes_cache_ttl_days", 30)
    age_days = (time.time() - meta.get("cached_at", 0)) / 86400
    if age_days > ttl_days:
        return None
    return audio_path


def prune_expired_cache(ttl_days: int | None = None) -> int:
    """Proactively delete cached theme audio past the TTL instead of just
    skipping it lazily on next read, so disk usage doesn't just grow
    forever for shows no longer being analyzed. Returns files removed."""
    if not CACHE_DIR.exists():
        return 0
    if ttl_days is None:
        ttl_days = load_settings().get("animethemes_cache_ttl_days", 30)
    if ttl_days <= 0:
        return 0
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for meta_path in CACHE_DIR.glob("*/*.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("cached_at", 0) < cutoff:
            audio_path = meta_path.with_suffix(".wav")
            meta_path.unlink(missing_ok=True)
            audio_path.unlink(missing_ok=True)
            removed += 1
    return removed


def clear_cache() -> int:
    """Delete every cached theme, regardless of age. Returns files removed."""
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    for f in CACHE_DIR.glob("*/*"):
        if f.is_file():
            f.unlink()
            removed += 1
    for d in CACHE_DIR.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return removed


async def download_and_cache_theme(anime_slug: str, theme: Theme, log=lambda msg: None) -> Path:
    """Download a theme's audio (or extract it from video) and cache it as
    a mono 22.05kHz wav for fingerprint matching. Returns the cached path."""
    cached = get_cached_theme_audio(anime_slug, theme.slug)
    if cached:
        log(f"Using cached theme audio for {theme.slug}")
        return cached

    if not theme.video:
        raise ValueError(f"No video/audio source available for theme {theme.slug}")

    audio_path, meta_path = _cache_paths(anime_slug, theme.slug)
    url = theme.video.audio_url()
    log(f"Downloading {theme.slug} ({theme.song_title or 'unknown song'}) from {url}")

    raw_path = audio_path.with_suffix(".src")
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(raw_path, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

    import subprocess

    log(f"Converting {theme.slug} to matching format")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw_path),
            "-vn", "-ac", "1", "-ar", "22050", "-f", "wav", str(audio_path),
        ],
        capture_output=True, text=True,
    )
    raw_path.unlink(missing_ok=True)
    if result.returncode != 0 or not audio_path.exists():
        raise RuntimeError(f"ffmpeg failed to convert theme audio for {theme.slug}: {result.stderr[-500:]}")

    meta_path.write_text(json.dumps({
        "anime_slug": anime_slug,
        "theme_slug": theme.slug,
        "type": theme.type,
        "song_title": theme.song_title,
        "source_url": url,
        "cached_at": time.time(),
    }))
    log(f"Cached theme audio for {theme.slug}")
    return audio_path
