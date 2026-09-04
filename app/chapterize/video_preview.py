"""Builds a browser-playable mp4 proxy of a library episode so the review
step can show a real <video> with a scrub bar, without requiring every
release's native codec (HEVC, 10-bit, FLAC/AC3/DTS audio, etc.) to be
something a browser can decode directly.

Stream-copies whatever's already browser-compatible (h264 video, aac
audio) and only transcodes what isn't, so the common case (h264 video
with non-aac audio, which is most fansub/BD encodes) is fast - it's an
audio-only re-encode, not a full video re-encode. Results are cached on
disk keyed by the source file's path/size/mtime and the chosen audio
track, so scrubbing the same episode repeatedly doesn't re-run ffmpeg.
"""
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from app.chapterize.config import PREVIEW_DIR

_BROWSER_VIDEO_CODECS = {"h264"}
_BROWSER_AUDIO_CODECS = {"aac"}

# Cap re-encoded video at 720p tall; never upscale a smaller source.
_MAX_HEIGHT = 720


class PreviewError(Exception):
    pass


def _probe_first_video_codec(path: Path) -> str | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PreviewError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None
    return streams[0].get("codec_name") if streams else None


def _cache_key(path: Path, audio_index: int | None) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{audio_index}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def get_or_build_preview(mkv_path: Path, audio_index: int | None) -> Path:
    """Return a cached browser-playable mp4 for this episode, building it
    first if needed. Safe to call repeatedly; a matching cache entry short
    circuits straight to the existing file."""
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(mkv_path, audio_index)
    out_path = PREVIEW_DIR / f"{key}.mp4"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    video_codec = _probe_first_video_codec(mkv_path)
    audio_codec = None
    if audio_index is not None:
        from app.chapterize.audio_match import probe_audio_streams
        streams = probe_audio_streams(mkv_path)
        if 0 <= audio_index < len(streams):
            audio_codec = streams[audio_index].codec

    video_args = ["-c:v", "copy"] if video_codec in _BROWSER_VIDEO_CODECS else [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-vf", f"scale=-2:'min({_MAX_HEIGHT},ih)'",
    ]
    audio_args = ["-c:a", "copy"] if audio_codec in _BROWSER_AUDIO_CODECS else ["-c:a", "aac", "-b:a", "160k"]

    map_args = ["-map", "0:v:0"]
    map_args += ["-map", f"0:a:{audio_index}"] if audio_index is not None else ["-map", "0:a:0?"]

    # Unique per call so two concurrent requests for the same uncached
    # episode (e.g. two browser tabs) don't clobber each other's output.
    tmp_path = out_path.with_suffix(f".{uuid.uuid4().hex[:8]}.building.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(mkv_path),
        *map_args, *video_args, *audio_args,
        "-movflags", "+faststart",
        str(tmp_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not tmp_path.exists():
        tmp_path.unlink(missing_ok=True)
        raise PreviewError(f"ffmpeg failed to build preview: {result.stderr[-800:]}")

    tmp_path.rename(out_path)
    return out_path


def clear_cache() -> int:
    """Delete every cached preview mp4, regardless of age. Returns files removed."""
    if not PREVIEW_DIR.exists():
        return 0
    removed = 0
    for f in PREVIEW_DIR.glob("*.mp4"):
        f.unlink()
        removed += 1
    return removed
