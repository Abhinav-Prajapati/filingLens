"""Run a validated tool call with the existing code (FactRepo, analytics, retrieval) and return
a plain result: rows of raw values, unit, notes and citations. No LLM involved here."""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from filingLens.analytics.compare import build_comparison
from filingLens.analytics.derived import load_series
from filingLens.analytics.transforms import cagr, yoy
from filingLens.analytics.trend import RATIO_UNIT, build_trend
from filingLens.domain.fiscal import fy_int
from filingLens.domain.models import Fact
from filingLens.domain.registry import Registry
from filingLens.domain.units import fmt_change
from filingLens.llm.tools import AskUser, Compare, GetTrend, GetValue, SearchReports
from filingLens.storage.db import Database
from filingLens.storage.repos import FactRepo


@dataclass
class ToolResult:
    tool: str
    title: str
    unit: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    clarify: str | None = None            # set by ask_user
    stats: list[dict[str, Any]] = field(default_factory=list)   # CAGR / changes, structured

    @property
    def empty(self) -> bool:
        return not self.rows and self.clarify is None


@dataclass
class Context:
    con: Database
    registry: Registry
    searcher: Callable[[], Any] | None = None    # lazy: loads the embedding model on demand
    _labels: dict[str, str] = field(default_factory=dict, repr=False)

    def doc_label(self, doc_id: str | None) -> str | None:
        """'TCS Annual Report FY25' / 'TCS FY24 results (XBRL)' for a stored document."""
        if not doc_id:
            return None
        if doc_id not in self._labels:
            row = self.con.execute("SELECT ticker, doc_type, fy FROM document WHERE doc_id=?",
                                   (doc_id,)).fetchone()
            if row is None:
                return None
            t, kind, fy = row
            self._labels[doc_id] = (f"{t} Annual Report {fy or ''}".strip()
                                    if kind == "annual_report" else f"{t} {fy or ''} results "
                                    "(XBRL)".replace("  ", " "))
        return self._labels[doc_id]

    def get(self, t: str, m: str, s: str, p: str) -> list[Fact]:
        return FactRepo(self.con).get_series(t, m, scope=s, period_type=p)

    def latest_fy(self, ticker: str | None = None) -> str | None:
        repo = FactRepo(self.con)
        if ticker:
            return repo.latest_fy(ticker)
        fys = [f for f in (repo.latest_fy(t) for t in self.registry.companies) if f]
        return max(fys, key=fy_int) if fys else None


def _cite(f: Fact, ctx: Context) -> dict[str, Any]:
    """Where a value came from: the XBRL filing, and the annual-report page that shows it."""
    return {"ticker": f.ticker, "metric": f.metric_id, "period": f.fy + (f" {f.quarter}" if
            f.quarter else ""), "xbrl_tag": f.xbrl_tag, "doc_id": f.doc_id[:12],
            "filing": ctx.doc_label(f.doc_id),
            "report": ctx.doc_label(f.pdf_doc_id) if f.page else None,
            "page": f.page, "verified": f.verified, "method": f.method}


def _fy_window(ctx: Context, ticker: str | None, last_n: int | None, from_fy: str | None,
               to_fy: str | None) -> tuple[int | None, int | None, list[str]]:
    """Resolve last_n_years / from_fy / to_fy to an inclusive FY range (ints, None = open)."""
    latest = ctx.latest_fy(ticker)
    hi = fy_int(to_fy) if to_fy and to_fy != "latest" else (fy_int(latest) if latest else None)
    lo = fy_int(from_fy) if from_fy and from_fy != "latest" else None
    notes = []
    if last_n and hi is not None:
        lo = hi - last_n + 1
        notes.append(f"last {last_n} years = FY{lo % 100:02d}-FY{hi % 100:02d} "
                     f"(latest data: {latest})")
    return lo, hi, notes


def _in_window(facts: list[Fact], lo: int | None, hi: int | None) -> list[Fact]:
    return [f for f in facts if (lo is None or fy_int(f.fy) >= lo) and
            (hi is None or fy_int(f.fy) <= hi)]


def get_value(a: GetValue, ctx: Context) -> ToolResult:
    period = "Q" if a.quarter else "FY"
    facts, notes = load_series(ctx.get, ctx.registry, a.ticker, a.metric, a.scope, period)
    fy = ctx.latest_fy(a.ticker) if a.fy == "latest" else a.fy
    hits = [f for f in facts if f.fy == fy and (a.quarter is None or f.quarter == a.quarter)]
    label = f"{fy} {a.quarter}" if a.quarter else str(fy)
    r = ToolResult("get_value", f"{a.ticker} {a.metric} {label} ({a.scope})",
                   columns=["ticker", "metric", "period", "value"], notes=notes)
    if not hits:
        have = sorted({f.fy for f in facts}, key=fy_int)
        r.notes.append(f"no value for {label}" + (f"; available: {', '.join(have)}" if have else ""))
        return r
    f = hits[0]
    r.unit = f.unit
    r.rows = [{"ticker": f.ticker, "metric": f.metric_id, "period": label, "value": f.value}]
    r.citations = [_cite(f, ctx)]
    return r


def get_trend(a: GetTrend, ctx: Context) -> ToolResult:
    facts, notes = load_series(ctx.get, ctx.registry, a.ticker, a.metric, a.scope, a.period)
    lo, hi, wnotes = _fy_window(ctx, a.ticker, a.last_n_years, a.from_fy, a.to_fy)
    facts = _in_window(facts, lo, hi)
    r = ToolResult("get_trend", f"{a.ticker} {a.metric} ({a.scope})", notes=notes + wnotes)
    if not facts:
        r.notes.append("no data in the requested years")
        return r
    t = build_trend(facts)
    r.unit = t.unit
    change = "change_pp" if t.is_ratio else "yoy"
    r.columns = ["period", "value", change]
    r.rows = [{"period": row.label, "value": row.value, change: row.change} for row in t.rows]
    r.citations = [_cite(row.fact, ctx) for row in t.rows if row.fact]
    if t.cagr is not None and t.cagr_span:
        r.notes.append(f"CAGR {t.cagr_span[0]}-{t.cagr_span[1]}: {t.cagr * 100:+.2f}%")
    if a.period == "FY":
        r.stats = _changes({a.ticker: facts}, [row.fy for row in t.rows])
    present = len([x for x in t.rows if x.value is not None])
    if a.last_n_years and present < a.last_n_years:
        r.notes.append(f"only {present} year(s) of data available")
    r.notes += t.notes
    return r


def _changes(series: dict[str, list[Fact]], periods: list[str]) -> list[dict[str, Any]]:
    """Change from first to last period of each series, from the original (not normalised)
    values. Amounts: total change and CAGR in %; ratios: change in percentage points."""
    out: list[dict[str, Any]] = []
    for label, facts in series.items():
        by_fy = {f.fy: f.value for f in facts if f.fy in periods and f.value is not None}
        have = sorted(by_fy, key=fy_int)
        if len(have) < 2:
            continue
        first, last = by_fy[have[0]], by_fy[have[-1]]
        base = {"series": label, "from": have[0], "to": have[-1]}
        if facts[0].unit == RATIO_UNIT:
            out.append(base | {"kind": "change_pp", "value": last - first, "unit": "pp"})
            continue
        g = yoy(first, last)
        if g is None:
            continue
        years = fy_int(have[-1]) - fy_int(have[0])
        c = cagr(first, last, years) if years > 1 else None
        out.append(base | {"kind": "change", "value": g * 100, "unit": "%",
                           "cagr": None if c is None else c * 100})
    return out


def _change_note(changes: list[dict[str, Any]]) -> str | None:
    """'change FY23-FY24: INFY +4.70%, TCS +6.85%', so growth is stated in the result rather
    than left for the model to compute."""
    def text(c: dict[str, Any]) -> str:
        if c["kind"] == "change_pp":
            return fmt_change(c["value"], pp=True)
        cg = c.get("cagr")
        return fmt_change(c["value"] / 100) + (f" (CAGR {fmt_change(cg / 100)})" if cg else "")

    if not changes:
        return None
    spans = {f"{c['from']}-{c['to']}" for c in changes}
    if len(spans) == 1:
        return f"change {spans.pop()}: " + ", ".join(f"{c['series']} {text(c)}" for c in changes)
    return "change: " + ", ".join(f"{c['series']} {c['from']}-{c['to']} {text(c)}"
                                  for c in changes)


def compare(a: Compare, ctx: Context) -> ToolResult:
    axis = "companies" if len(a.tickers) > 1 else ("metrics" if len(a.metrics) > 1 else "periods")
    pairs = ([(t, a.metrics[0], t) for t in a.tickers] if axis == "companies"
             else [(a.tickers[0], m, m) for m in a.metrics])
    series, notes = {}, []
    for t, m, label in pairs:
        series[label], n = load_series(ctx.get, ctx.registry, t, m, a.scope, "FY")
        notes += n
    periods = [ctx.latest_fy() if f == "latest" else f for f in a.fys] or None
    if a.last_n_years and not periods:
        lo, hi, wnotes = _fy_window(ctx, None, a.last_n_years, None, None)
        notes += wnotes
        if lo is not None and hi is not None:
            periods = [f"FY{y % 100:02d}" for y in range(lo, hi + 1)]
    title = f"{', '.join(series)} ({a.scope})"
    try:
        c = build_comparison(series, axis, a.normalise, [p for p in periods or [] if p] or None)
    except ValueError as e:
        return ToolResult("compare", title, notes=notes + [str(e)])
    cols = [str(x) for x in c.frame.columns]
    rows = [{"period": str(p), **{k: (None if isinstance(v, float) and math.isnan(v) else v)
                                  for k, v in zip(cols, rec, strict=True)}}
            for p, rec in zip(c.frame.index, c.frame.itertuples(index=False), strict=True)]
    cites = [_cite(f, ctx) for facts in series.values() for f in facts
             if not periods or f.fy in periods]
    changes = _changes(series, [str(r["period"]) for r in rows])
    change = _change_note(changes)
    return ToolResult("compare", title, c.unit, ["period", *cols], rows,
                      notes + c.notes + ([change] if change else []), cites, stats=changes)


def search_reports(a: SearchReports, ctx: Context) -> ToolResult:
    return search_passages(ctx, a.query, a.tickers,
                           [a.fy] if a.fy and a.fy != "latest" else [],
                           [a.section] if a.section else [], a.k)


def search_passages(ctx: Context, query: str, tickers: list[str], fys: list[str],
                    sections: list[str], k: int = 5,
                    tiers: list[tuple[list[str], list[str]]] | None = None,
                    must: list[str] | None = None) -> ToolResult:
    """Report passages for ``query`` among ``tickers``. ``tiers``: (fys, sections) filters
    tried in order, each topping up the hits of the ones before (most specific first); by
    default just (fys, sections). ``must``: keep only passages that mention one of these
    words (any ending: "profit" matches "profits")."""
    from filingLens.retrieval.search import Searcher, SearchFilters

    s = ctx.searcher() if ctx.searcher else Searcher(ctx.con)
    hits: list[Any] = []
    for tf, ts in tiers or [(fys, sections)]:
        if len(hits) >= k:
            break
        seen = {h.chunk_id for h in hits}
        found = s.search(query, SearchFilters(tickers=tuple(tickers), fys=tuple(tf),
                                              sections=tuple(ts)), k=k * 3 if must else k)
        if must:
            pat = re.compile(r"\b(" + "|".join(map(re.escape, must)) + r")", re.IGNORECASE)
            found = [h for h in found if pat.search(h.text)]
        hits += [h for h in found if h.chunk_id not in seen][:k - len(hits)]
    r = ToolResult("search_reports", f"search: {query}", columns=["rank", "source", "text"])
    r.rows = [{"rank": i, "source": h.context_line, "text": h.text} for i, h in enumerate(hits, 1)]
    r.citations = [{"ticker": h.ticker, "doc_id": h.doc_id[:12], "page": h.page,
                    "report": ctx.doc_label(h.doc_id), "section": h.section,
                    "chunk_id": h.chunk_id} for h in hits]
    if not hits:
        r.notes.append("no matching text in the loaded reports")
    return r


def ask_user(a: AskUser, ctx: Context) -> ToolResult:
    return ToolResult("ask_user", "clarification needed", clarify=a.question)


RUNNERS: dict[str, Callable[[Any, Context], ToolResult]] = {
    "get_value": get_value, "get_trend": get_trend, "compare": compare,
    "search_reports": search_reports, "ask_user": ask_user}


def execute(name: str, args: Any, ctx: Context) -> ToolResult:
    return RUNNERS[name](args, ctx)
