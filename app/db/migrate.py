"""Idempotent additive milestone migration: create any missing workspace, agent-run, audit, page-artifact, matching-run/result, embedding-cache and saved-opportunity tables.

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
