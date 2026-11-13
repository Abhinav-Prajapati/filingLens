"""xbrl_meta, facts_from_raw, resolve_ticker and build_facts (cross-filing merge)."""
from pathlib import Path

import pytest

from filingLens.domain.models import RawFact
from filingLens.domain.registry import load_registry
from filingLens.ingest.xbrl import (
    XbrlMeta,
    build_facts,
    facts_from_raw,
    is_result_filing,
    parse_xbrl_file,
    resolve_ticker,
    xbrl_meta,
)

FX = Path(__file__).parent / "fixtures" / "xbrl"
REG = load_registry()


def _filing(name, ticker=None, doc_id=None):
    p = parse_xbrl_file(FX / name)
    meta = xbrl_meta(p.facts)
    t = ticker or resolve_ticker(meta, name, REG, p.entity_ids)
    return facts_from_raw(p.facts, meta, t, doc_id or name, REG)


def _get(facts, metric, ptype, end):
    hits = [f for f in facts if f.metric_id == metric and f.period_type == ptype
            and f.period_end == end]
    assert len(hits) == 1, (metric, ptype, end, hits)
    return hits[0]


# ---- meta ----------------------------------------------------------------------------------
def test_meta_nse_annual():
    m = xbrl_meta(parse_xbrl_file(FX / "nse_consolidated_annual.xml").facts)
    assert m.scope == "consolidated" and m.rounding == "Crores" and m.rounding_factor == 1e7
    assert (m.period_start, m.period_end, m.fy) == ("2024-01-01", "2024-03-31", "FY24")
    assert (m.fy_start, m.fy_end) == ("2023-04-01", "2024-03-31")
    assert m.symbol == "TCS" and m.reporting_quarter == "Yearly"
    assert m.board_meeting_date == "2024-04-12"
    assert m.unit_basis == "absolute" and m.money_multiplier == 1.0


def test_meta_standalone_quarterly():
    m = xbrl_meta(parse_xbrl_file(FX / "standalone_quarterly.xml").facts)
    assert m.scope == "standalone" and m.rounding_factor == 1e5
    assert m.period_end == "2023-12-31" and m.fy == "FY24"


def test_meta_without_general_info_falls_back_to_contexts():
    m = xbrl_meta(parse_xbrl_file(FX / "dimensional.xml").facts)
    assert m.scope == "consolidated" and m.period_end == "2024-03-31" and m.fy == "FY24"
    assert m.rounding is None


@pytest.mark.parametrize("text,scope", [
    ("Consolidated", "consolidated"), ("Standalone", "standalone"),
    ("Non-consolidated", "standalone"), ("??", "unknown")])
def test_scope_text(text, scope):
    r = RawFact("NatureOfReportStandaloneConsolidated", "", text, None, "", None, "c",
                "2023-04-01", "2024-03-31", None, 0)
    assert xbrl_meta([r]).scope == scope


def test_unit_basis_assumed_when_decimals_not_negative():
    r = RawFact("RevenueFromOperations", "", "240893", 240893.0, "iso4217:INR", "0", "c",
                "2023-04-01", "2024-03-31", None, 0)
    assert xbrl_meta([r]).unit_basis == "assumed_absolute"


# ---- ticker ----------------------------------------------------------------------------------
def test_resolve_ticker_order():
    assert resolve_ticker(XbrlMeta(symbol="TCS"), "x.xml", REG) == "TCS"
    assert resolve_ticker(XbrlMeta(), "x.xml", REG, ["INFY"]) == "INFY"
    assert resolve_ticker(XbrlMeta(), "HINDUNILVR_Annual_1.xml", REG) == "HINDUNILVR"
    assert resolve_ticker(XbrlMeta(company_name="Hindustan Unilever Limited"), "x.xml",
                          REG) == "HINDUNILVR"
    assert resolve_ticker(XbrlMeta(symbol="RELIANCE"), "x.xml", REG) is None


# ---- canonical facts ---------------------------------------------------------------------------
def test_annual_filing_yields_fy_facts():
    """Regression for Phase 0: an NSE annual file must produce FY facts, not just Q4."""
    f = _filing("nse_consolidated_annual.xml")
    fy = _get(f.facts, "revenue_from_operations", "FY", "2024-03-31")
    assert fy.period_start == "2023-04-01" and fy.fy == "FY24" and fy.quarter is None
    assert fy.value == pytest.approx(1000.0) and fy.unit == "INR crore"
    assert fy.orig_value == 1e10 and fy.orig_unit == "Crores"
    q4 = _get(f.facts, "revenue_from_operations", "Q", "2024-03-31")
    assert q4.period_start == "2024-01-01" and q4.quarter == "Q4"
    assert q4.value == pytest.approx(260.0)


def test_all_expected_metrics_and_values():
    f = _filing("nse_consolidated_annual.xml")
    exp = {"other_income": 50, "total_income": 1050, "employee_expense": 600,
           "total_expenses": 800, "profit_before_tax": 250, "tax_expense": 60,
           "net_profit": 190}
    for m, v in exp.items():
        assert _get(f.facts, m, "FY", "2024-03-31").value == pytest.approx(v), m
    assert _get(f.facts, "total_assets", "INSTANT", "2024-03-31").value == pytest.approx(2000)
    assert _get(f.facts, "total_equity", "INSTANT", "2024-03-31").period_start == ""


def test_per_share_values_not_scaled():
    f = _filing("nse_consolidated_annual.xml")
    eps = _get(f.facts, "eps_basic", "FY", "2024-03-31")
    assert eps.value == 12.5 and eps.unit == "INR per share"


def test_tag_priority_first_candidate_wins():
    f = _filing("nse_consolidated_annual.xml")
    assert _get(f.facts, "net_profit", "FY", "2024-03-31").xbrl_tag == "ProfitLossForPeriod"


def test_dimensional_contexts_ignored():
    f = _filing("nse_consolidated_annual.xml")
    # OtherExpenses breakdown lives in a dimensional context and is not a metric
    assert all(x.xbrl_tag != "OtherExpenses" for x in f.facts)
    d = _filing("dimensional.xml")
    revs = [x for x in d.facts if x.metric_id == "revenue_from_operations"]
    assert [(x.period_type, x.value) for x in revs] == [("FY", pytest.approx(500.0))]
    assert _get(d.facts, "total_assets", "INSTANT", "2024-03-31").value == pytest.approx(900.0)


def test_unrecognised_period_length_skipped_with_note():
    d = _filing("dimensional.xml")
    assert any("c_2m" in n and "not Q/6M/9M/FY" in n for n in d.notes)


def test_quarterly_filing_q_and_9m():
    f = _filing("standalone_quarterly.xml")
    q3 = _get(f.facts, "revenue_from_operations", "Q", "2023-12-31")
    assert q3.quarter == "Q3" and q3.scope == "standalone" and q3.value == pytest.approx(20.0)
    nine = _get(f.facts, "revenue_from_operations", "9M", "2023-12-31")
    assert nine.period_start == "2023-04-01" and nine.value == pytest.approx(55.0)
    assert not [x for x in f.facts if x.period_type == "FY"]


def test_inline_filing_facts():
    f = _filing("inline.xhtml")
    assert f.meta.scope == "standalone" and f.ticker == "TCS"
    assert _get(f.facts, "revenue_from_operations", "FY", "2024-03-31").value == pytest.approx(
        240893.0)
    assert _get(f.facts, "finance_costs", "FY", "2024-03-31").value == 0.0


def test_conflicting_same_period_root_context_wins():
    def raw(ctx, v):
        return RawFact("RevenueFromOperations", "", str(v), v, "iso4217:INR", "-7", ctx,
                       "2023-04-01", "2024-03-31", None, 0)
    rows = [raw("FourOtherThing01D", 5e9), raw("FourD", 1e10)]
    f = facts_from_raw(rows, XbrlMeta(scope="consolidated"), "TCS", "d", REG)
    assert [x.orig_value for x in f.facts] == [1e10]
    assert any("conflicting" in n and "FourOtherThing01D" in n for n in f.notes)


def test_is_result_filing():
    assert is_result_filing(parse_xbrl_file(FX / "nse_consolidated_annual.xml").facts, REG)
    r = RawFact("SomethingElse", "", "1", 1.0, "", None, "c", None, None, "2024-03-31", 0)
    assert not is_result_filing([r], REG)


# ---- build_facts -------------------------------------------------------------------------------
def _with_rev(filing, value, period_end_meta, doc_id):
    """Copy of a filing whose FY revenue is changed (simulates a later restated filing)."""
    import copy
    f2 = copy.deepcopy(filing)
    f2.doc_id = doc_id
    f2.meta.period_end = period_end_meta
    f2.facts = [x.model_copy(update={"doc_id": doc_id, **(
        {"orig_value": value, "value": value / 1e7}
        if x.metric_id == "revenue_from_operations" and x.period_type == "FY" else {})})
        for x in filing.facts]
    return f2


def test_build_facts_later_filing_wins_and_logs_restatement():
    a = _filing("nse_consolidated_annual.xml", doc_id="a" * 64)
    b = _with_rev(a, 1.1e10, "2025-03-31", "b" * 64)
    for order in ([a, b], [b, a]):               # input order must not matter
        res = build_facts(order)
        rev = [f for f in res.facts if f.metric_id == "revenue_from_operations"
               and f.period_type == "FY"]
        assert len(rev) == 1 and rev[0].orig_value == 1.1e10 and rev[0].doc_id == "b" * 64
        assert len(res.restatements) == 1
        r = res.restatements[0]
        assert (r.old_value, r.new_value, r.metric_id) == (1e10, 1.1e10,
                                                           "revenue_from_operations")
        assert "later filing wins" in r.detail


def test_build_facts_same_values_no_restatement_and_no_duplicates():
    a = _filing("nse_consolidated_annual.xml", doc_id="a" * 64)
    b = _with_rev(a, 1e10, "2025-03-31", "b" * 64)
    res = build_facts([a, b])
    assert res.restatements == []
    assert len(res.facts) == len(a.facts)


def test_build_facts_scopes_kept_apart():
    a = _filing("nse_consolidated_annual.xml")
    s = _filing("nse_consolidated_annual.xml")
    s.meta.scope = "standalone"
    s.facts = [x.model_copy(update={"scope": "standalone", "orig_value": 1.0})
               for x in s.facts]
    res = build_facts([a, s])
    assert res.restatements == [] and len(res.facts) == 2 * len(a.facts)
