from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import BACKUP_DB_PATH

backup_engine = create_engine(f"sqlite:///{BACKUP_DB_PATH}", connect_args={"check_same_thread": False})
BackupSessionLocal = sessionmaker(bind=backup_engine, autoflush=False, autocommit=False)

BackupBase = declarative_base()


def get_backup_db():
    db = BackupSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_backup_db():
    import app.backup_models  # noqa: F401 ensure models are registered

    BackupBase.metadata.create_all(bind=backup_engine)
