import hashlib
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.auth_models import User, UserSession

SESSION_COOKIE = "mkv_session"
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 260_000


def _hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return salt.hex() + ":" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, _ = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        return secrets.compare_digest(stored, _hash_password(password, salt))
    except Exception:
        return False


def hash_password(password: str) -> str:
    return _hash_password(password)


def create_session(db: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=SESSION_DAYS)
    session = UserSession(token=token, user_id=user_id, expires_at=expires_at)
    db.add(session)
    db.commit()
    return token


def validate_session(db: Session, token: str) -> User | None:
    if not token:
        return None
    session = db.query(UserSession).filter(UserSession.token == token).first()
    if session is None or session.expires_at < datetime.utcnow():
        return None
    return db.query(User).filter(User.id == session.user_id).first()


def delete_session(db: Session, token: str) -> None:
    db.query(UserSession).filter(UserSession.token == token).delete()
    db.commit()


def prune_expired_sessions(db: Session) -> None:
    db.query(UserSession).filter(UserSession.expires_at < datetime.utcnow()).delete()
    db.commit()


def user_count(db: Session) -> int:
    return db.query(User).count()


def create_user(db: Session, username: str, password: str) -> User:
    user = User(username=username.strip(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username.strip()).first()
