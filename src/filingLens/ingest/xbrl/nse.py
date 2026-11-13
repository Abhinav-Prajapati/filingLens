"""NSE/BSE result-filing conventions, including the Phase 0 period fix.

NSE result filings use one context per column: ``OneD`` = current quarter,
``FourD`` = year to date, ``OneI`` = balance-sheet date, plus derived contexts
such as ``FourOperatingExpenses01D`` that belong to the same column.

In the files we have seen, **every ``Four*`` context carries the quarter's dates**
in its ``<xbrli:period>``. The real year-to-date period is only given by the facts
``DateOfStartOfReportingPeriod`` / ``DateOfEndOfReportingPeriod`` reported *in*
that column's root context. Taking context dates at face value turns the FY column
into a duplicate Q4 column, which is why the notebook produced no FY facts.
``apply_period_overrides`` fixes every duration context of such a column.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from filingLens.domain.fiscal import to_iso

if TYPE_CHECKING:
    from filingLens.ingest.xbrl.model import Context

TAG_REPORTING_START = "dateofstartofreportingperiod"
TAG_REPORTING_END = "dateofendofreportingperiod"

_ROOT_CTX = re.compile(r"^[A-Z][a-z]+[DI]$")      # OneD, FourD, OneI ...
_COLUMN = re.compile(r"^[A-Z][a-z]+")             # "Four" in FourOperatingExpenses01D

# (tag local name, namespace, text, contextRef) as collected by the parser
RawTuple = tuple[str, str, str, str]


def is_root_context(ctx_id: str) -> bool:
    """OneD / FourD / OneI style ids. Generic ids (c1, ctx_fy) are never roots."""
    return bool(_ROOT_CTX.match(ctx_id))


def column_of(ctx_id: str) -> str | None:
    """'FourOperatingExpenses01D' -> 'Four'."""
    m = _COLUMN.match(ctx_id)
    return m.group(0) if m else None


def reporting_periods(contexts: dict[str, Context],
                      raw: list[RawTuple]) -> dict[str, tuple[str, str]]:
    """column -> (start, end) from the reporting-period facts in that column's root
    duration context. Columns missing either date are left out."""
    found: dict[str, dict[str, str]] = {}
    for tag, _ns, text, cref in raw:
        tl = tag.lower()
        if tl not in (TAG_REPORTING_START, TAG_REPORTING_END):
            continue
        ctx = contexts.get(cref)
        if ctx is None or not ctx.is_root or ctx.instant or ctx.has_dim:
            continue
        col, d = column_of(cref), to_iso(text)
        if col and d:
            found.setdefault(col, {})["start" if tl == TAG_REPORTING_START else "end"] = d
    return {c: (v["start"], v["end"]) for c, v in found.items() if "start" in v and "end" in v}


def apply_period_overrides(contexts: dict[str, Context], raw: list[RawTuple]
                           ) -> tuple[dict[str, Context], list[str]]:
    """Return contexts with corrected periods, plus one warning per corrected root context.
    Instant contexts and contexts whose dates already agree are left unchanged."""
    periods = reporting_periods(contexts, raw)
    out = dict(contexts)
    warnings: list[str] = []
    for cid, c in contexts.items():
        col = column_of(cid)
        if c.instant or not col or col not in periods:
            continue
        s, e = periods[col]
        if (c.start, c.end) == (s, e):
            continue
        if c.is_root:
            warnings.append(f"context {cid}: period {c.start}..{c.end} replaced by "
                            f"reporting-period facts {s}..{e}")
        out[cid] = replace(c, start=s, end=e, period_source="reporting_period_facts")
    return out, warnings
