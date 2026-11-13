"""Indian fiscal-year helpers. FY24 = 1 Apr 2023 to 31 Mar 2024. Pure functions."""
from __future__ import annotations

import datetime as dt
import re

_QUARTER_BY_END_MONTH = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}


def to_date(s: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def to_iso(s: object) -> str | None:
    """Any date-like value -> 'YYYY-MM-DD', or None if it is not a date."""
    d = to_date(s) if s else None
    return d.isoformat() if d else None


def fy_label(d: dt.date) -> str:
    y = d.year if d.month <= 3 else d.year + 1
    return f"FY{y % 100:02d}"


def fy_int(label: str) -> int:
    return int(label[2:])


def quarter_of(d: dt.date) -> str | None:
    return _QUARTER_BY_END_MONTH.get(d.month)


def ptype(start: dt.date, end: dt.date) -> str:
    """Classify a duration: Q, 6M, 9M, FY or other."""
    days = (end - start).days + 1
    if 80 <= days <= 100:
        return "Q"
    if 170 <= days <= 195:
        return "6M"
    if 260 <= days <= 285:
        return "9M"
    if 355 <= days <= 372:
        return "FY"
    return "other"


def infer_fy_from_name(name: str) -> str | None:
    m = re.search(r"FY[\s_-]?(\d{4}|\d{2})(?!\d)", name, re.I)
    if m:
        return f"FY{int(m.group(1)[-2:]):02d}"
    m = re.search(r"(20\d{2})[-_](\d{2})(?!\d)", name)
    if m:
        return f"FY{int(m.group(2)):02d}"
    return None
