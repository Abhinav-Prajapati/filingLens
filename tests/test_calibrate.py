"""Calibration rules on hand-made pages (pure functions, no PDF)."""
from conftest import make_fact

from filingLens.ingest.calibrate import PageText, calibrate, number_tokens


def page(n, text, scope="consolidated", section="financial_statements", fy="FY24", ticker="TCS"):
    return PageText("doc", ticker, fy, n, scope, section, text)


def cite(facts, pages):
    citations, reports = calibrate(facts, pages)
    return {c.key[1]: (c.page, c.verified) for c in citations}, reports


def test_number_tokens_ignore_grouping():
    assert {"240893", "4422", "125.88", "24"} <= number_tokens("2,40,893 and 240,893; 4,422 (24) 125.88")


def test_best_page_scope_section_and_prior_year_column():
    f = make_fact(value=240893.0)
    pages = [page(5, "Revenue 2,40,893", scope="none", section="mdna"),
             page(9, "Revenue 2,40,893", scope="standalone"),
             page(7, "Revenue 2,55,324 2,40,893", fy="FY25")]          # FY25 report, comparative
    got, rep = cite([f], pages)
    assert got == {"revenue_from_operations": (7, "pdf")}
    assert rep["TCS"].rate == 1.0 and rep["TCS"].unit_ok


def test_scope_mismatch_flagged_when_only_other_scope_prints_it():
    got, rep = cite([make_fact(value=240893.0)], [page(9, "2,40,893", scope="standalone")])
    assert got == {"revenue_from_operations": (9, "pdf_scope_mismatch")}
    assert rep["TCS"].scope_mismatch == 1


def test_small_values_and_eps_need_a_statement_page_of_the_right_scope():
    facts = [make_fact(metric="finance_costs", value=778.0),
             make_fact(metric="eps_basic", value=125.88).model_copy(update={"unit": "INR per share"})]
    loose = [page(3, "Note 778 on page 125.88", section="mdna", scope="none")]
    assert cite(facts, loose)[0] == {}
    got, rep = cite(facts, loose + [page(8, "Finance costs 778 EPS 125.88")])
    assert got == {"finance_costs": (8, "pdf"), "eps_basic": (8, "pdf")}
    assert rep["TCS"].eligible == 2 and rep["TCS"].not_found == []


def test_quarters_skipped_unit_mismatch_and_no_report():
    q = make_fact(value=61237.0, ptype="Q", start="2024-01-01", quarter="Q4")
    fy = make_fact(value=240893.0)
    got, rep = cite([q, fy], [page(1, "240893.00 in lakh: 24089300")])
    assert got == {"revenue_from_operations": (1, "pdf")} and rep["TCS"].eligible == 1
    _, rep = cite([fy], [page(1, "only in lakh: 2,40,89,300")])
    assert rep["TCS"].unit_votes == {"lakh": 1} and not rep["TCS"].unit_ok
    _, rep = cite([fy], [page(1, "2,40,893", ticker="INFY")])          # no TCS report at all
    assert rep["TCS"].eligible == 0 and rep["TCS"].rate == 0.0
