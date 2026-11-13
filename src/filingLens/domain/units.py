"""Number formatting and unit conversion. Pure functions."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

UNIT_FACTORS = {
    "lakh": 1e5, "lac": 1e5, "crore": 1e7, "million": 1e6,
    "thousand": 1e3, "billion": 1e9, "unit": 1.0, "actual": 1.0, "rupee": 1.0,
}


def rounding_factor(text: str | None) -> float | None:
    t = (text or "").lower()
    for k, v in UNIT_FACTORS.items():
        if k in t:
            return v
    return None


def to_crore(value: float, multiplier_to_inr: float) -> float:
    """Convert a stored value (times its INR multiplier) to INR crore."""
    return value * multiplier_to_inr / 1e7


def same_value(a: float, b: float, rel: float = 1e-6) -> bool:
    """True when two reported figures are the same number (ignores float noise).
    Used to tell a restatement or a conflicting value from a re-read of the same value."""
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def fmt_indian(x: float, dec: int = 0) -> str:
    """12345678 -> 1,23,45,678 (lakh/crore grouping)."""
    q = Decimal(1).scaleb(-dec)
    d = Decimal(repr(float(abs(x)))).quantize(q, rounding=ROUND_HALF_UP)
    s = f"{d:f}"
    ip, _, fp = s.partition(".")
    if len(ip) > 3:
        head, tail, groups = ip[:-3], ip[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        ip = ",".join(groups + [tail])
    negative = x < 0 and d != 0
    return ("-" if negative else "") + ip + (("." + fp) if dec else "")
