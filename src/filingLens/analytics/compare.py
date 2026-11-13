"""Side-by-side comparison of several series on the same periods.

Axis "companies": same metric, different tickers. Axis "metrics": same ticker, different
metrics (must share a unit: crore is never mixed with per-share or %). Axis "periods": one
series, chosen FYs, change against the first. Missing values stay empty, with a note.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from filingLens.analytics.transforms import index100, share
from filingLens.analytics.trend import Trend, build_trend
from filingLens.domain.models import Fact


@dataclass
class Comparison:
    axis: str
    unit: str
    frame: pd.DataFrame                   # rows = periods, columns = series labels
    notes: list[str] = field(default_factory=list)
    sources: dict[str, list[Fact]] = field(default_factory=dict)


def _percent(fraction: float | None) -> float | None:
    return None if fraction is None else fraction * 100


def build_comparison(series: dict[str, list[Fact]], axis: str = "companies",
                     normalise: str = "none", periods: list[str] | None = None) -> Comparison:
    """``series``: label -> facts (e.g. {"TCS": [...], "INFY": [...]}).
    ``normalise``: "none", "index100" (each series = 100 at its first value) or "share"
    (each value as % of the row total; companies axis only)."""
    if axis not in ("companies", "metrics", "periods"):
        raise ValueError(f"unknown axis {axis!r}")
    notes: list[str] = []
    trends: dict[str, Trend] = {}
    for label, facts in series.items():
        if facts:
            trends[label] = build_trend(facts)
        else:
            notes.append(f"{label}: no data")
    units = {t.unit for t in trends.values()}
    if len(units) > 1:
        raise ValueError(f"cannot compare different units: {sorted(units)}")
    if axis == "companies" and len({t.scope for t in trends.values()}) > 1:
        notes.append("series mix consolidated and standalone figures")

    rows = sorted({r.label for t in trends.values() for r in t.rows})
    if periods:
        missing = [p for p in periods if p not in rows]
        rows = [p for p in rows if p in periods]
        notes += [f"{p}: no data for any series" for p in missing]
    data = {label: [next((r.value for r in t.rows if r.label == p), None) for p in rows]
            for label, t in trends.items()}
    for label, vals in data.items():
        notes += [f"{label} {p} missing" for p, v in zip(rows, vals, strict=True) if v is None]

    if normalise == "index100":
        data = {k: index100(v) for k, v in data.items()}
    elif normalise == "share":
        if axis != "companies":
            raise ValueError("share only makes sense across companies")
        totals = [sum(v[i] or 0.0 for v in data.values()) for i in range(len(rows))]
        data = {k: [_percent(share(x, tot)) for x, tot in zip(v, totals, strict=True)]
                for k, v in data.items()}

    frame = pd.DataFrame(data, index=pd.Index(rows, name="period"))
    if axis == "periods" and len(rows) >= 2 and len(data) == 1:
        (label, vals), = data.items()
        base = vals[0]
        frame["change_vs_first"] = [None if v is None or base in (None, 0) else v / base - 1
                                    for v in vals]
    unit = "index (first = 100)" if normalise == "index100" else (
        "% of total" if normalise == "share" else (units.pop() if units else ""))
    return Comparison(axis, unit, frame, notes, {k: v for k, v in series.items()})
