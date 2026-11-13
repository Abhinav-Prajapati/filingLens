"""Repositories. Services depend on the Protocols, so tests can swap implementations."""
from __future__ import annotations

import sqlite3
from typing import Protocol

from copilot.domain.fiscal import fy_int
from copilot.domain.models import Fact

_FACT_COLS = (
    "isin ticker metric_id period_type fy quarter period_start period_end scope value unit "
    "orig_value orig_unit xbrl_tag doc_id pdf_doc_id page verified qa method"
).split()


class FactRepoProtocol(Protocol):
    def upsert_facts(self, facts: list[Fact], isin_by_ticker: dict[str, str] | None = None) -> int: ...
    def get_series(self, ticker: str, metric_id: str, scope: str = "consolidated",
                   period_type: str = "FY", fy_from: str | None = None,
                   fy_to: str | None = None) -> list[Fact]: ...
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

    def list(self, check: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM validation", []
        if check:
            sql += " WHERE check_name=?"
            params.append(check)
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
            if prev is not None and abs(prev["orig_value"] - f.orig_value) > 1e-6 * max(
                    1.0, abs(f.orig_value)):
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
