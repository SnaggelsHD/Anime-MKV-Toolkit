import json

from sqlalchemy import Column, Float, Integer, Text

from app.chapterize.db import ChapterizeBase

DEFAULT_NAMING_SCHEMA = {
    "prologue": "Prologue",
    "opening": "Opening",
    "episode": "Episode",
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
