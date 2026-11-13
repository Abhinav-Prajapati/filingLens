"""Command line entry point.

    filinglens inspect  FILE.xml [--ticker TCS] [--json]      parse one file, no database
    filinglens ingest-xbrl FILE_OR_DIR ... [--db work/filingLens.db] [--ticker TCS]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from filingLens.domain.registry import load_registry
from filingLens.domain.units import fmt_indian, to_crore
from filingLens.ingest.audit import metric_coverage, period_type_counts, unmapped_tags
from filingLens.ingest.utils.files import XBRL_SUFFIXES
from filingLens.ingest.validate import run_checks
from filingLens.ingest.xbrl import (
    XbrlParseError,
    facts_from_raw,
    is_result_filing,
    parse_xbrl_file,
    resolve_ticker,
    xbrl_meta,
)

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


def _expand(paths: Sequence[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.rglob("*") if x.suffix.lower() in INBOX_SUFFIXES)
        else:
            out.append(p)
    return out


def cmd_ingest_xbrl(args: argparse.Namespace) -> int:
    from filingLens.ingest.pipeline import ingest_xbrl
    from filingLens.storage.db import connect

    reg = load_registry()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = connect(args.db)
    try:
        return _ingest_and_report(con, args, reg, ingest_xbrl)
    finally:
        con.close()


def _ingest_and_report(con, args, reg, ingest_xbrl) -> int:  # type: ignore[no-untyped-def]
    rep = ingest_xbrl(con, list(_expand(args.paths)), reg, ticker=args.ticker)
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
          f"checks={len(rep.checks)} (not passing: {len(bad)})  db={args.db}")
    for c in bad:
        print(f"  {c.status:4} {c.ticker} {c.scope} {c.period_end} {c.check}: {c.detail}")
    if rep.tickers:
        from filingLens.storage.repos import FactRepo
        fy = [c for c in FactRepo(con).coverage() if c["period_type"] == "FY"]
        print("\nFY coverage:")
        print(_table(fy, ["ticker", "scope", "metric_id", "n", "first_fy", "last_fy"]))
    return 0 if rep.ok else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="filinglens")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="parse one XBRL file and print what was found")
    p.add_argument("file")
    p.add_argument("--ticker")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("ingest-xbrl", help="ingest XBRL files or folders into the database")
    p.add_argument("paths", nargs="+")
    p.add_argument("--db", default="work/filingLens.db")
    p.add_argument("--ticker", help="force the ticker for every file")
    p.set_defaults(func=cmd_ingest_xbrl)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
