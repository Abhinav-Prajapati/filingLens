"""XBRL instance -> raw facts -> canonical facts.

Handles three shapes of file:

* plain XBRL instances (what NSE/BSE serve as ``*.xml``),
* inline XBRL (``ix:nonFraction`` inside XHTML), with ``scale``/``sign``/``format``,
* a ZIP that wraps exactly one of the above.

Matching is namespace-agnostic (by local name), so a taxonomy version bump
(``in-bse-fin/2020-03-31`` -> a later date) does not break parsing.

NSE quirk handled here (found in Phase 0)
-----------------------------------------
NSE result filings use one context per column: ``OneD`` = current quarter,
``FourD`` = year to date, ``OneI`` = balance-sheet date, plus derived contexts
such as ``FourOperatingExpenses01D``. In the files we have seen, **every
``Four*`` context carries the quarter's dates** in its ``<xbrli:period>``; the
real year-to-date period is only given by the facts ``DateOfStartOfReportingPeriod``
/ ``DateOfEndOfReportingPeriod`` reported *in* that context. Taking context dates
at face value turns the FY column into a duplicate Q4 column, which is why the
notebook produced no FY facts. ``parse_xbrl_file`` therefore overrides the
period of every duration context in a column with that column's reporting-period
facts, and records a warning for each override.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import etree

from filingLens.domain.fiscal import fy_label, ptype, quarter_of, to_date
from filingLens.domain.models import Fact, RawFact
from filingLens.domain.registry import Registry
from filingLens.domain.units import rounding_factor

PARSER_NAME = "filingLens.xbrl"
PARSER_VERSION = "1"

_TAG_START = "dateofstartofreportingperiod"
_TAG_END = "dateofendofreportingperiod"
_ROOT_CTX = re.compile(r"^[A-Z][a-z]+[DI]$")      # OneD, FourD, OneI ...
_COLUMN = re.compile(r"^[A-Z][a-z]+")             # "Four" in FourOperatingExpenses01D
_MONEY_UNIT = re.compile(r"(^|:)INR$", re.IGNORECASE)


class XbrlParseError(ValueError):
    """The file is not a usable XBRL / iXBRL document."""


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Context:
    id: str
    start: str | None
    end: str | None
    instant: str | None
    dims: tuple[tuple[str, str], ...] = ()
    period_source: str = "context"   # "context" | "reporting_period_facts"

    @property
    def has_dim(self) -> bool:
        return bool(self.dims)

    @property
    def is_root(self) -> bool:
        """True for the column's main context (OneD, FourD, OneI). Non-NSE ids: False."""
        return bool(_ROOT_CTX.match(self.id))


@dataclass
class ParsedFiling:
    path: str
    format: str                                  # "xbrl" | "ixbrl"
    contexts: dict[str, Context]
    units: dict[str, str]
    facts: list[RawFact]
    entity_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _local(tag: object) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _children(el: etree._Element, name: str) -> Iterator[etree._Element]:
    return (c for c in el.iter() if _local(c.tag) == name)


def _first_text(el: etree._Element, name: str) -> str | None:
    for c in _children(el, name):
        t = (c.text or "").strip()
        if t:
            return t
    return None


def _iso(s: str | None) -> str | None:
    d = to_date(s) if s else None
    return d.isoformat() if d else None


def _read_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if not data.strip():
        raise XbrlParseError(f"{path.name}: file is empty")
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist()
                     if n.lower().endswith((".xml", ".xbrl", ".html", ".htm", ".xhtml"))]
            if len(names) != 1:
                raise XbrlParseError(f"{path.name}: zip must contain exactly one XBRL file, "
                                     f"found {names}")
            data = z.read(names[0])
    return data


def _parse_tree(data: bytes, name: str) -> etree._Element:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False,
                             no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise XbrlParseError(f"{name}: not XML ({e})") from e
    if root is None:
        raise XbrlParseError(f"{name}: not XML (parser returned nothing)")
    return root


def _parse_contexts(root: etree._Element) -> dict[str, Context]:
    out: dict[str, Context] = {}
    for c in _children(root, "context"):
        cid = c.get("id")
        if not cid:
            continue
        per = next(_children(c, "period"), None)
        if per is None:
            continue
        dims: list[tuple[str, str]] = []
        for m in c.iter():
            ln = _local(m.tag)
            if ln == "explicitMember":
                dims.append((m.get("dimension", ""), (m.text or "").strip()))
            elif ln == "typedMember":
                dims.append((m.get("dimension", ""), "".join(m.itertext()).strip()))
        out[cid] = Context(cid, _iso(_first_text(per, "startDate")),
                           _iso(_first_text(per, "endDate")),
                           _iso(_first_text(per, "instant")), tuple(dims))
    return out


def _parse_units(root: etree._Element) -> dict[str, str]:
    units: dict[str, str] = {}
    for u in _children(root, "unit"):
        uid = u.get("id")
        if not uid:
            continue
        div = next(_children(u, "divide"), None)
        if div is not None:
            num = next(_children(div, "unitNumerator"), None)
            den = next(_children(div, "unitDenominator"), None)
            n = _first_text(num, "measure") if num is not None else None
            d = _first_text(den, "measure") if den is not None else None
            units[uid] = f"{n or ''}/{d or ''}"
        else:
            units[uid] = "*".join((m.text or "").strip() for m in _children(u, "measure"))
    return units


def parse_number(text: str) -> float | None:
    """Plain XBRL numeric text -> float. Returns None for text, booleans, NaN, inf."""
    t = text.strip().replace(",", "")
    if not t or not re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", t):
        return None
    v = float(t)
    return v if math.isfinite(v) else None


def _ix_number(el: etree._Element) -> float | None:
    """Inline XBRL ix:nonFraction -> float, applying format, scale and sign."""
    text = "".join(el.itertext()).strip()
    fmt = (el.get("format") or "").lower()
    if "zerodash" in fmt or "fixed-zero" in fmt or "fixedzero" in fmt:
        v: float | None = 0.0
    else:
        t = text
        if "numcommadecimal" in fmt or "num-comma-decimal" in fmt:   # 1.234,56
            t = t.replace(".", "").replace(" ", "").replace(",", ".")
        t = re.sub(r"[^\d.\-]", "", t)
        v = parse_number(t) if t else None
    if v is None:
        return None
    scale = el.get("scale")
    if scale and re.fullmatch(r"-?\d+", scale.strip()):
        v *= 10 ** int(scale)
    if el.get("sign") == "-":
        v = -v
    return v


def _resolve_qname(el: etree._Element, qname: str) -> tuple[str, str]:
    prefix, _, local = qname.rpartition(":")
    return local, (el.nsmap.get(prefix or None) or "")


def _column(ctx_id: str) -> str | None:
    m = _COLUMN.match(ctx_id)
    return m.group(0) if m else None


def _period_overrides(contexts: dict[str, Context],
                      raw: list[tuple[str, str, str, str]]) -> dict[str, tuple[str, str]]:
    """column -> (start, end) taken from DateOf{Start,End}OfReportingPeriod facts that are
    reported in that column's root duration context (e.g. FourD)."""
    found: dict[str, dict[str, str]] = {}
    for tag, _ns, text, cref in raw:
        tl = tag.lower()
        if tl not in (_TAG_START, _TAG_END):
            continue
        ctx = contexts.get(cref)
        if ctx is None or not ctx.is_root or ctx.instant or ctx.has_dim:
            continue
        col, d = _column(cref), _iso(text)
        if col and d:
            found.setdefault(col, {})["start" if tl == _TAG_START else "end"] = d
    return {c: (v["start"], v["end"]) for c, v in found.items() if "start" in v and "end" in v}


def parse_xbrl_bytes(data: bytes, name: str = "<bytes>") -> ParsedFiling:
    root = _parse_tree(data, name)
    contexts = _parse_contexts(root)
    if not contexts:
        root_name = _local(root.tag) or str(root.tag)
        raise XbrlParseError(f"{name}: no XBRL contexts found (root element <{root_name}>); "
                             "is this an XBRL instance, or an HTML error page?")
    units = _parse_units(root)
    is_inline = any(
        _local(el.tag) in ("nonFraction", "nonNumeric")
        and "inlinexbrl" in (etree.QName(el).namespace or "").lower()
        for el in root.iter() if isinstance(el.tag, str))

    # pass 1: collect (tag, ns, text, contextRef) plus attributes
    raw: list[tuple[str, str, str, str]] = []
    extra: list[tuple[float | None, str | None, str | None]] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        cref = el.get("contextRef")
        if not cref or cref not in contexts:
            continue
        if is_inline:
            ln = _local(el.tag)
            if ln not in ("nonFraction", "nonNumeric") or not el.get("name"):
                continue
            if el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
                continue
            tag, ns = _resolve_qname(el, el.get("name", ""))
            text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
            num = _ix_number(el) if ln == "nonFraction" else None
            if not text and num is None:
                continue
        else:
            text = (el.text or "").strip()
            if not text:
                continue
            q = etree.QName(el)
            tag, ns = q.localname, q.namespace or ""
            num = parse_number(text)
        raw.append((tag, ns, text, cref))
        extra.append((num, el.get("unitRef"), el.get("decimals")))

    # pass 2: correct NSE column periods, then build rows
    overrides = _period_overrides(contexts, raw)
    warnings: list[str] = []
    for cid, c in list(contexts.items()):
        col = _column(cid)
        if c.instant or not col or col not in overrides:
            continue
        s, e = overrides[col]
        if (c.start, c.end) != (s, e):
            if c.is_root:
                warnings.append(f"context {cid}: period {c.start}..{c.end} replaced by "
                                f"reporting-period facts {s}..{e}")
            contexts[cid] = Context(cid, s, e, None, c.dims, "reporting_period_facts")

    facts: list[RawFact] = []
    for (tag, ns, text, cref), (num, unit_ref, dec) in zip(raw, extra, strict=True):
        c = contexts[cref]
        facts.append(RawFact(tag=tag, ns=ns, value_text=text[:500], value_num=num,
                             unit=units.get(unit_ref or "", ""), decimals=dec, ctx_id=cref,
                             period_start=c.start, period_end=c.end, instant=c.instant,
                             has_dim=int(c.has_dim)))
    entity_ids = sorted({(i.text or "").strip() for i in _children(root, "identifier")} - {""})
    return ParsedFiling(name, "ixbrl" if is_inline else "xbrl", contexts, units, facts,
                        entity_ids, warnings)


def parse_xbrl_file(path: str | Path) -> ParsedFiling:
    p = Path(path)
    return parse_xbrl_bytes(_read_bytes(p), p.name)


# --------------------------------------------------------------------------------------
# filing metadata
# --------------------------------------------------------------------------------------
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


def _scope_from_text(v: str) -> str:
    vl = v.lower()
    if vl.startswith("stand") or ("non" in vl and "consol" in vl):
        return "standalone"
    if "consol" in vl:
        return "consolidated"
    return "unknown"


def xbrl_meta(rows: list[RawFact]) -> XbrlMeta:
    """Filing-level facts: scope, rounding, period and identity."""
    gen: dict[str, str] = {}
    for r in rows:
        gen.setdefault(r.tag.lower(), r.value_text)
    m = XbrlMeta()

    scope_txt = gen.get("natureofreportstandaloneconsolidated")
    if scope_txt is None:   # other taxonomies: any tag mentioning both words
        scope_txt = next((v for k, v in gen.items()
                          if "standalone" in k and "consolidated" in k), None)
    if scope_txt:
        m.scope = _scope_from_text(scope_txt)

    m.rounding = next((v for k, v in gen.items() if "levelofrounding" in k), None)
    m.rounding_factor = rounding_factor(m.rounding)
    m.fy_start = _iso(gen.get("dateofstartoffinancialyear")) or ""
    m.fy_end = _iso(gen.get("dateofendoffinancialyear")) or ""
    m.reporting_quarter = gen.get("reportingquarter")
    m.company_name = gen.get("nameofthecompany")
    m.symbol = gen.get("symbol")
    m.scrip_code = gen.get("scripcode")
    m.audited = gen.get("whetherresultsareauditedorunaudited")
    m.board_meeting_date = _iso(gen.get("dateofboardmeetingwhenfinancialresultswereapproved"))
    m.currency = gen.get("descriptionofpresentationcurrency")

    # reporting period = the main (quarter) column, as filed; fall back to the latest date seen
    starts = [r.value_text for r in rows if r.tag.lower() == _TAG_START]
    ends = [r.value_text for r in rows if r.tag.lower() == _TAG_END]
    m.period_start = (_iso(starts[0]) or "") if starts else ""
    m.period_end = max((_iso(e) or "" for e in ends), default="") or ""
    if not m.period_end:
        dates = [r.period_end or r.instant for r in rows
                 if not r.has_dim and (r.period_end or r.instant)]
        m.period_end = max((d for d in dates if d), default="")
    fy_end = to_date(m.fy_end) or to_date(m.period_end)
    m.fy = fy_label(fy_end) if fy_end else None

    # Units. XBRL stores monetary facts in the unit itself (INR), never pre-scaled; a
    # negative `decimals` (e.g. -7) confirms it. Phase 4 calibration re-checks against the PDF.
    money_dec = [r.decimals for r in rows
                 if r.value_num is not None and _MONEY_UNIT.search(r.unit or "")]
    if any(d and d.lstrip("-").isdigit() and d.startswith("-") for d in money_dec):
        m.unit_basis = "absolute"
    else:
        m.unit_basis = "assumed_absolute"
    return m


# --------------------------------------------------------------------------------------
# canonical facts
# --------------------------------------------------------------------------------------
def is_result_filing(rows: list[RawFact], registry: Registry) -> bool:
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


@dataclass
class FilingFacts:
    """Canonical facts extracted from one filing, before cross-filing merge."""

    doc_id: str
    ticker: str
    meta: XbrlMeta
    facts: list[Fact]
    notes: list[str] = field(default_factory=list)


def _fact_value(orig: float, unit_kind: str, meta: XbrlMeta) -> tuple[float, str]:
    if unit_kind == "money":
        return orig * meta.money_multiplier / 1e7, "INR crore"
    return orig, "INR per share"


def facts_from_raw(rows: list[RawFact], meta: XbrlMeta, ticker: str, doc_id: str,
                   registry: Registry) -> FilingFacts:
    """Map raw facts to the canonical metric list. Dimensional contexts are ignored.
    For each metric the first candidate tag that has data wins (registry order)."""
    out = FilingFacts(doc_id, ticker, meta, [])
    base = [r for r in rows if not r.has_dim and r.value_num is not None]
    # root contexts (OneD/FourD/OneI) first, so they win ties with derived contexts
    base.sort(key=lambda r: (not _ROOT_CTX.match(r.ctx_id), r.ctx_id))
    by_tag: dict[str, list[RawFact]] = {}
    for r in base:
        by_tag.setdefault(r.tag.lower(), []).append(r)

    for mid, spec in registry.metrics.items():
        chosen: list[RawFact] = []
        for cand in spec.tags:
            if spec.kind == "flow":
                rs = [r for r in by_tag.get(cand, []) if r.period_start and r.period_end]
            else:
                rs = [r for r in by_tag.get(cand, []) if r.instant]
            if rs:
                chosen = rs
                break
        seen: dict[tuple[str, str], RawFact] = {}
        for r in chosen:
            assert r.value_num is not None
            if spec.kind == "flow":
                ps, pe = to_date(r.period_start), to_date(r.period_end)
                if not ps or not pe:
                    continue
                pt = ptype(ps, pe)
                if pt == "other":
                    out.notes.append(f"{mid}: skipped {r.ctx_id} {ps}..{pe} "
                                     f"({(pe - ps).days + 1} days is not Q/6M/9M/FY)")
                    continue
                key = (ps.isoformat(), pe.isoformat())
            else:
                pe = to_date(r.instant)
                if not pe:
                    continue
                pt, key = "INSTANT", ("", pe.isoformat())
            if key in seen:
                prev = seen[key]
                if prev.value_num is not None and abs(prev.value_num - r.value_num) > 1e-6 * max(
                        1.0, abs(r.value_num)):
                    out.notes.append(f"{mid}: conflicting values for {key[0]}..{key[1]}: "
                                     f"{prev.ctx_id}={prev.value_num:g} kept, "
                                     f"{r.ctx_id}={r.value_num:g} ignored")
                continue
            seen[key] = r
            value, unit = _fact_value(r.value_num, spec.unit_kind, meta)
            out.facts.append(Fact(
                ticker=ticker, metric_id=mid, period_type=pt, fy=fy_label(pe),
                quarter=quarter_of(pe) if pt == "Q" else None,
                period_start=key[0], period_end=key[1], scope=meta.scope,
                value=value, unit=unit, orig_value=r.value_num, orig_unit=meta.rounding or "",
                xbrl_tag=r.tag, doc_id=doc_id, verified="no", qa="ok", method="xbrl"))
    return out


@dataclass(frozen=True)
class Restatement:
    ticker: str
    scope: str
    metric_id: str
    period_start: str
    period_end: str
    old_value: float
    new_value: float
    old_doc: str
    new_doc: str

    @property
    def detail(self) -> str:
        return (f"{self.metric_id}: {self.old_value:g} -> {self.new_value:g} "
                f"(later filing wins; {self.old_doc[:10]} -> {self.new_doc[:10]})")


@dataclass
class BuildResult:
    facts: list[Fact]
    restatements: list[Restatement]


def _filing_order(f: FilingFacts) -> tuple[str, str, str]:
    return (f.meta.period_end or "", f.meta.board_meeting_date or "", f.doc_id)


def build_facts(filings: list[FilingFacts]) -> BuildResult:
    """Merge canonical facts across filings. Filings are applied oldest first, so a later
    filing overwrites an earlier one for the same (ticker, metric, period, scope); each
    changed value is recorded as a restatement. Input order does not matter."""
    merged: dict[tuple[str, str, str, str, str], Fact] = {}
    restated: list[Restatement] = []
    for filing in sorted(filings, key=_filing_order):
        for f in filing.facts:
            k = (f.ticker, f.metric_id, f.period_start, f.period_end, f.scope)
            prev = merged.get(k)
            if prev is not None and abs(prev.orig_value - f.orig_value) > 1e-6 * max(
                    1.0, abs(f.orig_value)):
                restated.append(Restatement(f.ticker, f.scope, f.metric_id, f.period_start,
                                            f.period_end, prev.orig_value, f.orig_value,
                                            prev.doc_id, f.doc_id))
            merged[k] = f
    facts = sorted(merged.values(), key=lambda f: (f.ticker, f.scope, f.metric_id,
                                                   f.period_end, f.period_start))
    return BuildResult(facts, restated)
