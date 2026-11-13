"""XBRL ingestion: files -> document + xbrl_fact_raw -> fact -> validation.

Idempotent: a file is identified by the sha256 of its bytes, and canonical facts are
rebuilt from *all* stored XBRL filings of the affected tickers on every run, so
running twice gives the same rows, and adding a newer filing applies restatements
correctly whatever order files arrive in.

Note: rebuilding resets Phase 4 fields (verified/page) for those tickers; re-run
calibration after ingesting XBRL.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from filingLens.domain.registry import Registry
from filingLens.ingest.audit import missing_metrics
from filingLens.ingest.validate import CheckResult, qa_flags, run_checks
from filingLens.ingest.xbrl import (
    PARSER_NAME,
    PARSER_VERSION,
    BuildResult,
    FilingFacts,
    XbrlMeta,
    XbrlParseError,
    build_facts,
    facts_from_raw,
    is_result_filing,
    parse_xbrl_file,
    resolve_ticker,
    xbrl_meta,
)
from filingLens.storage.repos import DocumentRepo, FactRepo, RawFactRepo

RESTATEMENT = "restatement"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


@dataclass
class FileReport:
    path: str
    doc_id: str = ""
    ticker: str | None = None
    doc_type: str = ""
    scope: str = ""
    fy: str | None = None
    period_end: str = ""
    n_raw: int = 0
    n_facts: int = 0
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class IngestReport:
    files: list[FileReport] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    n_facts: int = 0
    n_restatements: int = 0
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.error is None for f in self.files)


def register_xbrl(con: sqlite3.Connection, path: str | Path, registry: Registry,
                  ticker: str | None = None) -> FileReport:
    """Parse one file and store its document row and raw facts. Does not build facts."""
    p = Path(path)
    rep = FileReport(str(p))
    try:
        parsed = parse_xbrl_file(p)
    except (XbrlParseError, OSError) as e:
        rep.error = str(e)
        return rep
    meta = xbrl_meta(parsed.facts)
    t = ticker or resolve_ticker(meta, p.name, registry, parsed.entity_ids)
    if t is None or t not in registry.companies:
        rep.error = (f"unknown company (symbol={meta.symbol!r}, name={meta.company_name!r}); "
                     "pass --ticker or add it to registry.yaml")
        return rep
    doc_id = sha256_file(p)
    is_result = is_result_filing(parsed.facts, registry)
    doc_type = "xbrl_result" if is_result else "xbrl_other"
    meta_d = meta.to_dict() | {"format": parsed.format, "warnings": parsed.warnings}
    DocumentRepo(con).upsert({
        "doc_id": doc_id, "ticker": t, "isin": registry.companies[t].isin, "doc_type": doc_type,
        "fy": meta.fy, "scope": meta.scope, "local_path": str(p.resolve()), "original_name": p.name,
        "parser": PARSER_NAME, "parser_ver": PARSER_VERSION, "meta_json": json.dumps(meta_d),
        "ingested_at": dt.datetime.now().isoformat(timespec="seconds")})
    RawFactRepo(con).replace(doc_id, parsed.facts)
    rep.doc_id, rep.ticker, rep.doc_type = doc_id, t, doc_type
    rep.scope, rep.fy, rep.period_end = meta.scope, meta.fy, meta.period_end
    rep.n_raw, rep.warnings = len(parsed.facts), list(parsed.warnings)
    if not is_result:
        rep.warnings.append("no mapped result tags found: stored as xbrl_other "
                            "(run `filinglens inspect` on it to see its tags)")
    return rep


def _meta_from_json(s: str | None) -> XbrlMeta:
    d = json.loads(s or "{}")
    known = XbrlMeta.__dataclass_fields__
    return XbrlMeta(**{k: v for k, v in d.items() if k in known})


def load_filings(con: sqlite3.Connection, tickers: list[str],
                 registry: Registry) -> list[FilingFacts]:
    raw = RawFactRepo(con)
    out = []
    for d in DocumentRepo(con).list(doc_type="xbrl_result", tickers=tickers):
        meta = _meta_from_json(d["meta_json"])
        out.append(facts_from_raw(raw.load(d["doc_id"]), meta, d["ticker"], d["doc_id"],
                                  registry))
    return out


def rebuild_facts(con: sqlite3.Connection, tickers: list[str], registry: Registry
                  ) -> tuple[BuildResult, list[CheckResult], list[FilingFacts]]:
    """Rebuild canonical facts and validation rows for these tickers from stored raw facts."""
    filings = load_filings(con, tickers, registry)
    built = build_facts(filings)
    repo = FactRepo(con)
    repo.delete_facts(tickers)
    repo.validation.clear(tickers)
    isin = {t: c.isin for t, c in registry.companies.items()}
    repo.upsert_facts(built.facts, isin_by_ticker=isin)   # table is empty: nothing to log
    repo.validation.add_many(
        (r.ticker, r.scope, r.period_end, RESTATEMENT, "info", r.detail)
        for r in built.restatements)
    checks = run_checks(built.facts)
    repo.validation.add_many(
        (c.ticker, c.scope, c.period_end, c.check, c.status, c.detail) for c in checks)
    repo.set_qa(tickers, qa_flags(checks))
    return built, checks, filings


def ingest_xbrl(con: sqlite3.Connection, paths: list[str | Path], registry: Registry,
                ticker: str | None = None) -> IngestReport:
    report = IngestReport()
    for p in paths:
        report.files.append(register_xbrl(con, p, registry, ticker))
    tickers = sorted({f.ticker for f in report.files if f.ticker and not f.error})
    report.tickers = tickers
    if not tickers:
        return report
    built, checks, filings = rebuild_facts(con, tickers, registry)
    by_doc = {f.doc_id: f for f in filings}
    for fr in report.files:
        if fr.doc_id in by_doc:
            fr.n_facts = len(by_doc[fr.doc_id].facts)
            fr.missing = missing_metrics(by_doc[fr.doc_id], registry)
    report.n_facts = len(built.facts)
    report.n_restatements = len(built.restatements)
    report.checks = checks
    return report
