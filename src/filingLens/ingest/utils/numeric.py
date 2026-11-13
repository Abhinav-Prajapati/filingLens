"""Number parsing for XBRL facts."""
from __future__ import annotations

import math
import re

from lxml import etree

_NUMBER = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
_ZERO_FORMATS = ("zerodash", "fixed-zero", "fixedzero")
_COMMA_DECIMAL_FORMATS = ("numcommadecimal", "num-comma-decimal")


def parse_number(text: str) -> float | None:
    """Plain XBRL numeric text -> float. Returns None for text, booleans, NaN and inf."""
    t = text.strip().replace(",", "")
    if not t or not _NUMBER.fullmatch(t):
        return None
    v = float(t)
    return v if math.isfinite(v) else None


def parse_ix_number(el: etree._Element) -> float | None:
    """Inline XBRL ``ix:nonFraction`` -> float in the fact's unit.

    The displayed text is transformed by ``format`` (e.g. ``ixt:fixed-zero`` shows "-" for 0),
    multiplied by 10**``scale`` (scale="7" means the page shows crore), and negated when
    ``sign="-"``."""
    fmt = (el.get("format") or "").lower()
    if any(f in fmt for f in _ZERO_FORMATS):
        v: float | None = 0.0
    else:
        t = "".join(el.itertext()).strip()
        if any(f in fmt for f in _COMMA_DECIMAL_FORMATS):            # 1.234,56
            t = t.replace(".", "").replace(" ", "").replace(",", ".")
        t = re.sub(r"[^\d.\-]", "", t)
        v = parse_number(t) if t else None
    if v is None:
        return None
    scale = (el.get("scale") or "").strip()
    if re.fullmatch(r"-?\d+", scale):
        v *= 10 ** int(scale)
    if el.get("sign") == "-":
        v = -v
    return v
