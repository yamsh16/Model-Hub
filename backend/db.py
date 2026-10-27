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
