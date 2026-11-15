"""Chunk vectors on disk: ``vectors.npy`` (float32, one row per chunk) plus ``vectors.json``
(model name, dim, and the chunk_id of every row, in row order).

The JSON list *is* the row -> chunk mapping, so it can never drift from the matrix.
``update_index`` is incremental: it embeds only chunks that are new since the last run,
drops rows of deleted chunks, and rebuilds everything if the model changed.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from filingLens.retrieval.embedder import Embedder
from filingLens.storage.db import Database


def chunk_passage(context_line: str, text: str) -> str:
    """What is embedded for a chunk: its context line (company, doc, FY, section, scope,
    page) followed by the text, so "TCS FY25 MD&A" style queries match."""
    return f"{context_line}\n{text}"


@dataclass
class VectorIndex:
    model: str
    vectors: np.ndarray                      # (n, dim) float32, rows L2-normalised
    chunk_ids: list[str]
    row_of: dict[str, int] = field(init=False)

    def __post_init__(self) -> None:
        if len(self.chunk_ids) != len(self.vectors):
            raise ValueError("vectors and chunk_ids differ in length")
        self.row_of = {c: i for i, c in enumerate(self.chunk_ids)}

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1]) if self.vectors.ndim == 2 else 0

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, self.vectors)
        p.with_suffix(".json").write_text(json.dumps(
            {"model": self.model, "dim": self.dim, "chunk_ids": self.chunk_ids}))

    @classmethod
    def load(cls, path: str | Path) -> VectorIndex | None:
        p = Path(path)
        if not p.exists() or not p.with_suffix(".json").exists():
            return None
        meta = json.loads(p.with_suffix(".json").read_text())
        return cls(meta["model"], np.load(p), meta["chunk_ids"])


@dataclass
class IndexReport:
    total: int
    added: int
    removed: int
    rebuilt: bool


def update_index(con: Database, embedder: Embedder, path: str | Path,
                 batch: int = 256) -> tuple[VectorIndex, IndexReport]:
    """Bring the vector file in line with the chunk table and save it."""
    rows = {r[0]: chunk_passage(r[1], r[2]) for r in con.execute(
        "SELECT chunk_id, context_line, text FROM chunk ORDER BY chunk_id")}
    old = VectorIndex.load(path)
    rebuilt = old is None or old.model != embedder.name
    if rebuilt or old is None:
        old = VectorIndex(embedder.name, np.zeros((0, embedder.dim), np.float32), [])
    keep = [c for c in old.chunk_ids if c in rows]
    new = [c for c in rows if c not in old.row_of]
    parts = [old.vectors[[old.row_of[c] for c in keep]]] if keep else []
    for i in range(0, len(new), batch):
        parts.append(embedder.embed([rows[c] for c in new[i:i + batch]]))
    vectors = np.concatenate(parts) if parts else np.zeros((0, embedder.dim), np.float32)
    index = VectorIndex(embedder.name, vectors.astype(np.float32), keep + new)
    index.save(path)
    return index, IndexReport(len(index.chunk_ids), len(new), len(old.chunk_ids) - len(keep),
                              rebuilt)


def indexed_ids(path: str | Path) -> tuple[str | None, set[str]]:
    """(model, chunk ids) of the index at ``path``, from its small JSON file only (the vector
    matrix is not read). (None, empty) when there is no index."""
    meta = Path(path).with_suffix(".json")
    if not meta.exists():
        return None, set()
    m = json.loads(meta.read_text())
    return m.get("model"), set(m.get("chunk_ids", []))


@dataclass
class DocProgress:
    doc_id: str
    total: int                               # chunks of the document
    done: int = 0                            # chunks with a vector


def pending_by_document(con: Database, path: str | Path, model: str | None = None
                        ) -> dict[str, DocProgress]:
    """Per document: chunks, and how many already have a vector in the index at ``path``
    (none when the index is missing or was built with another ``model``)."""
    built_with, ids = indexed_ids(path)
    have = ids if built_with is not None and (model is None or built_with == model) else set()
    out: dict[str, DocProgress] = {}
    for doc_id, chunk_id in con.execute("SELECT doc_id, chunk_id FROM chunk"):
        p = out.setdefault(doc_id, DocProgress(doc_id, 0))
        p.total += 1
        p.done += chunk_id in have
    return out


def embed_documents(con: Database, embedder: Embedder, path: str | Path, doc_ids: list[str],
                    progress: Callable[[str, int, int], None] | None = None,
                    cancelled: Callable[[], bool] | None = None, batch: int = 64) -> int:
    """Embed the chunks of ``doc_ids`` that have no vector yet, one document after another,
    saving the index after each document (an interrupted run keeps what it finished).
    Rows of chunks that no longer exist are dropped; a different model starts a new index.
    ``progress(doc_id, done, total)`` is called after every batch. Returns chunks embedded."""
    index = VectorIndex.load(path)
    if index is None or index.model != embedder.name:
        index = VectorIndex(embedder.name, np.zeros((0, embedder.dim), np.float32), [])
    live = {r[0] for r in con.execute("SELECT chunk_id FROM chunk")}
    keep = [c for c in index.chunk_ids if c in live]
    parts = [index.vectors[[index.row_of[c] for c in keep]] if keep else
             np.zeros((0, embedder.dim), np.float32)]
    ids, have = list(keep), set(keep)
    added = 0
    for doc_id in doc_ids:
        rows = [(r[0], chunk_passage(r[1], r[2])) for r in con.execute(
            "SELECT chunk_id, context_line, text FROM chunk WHERE doc_id=? ORDER BY chunk_id",
            (doc_id,))]
        todo = [(c, t) for c, t in rows if c not in have]
        done = len(rows) - len(todo)
        if progress:
            progress(doc_id, done, len(rows))
        for i in range(0, len(todo), batch):
            if cancelled and cancelled():
                break
            part = todo[i:i + batch]
            parts.append(embedder.embed([t for _, t in part]))
            ids += [c for c, _ in part]
            have.update(c for c, _ in part)
            added += len(part)
            done += len(part)
            if progress:
                progress(doc_id, done, len(rows))
        parts = [np.concatenate(parts).astype(np.float32)]
        VectorIndex(embedder.name, parts[0], ids).save(path)
        if cancelled and cancelled():
            break
    return added
