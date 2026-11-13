"""PDF ingestion, calibration and whole-inbox ingestion.

    ingest_pdf      file -> document + page + chunk rows (skipped when already parsed)
    calibrate_db    facts + stored report text -> citations on the fact rows
    ingest_inbox    work/inbox/<TICKER>/... -> XBRL, then PDFs, then calibration

Run calibration after every XBRL ingest: rebuilding facts clears their citations.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from filingLens.domain.registry import Registry
from filingLens.ingest.calibrate import PageText, TickerReport, calibrate
from filingLens.ingest.errors import IngestError
from filingLens.ingest.pdf import PARSER_NAME, PARSER_VERSION, parse_pdf
from filingLens.ingest.pipeline import IngestReport, ingest_xbrl
from filingLens.ingest.utils.files import XBRL_SUFFIXES, sha256_file
from filingLens.ingest.xbrl import XbrlMeta, resolve_ticker
from filingLens.storage.repos import DocumentRepo, FactRepo, TextRepo


@dataclass
class PdfReport:
    path: str
    doc_id: str = ""
    ticker: str | None = None
    doc_type: str = ""
    fy: str | None = None
    n_pages: int = 0
    n_chunks: int = 0
    n_scanned: int = 0
    parse_quality: float = 0.0
    skipped: bool = False          # already parsed with this parser version
    error: str | None = None


@dataclass
class InboxReport:
    xbrl: IngestReport | None = None
    pdfs: list[PdfReport] = field(default_factory=list)
    calibration: dict[str, TickerReport] = field(default_factory=dict)


def pdf_doc_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("brsr", "sustainab", "esg")):
        return "brsr"
    if "transcript" in n:
        return "transcript"
    if any(k in n for k in ("presentation", "ppt")):
        return "presentation"
    return "annual_report"


def ingest_pdf(con: sqlite3.Connection, path: str | Path, registry: Registry,
               ticker: str | None = None, doc_type: str | None = None,
               force: bool = False) -> PdfReport:
    p = Path(path)
    rep = PdfReport(str(p))
    t = ticker or resolve_ticker(XbrlMeta(), p.name, registry)
    if t is None or t not in registry.companies:
        rep.error = "unknown company: pass --ticker or put the file under inbox/<TICKER>/"
        return rep
    rep.ticker, rep.doc_type = t, doc_type or pdf_doc_type(p.name)
    rep.doc_id = doc_id = sha256_file(p)
    docs, text = DocumentRepo(con), TextRepo(con)
    old = docs.get(doc_id)
    if not force and old and old["parser_ver"] == PARSER_VERSION and old["n_pages"]:
        c = text.counts(doc_id)
        rep.skipped, rep.fy, rep.n_pages, rep.n_chunks = True, old["fy"], c["page"], c["chunk"]
        rep.n_scanned = c["scanned"]
        rep.parse_quality = old["parse_quality"] or 0.0
        return rep
    try:
        parsed = parse_pdf(p, doc_id, t, registry.companies[t].name, rep.doc_type)
    except (RuntimeError, ValueError) as e:            # PyMuPDF raises these on bad files
        rep.error = f"{p.name}: cannot read PDF ({e})"
        return rep
    docs.upsert({
        "doc_id": doc_id, "ticker": t, "isin": registry.companies[t].isin,
        "doc_type": rep.doc_type, "fy": parsed.fy, "local_path": str(p.resolve()),
        "original_name": p.name, "parser": PARSER_NAME, "parser_ver": PARSER_VERSION,
        "n_pages": len(parsed.pages), "parse_quality": parsed.parse_quality,
        "meta_json": json.dumps({}), "ingested_at": dt.datetime.now().isoformat(timespec="seconds")})
    text.replace(doc_id, parsed.pages, parsed.chunks)
    rep.fy, rep.n_pages, rep.n_chunks = parsed.fy, len(parsed.pages), len(parsed.chunks)
    rep.n_scanned = sum(pg.is_scanned for pg in parsed.pages)
    rep.parse_quality = parsed.parse_quality
    return rep


def calibrate_db(con: sqlite3.Connection, tickers: list[str]) -> dict[str, TickerReport]:
    """Recompute citations for these tickers from stored facts and annual-report text."""
    facts = FactRepo(con).list_facts(tickers)
    pages = [PageText(**r) for r in TextRepo(con).page_texts(tickers)]
    citations, reports = calibrate(facts, pages)
    FactRepo(con).set_citations(tickers, [(c.pdf_doc_id, c.page, c.verified, c.key)
                                          for c in citations])
    return reports


def ingest_inbox(con: sqlite3.Connection, inbox: str | Path, registry: Registry,
                 force: bool = False) -> InboxReport:
    """inbox/<TICKER>/** : XBRL files first (facts), then PDFs (text), then calibration."""
    out = InboxReport()
    xbrl: list[tuple[Path, str]] = []
    pdfs: list[tuple[Path, str]] = []
    for tdir in sorted(Path(inbox).iterdir()):
        t = tdir.name.upper()
        if not tdir.is_dir() or t not in registry.companies:
            continue
        for f in sorted(tdir.rglob("*")):
            if f.suffix.lower() in (*XBRL_SUFFIXES, ".zip"):
                xbrl.append((f, t))
            elif f.suffix.lower() == ".pdf":
                pdfs.append((f, t))
    if not xbrl and not pdfs:
        raise IngestError(f"{inbox}: no files under <TICKER>/ folders for {list(registry.companies)}")
    for t in sorted({t for _, t in xbrl}):
        rep = ingest_xbrl(con, [f for f, tt in xbrl if tt == t], registry, ticker=t)
        if out.xbrl is None:
            out.xbrl = rep
        else:
            out.xbrl.files += rep.files
            out.xbrl.tickers += rep.tickers
            out.xbrl.checks += rep.checks
            out.xbrl.n_facts += rep.n_facts
            out.xbrl.n_restatements += rep.n_restatements
    out.pdfs = [ingest_pdf(con, f, registry, ticker=t, force=force) for f, t in pdfs]
    out.calibration = calibrate_db(con, sorted({t for _, t in xbrl + pdfs}))
    return out
