"""File -> ParsedFiling (contexts, units, raw facts).

Handles plain XBRL instances (what NSE/BSE serve as ``*.xml``), inline XBRL
(``ix:nonFraction`` inside XHTML) and a ZIP wrapping exactly one of those.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from filingLens.domain.fiscal import to_iso
from filingLens.domain.models import RawFact
from filingLens.ingest.errors import XbrlParseError
from filingLens.ingest.utils.files import read_document_bytes
from filingLens.ingest.utils.numeric import parse_ix_number, parse_number
from filingLens.ingest.utils.xmltree import (
    first_text,
    iter_named,
    local_name,
    namespace_of,
    parse_xml,
    resolve_qname,
)
from filingLens.ingest.xbrl.model import Context, ParsedFiling
from filingLens.ingest.xbrl.nse import RawTuple, apply_period_overrides

PARSER_NAME = "filingLens.xbrl"
PARSER_VERSION = "1"

_IX_FACTS = ("nonFraction", "nonNumeric")
_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

# number, unitRef, decimals - kept alongside each RawTuple until periods are final
_Attrs = tuple[float | None, str | None, str | None]


def parse_contexts(root: etree._Element) -> dict[str, Context]:
    out: dict[str, Context] = {}
    for c in iter_named(root, "context"):
        cid = c.get("id")
        per = next(iter_named(c, "period"), None)
        if not cid or per is None:
            continue
        dims: list[tuple[str, str]] = []
        for m in c.iter():
            ln = local_name(m.tag)
            if ln == "explicitMember":
                dims.append((m.get("dimension", ""), (m.text or "").strip()))
            elif ln == "typedMember":
                dims.append((m.get("dimension", ""), "".join(m.itertext()).strip()))
        out[cid] = Context(cid, to_iso(first_text(per, "startDate")),
                           to_iso(first_text(per, "endDate")),
                           to_iso(first_text(per, "instant")), tuple(dims))
    return out


def parse_units(root: etree._Element) -> dict[str, str]:
    """unit id -> 'iso4217:INR', or 'iso4217:INR/xbrli:shares' for a divide."""
    units: dict[str, str] = {}
    for u in iter_named(root, "unit"):
        uid = u.get("id")
        if not uid:
            continue
        div = next(iter_named(u, "divide"), None)
        if div is None:
            units[uid] = "*".join((m.text or "").strip() for m in iter_named(u, "measure"))
            continue
        num = next(iter_named(div, "unitNumerator"), None)
        den = next(iter_named(div, "unitDenominator"), None)
        n = first_text(num, "measure") if num is not None else None
        d = first_text(den, "measure") if den is not None else None
        units[uid] = f"{n or ''}/{d or ''}"
    return units


def is_inline(root: etree._Element) -> bool:
    return any(local_name(el.tag) in _IX_FACTS and "inlinexbrl" in namespace_of(el).lower()
               for el in root.iter() if isinstance(el.tag, str))


def _read_plain(el: etree._Element) -> tuple[RawTuple, float | None] | None:
    text = (el.text or "").strip()
    if not text:
        return None
    q = etree.QName(el)
    return (q.localname, q.namespace or "", text, el.get("contextRef", "")), parse_number(text)


def _read_inline(el: etree._Element) -> tuple[RawTuple, float | None] | None:
    ln = local_name(el.tag)
    if ln not in _IX_FACTS or not el.get("name") or el.get(_XSI_NIL) == "true":
        return None
    tag, ns = resolve_qname(el, el.get("name", ""))
    text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
    num = parse_ix_number(el) if ln == "nonFraction" else None
    if not text and num is None:
        return None
    return (tag, ns, text, el.get("contextRef", "")), num


def collect_facts(root: etree._Element, contexts: dict[str, Context], inline: bool
                  ) -> list[tuple[RawTuple, _Attrs]]:
    """Every element with a known contextRef, read as plain or inline XBRL."""
    read = _read_inline if inline else _read_plain
    out = []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.get("contextRef") not in contexts:
            continue
        got = read(el)
        if got is not None:
            raw, num = got
            out.append((raw, (num, el.get("unitRef"), el.get("decimals"))))
    return out


def parse_xbrl_bytes(data: bytes, name: str = "<bytes>") -> ParsedFiling:
    root = parse_xml(data, name, XbrlParseError)
    contexts = parse_contexts(root)
    if not contexts:
        root_name = local_name(root.tag) or str(root.tag)
        raise XbrlParseError(f"{name}: no XBRL contexts found (root element <{root_name}>); "
                             "is this an XBRL instance, or an HTML error page?")
    units = parse_units(root)
    inline = is_inline(root)
    collected = collect_facts(root, contexts, inline)
    contexts, warnings = apply_period_overrides(contexts, [raw for raw, _ in collected])

    facts = []
    for (tag, ns, text, cref), (num, unit_ref, dec) in collected:
        c = contexts[cref]
        facts.append(RawFact(tag=tag, ns=ns, value_text=text[:500], value_num=num,
                             unit=units.get(unit_ref or "", ""), decimals=dec, ctx_id=cref,
                             period_start=c.start, period_end=c.end, instant=c.instant,
                             has_dim=int(c.has_dim)))
    entity_ids = sorted({(i.text or "").strip() for i in iter_named(root, "identifier")} - {""})
    return ParsedFiling(name, "ixbrl" if inline else "xbrl", contexts, units, facts,
                        entity_ids, warnings)


def parse_xbrl_file(path: str | Path) -> ParsedFiling:
    p = Path(path)
    return parse_xbrl_bytes(read_document_bytes(p, error=XbrlParseError), p.name)
