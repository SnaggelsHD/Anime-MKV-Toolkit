import os

DB_PATH = os.environ.get("DB_PATH", "/data/chapters.db")
BACKUP_DB_PATH = os.environ.get("BACKUP_DB_PATH", "/data/backup.db")
LIBRARIES_ROOT = os.environ.get("LIBRARIES_ROOT", "/libraries")
