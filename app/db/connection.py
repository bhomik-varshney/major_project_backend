from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


def make_engine(url: str):
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite"):
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 5}, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def set_sqlite_options(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    else:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    return engine


def initialize_database(engine):
    # Additive table creation only. Existing columns and facts are never rewritten.
    # Use versioned migrations for future changes to existing tables.
    Base.metadata.create_all(engine)


def session_factory(engine):
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
