from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship

from app.backup_db import BackupBase


def utcnow():
    return datetime.now(timezone.utc)


class BackupLibrary(BackupBase):
    __tablename__ = "backup_libraries"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    shows = relationship("BackupShow", back_populates="library", cascade="all, delete-orphan")


class BackupShow(BackupBase):
    __tablename__ = "backup_shows"
    __table_args__ = (UniqueConstraint("library_id", "name", name="uq_backup_show_library_name"),)

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("backup_libraries.id"), nullable=False)
    name = Column(String, nullable=False)

    library = relationship("BackupLibrary", back_populates="shows")
    episodes = relationship("BackupEpisode", back_populates="show", cascade="all, delete-orphan")


class BackupEpisode(BackupBase):
    __tablename__ = "backup_episodes"
    __table_args__ = (UniqueConstraint("show_id", "filename", name="uq_backup_episode_show_filename"),)

    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("backup_shows.id"), nullable=False)
    filename = Column(String, nullable=False)
    season = Column(String, nullable=True)
    episode = Column(String, nullable=True)

    show = relationship("BackupShow", back_populates="episodes")
    chapters = relationship(
        "BackupChapters", back_populates="episode", uselist=False, cascade="all, delete-orphan"
    )
    track_metadata = relationship(
        "BackupTrackMetadata", back_populates="episode", uselist=False, cascade="all, delete-orphan"
    )


class BackupChapters(BackupBase):
    __tablename__ = "backup_chapters"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("backup_episodes.id"), unique=True, nullable=False)
    chapter_xml = Column(Text, nullable=False)
    backed_up_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    episode = relationship("BackupEpisode", back_populates="chapters")


class BackupTrackMetadata(BackupBase):
    __tablename__ = "backup_track_metadata"

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey("backup_episodes.id"), unique=True, nullable=False)
    tracks_json = Column(Text, nullable=False)
    backed_up_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    episode = relationship("BackupEpisode", back_populates="track_metadata")
