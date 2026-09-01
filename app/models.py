from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    Text,
    ForeignKey,
    UniqueConstraint,
    DateTime,
)
from sqlalchemy.orm import relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Library(Base):
    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    path = Column(String, nullable=False)
    missing = Column(Boolean, nullable=False, default=False)

    shows = relationship("Show", back_populates="library", cascade="all, delete-orphan")


class Show(Base):
    __tablename__ = "shows"
    __table_args__ = (UniqueConstraint("library_id", "name", name="uq_show_library_name"),)

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("libraries.id"), nullable=False)
    name = Column(String, nullable=False)
    path = Column(String, nullable=False)
    missing = Column(Boolean, nullable=False, default=False)
    locked = Column(Boolean, nullable=False, default=False)

    library = relationship("Library", back_populates="shows")
    episodes = relationship("Episode", back_populates="show", cascade="all, delete-orphan")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("show_id", "filename", name="uq_episode_show_filename"),)

    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("shows.id"), nullable=False)
    filename = Column(String, nullable=False)
    path = Column(String, nullable=False)
    season = Column(String, nullable=True)
    episode = Column(String, nullable=True)
    missing = Column(Boolean, nullable=False, default=False)
    last_scanned_at = Column(DateTime, nullable=True)

    show = relationship("Show", back_populates="episodes")
    chapters = relationship("Chapters", back_populates="episode", uselist=False, cascade="all, delete-orphan")
    track_metadata = relationship("TrackMetadata", back_populates="episode", uselist=False, cascade="all, delete-orphan")


class Chapters(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), unique=True, nullable=False)
    chapter_xml = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    episode = relationship("Episode", back_populates="chapters")


class TrackMetadata(Base):
    __tablename__ = "track_metadata"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), unique=True, nullable=False)
    tracks_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    episode = relationship("Episode", back_populates="track_metadata")
