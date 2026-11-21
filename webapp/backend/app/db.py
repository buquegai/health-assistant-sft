from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import BACKEND_DIR, settings


if settings.database_url.startswith("sqlite:///"):
    database_path = settings.database_url.removeprefix("sqlite:///")
    if database_path != ":memory:" and not database_path.startswith("/") and not (len(database_path) > 1 and database_path[1] == ":"):
        resolved = (BACKEND_DIR / database_path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{resolved.as_posix()}"
    else:
        database_url = settings.database_url
else:
    database_url = settings.database_url

engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if database_url.startswith("sqlite") else {},
    future=True,
)


if database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
