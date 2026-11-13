"""Raw facts -> canonical facts (one filing), then merge across filings."""
from __future__ import annotations

from dataclasses import dataclass, field

from filingLens.domain.fiscal import fy_label, ptype, quarter_of, to_date
from filingLens.domain.models import Fact, RawFact
from filingLens.domain.registry import Metric, Registry
from filingLens.domain.units import same_value, to_crore
from filingLens.ingest.xbrl.meta import XbrlMeta
from filingLens.ingest.xbrl.nse import is_root_context

FactKey = tuple[str, str, str, str, str]   # ticker, metric_id, period_start, period_end, scope


@dataclass
class FilingFacts:
    """Canonical facts extracted from one filing, before cross-filing merge."""

    doc_id: str
    ticker: str
    meta: XbrlMeta
    facts: list[Fact]
    notes: list[str] = field(default_factory=list)


def _index_by_tag(rows: list[RawFact]) -> dict[str, list[RawFact]]:
    """Non-dimensional numeric rows by lowercase tag; root contexts (OneD/FourD/OneI) first,
    so they win ties with derived contexts that report the same period."""
    base = [r for r in rows if not r.has_dim and r.value_num is not None]
    base.sort(key=lambda r: (not is_root_context(r.ctx_id), r.ctx_id))
    by_tag: dict[str, list[RawFact]] = {}
    for r in base:
        by_tag.setdefault(r.tag.lower(), []).append(r)
    return by_tag


def _rows_for_metric(spec: Metric, by_tag: dict[str, list[RawFact]]) -> list[RawFact]:
    """Rows of the first candidate tag (registry order) that has usable data."""
    for cand in spec.tags:
        if spec.kind == "flow":
            rs = [r for r in by_tag.get(cand, []) if r.period_start and r.period_end]
        else:
            rs = [r for r in by_tag.get(cand, []) if r.instant]
        if rs:
            return rs
    return []


def _classify(r: RawFact, spec: Metric) -> tuple[str, tuple[str, str], str | None] | None:
    """-> (period_type, (period_start, period_end), skip-note). None when dates are missing."""
    if spec.kind != "flow":
        pe = to_date(r.instant)
        return ("INSTANT", ("", pe.isoformat()), None) if pe else None
    ps, pe = to_date(r.period_start), to_date(r.period_end)
    if not ps or not pe:
        return None
    pt = ptype(ps, pe)
    note = (f"{spec.metric_id}: skipped {r.ctx_id} {ps}..{pe} "
            f"({(pe - ps).days + 1} days is not Q/6M/9M/FY)") if pt == "other" else None
    return pt, (ps.isoformat(), pe.isoformat()), note


def _to_fact(r: RawFact, spec: Metric, pt: str, period: tuple[str, str], meta: XbrlMeta,
             ticker: str, doc_id: str) -> Fact:
    assert r.value_num is not None
    end = to_date(period[1])
    assert end is not None
    if spec.unit_kind == "money":
        value, unit = to_crore(r.value_num, meta.money_multiplier), "INR crore"
    else:
        value, unit = r.value_num, "INR per share"
    return Fact(ticker=ticker, metric_id=spec.metric_id, period_type=pt, fy=fy_label(end),
                quarter=quarter_of(end) if pt == "Q" else None,
                period_start=period[0], period_end=period[1], scope=meta.scope,
                value=value, unit=unit, orig_value=r.value_num, orig_unit=meta.rounding or "",
                xbrl_tag=r.tag, doc_id=doc_id, verified="no", qa="ok", method="xbrl")


def facts_from_raw(rows: list[RawFact], meta: XbrlMeta, ticker: str, doc_id: str,
                   registry: Registry) -> FilingFacts:
    """Map raw facts to the canonical metric list. Dimensional contexts are ignored.
    For each metric the first candidate tag that has data wins (registry order)."""
    out = FilingFacts(doc_id, ticker, meta, [])
    by_tag = _index_by_tag(rows)
    for spec in registry.metrics.values():
        seen: dict[tuple[str, str], RawFact] = {}
        for r in _rows_for_metric(spec, by_tag):
            got = _classify(r, spec)
            if got is None:
                continue
            pt, period, note = got
            if note:
                out.notes.append(note)
                continue
            prev = seen.get(period)
            if prev is not None:
                assert prev.value_num is not None and r.value_num is not None
                if not same_value(prev.value_num, r.value_num):
                    out.notes.append(f"{spec.metric_id}: conflicting values for "
                                     f"{period[0]}..{period[1]}: {prev.ctx_id}="
                                     f"{prev.value_num:g} kept, {r.ctx_id}={r.value_num:g} ignored")
                continue
            seen[period] = r
            out.facts.append(_to_fact(r, spec, pt, period, meta, ticker, doc_id))
    return out


@dataclass(frozen=True)
class Restatement:
    ticker: str
    scope: str
    metric_id: str
    period_start: str
    period_end: str
    old_value: float
    new_value: float
    old_doc: str
    new_doc: str

    @property
    def detail(self) -> str:
        return (f"{self.metric_id}: {self.old_value:g} -> {self.new_value:g} "
                f"(later filing wins; {self.old_doc[:10]} -> {self.new_doc[:10]})")


@dataclass
class BuildResult:
    facts: list[Fact]
    restatements: list[Restatement]


def _filing_order(f: FilingFacts) -> tuple[str, str, str]:
    return (f.meta.period_end or "", f.meta.board_meeting_date or "", f.doc_id)


def build_facts(filings: list[FilingFacts]) -> BuildResult:
    """Merge canonical facts across filings. Filings are applied oldest first, so a later
    filing overwrites an earlier one for the same (ticker, metric, period, scope); each
    changed value is recorded as a restatement. Input order does not matter."""
    merged: dict[FactKey, Fact] = {}
    restated: list[Restatement] = []
    for filing in sorted(filings, key=_filing_order):
        for f in filing.facts:
            k = (f.ticker, f.metric_id, f.period_start, f.period_end, f.scope)
            prev = merged.get(k)
            if prev is not None and not same_value(prev.orig_value, f.orig_value):
                restated.append(Restatement(f.ticker, f.scope, f.metric_id, f.period_start,
                                            f.period_end, prev.orig_value, f.orig_value,
                                            prev.doc_id, f.doc_id))
            merged[k] = f
    facts = sorted(merged.values(), key=lambda f: (f.ticker, f.scope, f.metric_id,
                                                   f.period_end, f.period_start))
    return BuildResult(facts, restated)
