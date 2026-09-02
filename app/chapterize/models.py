import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, Text

from app.chapterize.db import ChapterizeBase


def utcnow():
    return datetime.now(timezone.utc)

DEFAULT_NAMING_SCHEMA = {
    "prologue": "Prologue",
    "opening": "Opening",
    "ending": "Ending",
    "epilogue": "Epilogue",
    "end": "End",
}


class ChapterizeSettings(ChapterizeBase):
    """Singleton row (id=1) holding the chapter analyzer's settings."""

    __tablename__ = "chapterize_settings"

    id = Column(Integer, primary_key=True)
    naming_schema_json = Column(Text, nullable=False, default=json.dumps(DEFAULT_NAMING_SCHEMA))
    # real OP/ED matches (chroma cosine similarity) have been observed scoring
    # in the high 90s, while false positives stay under 0.7 - 0.8 leaves a
    # comfortable margin on both sides.
    match_threshold = Column(Float, nullable=False, default=0.8)
    animethemes_cache_ttl_days = Column(Integer, nullable=False, default=30)


class ChapterizeResult(ChapterizeBase):
    """One row per Toolkit episode (by id) that a "Save chapters to MKV
    files" actually wrote to (or attempted and failed on) - not written for
    an episode that was skipped without an attempt (locked show, analysis
    error). Keyed by the Toolkit's own Episode.id rather than mirroring
    library/show/episode names like cleanup_models.py does, since this
    feature has no independent existence from the Toolkit's own scan DB."""

    __tablename__ = "chapterize_results"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, unique=True, nullable=False, index=True)
    analyzed_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    ok = Column(Boolean, nullable=False, default=False)
    error = Column(Text, nullable=True)
