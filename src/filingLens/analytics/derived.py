"""Derived metrics: ratios computed from stored facts, plus a loader that returns either a
stored or a derived series through an injected ``get_series`` (so this stays DB-free)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from filingLens.analytics.transforms import ratio
from filingLens.analytics.trend import RATIO_UNIT
from filingLens.domain.models import Fact
from filingLens.domain.registry import Registry


@dataclass(frozen=True)
class Formula:
    numerator: str
    denominator: str
    desc: str


DERIVED: dict[str, Formula] = {
    "net_margin": Formula("net_profit", "revenue_from_operations", "Net profit margin"),
    "pbt_margin": Formula("profit_before_tax", "revenue_from_operations", "PBT margin"),
    "effective_tax_rate": Formula("tax_expense", "profit_before_tax", "Effective tax rate"),
    "employee_cost_ratio": Formula("employee_expense", "revenue_from_operations",
                                   "Employee cost to revenue"),
}

GetSeries = Callable[[str, str, str, str], list[Fact]]   # (ticker, metric, scope, period_type)


def compute(name: str, num: list[Fact], den: list[Fact]) -> tuple[list[Fact], list[str]]:
    """Pair numerator and denominator facts by period and divide. The result is a Fact series
    in %, method "derived", tagged with its inputs, so trends/compare/citations reuse it."""
    f = DERIVED[name]
    by_period = {(d.ticker, d.scope, d.period_type, d.period_start, d.period_end): d for d in den}
    out, notes = [], []
    for n in num:
        d = by_period.get((n.ticker, n.scope, n.period_type, n.period_start, n.period_end))
        if d is None:
            notes.append(f"{name} {n.fy}: {f.denominator} missing")
            continue
        r = ratio(n.value, d.value)
        if r is None:
            notes.append(f"{name} {n.fy}: {f.denominator} is zero or negative, ratio undefined")
            continue
        out.append(n.model_copy(update={
            "metric_id": name, "value": r * 100, "unit": RATIO_UNIT, "orig_value": r * 100,
            "orig_unit": "", "method": "derived",
            "xbrl_tag": f"{n.xbrl_tag} / {d.xbrl_tag}"}))
    return out, notes


def load_series(get_series: GetSeries, registry: Registry, ticker: str, metric: str,
                scope: str = "consolidated", period_type: str = "FY") -> tuple[list[Fact], list[str]]:
    """A stored metric, or a derived one computed from its two inputs. Balance-sheet (stock)
    metrics are point-in-time, so an FY request reads their year-end INSTANT values."""
    if metric in DERIVED:
        f = DERIVED[metric]
        return compute(metric, get_series(ticker, f.numerator, scope, period_type),
                       get_series(ticker, f.denominator, scope, period_type))
    spec = registry.metrics.get(metric)
    if spec is None:
        raise KeyError(f"unknown metric {metric!r}")
    if spec.kind == "stock" and period_type == "FY":
        period_type = "INSTANT"
    facts = get_series(ticker, metric, scope, period_type)
    return facts, ([] if facts else [f"{ticker} {metric} ({scope}, {period_type}): no data"])
