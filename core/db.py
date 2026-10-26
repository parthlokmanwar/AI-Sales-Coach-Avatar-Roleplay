"""
core/db.py
──────────
SQLAlchemy engine + session factory for the enterprise data layer.

The connection target is driven by the DATABASE_URL env var so the SAME code
runs on PostgreSQL in production and on a local SQLite file in development:

    # production
    DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/posiview
    # development (default — zero infra, just works)
    (unset)  →  sqlite:///<repo>/posiview_dev.db

Everything else in the app imports `Base`, `get_session()` (FastAPI dependency),
and `session_scope()` (context manager for scripts/seeding) from here.
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger("hdfc")

ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SQLITE = f"sqlite:///{(ROOT / 'posiview_dev.db').as_posix()}"

DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_SQLITE)
_IS_SQLITE = DATABASE_URL.startswith("sqlite")


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in core/models.py."""


# SQLite needs check_same_thread=False because FastAPI touches it from the
# threadpool; Postgres uses a real connection pool with pre-ping for resilience.
if _IS_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        future=True,
    )

    # Enforce foreign keys on SQLite (off by default) so dev behaves like prod.
    @event.listens_for(engine, "connect")
    def _sqlite_fk_pragma(dbapi_conn, _rec):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create any missing tables. Safe to call on every startup.

    For Phase 0 this is the fast path so the app runs with zero migration
    ceremony. Alembic remains the source of truth for production schema changes;
    create_all() only fills in tables that don't yet exist.
    """
    import core.models  # noqa: F401  (register mappers before create_all)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    backend = "sqlite (dev)" if _IS_SQLITE else "postgresql"
    log.info(f"[DB] schema ready on {backend}")


# Columns added to a model AFTER its table already exists on a deployment.
# create_all() never ALTERs existing tables, so they are added here instead.
# TIMESTAMP is valid DDL on both SQLite and PostgreSQL.
_LATE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "users": [("tour_completed_at", "TIMESTAMP")],
    # Slot-booking linkage for scored reports (three-mode redesign).
    "roleplay_sessions": [("slot_id", "VARCHAR(64)"), ("booking_id", "VARCHAR(64)")],
}


def _ensure_columns() -> None:
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in _LATE_COLUMNS.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl_type in columns:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
                    log.info(f"[DB] added missing column {table}.{name}")


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for scripts/seeding/middleware — commits or rolls back."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
