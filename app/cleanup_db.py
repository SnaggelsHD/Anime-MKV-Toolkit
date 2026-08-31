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


def init_cleanup_db():
    import app.cleanup_models  # noqa: F401 ensure models are registered

    CleanupBase.metadata.create_all(bind=cleanup_engine)
    _seed_defaults()


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
