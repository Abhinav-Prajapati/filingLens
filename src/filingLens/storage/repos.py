"""Repositories. Services depend on the Protocols, so tests can swap implementations."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import astuple, fields
from typing import Protocol

from filingLens.domain.fiscal import fy_int
from filingLens.domain.models import Chunk, Fact, Page, RawFact
from filingLens.domain.units import same_value

_FACT_COLS = (
    "isin ticker metric_id period_type fy quarter period_start period_end scope value unit "
    "orig_value orig_unit xbrl_tag doc_id pdf_doc_id page verified qa method"
).split()


class FactRepoProtocol(Protocol):
    def upsert_facts(self, facts: list[Fact], isin_by_ticker: dict[str, str] | None = None) -> int: ...
    def get_series(self, ticker: str, metric_id: str, scope: str = "consolidated",
                   period_type: str = "FY", fy_from: str | None = None,
                   fy_to: str | None = None) -> list[Fact]: ...
    def list_facts(self, tickers: Iterable[str] | None = None) -> list[Fact]: ...
    def delete_facts(self, tickers: Iterable[str], method: str = "xbrl") -> int: ...
    def set_qa(self, tickers: Iterable[str],
               flags: dict[tuple[str, str, str, str, str], str]) -> None: ...
    def set_citations(self, tickers: Iterable[str],
                      rows: Iterable[tuple[str, int, str, tuple[str, ...]]]) -> None: ...
    def latest_fy(self, ticker: str) -> str | None: ...
    def coverage(self) -> list[dict]: ...


class ValidationRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def add(self, ticker: str, scope: str | None, period_end: str, check: str,
            status: str, detail: str) -> None:
        self.con.execute(
            "INSERT INTO validation(ticker,scope,period_end,check_name,status,detail) VALUES (?,?,?,?,?,?)",
            (ticker, scope, period_end, check, status, detail))
        self.con.commit()

    def add_many(self, rows: Iterable[tuple[str, str | None, str, str, str, str]]) -> int:
        """rows: (ticker, scope, period_end, check_name, status, detail)."""
        cur = self.con.executemany(
            "INSERT INTO validation(ticker,scope,period_end,check_name,status,detail) VALUES (?,?,?,?,?,?)",
            list(rows))
        self.con.commit()
        return cur.rowcount

    def clear(self, tickers: Iterable[str], checks: Iterable[str] | None = None,
              exclude_checks: Iterable[str] = ()) -> None:
        """Delete validation rows for these tickers (optionally only/except some checks)."""
        tk = list(tickers)
        sql = f"DELETE FROM validation WHERE ticker IN ({','.join('?' * len(tk))})"
        params: list = list(tk)
        if checks is not None:
            ck = list(checks)
            sql += f" AND check_name IN ({','.join('?' * len(ck))})"
            params += ck
        ex = list(exclude_checks)
        if ex:
            sql += f" AND check_name NOT IN ({','.join('?' * len(ex))})"
            params += ex
        self.con.execute(sql, params)
        self.con.commit()

    def list(self, check: str | None = None, ticker: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM validation WHERE 1=1", []
        if check:
            sql += " AND check_name=?"
            params.append(check)
        if ticker:
            sql += " AND ticker=?"
            params.append(ticker)
        return [dict(r) for r in self.con.execute(sql + " ORDER BY id", params)]


class FactRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.validation = ValidationRepo(con)

    def upsert_facts(self, facts: list[Fact], isin_by_ticker: dict[str, str] | None = None) -> int:
        """Insert or replace by (ticker, metric, period, scope). A changed value is logged as a
        restatement (later filing wins). Returns number of rows written."""
        isin_by_ticker = isin_by_ticker or {}
        n = 0
        for f in facts:
            prev = self.con.execute(
                "SELECT orig_value FROM fact WHERE ticker=? AND metric_id=? AND period_start=? "
                "AND period_end=? AND scope=?",
                (f.ticker, f.metric_id, f.period_start, f.period_end, f.scope)).fetchone()
            if prev is not None and not same_value(prev["orig_value"], f.orig_value):
                self.validation.add(
                    f.ticker, f.scope, f.period_end, "restatement", "info",
                    f"{f.metric_id}: {prev['orig_value']:g} -> {f.orig_value:g} (later filing wins)")
            row = f.model_dump()
            row["isin"] = isin_by_ticker.get(f.ticker, "")
            self.con.execute(
                f"INSERT OR REPLACE INTO fact({','.join(_FACT_COLS)}) "
                f"VALUES ({','.join('?' * len(_FACT_COLS))})",
                [row[c] for c in _FACT_COLS])
            n += 1
        self.con.commit()
        return n

    def get_series(self, ticker: str, metric_id: str, scope: str = "consolidated",
                   period_type: str = "FY", fy_from: str | None = None,
                   fy_to: str | None = None) -> list[Fact]:
        sql = ("SELECT * FROM fact WHERE ticker=? AND metric_id=? AND scope=? "
               "AND period_type=? AND value IS NOT NULL")
        params: list = [ticker, metric_id, scope, period_type]
        if fy_from:
            sql += " AND CAST(substr(fy,3) AS INTEGER) >= ?"
            params.append(fy_int(fy_from))
        if fy_to:
            sql += " AND CAST(substr(fy,3) AS INTEGER) <= ?"
            params.append(fy_int(fy_to))
        rows = self.con.execute(sql + " ORDER BY period_end", params).fetchall()
        return [Fact(**{k: r[k] for k in r.keys() if k in Fact.model_fields}) for r in rows]

    def list_facts(self, tickers: Iterable[str] | None = None) -> list[Fact]:
        sql, params = "SELECT * FROM fact", []
        if tickers is not None:
            tk = list(tickers)
            sql += f" WHERE ticker IN ({','.join('?' * len(tk))})"
            params = tk
        rows = self.con.execute(sql + " ORDER BY ticker, metric_id, period_end", params).fetchall()
        return [Fact(**{k: r[k] for k in r.keys() if k in Fact.model_fields}) for r in rows]

    def delete_facts(self, tickers: Iterable[str], method: str = "xbrl") -> int:
        tk = list(tickers)
        cur = self.con.execute(
            f"DELETE FROM fact WHERE method=? AND ticker IN ({','.join('?' * len(tk))})",
            [method, *tk])
        self.con.commit()
        return cur.rowcount

    def set_qa(self, tickers: Iterable[str],
               flags: dict[tuple[str, str, str, str, str], str]) -> None:
        """Reset qa to 'ok' for the tickers, then apply flags keyed by
        (ticker, scope, period_start, period_end, metric_id)."""
        tk = list(tickers)
        self.con.execute(f"UPDATE fact SET qa='ok' WHERE ticker IN ({','.join('?' * len(tk))})",
                         tk)
        self.con.executemany(
            "UPDATE fact SET qa=? WHERE ticker=? AND scope=? AND period_start=? "
            "AND period_end=? AND metric_id=?",
            [(st, *k) for k, st in flags.items()])
        self.con.commit()

    def set_citations(self, tickers: Iterable[str],
                      rows: Iterable[tuple[str, int, str, tuple[str, ...]]]) -> None:
        """Clear citations for the tickers, then set (pdf_doc_id, page, verified) per fact key
        (ticker, metric_id, period_start, period_end, scope)."""
        tk = list(tickers)
        self.con.execute(
            "UPDATE fact SET pdf_doc_id=NULL, page=NULL, verified='no' "
            f"WHERE ticker IN ({','.join('?' * len(tk))})", tk)
        self.con.executemany(
            "UPDATE fact SET pdf_doc_id=?, page=?, verified=? WHERE ticker=? AND metric_id=? "
            "AND period_start=? AND period_end=? AND scope=?",
            [(doc, page, ver, *key) for doc, page, ver, key in rows])
        self.con.commit()

    def latest_fy(self, ticker: str) -> str | None:
        r = self.con.execute(
            "SELECT MAX(CAST(substr(fy,3) AS INTEGER)) y FROM fact "
            "WHERE ticker=? AND period_type='FY'", (ticker,)).fetchone()
        return f"FY{r['y']:02d}" if r and r["y"] is not None else None

    def coverage(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT ticker, scope, metric_id, period_type, COUNT(*) n, MIN(fy) first_fy, "
            "MAX(fy) last_fy, SUM(verified='pdf') pdf_verified, SUM(qa!='ok') qa_flags "
            "FROM fact GROUP BY 1,2,3,4 ORDER BY 1,2,3,4").fetchall()
        return [dict(r) for r in rows]


_DOC_COLS = ("doc_id ticker isin doc_type fy scope source_url local_path original_name parser "
             "parser_ver n_pages parse_quality meta_json ingested_at").split()


class DocumentRepo:
    """Registered source files. doc_id = sha256 of the file bytes."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def upsert(self, doc: dict) -> None:
        cols = [c for c in _DOC_COLS if c in doc]
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "doc_id")
        self.con.execute(
            f"INSERT INTO document({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(doc_id) DO UPDATE SET {updates}", [doc[c] for c in cols])
        self.con.commit()

    def get(self, doc_id: str) -> dict | None:
        r = self.con.execute("SELECT * FROM document WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(r) if r else None

    def list(self, doc_type: str | None = None,
             tickers: Iterable[str] | None = None) -> list[dict]:
        sql, params = "SELECT * FROM document WHERE 1=1", []
        if doc_type:
            sql += " AND doc_type=?"
            params.append(doc_type)
        if tickers is not None:
            tk = list(tickers)
            sql += f" AND ticker IN ({','.join('?' * len(tk))})"
            params += tk
        return [dict(r) for r in self.con.execute(sql + " ORDER BY ticker, doc_id", params)]


_RAW_COLS = [f.name for f in fields(RawFact)]


class RawFactRepo:
    """Every fact of every XBRL file, as parsed (table xbrl_fact_raw)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def replace(self, doc_id: str, rows: list[RawFact]) -> int:
        self.con.execute("DELETE FROM xbrl_fact_raw WHERE doc_id=?", (doc_id,))
        self.con.executemany(
            f"INSERT INTO xbrl_fact_raw(doc_id,{','.join(_RAW_COLS)}) "
            f"VALUES (?,{','.join('?' * len(_RAW_COLS))})",
            [(doc_id, *astuple(r)) for r in rows])
        self.con.commit()
        return len(rows)

    def load(self, doc_id: str) -> list[RawFact]:
        rows = self.con.execute(
            f"SELECT {','.join(_RAW_COLS)} FROM xbrl_fact_raw WHERE doc_id=? ORDER BY id",
            (doc_id,)).fetchall()
        return [RawFact(*tuple(r)) for r in rows]

    def count(self, doc_id: str | None = None) -> int:
        if doc_id:
            return self.con.execute("SELECT COUNT(*) FROM xbrl_fact_raw WHERE doc_id=?",
                                    (doc_id,)).fetchone()[0]
        return self.con.execute("SELECT COUNT(*) FROM xbrl_fact_raw").fetchone()[0]


class TextRepo:
    """Pages and chunks of PDF documents. Keeps the chunk_fts keyword index in sync."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def replace(self, doc_id: str, pages: list[Page], chunks: list[Chunk]) -> None:
        old = [(r[0],) for r in self.con.execute("SELECT chunk_id FROM chunk WHERE doc_id=?",
                                                  (doc_id,))]
        self.con.executemany("DELETE FROM chunk_fts WHERE chunk_id=?", old)
        self.con.execute("DELETE FROM chunk WHERE doc_id=?", (doc_id,))
        self.con.execute("DELETE FROM page WHERE doc_id=?", (doc_id,))
        self.con.executemany("INSERT INTO page VALUES (?,?,?,?,?,?)", [astuple(p) for p in pages])
        self.con.executemany(
            f"INSERT INTO chunk({','.join(_CHUNK_COLS)}) VALUES ({','.join('?' * len(_CHUNK_COLS))})",
            [astuple(c) for c in chunks])
        self.con.executemany("INSERT INTO chunk_fts(chunk_id, body) VALUES (?,?)",
                             [(c.chunk_id, f"{c.context_line}\n{c.text}") for c in chunks])
        self.con.commit()

    def page_texts(self, tickers: Iterable[str], doc_type: str = "annual_report") -> list[dict]:
        """Chunk text grouped by (doc, page, scope, section): one row per panel of a page."""
        tk = list(tickers)
        sql = ("SELECT c.doc_id, c.ticker, c.fy, c.page_start AS page, c.scope, c.section, "
               "group_concat(c.text, char(10)) AS text FROM chunk c "
               "JOIN document d ON d.doc_id = c.doc_id "
               f"WHERE d.doc_type=? AND c.ticker IN ({','.join('?' * len(tk))}) "
               "GROUP BY c.doc_id, c.page_start, c.scope, c.section ORDER BY c.doc_id, page")
        return [dict(r) for r in self.con.execute(sql, [doc_type, *tk])]

    def counts(self, doc_id: str) -> dict[str, int]:
        pages, scanned = self.con.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_scanned), 0) FROM page WHERE doc_id=?",
            (doc_id,)).fetchone()
        chunks = self.con.execute("SELECT COUNT(*) FROM chunk WHERE doc_id=?",
                                  (doc_id,)).fetchone()[0]
        return {"page": pages, "chunk": chunks, "scanned": scanned}


_CHUNK_COLS = [f.name for f in fields(Chunk)]
