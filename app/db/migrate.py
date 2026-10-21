"""Idempotent additive migration: create missing workspace, resume, matching,
embedding-cache, saved-opportunity, skill-gap and application/timeline tables.

Existing tables, columns, IDs, files, and profile facts are not changed.
Run with: python -m app.db.migrate
"""
from app.core.config import get_settings
from app.db.connection import initialize_database, make_engine


if __name__ == "__main__":
    engine = make_engine(get_settings().database_url)
    initialize_database(engine)
    engine.dispose()
    print("Database initialized; existing records preserved.")
