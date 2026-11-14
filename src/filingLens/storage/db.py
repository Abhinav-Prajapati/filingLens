"""Postgres connection and schema (FILINGLENS_DATABASE_URL; see filingLens.config).

``connect()`` returns a small wrapper with the interface the repositories use:
``execute(sql, params)`` returning a cursor, ``executemany``, ``commit`` and ``close``. SQL is
written with ``?`` placeholders and the wrapper turns them into psycopg's ``%s``. Rows can be
read by position (``r[0]``) or by column name (``r["ticker"]``), and ``dict(r)`` works.

The connection is in autocommit mode: each statement commits on its own, ``executemany`` runs
in one transaction, and ``commit()`` is a no-op kept for the callers. Use ``transaction()`` to
group statements.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Self

import psycopg

from filingLens.config import database_url

SCHEMA_VERSION = 2                 # 1: SQLite; 2: Postgres
_SCHEMA = Path(__file__).with_name("schema.sql")


class Row(tuple):
    """A result row: a tuple that can also be indexed by column name."""

    _names: tuple[str, ...]

    def __new__(cls, values: Sequence[Any], names: tuple[str, ...]) -> Self:
        r = super().__new__(cls, values)
        r._names = names
        return r

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._names.index(key))
            except ValueError:
                raise KeyError(key) from None
        return tuple.__getitem__(self, key)

    def keys(self) -> tuple[str, ...]:
        return self._names


def _row_factory(cursor: Any) -> Any:
    names = tuple(d.name for d in cursor.description or ())
    return lambda values: Row(values, names)


def to_pg(sql: str) -> str:
    """``?`` placeholders -> ``%s``; a literal ``%`` -> ``%%``. Quoted text is left alone."""
    out, quoted = [], False
    for ch in sql:
        if ch == "'":
            quoted = not quoted
        if ch == "%":
            out.append("%%")
        elif ch == "?" and not quoted:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class Result:
    """What ``execute`` returns: iterate it, or ``fetchone`` / ``fetchall``."""

    def __init__(self, cur: psycopg.Cursor):
        self._cur = cur

    def fetchone(self) -> Any:
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class Database:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Result:
        cur = self.conn.cursor(row_factory=_row_factory)
        cur.execute(to_pg(sql), tuple(params))   # type: ignore[arg-type]
        return Result(cur)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(r) for r in seq]
        if rows:
            with self.conn.transaction(), self.conn.cursor() as cur:
                cur.executemany(to_pg(sql), rows)   # type: ignore[arg-type]

    def transaction(self) -> AbstractContextManager[Any]:
        return self.conn.transaction()

    def commit(self) -> None:
        """Autocommit is on; kept so callers written for transactions still read naturally."""

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def connect(url: str | None = None) -> Database:
    """Connect to Postgres (default: FILINGLENS_DATABASE_URL) and create the schema if needed."""
    conn = psycopg.connect(url or database_url(), autocommit=True)
    db = Database(conn)
    init_schema(db)
    return db


def init_schema(db: Database) -> None:
    with db.transaction():
        db.conn.execute("SELECT pg_advisory_xact_lock(4242)")   # one initialiser at a time
        db.conn.execute(_SCHEMA.read_text(encoding="utf-8"))
        row = db.execute("SELECT version FROM schema_info").fetchone()
        if row is None:
            db.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
        elif row[0] > SCHEMA_VERSION:
            raise RuntimeError(f"database schema v{row[0]} is newer than code v{SCHEMA_VERSION}")
        # future migrations: elif row[0] < 3: ...
