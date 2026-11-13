"""Cross-check XBRL facts against annual-report text and attach page citations.

Pure functions: facts + page texts in, citations + report out.

* Only FY and INSTANT facts are checked (annual reports do not print quarters).
* A report prints this year and last year, so the FY25 report also verifies FY24 facts.
* Numbers are compared without grouping commas, so "2,40,893" and "240,893" both match
  240893. Signs are ignored ("(24)" matches -24).
* Small money values (< 1,000 crore) and EPS match too many unrelated numbers (note numbers,
  page numbers), so they only count on financial-statement pages of the right scope.
* Unit check: Phase 3 stores money as INR crore. If the report's numbers match the facts
  better as lakh or million, the ticker is flagged instead of being silently converted.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from filingLens.domain.fiscal import fy_int
from filingLens.domain.models import Fact

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ALT_UNITS = {"lakh": 100.0, "million": 10.0}     # crore value * factor = value in that unit
FactKey = tuple[str, str, str, str, str]         # ticker, metric_id, period_start, period_end, scope


@dataclass(frozen=True)
class PageText:
    """Text of one (document, page, scope, section) group, e.g. one half of a spread."""

    doc_id: str
    ticker: str
    fy: str | None
    page: int
    scope: str
    section: str
    text: str


@dataclass(frozen=True)
class Citation:
    key: FactKey
    pdf_doc_id: str
    page: int
    verified: str          # "pdf" | "pdf_scope_mismatch"


@dataclass
class TickerReport:
    ticker: str
    eligible: int = 0
    verified: int = 0
    scope_mismatch: int = 0
    unit_votes: dict[str, int] = field(default_factory=dict)
    not_found: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.verified / self.eligible if self.eligible else 0.0

    @property
    def unit_ok(self) -> bool:
        """False when lakh or million matches the report better than crore."""
        crore = self.unit_votes.get("crore", 0)
        return all(v <= crore for v in self.unit_votes.values())


def number_tokens(text: str) -> set[str]:
    return {t.replace(",", "") for t in _NUM.findall(text)}


def variants(value: float, per_share: bool = False) -> set[str]:
    """How a value may be printed: 240893 -> {'240893', '240893.0', '240893.00'}."""
    x = abs(value)
    if per_share:
        return {f"{x:.2f}"}
    return {f"{x:.0f}", f"{x:.1f}", f"{x:.2f}"}


def is_eligible(f: Fact) -> bool:
    return f.period_type in ("FY", "INSTANT") and f.value is not None


def _fact_key(f: Fact) -> FactKey:
    return (f.ticker, f.metric_id, f.period_start, f.period_end, f.scope)


def _score(f: Fact, p: PageText) -> int:
    score = 20 if p.scope == f.scope else (10 if p.scope in ("mixed", "none") else 0)
    score += 8 if p.section == "financial_statements" else 0
    if p.fy:
        gap = fy_int(p.fy) - fy_int(f.fy)
        score += 5 if gap == 0 else (3 if gap == 1 else 0)   # 1 = prior-year column
    return score


def _needs_strict_match(f: Fact) -> bool:
    assert f.value is not None
    return f.unit != "INR crore" or abs(f.value) < 1000


def find_citation(f: Fact, pages: list[PageText], tokens: list[set[str]]) -> Citation | None:
    """Best page that prints the fact's value, or None."""
    assert f.value is not None
    want = variants(f.value, per_share=f.unit != "INR crore")
    strict = _needs_strict_match(f)
    best: tuple[tuple[int, int], PageText] | None = None
    for p, toks in zip(pages, tokens, strict=True):
        if p.ticker != f.ticker or not (want & toks):
            continue
        if strict and (p.section != "financial_statements" or p.scope != f.scope):
            continue
        rank = (_score(f, p), -p.page)
        if best is None or rank > best[0]:
            best = (rank, p)
    if best is None:
        return None
    p = best[1]
    ok = p.scope in (f.scope, "mixed", "none") or f.scope == "unknown"
    return Citation(_fact_key(f), p.doc_id, p.page, "pdf" if ok else "pdf_scope_mismatch")


def unit_votes(facts: list[Fact], tokens: set[str]) -> dict[str, int]:
    """How many large money facts appear in the report as crore, lakh or million."""
    votes: Counter[str] = Counter()
    for f in facts:
        if f.unit != "INR crore" or f.value is None or abs(f.value) < 1000:
            continue
        if variants(f.value) & tokens:
            votes["crore"] += 1
        for unit, factor in _ALT_UNITS.items():
            if variants(f.value * factor) & tokens:
                votes[unit] += 1
    return dict(votes)


def calibrate(facts: list[Fact], pages: list[PageText]
              ) -> tuple[list[Citation], dict[str, TickerReport]]:
    """Citations for every eligible fact found in the pages, and a report per ticker."""
    tokens = [number_tokens(p.text) for p in pages]
    reports: dict[str, TickerReport] = {}
    citations: list[Citation] = []
    for t in sorted({f.ticker for f in facts}):
        rep = reports[t] = TickerReport(t)
        own = [f for f in facts if f.ticker == t and is_eligible(f)]
        all_tokens = set().union(*(tk for p, tk in zip(pages, tokens, strict=True)
                                   if p.ticker == t))
        rep.unit_votes = unit_votes(own, all_tokens)
        if not all_tokens:
            continue                       # no report for this ticker: nothing to verify
        rep.eligible = len(own)
        for f in own:
            c = find_citation(f, pages, tokens)
            if c is None:
                rep.not_found.append(f"{f.metric_id} {f.period_type} {f.fy} {f.scope}")
                continue
            citations.append(c)
            rep.verified += 1
            rep.scope_mismatch += c.verified == "pdf_scope_mismatch"
    return citations, reports
