import hashlib
import secrets
import re
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import LoginSession, User


password_hasher = PasswordHasher()
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")


def normalize_username(value: str) -> str:
    return value.strip().lower()


def validate_username(value: str) -> str | None:
    normalized = normalize_username(value)
    if not USERNAME_RE.fullmatch(normalized):
        return "用户名需为 3～24 位英文字母、数字或下划线"
    return None


def validate_password(value: str) -> str | None:
    if not 8 <= len(value) <= 64:
        return "密码需为 8～64 个字符"
    if not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value):
        return "密码至少包含一个英文字母和一个数字"
    return None


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def new_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    session = LoginSession(
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days),
    )
    db.add(session)
    db.commit()
    return token


def get_session_user(db: Session, token: str | None) -> tuple[LoginSession, User] | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    login_session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash))
    if login_session is None:
        return None
    expires_at = login_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        db.delete(login_session)
        db.commit()
        return None
    user = db.get(User, login_session.user_id)
    return (login_session, user) if user else None


def delete_session(db: Session, token: str | None) -> None:
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        login_session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash))
        if login_session:
            db.delete(login_session)
            db.commit()
