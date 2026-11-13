"""End-to-end XBRL ingestion into an in-memory DB, the audit report and the CLI."""
import shutil
from pathlib import Path

import pytest

from filingLens.cli import main
from filingLens.domain.registry import load_registry
from filingLens.ingest.audit import (
    metric_coverage,
    missing_metrics,
    period_type_counts,
    unmapped_tags,
)
from filingLens.ingest.pipeline import ingest_xbrl
from filingLens.ingest.utils.files import sha256_file
from filingLens.ingest.xbrl import facts_from_raw, parse_xbrl_file, xbrl_meta
from filingLens.storage.repos import DocumentRepo, FactRepo, RawFactRepo

FX = Path(__file__).parent / "fixtures" / "xbrl"
REG = load_registry()
ANNUAL = FX / "nse_consolidated_annual.xml"


def counts(con):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("document", "xbrl_fact_raw", "fact", "validation")}


def test_ingest_annual_creates_fy_facts(con):
    rep = ingest_xbrl(con, [ANNUAL], REG)
    assert rep.ok and rep.tickers == ["TCS"]
    fr = rep.files[0]
    assert fr.doc_type == "xbrl_result" and fr.scope == "consolidated" and fr.fy == "FY24"
    assert fr.doc_id == sha256_file(ANNUAL) and fr.n_raw > 0 and fr.n_facts > 0
    s = FactRepo(con).get_series("TCS", "revenue_from_operations")
    assert [(f.fy, f.value) for f in s] == [("FY24", pytest.approx(1000.0))]
    assert s[0].doc_id == fr.doc_id
    assert con.execute("SELECT isin FROM fact LIMIT 1").fetchone()[0] == "INE467B01029"


def test_ingest_is_idempotent(con):
    ingest_xbrl(con, [ANNUAL, FX / "standalone_quarterly.xml"], REG)
    first = counts(con)
    ingest_xbrl(con, [ANNUAL, FX / "standalone_quarterly.xml"], REG)
    assert counts(con) == first
    assert first["document"] == 2


def test_document_and_raw_rows(con):
    rep = ingest_xbrl(con, [ANNUAL], REG)
    doc = DocumentRepo(con).get(rep.files[0].doc_id)
    assert doc["ticker"] == "TCS" and doc["original_name"] == ANNUAL.name
    assert '"rounding": "Crores"' in doc["meta_json"]
    raw = RawFactRepo(con).load(doc["doc_id"])
    assert raw == parse_xbrl_file(ANNUAL).facts            # round-trips through SQLite


def test_validation_rows_and_qa_written(con):
    ingest_xbrl(con, [ANNUAL], REG)
    rows = FactRepo(con).validation.list(ticker="TCS")
    checks = {(r["check_name"], r["status"]) for r in rows}
    assert ("income_sum", "pass") in checks and ("pat_bridge", "pass") in checks
    assert {f.qa for f in FactRepo(con).list_facts(["TCS"])} == {"ok"}


def test_restatement_across_filings(con, tmp_path):
    later = tmp_path / "TCS_later.xml"
    text = ANNUAL.read_text().replace(
        '<in-bse-fin:RevenueFromOperations contextRef="FourD" unitRef="INR" decimals="-7">'
        "10000000000.00",
        '<in-bse-fin:RevenueFromOperations contextRef="FourD" unitRef="INR" decimals="-7">'
        "10100000000.00").replace("2024-04-12", "2024-05-30")   # later board meeting
    later.write_text(text)
    ingest_xbrl(con, [ANNUAL], REG)
    rep = ingest_xbrl(con, [later], REG)                  # arrives in a separate run
    assert rep.n_restatements == 1
    s = FactRepo(con).get_series("TCS", "revenue_from_operations")
    assert len(s) == 1 and s[0].value == pytest.approx(1010.0)
    log = FactRepo(con).validation.list("restatement")
    assert len(log) == 1 and "1e+10 -> 1.01e+10" in log[0]["detail"]
    ingest_xbrl(con, [ANNUAL, later], REG)                # re-run: still one log row
    assert len(FactRepo(con).validation.list("restatement")) == 1


def test_bad_file_reported_not_raised(con, tmp_path):
    bad = tmp_path / "TCS_bad.xml"
    bad.write_text("<html>blocked</html>")
    rep = ingest_xbrl(con, [bad, ANNUAL], REG)
    assert not rep.ok and "no XBRL contexts" in rep.files[0].error
    assert rep.files[1].error is None and rep.n_facts > 0


def test_unknown_company_reported(con, tmp_path):
    f = tmp_path / "x.xml"
    f.write_text(ANNUAL.read_text().replace(">TCS<", ">ACME<")
                 .replace("Tata Consultancy Services Limited", "Acme Ltd"))
    rep = ingest_xbrl(con, [f], REG)
    assert "unknown company" in rep.files[0].error


def test_ticker_override(con):
    rep = ingest_xbrl(con, [FX / "dimensional.xml"], REG, ticker="HINDUNILVR")
    assert rep.files[0].ticker == "HINDUNILVR"


def test_non_result_file_is_xbrl_other(con, tmp_path):
    f = tmp_path / "TCS_shp.xml"
    f.write_text("""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:s="urn:s">
      <xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="x">TCS</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:instant>2024-03-31</xbrli:instant></xbrli:period>
      </xbrli:context><s:NumberOfShareholders contextRef="c">5</s:NumberOfShareholders>
      </xbrli:xbrl>""")
    rep = ingest_xbrl(con, [f], REG)
    assert rep.files[0].doc_type == "xbrl_other"
    assert any("xbrl_other" in w for w in rep.files[0].warnings)


# ---- audit -------------------------------------------------------------------------------------
def test_audit_reports():
    p = parse_xbrl_file(ANNUAL)
    assert period_type_counts(p.facts) == {"FY": 12, "INSTANT": 2, "Q": 10}
    filing = facts_from_raw(p.facts, xbrl_meta(p.facts), "TCS", "d", REG)
    miss = missing_metrics(filing, REG)
    assert set(miss) == {"finance_costs", "depreciation_amortisation", "eps_diluted"}
    cov = {c["metric_id"]: c for c in metric_coverage(filing, REG)}
    assert cov["revenue_from_operations"]["found"] == "FY,Q"
    assert cov["finance_costs"]["found"] == "MISSING"
    um = unmapped_tags(p.facts, REG)
    tags = [u["tag"] for u in um]
    assert "OtherExpenses" in tags                     # non-dimensional FourD value
    assert "RevenueFromOperations" not in tags
    assert um == sorted(um, key=lambda u: -u["max_abs"])


# ---- CLI ---------------------------------------------------------------------------------------
def test_cli_inspect(capsys):
    assert main(["inspect", str(ANNUAL)]) == 0
    out = capsys.readouterr().out
    assert "replaced by reporting-period facts 2023-04-01..2024-03-31" in out
    assert "revenue_from_operations" in out and "1,000" in out


def test_cli_inspect_json(capsys):
    import json
    assert main(["inspect", str(ANNUAL), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ticker"] == "TCS" and d["period_type_counts"]["FY"] == 12


def test_cli_inspect_bad_file(tmp_path, capsys):
    f = tmp_path / "bad.xml"
    f.write_text("")
    assert main(["inspect", str(f)]) == 2
    assert "empty" in capsys.readouterr().err


def test_cli_ingest_folder(tmp_path, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(ANNUAL, inbox / "TCS_Annual_1.xml")
    db = tmp_path / "w" / "t.db"
    assert main(["ingest-xbrl", str(inbox), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "FY coverage" in out and "revenue_from_operations" in out and db.exists()
