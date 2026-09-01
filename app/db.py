from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import app.models  # noqa: F401 ensure models are registered

    Base.metadata.create_all(bind=engine)
    _migrate_schema()


def _migrate_schema():
    """Add columns introduced after a table already existed. SQLite supports
    ALTER TABLE ADD COLUMN directly; this is idempotent (checks first)."""
    migrations = {
        "libraries": [("missing", "BOOLEAN NOT NULL DEFAULT 0")],
        "shows": [("missing", "BOOLEAN NOT NULL DEFAULT 0"), ("locked", "BOOLEAN NOT NULL DEFAULT 0")],
        "episodes": [("missing", "BOOLEAN NOT NULL DEFAULT 0"), ("last_scanned_at", "DATETIME")],
    }
    with engine.connect() as conn:
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl_type in columns:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")

        # Episodes scanned before last_scanned_at existed already have chapters/
        # track_metadata rows; backfill from those so they don't show as "never
        # scanned" (which would also block backing them up from the UI).
        conn.exec_driver_sql(
            """
            UPDATE episodes
            SET last_scanned_at = COALESCE(
                (SELECT updated_at FROM chapters WHERE chapters.episode_id = episodes.id),
                (SELECT updated_at FROM track_metadata WHERE track_metadata.episode_id = episodes.id)
            )
            WHERE last_scanned_at IS NULL
              AND (
                EXISTS (SELECT 1 FROM chapters WHERE chapters.episode_id = episodes.id)
                OR EXISTS (SELECT 1 FROM track_metadata WHERE track_metadata.episode_id = episodes.id)
              )
            """
        )
        conn.commit()
