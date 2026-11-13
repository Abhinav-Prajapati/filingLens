"""Elementary calculations. Pure, and None instead of a misleading number.

Results are fractions (0.068 = 6.8%); formatting is the presenter's job.
"""
from __future__ import annotations

from collections.abc import Sequence


def yoy(prev: float | None, cur: float | None) -> float | None:
    """Growth from prev to cur. None when either is missing or prev <= 0 (growth from zero or
    from a loss has no meaningful percentage)."""
    if prev is None or cur is None or prev <= 0:
        return None
    return cur / prev - 1


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    """Compound annual growth rate over ``years`` (the real gap between the two FYs, never a
    count of available points). None for years <= 0 or a non-positive endpoint."""
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def ratio(num: float | None, den: float | None) -> float | None:
    """num / den, None when den <= 0 (a margin on zero or negative revenue is meaningless)."""
    if num is None or den is None or den <= 0:
        return None
    return num / den


margin = ratio          # margin(net_profit, revenue)
share = ratio           # share(part, total)


def index100(values: Sequence[float | None]) -> list[float | None]:
    """Rebase a series to 100 at its first positive value (to compare growth of companies of
    very different size). Values before the base, missing or non-positive base -> None."""
    base = next((v for v in values if v is not None and v > 0), None)
    if base is None:
        return [None] * len(values)
    started, out = False, []
    for v in values:
        started = started or v == base
        out.append(v / base * 100 if started and v is not None else None)
    return out
