from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship

from app.cleanup_db import CleanupBase


def utcnow():
    return datetime.now(timezone.utc)


class CleanupLibrary(CleanupBase):
    __tablename__ = "cleanup_libraries"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    shows = relationship("CleanupShow", back_populates="library", cascade="all, delete-orphan")


class CleanupShow(CleanupBase):
    __tablename__ = "cleanup_shows"
    __table_args__ = (UniqueConstraint("library_id", "name", name="uq_cleanup_show_library_name"),)

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("cleanup_libraries.id"), nullable=False)
    name = Column(String, nullable=False)

    library = relationship("CleanupLibrary", back_populates="shows")
    episodes = relationship("CleanupEpisode", back_populates="show", cascade="all, delete-orphan")


class CleanupEpisode(CleanupBase):
    __tablename__ = "cleanup_episodes"
    __table_args__ = (UniqueConstraint("show_id", "filename", name="uq_cleanup_episode_show_filename"),)

    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("cleanup_shows.id"), nullable=False)
    filename = Column(String, nullable=False)
    season = Column(String, nullable=True)
    episode = Column(String, nullable=True)

    show = relationship("CleanupShow", back_populates="episodes")
    result = relationship(
        "CleanupResult", back_populates="episode", uselist=False, cascade="all, delete-orphan"
    )


class CleanupResult(CleanupBase):
    __tablename__ = "cleanup_results"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("cleanup_episodes.id"), unique=True, nullable=False)
    cleaned_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    ok = Column(Boolean, nullable=False, default=False)
    summary_json = Column(Text, nullable=False, default="[]")
    warnings_json = Column(Text, nullable=False, default="[]")
    error = Column(Text, nullable=True)

    episode = relationship("CleanupEpisode", back_populates="result")
