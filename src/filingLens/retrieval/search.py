"""Hybrid search over chunks: dense (vectors) + keyword (SQLite FTS5 / BM25), fused with
Reciprocal Rank Fusion. Filters (company, FY, document type, scope, section) are applied
*before* ranking, so a TCS question can never return another company's text.

Tuned on tests/golden/recall.yaml (11 questions, TCS FY25 report), recall@5:
dense 8/11, keyword 8/11, plain hybrid (k=60, all words) 7/11 -> with stopwords dropped from
the keyword query and RRF k=10: 9/11. Common words ("how", "does", "company", "kitne") matched
hundreds of chunks, and those mid-ranked-in-both chunks out-scored a #1 from one list.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

from filingLens.retrieval.embedder import Embedder
from filingLens.retrieval.index import VectorIndex

RRF_K = 10          # smaller k favours a strong #1 in either list (60 scored 7/11, 10 -> 9/11)

# words that match almost every chunk: English question words and Hinglish function words
STOPWORDS = frozenset("""
a an the is are was were be been being of in on at to for from by with and or not as how what which who
whom when where why does do did has have had many much this that these those it its their there any
some about into over during per year years company companies
kya kitna kitne kitni mein me ne ko ka ki ke hai hain tha thi se par aur bhi diya kiya karte karta
""".split())  # noqa: SIM905 - one word list is easier to read and edit than 80 quoted strings


@dataclass(frozen=True)
class SearchFilters:
    tickers: tuple[str, ...] = ()
    fys: tuple[str, ...] = ()
    doc_types: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()

    def where(self, alias: str = "c") -> tuple[str, list[str]]:
        """SQL condition on the chunk table and its parameters."""
        parts, params = ["1=1"], []
        for col, vals in (("ticker", self.tickers), ("fy", self.fys), ("doc_type", self.doc_types),
                          ("scope", self.scopes), ("section", self.sections)):
            if vals:
                parts.append(f"{alias}.{col} IN ({','.join('?' * len(vals))})")
                params += list(vals)
        return " AND ".join(parts), params


@dataclass
class Hit:
    chunk_id: str
    score: float                                     # fused RRF score
    ranks: dict[str, int] = field(default_factory=dict)   # "dense"/"keyword" -> 1-based rank
    ticker: str = ""
    fy: str | None = None
    doc_id: str = ""
    page: int = 0
    section: str = ""
    scope: str = ""
    context_line: str = ""
    text: str = ""


def rrf(rankings: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float, dict[str, int]]]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1 / (k + rank). Ties are broken by
    the best single rank, then by id, so the order is deterministic."""
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ids in rankings.items():
        for r, cid in enumerate(ids, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + r)
            ranks.setdefault(cid, {})[name] = r
    return sorted(((c, s, ranks[c]) for c, s in scores.items()),
                  key=lambda x: (-x[1], min(x[2].values()), x[0]))


def fts_query(text: str, drop: frozenset[str] = frozenset()) -> str | None:
    """User text -> safe FTS5 query: quoted words joined by OR (punctuation in a question would
    otherwise be FTS5 syntax). Stopwords, single letters and ``drop`` (e.g. tickers already
    in the filter) are left out; if nothing else remains, all words are kept."""
    words = re.findall(r"\w+", text.lower())
    useful = [w for w in words if len(w) > 1 and w not in STOPWORDS and w not in drop]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(useful or words)) or None


def allowed_ids(con: sqlite3.Connection, filters: SearchFilters) -> list[str]:
    where, params = filters.where()
    return [r[0] for r in con.execute(f"SELECT c.chunk_id FROM chunk c WHERE {where}", params)]


def dense_search(index: VectorIndex, query_vec: np.ndarray, allowed: list[str],
                 k: int) -> list[str]:
    rows = [index.row_of[c] for c in allowed if c in index.row_of]
    if not rows:
        return []
    scores = index.vectors[rows] @ query_vec.astype(np.float32).ravel()
    top = np.argsort(-scores, kind="stable")[:k]
    return [index.chunk_ids[rows[i]] for i in top]


def keyword_search(con: sqlite3.Connection, query: str, filters: SearchFilters,
                   k: int) -> list[str]:
    q = fts_query(query, frozenset(t.lower() for t in filters.tickers))
    if q is None:
        return []
    where, params = filters.where()
    sql = ("SELECT f.chunk_id FROM chunk_fts f JOIN chunk c ON c.chunk_id = f.chunk_id "
           f"WHERE chunk_fts MATCH ? AND {where} ORDER BY bm25(chunk_fts) LIMIT ?")
    return [r[0] for r in con.execute(sql, [q, *params, k])]


class Searcher:
    """Hybrid search. Without an index (or embedder) it falls back to keyword-only."""

    def __init__(self, con: sqlite3.Connection, index: VectorIndex | None = None,
                 embedder: Embedder | None = None, pool: int = 50):
        if index is not None and embedder is not None and index.model != embedder.name:
            raise ValueError(f"index built with {index.model}, query embedder is {embedder.name}")
        self.con, self.index, self.embedder, self.pool = con, index, embedder, pool

    def search(self, query: str, filters: SearchFilters = SearchFilters(), k: int = 5) -> list[Hit]:
        rankings = {"keyword": keyword_search(self.con, query, filters, self.pool)}
        if self.index is not None and self.embedder is not None:
            qv = self.embedder.embed([query])[0]
            rankings["dense"] = dense_search(self.index, qv, allowed_ids(self.con, filters),
                                             self.pool)
        fused = rrf(rankings)[:k]
        return self._hydrate(fused)

    def _hydrate(self, fused: list[tuple[str, float, dict[str, int]]]) -> list[Hit]:
        if not fused:
            return []
        ids = [c for c, _, _ in fused]
        rows = {r["chunk_id"]: r for r in self.con.execute(
            "SELECT chunk_id, ticker, fy, doc_id, page_start, section, scope, context_line, text "
            f"FROM chunk WHERE chunk_id IN ({','.join('?' * len(ids))})", ids)}
        return [Hit(c, s, rk, rows[c]["ticker"], rows[c]["fy"], rows[c]["doc_id"],
                    rows[c]["page_start"], rows[c]["section"], rows[c]["scope"],
                    rows[c]["context_line"], rows[c]["text"]) for c, s, rk in fused]
