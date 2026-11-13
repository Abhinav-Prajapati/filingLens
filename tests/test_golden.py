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
