"""Accounting consistency checks on canonical facts. Pure functions: facts in, results out.

A failing check never deletes data; it flags the facts involved (``qa`` = warn/fail).

| check            | rule                                                 | on failure |
|------------------|------------------------------------------------------|------------|
| income_sum       | total income = revenue from operations + other income | fail       |
| pbt_bridge       | total income - total expenses ~ PBT                   | warn       |
| pat_bridge       | PBT - tax ~ net profit                                | warn       |
| quarter_sum      | Q1+Q2+Q3+Q4 = FY (revenue, PBT, net profit)           | fail       |
| revenue_positive | revenue > 0                                           | fail       |

Values compared are canonical (INR crore), so absolute tolerances are in crore.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from filingLens.domain.models import Fact

QUARTER_SUM_METRICS = ("revenue_from_operations", "profit_before_tax", "net_profit")
_SEVERITY = {"pass": 0, "info": 0, "warn": 1, "fail": 2}

QaKey = tuple[str, str, str, str, str]   # ticker, scope, period_start, period_end, metric_id


@dataclass(frozen=True)
class CheckResult:
    ticker: str
    scope: str
    period_start: str
    period_end: str
    check: str
    status: str                    # pass | warn | fail
    detail: str
    metrics: tuple[str, ...] = ()


def _close(a: float, b: float, rel: float, abs_tol: float) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b)) + abs_tol


def check_income_sum(rev: float, oi: float, ti: float) -> tuple[bool, str]:
    return _close(ti, rev + oi, 0.005, 1.0), f"total income {ti:,.2f} vs rev+other {rev + oi:,.2f}"


def check_pbt_bridge(ti: float, te: float, pbt: float) -> tuple[bool, str]:
    ok = abs((ti - te) - pbt) <= 0.03 * abs(ti)
    return ok, (f"income-expenses {ti - te:,.2f} vs PBT {pbt:,.2f}"
                + ("" if ok else " (exceptional items or JV share can explain a gap)"))


def check_pat_bridge(pbt: float, tax: float, np_: float) -> tuple[bool, str]:
    ok = abs((pbt - tax) - np_) <= 0.03 * abs(pbt) + 1.0
    return ok, f"PBT-tax {pbt - tax:,.2f} vs net profit {np_:,.2f}"


def run_checks(facts: list[Fact]) -> list[CheckResult]:
    """Run every check on facts that have a canonical value."""
    usable = [f for f in facts if f.value is not None]
    groups: dict[tuple[str, str, str, str, str], dict[str, float]] = defaultdict(dict)
    for f in usable:
        assert f.value is not None
        groups[(f.ticker, f.scope, f.period_type, f.period_start, f.period_end)][f.metric_id] = (
            f.value)

    out: list[CheckResult] = []
    for (t, sc, _pt, ps, pe), v in sorted(groups.items()):
        def add(check: str, ok: bool, detail: str, mets: tuple[str, ...], bad: str = "fail",
                t: str = t, sc: str = sc, ps: str = ps, pe: str = pe) -> None:
            out.append(CheckResult(t, sc, ps, pe, check, "pass" if ok else bad, detail, mets))

        rev, oi, ti = (v.get(m) for m in ("revenue_from_operations", "other_income",
                                          "total_income"))
        te, pbt, tax, npf = (v.get(m) for m in ("total_expenses", "profit_before_tax",
                                                "tax_expense", "net_profit"))
        if rev is not None and oi is not None and ti is not None:
            add("income_sum", *check_income_sum(rev, oi, ti),
                ("total_income", "revenue_from_operations", "other_income"))
        if ti is not None and te is not None and pbt is not None:
            add("pbt_bridge", *check_pbt_bridge(ti, te, pbt),
                ("total_income", "total_expenses", "profit_before_tax"), bad="warn")
        if pbt is not None and tax is not None and npf is not None:
            add("pat_bridge", *check_pat_bridge(pbt, tax, npf),
                ("profit_before_tax", "tax_expense", "net_profit"), bad="warn")
        if rev is not None:
            add("revenue_positive", rev > 0, f"revenue {rev:,.2f}", ("revenue_from_operations",))

    out.extend(_quarter_sum(usable))
    return out


def _quarter_sum(facts: list[Fact]) -> list[CheckResult]:
    qs: dict[tuple[str, str, str, str], dict[str, float]] = defaultdict(dict)
    fys: dict[tuple[str, str, str, str], Fact] = {}
    for f in facts:
        if f.metric_id not in QUARTER_SUM_METRICS or f.value is None:
            continue
        k = (f.ticker, f.scope, f.fy, f.metric_id)
        if f.period_type == "Q" and f.quarter:
            qs[k][f.quarter] = f.value
        elif f.period_type == "FY":
            fys[k] = f
    out = []
    for k, quarters in sorted(qs.items()):
        if set(quarters) != {"Q1", "Q2", "Q3", "Q4"} or k not in fys:
            continue
        fy = fys[k]
        assert fy.value is not None
        tot = sum(quarters.values())
        ok = _close(tot, fy.value, 0.005, 1.0)
        out.append(CheckResult(k[0], k[1], fy.period_start, fy.period_end, "quarter_sum",
                               "pass" if ok else "fail",
                               f"{k[3]}: sum(Q)={tot:,.2f} vs FY={fy.value:,.2f}", (k[3],)))
    return out


def qa_flags(results: list[CheckResult]) -> dict[QaKey, str]:
    """Worst status per fact (fail beats warn). Facts with no failing check are omitted."""
    flags: dict[QaKey, str] = {}
    for r in results:
        if r.status not in ("warn", "fail"):
            continue
        for m in r.metrics:
            k = (r.ticker, r.scope, r.period_start, r.period_end, m)
            if _SEVERITY[r.status] > _SEVERITY.get(flags.get(k, "pass"), 0):
                flags[k] = r.status
    return flags


def apply_qa(facts: list[Fact], results: list[CheckResult]) -> list[Fact]:
    """Return copies of facts with ``qa`` set from the check results."""
    flags = qa_flags(results)
    return [f.model_copy(update={"qa": flags.get(
        (f.ticker, f.scope, f.period_start, f.period_end, f.metric_id), "ok")}) for f in facts]
