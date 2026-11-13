"""Namespace-agnostic lxml helpers. Elements are matched by local name only, so a
taxonomy version bump (new namespace URI) never breaks a lookup."""
from __future__ import annotations

from collections.abc import Iterator

from lxml import etree

from filingLens.ingest.errors import IngestError


def local_name(tag: object) -> str:
    """'{http://...}context' -> 'context'. Comments/PIs (non-str tags) -> ''."""
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def namespace_of(el: etree._Element) -> str:
    return etree.QName(el).namespace or ""


def iter_named(el: etree._Element, name: str) -> Iterator[etree._Element]:
    """All descendants (and el itself) whose local name is ``name``."""
    return (c for c in el.iter() if local_name(c.tag) == name)


def first_text(el: etree._Element, name: str) -> str | None:
    """Stripped text of the first descendant called ``name`` that has text."""
    for c in iter_named(el, name):
        t = (c.text or "").strip()
        if t:
            return t
    return None


def resolve_qname(el: etree._Element, qname: str) -> tuple[str, str]:
    """'prefix:Local' written in an attribute -> (local, namespace URI) using el's nsmap."""
    prefix, _, local = qname.rpartition(":")
    return local, (el.nsmap.get(prefix or None) or "")


def parse_xml(data: bytes, name: str, error: type[IngestError] = IngestError) -> etree._Element:
    """Parse bytes leniently (recover from small errors) and safely (no network access,
    no entity expansion). Raises ``error`` when nothing usable comes out."""
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False,
                             no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise error(f"{name}: not XML ({e})") from e
    if root is None:
        raise error(f"{name}: not XML (parser returned nothing)")
    return root
