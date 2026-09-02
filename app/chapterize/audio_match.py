"""Finds where a theme song (OP/ED) plays within an episode's audio track
using chroma-feature cross-correlation. This is pitch/harmony matching
(robust to the loudness/eq differences between a clean theme release and
an episode's mixed audio) rather than exact audio fingerprinting, which
is why results stay editable in the UI rather than being applied blindly.
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import correlate

SR = 22050
HOP_LENGTH = 1024

# Common ways releases tag the Japanese audio track.
_JAPANESE_LANGUAGE_CODES = {"jpn", "ja", "jp", "japanese"}


class FfmpegError(Exception):
    pass


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FfmpegError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise FfmpegError(f"ffprobe returned no duration for {path}")


@dataclass
class AudioStreamInfo:
    audio_index: int  # 0-based position among audio streams, for ffmpeg's 0:a:N map specifier
    language: str | None
    is_default: bool
    codec: str | None


def probe_audio_streams(path: Path) -> list[AudioStreamInfo]:
    """List the file's audio streams in order, with language/default/codec
    info, so the right one can be picked instead of trusting ffmpeg's
    automatic (default-flagged) stream selection."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FfmpegError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return []

    infos = []
    for i, s in enumerate(streams):
        tags = s.get("tags") or {}
        language = (tags.get("language") or tags.get("LANGUAGE") or "").strip().lower() or None
        disposition = s.get("disposition") or {}
        infos.append(AudioStreamInfo(
            audio_index=i,
            language=language,
            is_default=bool(disposition.get("default")),
            codec=s.get("codec_name"),
        ))
    return infos


def select_japanese_audio_index_from(streams: list[AudioStreamInfo]) -> int | None:
    """Pick the audio stream to analyze: the first track tagged as
    Japanese regardless of its default flag, falling back to whichever
    track is marked default, then to the first audio stream. Returns None
    if there are no audio streams."""
    if not streams:
        return None
    for s in streams:
        if s.language in _JAPANESE_LANGUAGE_CODES:
            return s.audio_index
    for s in streams:
        if s.is_default:
            return s.audio_index
    return streams[0].audio_index


def select_japanese_audio_index(path: Path) -> int | None:
    return select_japanese_audio_index_from(probe_audio_streams(path))


def extract_audio(src: Path, dest_wav: Path, audio_index: int | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if audio_index is not None:
        cmd += ["-map", f"0:a:{audio_index}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "wav", str(dest_wav)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest_wav.exists():
        raise FfmpegError(f"ffmpeg audio extraction failed for {src}: {result.stderr[-500:]}")


def load_mono(path: Path) -> np.ndarray:
    """Decode any ffmpeg-readable audio file to a mono float32 array at SR."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg decode failed for {path}: {result.stderr[-500:].decode(errors='replace')}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def chroma_features(y: np.ndarray) -> np.ndarray:
    """12 x T chroma-like feature matrix via an STFT + pitch-class folding.
    Avoids depending on librosa's chroma_cqt (slow for long episodes)."""
    import librosa

    chroma = librosa.feature.chroma_stft(y=y, sr=SR, hop_length=HOP_LENGTH, n_fft=4096)
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    norms[norms == 0] = 1.0
    return chroma / norms


@dataclass
class MatchResult:
    start: float
    end: float
    score: float


def _score_curve(episode_chroma: np.ndarray, theme_chroma: np.ndarray) -> tuple[np.ndarray, int] | tuple[None, int]:
    """Per-offset alignment score: mean cosine-similarity of theme_chroma
    against episode_chroma at every possible start offset. Both inputs are
    column-normalized, so summing per-bin valid cross-correlations gives
    the sum of frame dot-products at each offset in one FFT pass per bin."""
    t_ep = episode_chroma.shape[1]
    t_th = theme_chroma.shape[1]
    if t_th == 0 or t_ep < t_th:
        return None, t_th

    scores = np.zeros(t_ep - t_th + 1, dtype=np.float64)
    for bin_idx in range(episode_chroma.shape[0]):
        scores += correlate(episode_chroma[bin_idx], theme_chroma[bin_idx], mode="valid", method="fft")
    return scores / t_th, t_th


def find_best_match(episode_chroma: np.ndarray, theme_chroma: np.ndarray) -> MatchResult | None:
    """Slide theme_chroma over episode_chroma and return the single
    best-aligned window as (start_seconds, end_seconds, score)."""
    matches = find_all_matches(episode_chroma, theme_chroma, threshold=-1.0, max_matches=1)
    return matches[0] if matches else None


def find_all_matches(
    episode_chroma: np.ndarray, theme_chroma: np.ndarray, threshold: float = 0.0, max_matches: int = 3,
) -> list[MatchResult]:
    """Find up to max_matches distinct occurrences of theme_chroma inside
    episode_chroma, each scoring at or above threshold. Handles a theme
    appearing more than once in the same episode (e.g. an OP reused as an
    insert song) via greedy peak-picking with non-max suppression: after
    taking the best remaining peak, the window it occupies (plus one theme
    length of padding on each side) is suppressed so nearby offsets of the
    same underlying peak aren't picked again."""
    scores, t_th = _score_curve(episode_chroma, theme_chroma)
    if scores is None:
        return []

    work = scores.copy()
    results = []
    for _ in range(max_matches):
        offset = int(np.argmax(work))
        score = float(work[offset])
        if score < threshold:
            break
        start = offset * HOP_LENGTH / SR
        end = (offset + t_th) * HOP_LENGTH / SR
        results.append(MatchResult(start=start, end=end, score=score))

        lo = max(0, offset - t_th)
        hi = min(len(work), offset + t_th + 1)
        work[lo:hi] = -np.inf

    return results
