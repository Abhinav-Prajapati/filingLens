"""parse_xbrl_file: contexts, units, dimensions, NSE period fix, inline XBRL, bad files."""
import zipfile
from pathlib import Path

import pytest

from filingLens.ingest.xbrl import XbrlParseError, parse_number, parse_xbrl_bytes, parse_xbrl_file

FX = Path(__file__).parent / "fixtures" / "xbrl"


def _by(parsed, tag, ctx):
    return next(r for r in parsed.facts if r.tag == tag and r.ctx_id == ctx)


# ---- NSE annual: the Phase 0 bug -----------------------------------------------------------
def test_nse_fourd_period_is_corrected_to_full_year():
    p = parse_xbrl_file(FX / "nse_consolidated_annual.xml")
    fourd = p.contexts["FourD"]
    assert (fourd.start, fourd.end) == ("2023-04-01", "2024-03-31")
    assert fourd.period_source == "reporting_period_facts"
    r = _by(p, "RevenueFromOperations", "FourD")
    assert (r.period_start, r.period_end, r.value_num) == ("2023-04-01", "2024-03-31", 1e10)


def test_nse_quarter_context_untouched():
    p = parse_xbrl_file(FX / "nse_consolidated_annual.xml")
    oned = p.contexts["OneD"]
    assert (oned.start, oned.end, oned.period_source) == ("2024-01-01", "2024-03-31", "context")


def test_nse_override_applies_to_whole_column_and_warns_once():
    p = parse_xbrl_file(FX / "nse_consolidated_annual.xml")
    derived = p.contexts["FourOperatingExpenses01D"]
    assert (derived.start, derived.end) == ("2023-04-01", "2024-03-31")
    assert derived.has_dim                         # dims survive the override
    assert p.contexts["OneI"].instant == "2024-03-31"
    assert len(p.warnings) == 1 and "FourD" in p.warnings[0]


def test_correct_periods_are_not_overridden():
    p = parse_xbrl_file(FX / "standalone_quarterly.xml")
    assert p.warnings == []
    assert (p.contexts["FourD"].start, p.contexts["FourD"].end) == ("2023-04-01", "2023-12-31")


# ---- general parsing -----------------------------------------------------------------------
def test_units_including_divide():
    p = parse_xbrl_file(FX / "nse_consolidated_annual.xml")
    assert p.units == {"INR": "iso4217:INR", "INRPerShare": "iso4217:INR/xbrli:shares"}
    eps = _by(p, "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "FourD")
    assert eps.unit == "iso4217:INR/xbrli:shares" and eps.decimals == "INF"


def test_text_facts_kept_without_number():
    p = parse_xbrl_file(FX / "nse_consolidated_annual.xml")
    r = _by(p, "NatureOfReportStandaloneConsolidated", "OneD")
    assert r.value_text == "Consolidated" and r.value_num is None


def test_namespace_and_entity_ids():
    p = parse_xbrl_file(FX / "standalone_quarterly.xml")
    assert p.format == "xbrl" and p.entity_ids == ["INFY"]
    assert _by(p, "RevenueFromOperations", "OneD").ns.endswith("2023-03-31/in-bse-fin")


def test_empty_elements_skipped():
    p = parse_xbrl_file(FX / "standalone_quarterly.xml")
    assert not [r for r in p.facts if r.tag == "FinanceCosts"]


def test_dimensions_flagged_explicit_and_typed():
    p = parse_xbrl_file(FX / "dimensional.xml")
    assert p.contexts["c_seg1"].dims == (("f:SegmentAxis", "f:HomeCareMember"),)
    assert p.contexts["c_seg2"].dims == (("f:RowAxis", "2"),)
    assert not p.contexts["c_fy"].has_dim
    assert {r.ctx_id: r.has_dim for r in p.facts if r.tag == "RevenueFromOperations"} == {
        "c_fy": 0, "c_seg1": 1, "c_seg2": 1, "c_2m": 0}


def test_boolean_text_is_not_a_number():
    p = parse_xbrl_file(FX / "dimensional.xml")
    assert _by(p, "Remarks", "c_fy").value_num is None


@pytest.mark.parametrize("text,expected", [
    ("2408930000000.00", 2408930000000.0), ("-610000000.00", -610000000.0),
    ("1,234.5", 1234.5), ("0", 0.0), ("1e3", 1000.0), (".5", 0.5),
    ("true", None), ("NaN", None), ("inf", None), ("", None), ("12abc", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


# ---- inline XBRL ---------------------------------------------------------------------------
def test_inline_xbrl_scale_sign_format_nil():
    p = parse_xbrl_file(FX / "inline.xhtml")
    assert p.format == "ixbrl"
    vals = {r.tag: r.value_num for r in p.facts}
    assert vals["RevenueFromOperations"] == pytest.approx(240893e7)   # nested <b> + scale 7
    assert vals["OtherIncome"] == pytest.approx(-12.5e7)              # sign="-"
    assert vals["FinanceCosts"] == 0.0                                # fixed-zero
    assert "TaxExpense" not in vals                                   # xsi:nil
    assert vals["NatureOfReportStandaloneConsolidated"] is None
    rev = _by(p, "RevenueFromOperations", "FY")
    assert rev.ns.endswith("in-bse-fin") and rev.value_text == "2,40,893"


# ---- containers and bad input ----------------------------------------------------------------
def test_zip_with_one_xml(tmp_path):
    z = tmp_path / "f.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(FX / "nse_consolidated_annual.xml", "inner.xml")
    assert len(parse_xbrl_file(z).facts) == len(
        parse_xbrl_file(FX / "nse_consolidated_annual.xml").facts)


def test_zip_with_two_files_rejected(tmp_path):
    z = tmp_path / "f.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.xml", "<a/>")
        zf.writestr("b.xml", "<b/>")
    with pytest.raises(XbrlParseError, match="exactly one"):
        parse_xbrl_file(z)


@pytest.mark.parametrize("data,match", [
    (b"", "empty"),
    (b"   \n", "empty"),
    (b"<html><body>Access Denied</body></html>", "no XBRL contexts"),
    (b"not xml at all", "XML|contexts"),
])
def test_bad_files_raise_clear_errors(tmp_path, data, match):
    f = tmp_path / "bad.xml"
    f.write_bytes(data)
    with pytest.raises(XbrlParseError, match=match):
        parse_xbrl_file(f)


def test_parse_bytes_name_in_error():
    with pytest.raises(XbrlParseError, match="mything"):
        parse_xbrl_bytes(b"<root/>", "mything")
