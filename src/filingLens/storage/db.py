"""Connection factory and schema management. Pass ':memory:' for tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    init_schema(con)
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    current = con.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"database schema v{current} is newer than code v{SCHEMA_VERSION}")
    # future migrations: if current < 2: ...
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
