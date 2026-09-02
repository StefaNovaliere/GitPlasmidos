"""Engine and session wiring. SQLite by default, overridable with DATABASE_URL."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "visoradn.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args=(
        {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    ),
    future=True,
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_missing_columns() -> None:
    """Add columns the models grew since a database was first created.

    This project has no migration tool on purpose - it is a portfolio MVP with
    one SQLite file. But silently crashing with "no such column" on a database
    from an earlier commit is a worse trade than fifteen lines that add the
    column and move on. Only ever additive.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            rows = connection.execute(
                text(f'PRAGMA table_info("{table.name}")')
            ).fetchall()
            if not rows:
                continue  # create_all will make it
            present = {row[1] for row in rows}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = column.type.compile(engine.dialect)
                default = "" if column.nullable else " NOT NULL DEFAULT ''"
                if not column.nullable and "INT" in ddl.upper():
                    default = " NOT NULL DEFAULT 0"
                connection.execute(
                    text(
                        f'ALTER TABLE "{table.name}" '
                        f'ADD COLUMN "{column.name}" {ddl}{default}'
                    )
                )


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
