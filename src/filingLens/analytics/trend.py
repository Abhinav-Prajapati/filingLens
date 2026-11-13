"""One metric of one company over time: values, YoY, CAGR and gaps (never interpolated)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from filingLens.analytics.transforms import cagr, yoy
from filingLens.domain.fiscal import fy_int
from filingLens.domain.models import Fact

RATIO_UNIT = "%"          # derived ratios: value in %, change in percentage points


@dataclass
class TrendRow:
    label: str                    # "FY24" or "FY24 Q3"
    fy: str
    quarter: str | None
    value: float | None           # None = missing (shown, never filled in)
    change: float | None          # YoY: fraction for amounts, percentage points for ratios
    fact: Fact | None             # source, for citations


@dataclass
class Trend:
    ticker: str
    metric_id: str
    scope: str
    unit: str
    rows: list[TrendRow]
    cagr: float | None = None
    cagr_span: tuple[str, str] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_ratio(self) -> bool:
        return self.unit == RATIO_UNIT

    def to_frame(self) -> pd.DataFrame:
        col = "change_pp" if self.is_ratio else "yoy"
        return pd.DataFrame([{"period": r.label, "value": r.value, col: r.change}
                             for r in self.rows])


def _key(f: Fact) -> tuple[int, int]:
    return fy_int(f.fy), int(f.quarter[1]) if f.quarter else 0


def _label(fy: int, q: int) -> str:
    return f"FY{fy % 100:02d}" + (f" Q{q}" if q else "")


def build_trend(facts: list[Fact]) -> Trend:
    """``facts``: one series (same ticker, metric, scope and period type), any order.
    Every period between the first and the last appears; missing ones carry None and a note.
    YoY compares with the same period one year earlier and is never computed across a gap."""
    if not facts:
        raise ValueError("empty series")
    first = facts[0]
    if len({(f.ticker, f.metric_id, f.scope, f.period_type) for f in facts}) > 1:
        raise ValueError("build_trend needs one ticker, metric, scope and period type")
    by_key = {_key(f): f for f in facts}
    quarterly = first.period_type == "Q"
    lo, hi = min(by_key), max(by_key)
    keys = [(y, q) for y in range(lo[0], hi[0] + 1)
            for q in ((1, 2, 3, 4) if quarterly else (0,))
            if lo <= (y, q) <= hi]

    t = Trend(first.ticker, first.metric_id, first.scope, first.unit, [])
    for y, q in keys:
        f = by_key.get((y, q))
        prev = by_key.get((y - 1, q))
        cur_v = f.value if f else None
        prev_v = prev.value if prev else None
        if t.is_ratio:
            change = (cur_v - prev_v) if cur_v is not None and prev_v is not None else None
        else:
            change = yoy(prev_v, cur_v)
            if prev_v is not None and prev_v <= 0 and cur_v is not None:
                t.notes.append(f"{_label(y, q)}: no growth % from a zero or negative base")
        if f is None or f.value is None:
            t.notes.append(f"{_label(y, q)} missing (not interpolated)")
        t.rows.append(TrendRow(_label(y, q), f"FY{y % 100:02d}", f"Q{q}" if q else None,
                               cur_v, change, f))

    present = [r for r in t.rows if r.value is not None]
    if not quarterly and not t.is_ratio and len(present) >= 2:
        a, b = present[0], present[-1]
        t.cagr = cagr(a.value, b.value, fy_int(b.fy) - fy_int(a.fy))
        t.cagr_span = (a.fy, b.fy)
        if t.cagr is None:
            t.notes.append(f"CAGR {a.fy}-{b.fy} not defined (non-positive endpoint)")
    elif len(present) < 2:
        t.notes.append("only one data point: no growth figures")
    return t
