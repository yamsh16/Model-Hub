import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Matches docker-compose.yml. Override with DATABASE_URL (e.g. in production).
DEFAULT_DATABASE_URL = "postgresql+psycopg://modelhub:modelhub@127.0.0.1:5432/modelhub"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    return create_engine(url or database_url(), pool_pre_ping=True)


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


_session_factory = None


def get_db():
    """FastAPI dependency: one session per request. The engine is created on first use so that
    importing the app (and running tests that never touch the database) needs no database."""
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory(make_engine())
    with _session_factory() as session:
        yield session
