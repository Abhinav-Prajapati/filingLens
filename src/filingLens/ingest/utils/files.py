"""File helpers: identity (sha256) and reading bytes, unwrapping single-file ZIPs."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from filingLens.ingest.errors import IngestError

XBRL_SUFFIXES = (".xml", ".xbrl", ".html", ".htm", ".xhtml")


def sha256_file(path: str | Path) -> str:
    """Document identity: the same bytes always give the same doc_id."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_document_bytes(path: str | Path, inner_suffixes: tuple[str, ...] = XBRL_SUFFIXES,
                        error: type[IngestError] = IngestError) -> bytes:
    """Read a file. Empty files are rejected; a ZIP must hold exactly one file whose name
    ends with one of ``inner_suffixes``, and that file's bytes are returned."""
    p = Path(path)
    data = p.read_bytes()
    if not data.strip():
        raise error(f"{p.name}: file is empty")
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(inner_suffixes)]
            if len(names) != 1:
                raise error(f"{p.name}: zip must contain exactly one document, found {names}")
            data = z.read(names[0])
    return data
