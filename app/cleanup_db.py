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
