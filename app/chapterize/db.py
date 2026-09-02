import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.chapterize.config import CHAPTERIZE_DB_PATH

chapterize_engine = create_engine(f"sqlite:///{CHAPTERIZE_DB_PATH}", connect_args={"check_same_thread": False})
ChapterizeSessionLocal = sessionmaker(bind=chapterize_engine, autoflush=False, autocommit=False)

ChapterizeBase = declarative_base()


def get_chapterize_db():
    db = ChapterizeSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_chapterize_db():
    import app.chapterize.models  # noqa: F401 ensure models are registered

    ChapterizeBase.metadata.create_all(bind=chapterize_engine)
    _seed_defaults()


def _seed_defaults():
    from app.chapterize.models import ChapterizeSettings, DEFAULT_NAMING_SCHEMA

    db = ChapterizeSessionLocal()
    try:
        if db.query(ChapterizeSettings).first() is None:
            db.add(ChapterizeSettings(id=1, naming_schema_json=json.dumps(DEFAULT_NAMING_SCHEMA)))
            db.commit()
    finally:
        db.close()


def load_settings() -> dict:
    """Convenience accessor for non-request contexts (the analysis job
    thread, the animethemes cache module) that don't have a FastAPI
    dependency-injected session."""
    db = ChapterizeSessionLocal()
    try:
        from app.chapterize.models import ChapterizeSettings, DEFAULT_NAMING_SCHEMA

        row = db.query(ChapterizeSettings).first()
        if row is None:
            return {
                "naming_schema": dict(DEFAULT_NAMING_SCHEMA),
                "match_threshold": 0.8,
                "animethemes_cache_ttl_days": 30,
            }
        try:
            naming_schema = json.loads(row.naming_schema_json)
        except (json.JSONDecodeError, TypeError):
            naming_schema = dict(DEFAULT_NAMING_SCHEMA)
        return {
            "naming_schema": naming_schema,
            "match_threshold": row.match_threshold,
            "animethemes_cache_ttl_days": row.animethemes_cache_ttl_days,
        }
    finally:
        db.close()
