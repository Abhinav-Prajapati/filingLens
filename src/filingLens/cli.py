"""Command line entry point.

    filinglens inspect      FILE.xml [--ticker TCS] [--json]   parse one XBRL file, no database
    filinglens inspect-pdf  FILE.pdf [--page 91]               parse one PDF, no database
    filinglens ingest-xbrl  FILE_OR_DIR ... [--db work/filingLens.db] [--ticker TCS]
    filinglens ingest-pdf   FILE_OR_DIR ... [--db ...] [--ticker TCS] [--force]
    filinglens ingest       [work/inbox] [--db ...] [--force]  XBRL + PDFs + calibration
    filinglens download     [TCS INFY ...] [--fy FY24] [--list] NSE filings -> work/inbox
    filinglens trend        TCS revenue_from_operations         values, YoY, CAGR
    filinglens compare      net_margin --ticker TCS --ticker INFY   side by side
    filinglens ask          "TCS ka revenue pichle 2 saal"      question -> tool call -> answer
    filinglens llm-check    [-n 5]                              test the LLM connection
    filinglens index        [--db ...] [--model bge-m3|hash]   embed new chunks -> vectors.npy
    filinglens search "QUERY" [--ticker TCS] [--fy FY25] [--section mdna] [-k 5]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from filingLens.domain.registry import load_registry
from filingLens.domain.units import fmt_change, fmt_indian, fmt_value, to_crore
from filingLens.ingest.audit import metric_coverage, period_type_counts, unmapped_tags
from filingLens.ingest.calibrate import TickerReport
from filingLens.ingest.errors import IngestError
from filingLens.ingest.pdf import parse_pdf
from filingLens.ingest.pdf_pipeline import (
    PdfReport,
    calibrate_db,
    ingest_inbox,
    ingest_pdf,
    pdf_doc_type,
)
from filingLens.ingest.pipeline import IngestReport, ingest_xbrl
from filingLens.ingest.utils.files import XBRL_SUFFIXES
from filingLens.ingest.validate import run_checks
from filingLens.ingest.xbrl import (
    XbrlMeta,
    XbrlParseError,
    facts_from_raw,
    is_result_filing,
    parse_xbrl_file,
    resolve_ticker,
    xbrl_meta,
)
from filingLens.storage.repos import FactRepo

INBOX_SUFFIXES = (*XBRL_SUFFIXES, ".zip")


def _table(rows: Sequence[dict], cols: Sequence[str]) -> str:
    if not rows:
        return "  (none)"
    w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    line = lambda r: "  " + "  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols)
    return "\n".join([line({c: c for c in cols}), line({c: "-" * w[c] for c in cols}),
                      *(line(r) for r in rows)])


def _fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return ""
    return fmt_indian(v, 2) if "per share" in unit or abs(v) < 100 else fmt_indian(v, 0)


def cmd_inspect(args: argparse.Namespace) -> int:
    reg = load_registry()
    path = Path(args.file)
    try:
        parsed = parse_xbrl_file(path)
    except (XbrlParseError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    meta = xbrl_meta(parsed.facts)
    ticker = args.ticker or resolve_ticker(meta, path.name, reg, parsed.entity_ids) or "?"
    filing = facts_from_raw(parsed.facts, meta, ticker, "inspect", reg)
    checks = run_checks(filing.facts)

    if args.json:
        print(json.dumps({
            "file": str(path), "format": parsed.format, "ticker": ticker, "meta": meta.to_dict(),
            "warnings": parsed.warnings, "notes": filing.notes,
            "period_type_counts": period_type_counts(parsed.facts),
            "facts": [f.model_dump() for f in filing.facts],
            "checks": [c.__dict__ for c in checks]}, indent=1, default=str))
        return 0

    n_dim = sum(r.has_dim for r in parsed.facts)
    print(f"file      {path.name}")
    print(f"format    {parsed.format}   contexts={len(parsed.contexts)}  raw facts="
          f"{len(parsed.facts)} ({n_dim} dimensional, ignored for headline numbers)")
    print(f"company   {meta.company_name}  symbol={meta.symbol}  -> ticker {ticker}")
    print(f"filing    scope={meta.scope}  {meta.fy}  period {meta.period_start}..{meta.period_end}"
          f"  reporting={meta.reporting_quarter}  {meta.audited}")
    print(f"units     declared rounding={meta.rounding}  basis={meta.unit_basis} "
          f"(values / 1e7 = INR crore)")
    print(f"result?   {is_result_filing(parsed.facts, reg)}")
    for w in parsed.warnings:
        print(f"WARNING   {w}")
    for n in filing.notes:
        print(f"note      {n}")

    print("\nperiod types (non-dimensional numeric facts):")
    print(_table([{"period_type": k, "n": v} for k, v in period_type_counts(parsed.facts).items()],
                 ["period_type", "n"]))

    print("\ncanonical facts:")
    rows = [{"metric": f.metric_id, "type": f.period_type, "fy": f.fy, "q": f.quarter or "",
                 "period": f"{f.period_start or '':10}..{f.period_end}",
                 "value": _fmt_value(f.value, f.unit), "unit": f.unit, "tag": f.xbrl_tag}
            for f in sorted(filing.facts, key=lambda f: (f.metric_id, f.period_type))]
    print(_table(rows, ["metric", "type", "fy", "q", "period", "value", "unit", "tag"]))

    cov = [c for c in metric_coverage(filing, reg) if c["found"] == "MISSING"]
    if cov:
        print("\nMISSING metrics:", ", ".join(c["metric_id"] for c in cov))

    print("\nvalidation:")
    print(_table([{"check": c.check, "period": f"{c.period_start}..{c.period_end}",
                       "status": c.status, "detail": c.detail} for c in checks],
                 ["check", "period", "status", "detail"]))

    print(f"\nlargest unmapped tags (top {args.top}):")
    um = unmapped_tags(parsed.facts, reg, args.top)
    print(_table([dict(u, max_abs=(fmt_indian(to_crore(u["max_abs"], 1.0), 2) + " cr")
                       if u["unit"].endswith("INR") else fmt_indian(u["max_abs"], 2))
                  for u in um], ["tag", "kind", "n", "max_abs", "unit"]))
    return 0


def _expand(paths: Sequence[str], suffixes: Sequence[str] = INBOX_SUFFIXES) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.rglob("*") if x.suffix.lower() in suffixes)
        else:
            out.append(p)
    return out


@contextmanager
def _db(path: str) -> Iterator[sqlite3.Connection]:
    from filingLens.storage.db import connect

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


def _print_xbrl(con: sqlite3.Connection, rep: IngestReport) -> None:
    print(_table([{"file": Path(f.path).name[:48], "ticker": f.ticker or "", "type": f.doc_type,
                   "scope": f.scope, "fy": f.fy or "", "period_end": f.period_end, "raw": f.n_raw,
                   "facts": f.n_facts, "missing": len(f.missing), "error": f.error or ""}
                  for f in rep.files],
                 ["file", "ticker", "type", "scope", "fy", "period_end", "raw", "facts",
                  "missing", "error"]))
    for f in rep.files:
        for w in f.warnings:
            print(f"WARNING {Path(f.path).name}: {w}")
    bad = [c for c in rep.checks if c.status != "pass"]
    print(f"\nfacts={rep.n_facts}  restatements={rep.n_restatements}  "
          f"checks={len(rep.checks)} (not passing: {len(bad)})")
    for c in bad:
        print(f"  {c.status:4} {c.ticker} {c.scope} {c.period_end} {c.check}: {c.detail}")
    fy = [c for c in FactRepo(con).coverage() if c["period_type"] == "FY"]
    print("\nFY coverage:")
    print(_table(fy, ["ticker", "scope", "metric_id", "n", "first_fy", "last_fy", "pdf_verified"]))


def _print_pdfs(reps: list[PdfReport]) -> None:
    print(_table([{"file": Path(r.path).name[:48], "ticker": r.ticker or "", "type": r.doc_type,
                   "fy": r.fy or "", "pages": r.n_pages, "chunks": r.n_chunks,
                   "scanned": r.n_scanned, "quality": r.parse_quality,
                   "status": r.error or ("cached" if r.skipped else "parsed")} for r in reps],
                 ["file", "ticker", "type", "fy", "pages", "chunks", "scanned", "quality",
                  "status"]))


def _print_calibration(reports: dict[str, TickerReport]) -> None:
    print("\ncalibration (FY + INSTANT facts found in the annual report):")
    print(_table([{"ticker": t, "eligible": r.eligible, "verified": r.verified,
                   "rate": f"{r.rate:.0%}", "scope_mismatch": r.scope_mismatch,
                   "units": " ".join(f"{k}={v}" for k, v in r.unit_votes.items()),
                   "unit_check": "ok" if r.unit_ok else "MISMATCH"}
                  for t, r in reports.items()],
                 ["ticker", "eligible", "verified", "rate", "scope_mismatch", "units",
                  "unit_check"]))
    for t, r in reports.items():
        for nf in r.not_found:
            print(f"  not found {t}: {nf}")


def cmd_ingest_xbrl(args: argparse.Namespace) -> int:
    reg = load_registry()
    with _db(args.db) as con:
        rep = ingest_xbrl(con, _expand(args.paths), reg, ticker=args.ticker)
        _print_xbrl(con, rep)
        if rep.tickers:                     # rebuilding facts cleared their citations
            _print_calibration(calibrate_db(con, rep.tickers))
    return 0 if rep.ok else 1


def cmd_ingest_pdf(args: argparse.Namespace) -> int:
    reg = load_registry()
    with _db(args.db) as con:
        reps = [ingest_pdf(con, p, reg, ticker=args.ticker, force=args.force)
                for p in _expand(args.paths, (".pdf",))]
        _print_pdfs(reps)
        tickers = sorted({r.ticker for r in reps if r.ticker and not r.error})
        if tickers:
            _print_calibration(calibrate_db(con, tickers))
    return 0 if all(r.error is None for r in reps) else 1


def cmd_ingest(args: argparse.Namespace) -> int:
    reg = load_registry()
    with _db(args.db) as con:
        try:
            rep = ingest_inbox(con, args.inbox, reg, force=args.force)
        except IngestError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if rep.xbrl:
            _print_xbrl(con, rep.xbrl)
        print()
        _print_pdfs(rep.pdfs)
        _print_calibration(rep.calibration)
    ok = (rep.xbrl is None or rep.xbrl.ok) and all(r.error is None for r in rep.pdfs)
    return 0 if ok else 1


def cmd_inspect_pdf(args: argparse.Namespace) -> int:
    reg = load_registry()
    path = Path(args.file)
    ticker = args.ticker or resolve_ticker(XbrlMeta(), path.name, reg) or "?"
    company = reg.companies[ticker].name if ticker in reg.companies else ticker
    try:
        parsed = parse_pdf(path, "inspect", ticker, company, pdf_doc_type(path.name))
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"file      {path.name}\nticker    {ticker}   fy {parsed.fy}   pages {len(parsed.pages)}"
          f"   panels {len(parsed.panels)}   chunks {len(parsed.chunks)}"
          f"   quality {parsed.parse_quality}")
    if args.page:
        for c in parsed.chunks:
            if c.page_start == args.page:
                print(f"\n--- {c.context_line}\n{c.text}")
        return 0
    rows, prev = [], None
    for p in parsed.panels:                  # one row per run of panels with the same section
        if (p.section, p.scope) != prev:
            rows.append({"from_page": f"{p.page_no}{p.side}", "printed": p.printed or "",
                         "section": p.section, "scope": p.scope})
            prev = (p.section, p.scope)
    print("\nsections (each row runs until the next one):")
    print(_table(rows, ["from_page", "printed", "section", "scope"]))
    return 0


def _vectors_path(args: argparse.Namespace) -> Path:
    return Path(args.vectors) if args.vectors else Path(args.db).parent / "vectors.npy"


def cmd_index(args: argparse.Namespace) -> int:
    from filingLens.retrieval.embedder import get_embedder
    from filingLens.retrieval.index import update_index

    with _db(args.db) as con:
        n = con.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
        if not n:
            print("ERROR: no chunks in the database; run `filinglens ingest` first",
                  file=sys.stderr)
            return 2
        emb = get_embedder(args.model)
        print(f"model {emb.name}  dim {emb.dim}  device {getattr(emb, 'device', 'cpu')}")
        _, rep = update_index(con, emb, _vectors_path(args))
    print(f"vectors {rep.total}  added {rep.added}  removed {rep.removed}"
          f"{'  (rebuilt: new model or no index)' if rep.rebuilt else ''}  -> {_vectors_path(args)}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from filingLens.retrieval.index import VectorIndex
    from filingLens.retrieval.search import Searcher, SearchFilters

    filters = SearchFilters(tuple(t.upper() for t in args.ticker), tuple(args.fy),
                            tuple(args.doc_type), tuple(args.scope), tuple(args.section))
    with _db(args.db) as con:
        index = None if args.keyword_only else VectorIndex.load(_vectors_path(args))
        emb = None
        if index is not None:
            from filingLens.retrieval.embedder import get_embedder
            emb = get_embedder("hash" if index.model.startswith("hash") else index.model)
        elif not args.keyword_only:
            print("note: no vector index (run `filinglens index`); keyword search only",
                  file=sys.stderr)
        hits = Searcher(con, index, emb).search(args.query, filters, k=args.k)
    for i, h in enumerate(hits, 1):
        ranks = " ".join(f"{k}#{v}" for k, v in sorted(h.ranks.items()))
        snippet = re.sub(r"\s+", " ", h.text)[:args.chars]
        print(f"[{i}] {h.context_line}   ({ranks}, rrf {h.score:.4f})\n    {snippet}\n")
    if not hits:
        print("no results")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    import datetime as dt

    from filingLens.acquire import NSEClient, acquire

    reg = load_registry()
    tickers = [t.upper() for t in (args.tickers or list(reg.companies))]
    unknown = [t for t in tickers if t not in reg.companies]
    if unknown:
        print(f"ERROR: not in registry.yaml: {unknown}", file=sys.stderr)
        return 2
    years = int(args.years)
    to_date = dt.date.today()
    rep = acquire(NSEClient(delay=args.delay), args.inbox, tickers,
                  periods=args.period or ["Annual"], fys=args.fy, scope=args.scope,
                  from_date=to_date.replace(year=to_date.year - years), to_date=to_date,
                  annual_reports=args.annual_reports, dry_run=args.list, limit=args.limit)
    print(_table([{"ticker": f.ticker, "kind": f.kind, "period": f.period, "fy": f.fy or "?",
                   "scope": f.scope or "?", "file": f.filename[:60]} for f in rep.found],
                 ["ticker", "kind", "period", "fy", "scope", "file"]))
    if args.list:
        print(f"\n{len(rep.found)} files listed (dry run, nothing downloaded)")
    else:
        print(f"\ndownloaded {len(rep.downloaded)}  already had {len(rep.skipped)}  "
              f"failed {len(rep.failed)}  -> {args.inbox}")
        for u in rep.failed:
            print(f"  failed: {u}")
        if rep.downloaded:
            print(f"next: filinglens ingest {args.inbox} && filinglens index")
    if rep.blocked:
        print(f"STOPPED: {rep.blocked}", file=sys.stderr)
        return 1
    return 0 if not rep.failed else 1


def _getter(con: sqlite3.Connection):  # type: ignore[no-untyped-def]
    repo = FactRepo(con)
    return lambda t, m, s, p: repo.get_series(t, m, scope=s, period_type=p)


def cmd_trend(args: argparse.Namespace) -> int:
    from filingLens.analytics.derived import load_series
    from filingLens.analytics.trend import build_trend

    reg = load_registry()
    with _db(args.db) as con:
        try:
            facts, notes = load_series(_getter(con), reg, args.ticker.upper(), args.metric,
                                       args.scope, args.period)
        except KeyError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    if not facts:
        print("no data: " + "; ".join(notes))
        return 1
    t = build_trend(facts)
    print(f"{t.ticker}  {t.metric_id}  ({t.scope}, {t.unit})")
    print(_table([{"period": r.label, "value": fmt_value(r.value, t.unit),
                   "yoy": fmt_change(r.change, t.is_ratio),
                   "source": (f"p.{r.fact.page} {r.fact.verified}" if r.fact and r.fact.page
                              else (r.fact.method if r.fact else ""))} for r in t.rows],
                 ["period", "value", "yoy", "source"]))
    if t.cagr is not None and t.cagr_span:
        print(f"CAGR {t.cagr_span[0]}-{t.cagr_span[1]}: {fmt_change(t.cagr)}")
    for n in notes + t.notes:
        print(f"note: {n}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from filingLens.analytics.compare import build_comparison
    from filingLens.analytics.derived import load_series

    reg = load_registry()
    tickers = [t.upper() for t in args.ticker]
    if len(tickers) > 1 and len(args.metrics) > 1:
        print("ERROR: compare several companies on one metric, or several metrics for one "
              "company", file=sys.stderr)
        return 2
    axis = "companies" if len(tickers) > 1 else ("metrics" if len(args.metrics) > 1 else "periods")
    pairs = ([(t, args.metrics[0], t) for t in tickers] if axis == "companies"
             else [(tickers[0], m, m) for m in args.metrics])
    series, notes = {}, []
    with _db(args.db) as con:
        get = _getter(con)
        for t, m, label in pairs:
            try:
                series[label], n = load_series(get, reg, t, m, args.scope, args.period)
            except KeyError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 2
            notes += n
    try:
        c = build_comparison(series, axis, args.normalise, args.fy or None)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    import pandas as pd

    rows = []
    for period, r in c.frame.iterrows():
        row: dict[str, str] = {"period": str(period)}
        for col in c.frame.columns:
            v = None if pd.isna(r[col]) else float(r[col])
            row[col] = fmt_change(v) if col == "change_vs_first" else fmt_value(
                v, "%" if c.unit in ("%", "% of total") else c.unit)
        rows.append(row)
    print(f"{axis}: {', '.join(series)}  ({args.scope}, {c.unit})")
    print(_table(rows, ["period", *map(str, c.frame.columns)]))
    for note in notes + c.notes:
        print(f"note: {note}")
    return 0


def _llm_context(con: sqlite3.Connection, vectors: Path):  # type: ignore[no-untyped-def]
    from filingLens.llm.executor import Context

    def searcher():  # type: ignore[no-untyped-def]
        from filingLens.retrieval.index import VectorIndex
        from filingLens.retrieval.search import Searcher

        index = VectorIndex.load(vectors)
        if index is None:
            return Searcher(con)
        try:
            from filingLens.retrieval.embedder import get_embedder
            emb = get_embedder("hash" if index.model.startswith("hash") else index.model)
        except ImportError:                       # embed extra not installed: keyword only
            return Searcher(con)
        return Searcher(con, index, emb)

    return Context(con, load_registry(), searcher)


_PAGE = re.compile(r"page (\d+)(?: \(p\. (\d+)\))?")


def _source_label(source: str) -> str:
    """'... | mdna | scope: none | page 44 (p. 84)' -> 'p.44 (printed 84, mdna)'."""
    m = _PAGE.search(source)
    section = source.split(" | ")[2] if source.count(" | ") >= 2 else ""
    if not m:
        return source
    return f"p.{m.group(1)}" + (f" (printed {m.group(2)}, {section})" if m.group(2)
                                else f" ({section})")


def _print_tool_result(res, evidence: bool = True) -> None:  # type: ignore[no-untyped-def]
    from filingLens.llm.answer import _cell

    if res.clarify:
        print(f"? {res.clarify}")
        return
    if res.tool == "search_reports":
        if evidence:
            for row in res.rows:
                snippet = re.sub(r"\s+", " ", row["text"])[:300]
                print(f"[{row['rank']}] {row['source']}\n    {snippet}\n")
        labels = list(dict.fromkeys(_source_label(r["source"]) for r in res.rows))
        if labels:
            print("sources: " + "; ".join(labels))
    else:
        print(f"{res.title}" + (f"  [{res.unit}]" if res.unit else ""))
        if res.rows:
            print(_table([{c: _cell(c, r.get(c), res.unit) for c in res.columns}
                          for r in res.rows], res.columns))
        pages = sorted({(c.get("ticker"), c.get("page")) for c in res.citations
                        if c.get("page")}, key=str)
        if pages:
            print("sources: " + ", ".join(f"{t} annual report p.{p}" for t, p in pages))
    for n in res.notes:
        print(f"note: {n}")


def cmd_ask(args: argparse.Namespace) -> int:
    from filingLens.llm.answer import Answer, compose_answer
    from filingLens.llm.client import LLMConfig, make_client
    from filingLens.llm.planner import ask
    from filingLens.llm.prompt import build_system_prompt

    cfg = LLMConfig.from_env()
    with _db(args.db) as con:
        ctx = _llm_context(con, _vectors_path(args))
        if args.show_prompt:
            print(build_system_prompt(ctx.registry, ctx.latest_fy()))
            return 0
        if cfg.missing():
            print(f"ERROR: LLM not configured; set {', '.join(cfg.missing())} (env or .env)",
                  file=sys.stderr)
            return 2
        client = make_client(cfg)
        res = ask(args.question, client, ctx)
        ans = (compose_answer(args.question, res.result, client)
               if res.ok and res.result and not args.no_answer else Answer())
        if args.json:
            print(json.dumps({"question": res.question, "tool": res.tool, "args": res.args,
                              "attempts": res.attempts, "errors": res.errors,
                              "seconds": res.seconds, "answer": ans.__dict__,
                              "result": res.result.__dict__ if res.result else None},
                             indent=1, default=str))
            return 0 if res.ok else 1
        print(f"→ {res.tool}({json.dumps(res.args, ensure_ascii=False)})   "
              f"[{res.seconds}s, attempts {res.attempts}]")
        for e in res.errors:
            print(f"  model error fixed by retry: {e}" if res.ok else f"  error: {e}")
        print()
        if ans.text:
            print(f"{ans.text}\n")
        elif ans.rejected:
            print(f"(no written answer: {ans.rejected}; showing the data instead)\n")
        if res.result:
            show_passages = args.evidence or not ans.text
            _print_tool_result(res.result, evidence=show_passages)
    return 0 if res.ok else 1


def cmd_llm_check(args: argparse.Namespace) -> int:
    from filingLens.llm.client import LLMConfig, make_client
    from filingLens.llm.planner import ask
    from filingLens.llm.prompt import load_examples

    cfg = LLMConfig.from_env()
    print(f"config: {cfg.masked()}")
    if cfg.missing():
        print(f"ERROR: set {', '.join(cfg.missing())} (environment or .env)", file=sys.stderr)
        return 2
    examples = load_examples()[: args.n]
    client = make_client(cfg)
    ok = 0
    with _db(args.db) as con:
        ctx = _llm_context(con, _vectors_path(args))
        for e in examples:
            res = ask(e["q"], client, ctx)
            good = res.tool == e["tool"]
            ok += good
            print(f"{'PASS' if good else 'FAIL'}  {res.seconds:5.2f}s  {e['q']!r} -> {res.tool} "
                  f"{json.dumps(res.args, ensure_ascii=False) if res.args else res.errors}")
    print(f"\n{ok}/{len(examples)} picked the expected tool (note: these examples are also in "
          "the prompt, so this checks wiring, not accuracy)")
    return 0 if ok == len(examples) else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="filinglens")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="parse one XBRL file and print what was found")
    p.add_argument("file")
    p.add_argument("--ticker")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("inspect-pdf", help="parse one PDF (no database): sections, chunks")
    p.add_argument("file")
    p.add_argument("--ticker")
    p.add_argument("--page", type=int, help="print the chunks of this PDF page")
    p.set_defaults(func=cmd_inspect_pdf)
    for name, func, hlp in (("ingest-xbrl", cmd_ingest_xbrl, "ingest XBRL files or folders"),
                            ("ingest-pdf", cmd_ingest_pdf, "ingest PDF files or folders")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("paths", nargs="+")
        p.add_argument("--db", default="work/filingLens.db")
        p.add_argument("--ticker", help="force the ticker for every file")
        if name == "ingest-pdf":
            p.add_argument("--force", action="store_true", help="re-parse cached PDFs")
        p.set_defaults(func=func)
    p = sub.add_parser("ingest", help="ingest inbox/<TICKER>/ (XBRL + PDFs) and calibrate")
    p.add_argument("inbox", nargs="?", default="work/inbox")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--force", action="store_true", help="re-parse cached PDFs")
    p.set_defaults(func=cmd_ingest)
    p = sub.add_parser("download", help="download NSE filings into the inbox (polite, cached)")
    p.add_argument("tickers", nargs="*", help="default: every company in registry.yaml")
    p.add_argument("--inbox", default="work/inbox")
    p.add_argument("--period", action="append", choices=["Annual", "Quarterly"],
                   help="results period (repeatable; default Annual)")
    p.add_argument("--fy", action="append", default=[], help="only these FYs, e.g. --fy FY24")
    p.add_argument("--scope", default="both", choices=["both", "consolidated", "standalone"])
    p.add_argument("--years", default=5, help="look back this many years (default 5)")
    p.add_argument("--annual-reports", action="store_true", help="also annual-report PDFs")
    p.add_argument("--list", action="store_true", help="dry run: list what would be fetched")
    p.add_argument("--limit", type=int, help="download at most N files")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("trend", help="one metric over time: values, YoY, CAGR")
    p.add_argument("ticker")
    p.add_argument("metric", help="registry metric or derived: net_margin, pbt_margin, "
                                  "effective_tax_rate, employee_cost_ratio")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--scope", default="consolidated", choices=["consolidated", "standalone"])
    p.add_argument("--period", default="FY", choices=["FY", "Q"])
    p.set_defaults(func=cmd_trend)
    p = sub.add_parser("compare", help="companies, metrics or years side by side")
    p.add_argument("metrics", nargs="+")
    p.add_argument("--ticker", action="append", required=True)
    p.add_argument("--fy", action="append", default=[], help="only these years, e.g. FY24")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--scope", default="consolidated", choices=["consolidated", "standalone"])
    p.add_argument("--period", default="FY", choices=["FY", "Q"])
    p.add_argument("--normalise", default="none", choices=["none", "index100", "share"])
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser("ask", help="ask a question in English or Hinglish (needs an LLM)")
    p.add_argument("question", nargs="?", default="")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--vectors")
    p.add_argument("--json", action="store_true")
    p.add_argument("--show-prompt", action="store_true", help="print the system prompt and exit")
    p.add_argument("--no-answer", action="store_true", help="skip the written answer step")
    p.add_argument("--evidence", action="store_true", help="also print the retrieved passages")
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser("llm-check", help="test the LLM connection on a few example questions")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--vectors")
    p.add_argument("-n", type=int, default=5)
    p.set_defaults(func=cmd_llm_check)
    p = sub.add_parser("index", help="embed new chunks into the vector file (incremental)")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--vectors", help="default: vectors.npy next to the database")
    p.add_argument("--model", default="bge-m3", help="bge-m3 (default), hash, or a model id")
    p.set_defaults(func=cmd_index)
    p = sub.add_parser("search", help="hybrid search over report text")
    p.add_argument("query")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--vectors")
    p.add_argument("--ticker", action="append", default=[])
    p.add_argument("--fy", action="append", default=[])
    p.add_argument("--doc-type", action="append", default=[])
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--section", action="append", default=[])
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--chars", type=int, default=300, help="snippet length")
    p.add_argument("--keyword-only", action="store_true")
    p.set_defaults(func=cmd_search)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
