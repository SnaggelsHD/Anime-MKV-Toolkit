import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

AUTH_DB_PATH = os.environ.get("AUTH_DB_PATH", "/data/auth.db")

AuthBase = declarative_base()

auth_engine = create_engine(
    f"sqlite:///{AUTH_DB_PATH}",
    connect_args={"check_same_thread": False},
)
AuthSessionLocal = sessionmaker(bind=auth_engine)


def get_auth_db():
    db = AuthSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_auth_db():
    AuthBase.metadata.create_all(bind=auth_engine)
