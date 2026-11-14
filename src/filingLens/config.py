"""Settings from the environment, falling back to a ``.env`` file in the working directory.

    FILINGLENS_DATABASE_URL   postgresql://user:password@host:port/db
    FILINGLENS_S3_ENDPOINT    http://127.0.0.1:8333 (any S3-compatible store)
    FILINGLENS_S3_BUCKET / _ACCESS_KEY / _SECRET_KEY
"""
from __future__ import annotations

import os
from functools import cache
from pathlib import Path

DEFAULT_DATABASE_URL = "postgresql://filinglens@127.0.0.1:5442/filinglens"


@cache
def _dotenv(path: str = ".env") -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def setting(name: str, default: str = "") -> str:
    """The environment wins over .env, so tests and containers can override."""
    return os.environ.get(name) or _dotenv().get(name) or default


def database_url() -> str:
    return setting("FILINGLENS_DATABASE_URL", DEFAULT_DATABASE_URL)
