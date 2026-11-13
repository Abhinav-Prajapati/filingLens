"""ingest/utils (files, xmltree, numeric) and the NSE period rules, tested in isolation."""
import zipfile

import pytest
from lxml import etree

from filingLens.ingest.errors import IngestError, XbrlParseError
from filingLens.ingest.utils.files import read_document_bytes, sha256_file
from filingLens.ingest.utils.numeric import parse_ix_number
from filingLens.ingest.utils.xmltree import (
    first_text,
    iter_named,
    local_name,
    parse_xml,
    resolve_qname,
)
from filingLens.ingest.xbrl.model import Context
from filingLens.ingest.xbrl.nse import apply_period_overrides, column_of, is_root_context


# ---- files ---------------------------------------------------------------------------------
def test_sha256_is_content_identity(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    c.write_bytes(b"other")
    assert sha256_file(a) == sha256_file(b) != sha256_file(c)
    assert len(sha256_file(a)) == 64


def test_read_document_bytes_plain_and_zip(tmp_path):
    f = tmp_path / "x.xml"
    f.write_bytes(b"<a/>")
    assert read_document_bytes(f) == b"<a/>"
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "ignored: not a document suffix")
        zf.writestr("inner.xml", "<b/>")
    assert read_document_bytes(z) == b"<b/>"


def test_read_document_bytes_uses_given_error_type(tmp_path):
    f = tmp_path / "e.xml"
    f.write_bytes(b"")
    with pytest.raises(XbrlParseError):
        read_document_bytes(f, error=XbrlParseError)
    with pytest.raises(IngestError, match="empty"):
        read_document_bytes(f)


# ---- xmltree --------------------------------------------------------------------------------
DOC = b"""<r xmlns:a="urn:a" xmlns:b="urn:b"><a:x>  </a:x><b:x>hit</b:x><!-- c -->
          <a:y name="b:Thing"/></r>"""


def test_xmltree_helpers_ignore_namespaces():
    root = parse_xml(DOC, "t")
    assert local_name(root.tag) == "r"
    assert len(list(iter_named(root, "x"))) == 2
    assert first_text(root, "x") == "hit"            # skips the blank one
    assert first_text(root, "missing") is None
    y = next(iter_named(root, "y"))
    assert resolve_qname(y, y.get("name")) == ("Thing", "urn:b")
    comment = next(e for e in root.iter() if not isinstance(e.tag, str))
    assert local_name(comment.tag) == ""


def test_parse_xml_errors():
    with pytest.raises(XbrlParseError):
        parse_xml(b"\x00\x01", "bin", XbrlParseError)


# ---- numeric ---------------------------------------------------------------------------------
IX = 'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"'


@pytest.mark.parametrize("attrs,text,expected", [
    ('scale="7"', "2,40,893", 240893e7),
    ('scale="5" sign="-"', "12.5", -12.5e5),
    ('format="ixt:fixed-zero"', "-", 0.0),
    ('format="ixt:num-comma-decimal"', "1.234,5", 1234.5),
    ("", "n/a", None),
])
def test_parse_ix_number(attrs, text, expected):
    el = etree.fromstring(f"<ix:nonFraction {IX} {attrs}>{text}</ix:nonFraction>")
    got = parse_ix_number(el)
    assert got == (pytest.approx(expected) if expected is not None else None)


# ---- NSE rules -----------------------------------------------------------------------------
def test_root_and_column_names():
    assert is_root_context("FourD") and is_root_context("OneI")
    assert not is_root_context("FourOperatingExpenses01D") and not is_root_context("c_fy")
    assert column_of("FourOperatingExpenses01D") == "Four" and column_of("c_fy") is None


def test_apply_period_overrides():
    ctx = {"FourD": Context("FourD", "2024-01-01", "2024-03-31", None),
           "FourX01D": Context("FourX01D", "2024-01-01", "2024-03-31", None, (("d", "m"),)),
           "OneD": Context("OneD", "2024-01-01", "2024-03-31", None),
           "OneI": Context("OneI", None, None, "2024-03-31")}
    raw = [("DateOfStartOfReportingPeriod", "", "2023-04-01", "FourD"),
           ("DateOfEndOfReportingPeriod", "", "2024-03-31", "FourD"),
           ("DateOfStartOfReportingPeriod", "", "2024-01-01", "OneD"),
           ("DateOfEndOfReportingPeriod", "", "2024-03-31", "OneD")]
    out, warnings = apply_period_overrides(ctx, raw)
    assert (out["FourD"].start, out["FourX01D"].start) == ("2023-04-01", "2023-04-01")
    assert out["FourX01D"].dims == (("d", "m"),)
    assert out["OneD"] is ctx["OneD"] and out["OneI"] is ctx["OneI"]
    assert len(warnings) == 1 and ctx["FourD"].start == "2024-01-01"   # input not mutated


def test_override_needs_both_dates():
    ctx = {"FourD": Context("FourD", "2024-01-01", "2024-03-31", None)}
    out, warnings = apply_period_overrides(
        ctx, [("DateOfStartOfReportingPeriod", "", "2023-04-01", "FourD")])
    assert out == ctx and warnings == []
