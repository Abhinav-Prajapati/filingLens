"""Golden tests against real NSE filings (tests/golden/data). Values in expected.yaml were
read by hand from the filing. A missing file is skipped, not failed."""
from pathlib import Path

import pytest
import yaml

from filingLens.domain.registry import load_registry
from filingLens.ingest.validate import run_checks
from filingLens.ingest.xbrl import facts_from_raw, parse_xbrl_file, resolve_ticker, xbrl_meta

GOLDEN = Path(__file__).parent / "golden"
EXPECTED = yaml.safe_load((GOLDEN / "expected.yaml").read_text())
REG = load_registry()


@pytest.fixture(params=sorted(EXPECTED), ids=lambda n: n.split("_")[0])
def golden(request):
    path = GOLDEN / "data" / request.param
    if not path.exists():
        pytest.skip(f"golden file not present: {path}")
    p = parse_xbrl_file(path)
    meta = xbrl_meta(p.facts)
    ticker = resolve_ticker(meta, path.name, REG, p.entity_ids)
    return EXPECTED[request.param], meta, ticker, facts_from_raw(p.facts, meta, ticker, "g", REG)


def test_golden_filing_identity(golden):
    exp, meta, ticker, _ = golden
    assert ticker == exp["ticker"]
    assert meta.scope == exp["scope"] and meta.fy == exp["fy"] and meta.rounding == exp["rounding"]


def test_golden_values(golden):
    exp, _, _, filing = golden
    got = {f"{f.metric_id} {f.period_type} {f.period_end}": f.value for f in filing.facts}
    for key, value in exp["facts"].items():
        assert key in got, f"missing {key}"
        assert got[key] == pytest.approx(value, abs=0.005), key


def test_golden_has_fy_facts_for_every_metric(golden):
    _, _, _, filing = golden
    flows = {m for m, s in REG.metrics.items() if s.kind == "flow"}
    stocks = {m for m, s in REG.metrics.items() if s.kind == "stock"}
    assert {f.metric_id for f in filing.facts if f.period_type == "FY"} == flows
    assert {f.metric_id for f in filing.facts if f.period_type == "INSTANT"} == stocks


def test_golden_validation_has_no_failures(golden):
    _, _, _, filing = golden
    assert [c for c in run_checks(filing.facts) if c.status == "fail"] == []


# ---- real annual report (kept out of git; skipped when absent) --------------------------------
GOLDEN_PDF = Path(__file__).parents[1] / "tcs-integrated-annual-report-2024-2025-2pager.pdf"


def test_golden_pdf_verifies_tcs_facts():
    if not GOLDEN_PDF.exists():
        pytest.skip(f"golden PDF not present: {GOLDEN_PDF}")
    from filingLens.ingest.calibrate import PageText, calibrate
    from filingLens.ingest.pdf import parse_pdf

    xml = GOLDEN / "data" / "TCS_Annual_INDAS_104549_1090527_12042024090154.xml"
    p = parse_xbrl_file(xml)
    facts = facts_from_raw(p.facts, xbrl_meta(p.facts), "TCS", "x", REG).facts
    pdf = parse_pdf(GOLDEN_PDF, "ar", "TCS", "Tata Consultancy Services")
    assert pdf.fy == "FY25" and len(pdf.pages) == 169
    groups: dict = {}
    for c in pdf.chunks:
        groups.setdefault((c.page_start, c.scope, c.section), []).append(c.text)
    pages = [PageText("ar", "TCS", pdf.fy, pg, sc, se, "\n".join(t))
             for (pg, sc, se), t in groups.items()]
    citations, reports = calibrate(facts, pages)
    assert reports["TCS"].rate >= 0.6 and reports["TCS"].unit_ok      # Phase 4 exit criterion
    pages_by_metric = {c.key[1]: c.page for c in citations}
    assert pages_by_metric["revenue_from_operations"] == 91             # consolidated P&L, p. 179
