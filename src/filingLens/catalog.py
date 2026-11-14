"""What data exists: locally (database + vector index) and on NSE (downloadable filings).

    local_status(con, registry, vectors)      one row per company: years, latest quarter,
                                              metrics, filings, reports, chunks, indexed, verified
    mark_have(filings, inbox, con)            tag each NSE filing "have" or "new"
    summarise_remote(filings)                 one row per company: years / quarters on NSE
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from filingLens.acquire import Filing
from filingLens.domain.fiscal import fy_int
from filingLens.domain.registry import Registry
from filingLens.storage.db import Database


def _fy_span(fys: list[str]) -> str:
    if not fys:
        return "-"
    s = sorted(set(fys), key=fy_int)
    missing = [f"FY{y % 100:02d}" for y in range(fy_int(s[0]), fy_int(s[-1]) + 1)
               if f"FY{y % 100:02d}" not in s]
    return f"{s[0]}-{s[-1]} ({len(s)})" + (f" missing {','.join(missing)}" if missing else "")


def local_status(con: Database, registry: Registry,
                 vectors: Path | None = None) -> list[dict]:
    q = lambda sql, *p: con.execute(sql, p).fetchall()
    indexed: dict[str, int] = defaultdict(int)
    if vectors and vectors.with_suffix(".json").exists():
        ids = set(json.loads(vectors.with_suffix(".json").read_text())["chunk_ids"])
        for cid, t in q("SELECT chunk_id, ticker FROM chunk"):
            indexed[t] += cid in ids
    tickers = sorted(set(registry.companies) | {r[0] for r in q("SELECT DISTINCT ticker FROM fact")})
    n_metrics = len(registry.metrics)
    out = []
    for t in tickers:
        fys = [r[0] for r in q("SELECT DISTINCT fy FROM fact WHERE ticker=? AND period_type='FY'", t)]
        latest = max(fys, key=fy_int) if fys else None
        mets = q("SELECT COUNT(DISTINCT metric_id) FROM fact WHERE ticker=? AND fy=? AND "
                 "period_type IN ('FY','INSTANT') AND method='xbrl'", t, latest or "")[0][0]
        lq = q("SELECT fy, quarter FROM fact WHERE ticker=? AND period_type='Q'", t)
        lq_label = max((f"{fy} {qq}" for fy, qq in lq), key=lambda s: (fy_int(s[:4]), s[-2:]),
                       default="-")
        scopes = sorted(r[0] for r in q("SELECT DISTINCT scope FROM fact WHERE ticker=?", t))
        docs = dict(q("SELECT doc_type, COUNT(*) FROM document WHERE ticker=? GROUP BY doc_type", t))
        reports = [r[0] or "?" for r in q("SELECT fy FROM document WHERE ticker=? AND "
                                          "doc_type='annual_report' ORDER BY fy", t)]
        chunks = q("SELECT COUNT(*) FROM chunk WHERE ticker=?", t)[0][0]
        elig, ver = q("SELECT COUNT(*), COUNT(*) FILTER (WHERE verified='pdf') FROM fact WHERE ticker=? AND "
                      "period_type IN ('FY','INSTANT')", t)[0]
        out.append({
            "ticker": t, "in_registry": t in registry.companies,
            "fy_facts": _fy_span(fys), "latest_quarter": lq_label,
            "metrics": f"{mets}/{n_metrics}" if latest else "-",
            "scopes": ",".join(scopes) or "-",
            "xbrl_files": docs.get("xbrl_result", 0), "xbrl_unusable": docs.get("xbrl_other", 0),
            "annual_reports": ",".join(reports) or "-", "chunks": chunks,
            "indexed": indexed.get(t, 0),
            "pdf_verified": f"{(ver or 0) / elig:.0%}" if elig else "-"})
    return out


def mark_have(filings: list[Filing], inbox: Path, con: Database | None) -> list[str]:
    """'have' when the file is in the inbox or already ingested (same file name), else 'new'."""
    known: set[str] = set()
    reports: set[tuple[str, str]] = set()              # (ticker, fy) of ingested annual reports
    if con is not None:
        known = {r[0] for r in con.execute("SELECT original_name FROM document")}
        reports = {(r[0], r[1]) for r in con.execute(
            "SELECT ticker, fy FROM document WHERE doc_type='annual_report'")}
    return ["have" if f.dest(inbox).exists() or f.filename in known
            or (f.kind == "annual_report" and (f.ticker, f.fy) in reports) else "new"
            for f in filings]


def summarise_remote(filings: list[Filing], status: list[str]) -> list[dict]:
    rows: dict[str, dict] = {}
    for f, st in zip(filings, status, strict=True):
        r = rows.setdefault(f.ticker, {"ticker": f.ticker, "annual_fy": set(), "quarters": set(),
                                       "reports": set(), "files": 0, "new": 0})
        r["files"] += 1
        r["new"] += st == "new"
        if f.kind == "annual_report":
            r["reports"].add(f.fy or "?")
        elif f.period == "Annual" or f.quarter == "Q4":
            r["annual_fy"].add(f.fy or "?")
        if f.quarter:
            r["quarters"].add(f.label)
    out = []
    for r in rows.values():
        fys = [x for x in r["annual_fy"] if x != "?"]
        qs = sorted(r["quarters"], key=lambda s: (fy_int(s[:4]), s[-2:]))
        out.append({"ticker": r["ticker"], "full_years": _fy_span(fys),
                    "quarters": f"{qs[0]} .. {qs[-1]} ({len(qs)})" if qs else "-",
                    "annual_reports": ",".join(sorted(r["reports"])) or "-",
                    "files": r["files"], "new": r["new"]})
    return out
