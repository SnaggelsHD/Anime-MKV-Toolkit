from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import CLEANUP_DB_PATH

cleanup_engine = create_engine(f"sqlite:///{CLEANUP_DB_PATH}", connect_args={"check_same_thread": False})
CleanupSessionLocal = sessionmaker(bind=cleanup_engine, autoflush=False, autocommit=False)

CleanupBase = declarative_base()


def get_cleanup_db():
    db = CleanupSessionLocal()
    try:
        yield db
    finally:
        db.close()


STEP_TOGGLE_COLUMNS = [
    "set_title",
    "clear_date",
    "clear_writing_app",
    "clear_muxing_app",
    "force_first_track_japanese",
    "set_video_default",
    "rename_audio_tracks",
    "rename_subtitle_tracks",
]


def init_cleanup_db():
    import app.cleanup_models  # noqa: F401 ensure models are registered

    CleanupBase.metadata.create_all(bind=cleanup_engine)
    _migrate_settings_columns()
    _seed_defaults()


def _migrate_settings_columns():
    """create_all() only creates missing tables, not missing columns on a
    table that already exists - a cleanup.db from before these step toggles
    existed needs them added by hand. Every toggle defaults to enabled, so
    existing installs keep behaving exactly as before until someone turns a
    step off in Settings."""
    with cleanup_engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(cleanup_settings)").fetchall()}
        for column_name in STEP_TOGGLE_COLUMNS:
            if column_name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE cleanup_settings ADD COLUMN {column_name} BOOLEAN NOT NULL DEFAULT 1")


def _seed_defaults():
    from app.cleanup_models import CleanupCodecMapping, CleanupSettings
    from app.mkv_cleanup import DEFAULT_CODEC_NAMES

    db = CleanupSessionLocal()
    try:
        if db.query(CleanupCodecMapping).count() == 0:
            for codec_key, display_name in DEFAULT_CODEC_NAMES.items():
                db.add(CleanupCodecMapping(codec_key=codec_key, display_name=display_name, is_builtin=True))
        if db.query(CleanupSettings).first() is None:
            db.add(CleanupSettings(id=1, forced_suffix="Forced", commentary_suffix="Commentary"))
        db.commit()
    finally:
        db.close()
