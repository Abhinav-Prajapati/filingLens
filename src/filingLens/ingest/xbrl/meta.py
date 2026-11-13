"""Filing-level information: scope, rounding, period, identity (which company)."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from filingLens.domain.fiscal import fy_label, to_date, to_iso
from filingLens.domain.models import RawFact
from filingLens.domain.registry import Registry
from filingLens.domain.units import rounding_factor
from filingLens.ingest.xbrl.nse import TAG_REPORTING_END, TAG_REPORTING_START

_MONEY_UNIT = re.compile(r"(^|:)INR$", re.IGNORECASE)


@dataclass
class XbrlMeta:
    scope: str = "unknown"              # consolidated | standalone | unknown
    rounding: str | None = None         # declared, e.g. "Crores"
    rounding_factor: float | None = None
    period_start: str = ""              # reporting period of the filing's main column
    period_end: str = ""
    fy_start: str = ""
    fy_end: str = ""
    fy: str | None = None               # "FY24"
    reporting_quarter: str | None = None  # NSE: "Yearly", "First quarter", ...
    company_name: str | None = None
    symbol: str | None = None
    scrip_code: str | None = None
    audited: str | None = None
    board_meeting_date: str | None = None
    currency: str | None = None
    unit_basis: str = "absolute"        # money facts in absolute INR (XBRL rule)
    money_multiplier: float = 1.0       # orig_value * this = INR

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> XbrlMeta:
        """Inverse of to_dict; unknown keys (e.g. 'format', 'warnings') are ignored."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def scope_from_text(v: str) -> str:
    vl = v.lower()
    if vl.startswith("stand") or ("non" in vl and "consol" in vl):
        return "standalone"
    if "consol" in vl:
        return "consolidated"
    return "unknown"


def _unit_basis(rows: list[RawFact]) -> str:
    """XBRL stores monetary facts in the unit itself (INR), never pre-scaled; a negative
    `decimals` (e.g. -7) confirms it. Phase 4 calibration re-checks against the PDF."""
    money_dec = [r.decimals for r in rows
                 if r.value_num is not None and _MONEY_UNIT.search(r.unit or "")]
    confirmed = any(d and d.startswith("-") and d[1:].isdigit() for d in money_dec)
    return "absolute" if confirmed else "assumed_absolute"


def xbrl_meta(rows: list[RawFact]) -> XbrlMeta:
    """Read the filing's general-information facts, falling back to context dates."""
    gen: dict[str, str] = {}
    for r in rows:
        gen.setdefault(r.tag.lower(), r.value_text)
    m = XbrlMeta()

    scope_txt = gen.get("natureofreportstandaloneconsolidated")
    if scope_txt is None:   # other taxonomies: any tag mentioning both words
        scope_txt = next((v for k, v in gen.items()
                          if "standalone" in k and "consolidated" in k), None)
    if scope_txt:
        m.scope = scope_from_text(scope_txt)

    m.rounding = next((v for k, v in gen.items() if "levelofrounding" in k), None)
    m.rounding_factor = rounding_factor(m.rounding)
    m.fy_start = to_iso(gen.get("dateofstartoffinancialyear")) or ""
    m.fy_end = to_iso(gen.get("dateofendoffinancialyear")) or ""
    m.reporting_quarter = gen.get("reportingquarter")
    m.company_name = gen.get("nameofthecompany")
    m.symbol = gen.get("symbol")
    m.scrip_code = gen.get("scripcode")
    m.audited = gen.get("whetherresultsareauditedorunaudited")
    m.board_meeting_date = to_iso(gen.get("dateofboardmeetingwhenfinancialresultswereapproved"))
    m.currency = gen.get("descriptionofpresentationcurrency")

    # reporting period = the main (quarter) column, as filed; fall back to the latest date seen
    starts = [r.value_text for r in rows if r.tag.lower() == TAG_REPORTING_START]
    ends = [r.value_text for r in rows if r.tag.lower() == TAG_REPORTING_END]
    m.period_start = (to_iso(starts[0]) or "") if starts else ""
    m.period_end = max((to_iso(e) or "" for e in ends), default="")
    if not m.period_end:
        dates = [r.period_end or r.instant for r in rows if not r.has_dim]
        m.period_end = max((d for d in dates if d), default="")
    fy_end = to_date(m.fy_end) or to_date(m.period_end)
    m.fy = fy_label(fy_end) if fy_end else None
    m.unit_basis = _unit_basis(rows)
    return m


def is_result_filing(rows: list[RawFact], registry: Registry) -> bool:
    """True when at least one tag maps to a registry metric."""
    tags = registry.result_tags()
    return any(r.tag.lower() in tags for r in rows)


def resolve_ticker(meta: XbrlMeta, filename: str, registry: Registry,
                   entity_ids: Iterable[str] = ()) -> str | None:
    """Symbol in the filing, then the entity identifier, then the filename prefix, then name."""
    cands = [meta.symbol or "", *entity_ids, re.split(r"[_\-. ]", filename)[0]]
    for c in cands:
        if c and c.strip().upper() in registry.companies:
            return c.strip().upper()
    name = (meta.company_name or "").lower()
    for t, comp in registry.companies.items():
        if name and any(a in name for a in comp.aliases if len(a) > 3):
            return t
    return None
