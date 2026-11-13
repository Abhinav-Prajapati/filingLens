"""PDF ingestion + calibration through SQLite, the inbox runner and the CLI."""
import shutil
from pathlib import Path

from filingLens.cli import main
from filingLens.domain.registry import load_registry
from filingLens.ingest.pdf_pipeline import calibrate_db, ingest_inbox, ingest_pdf
from filingLens.ingest.pipeline import ingest_xbrl
from filingLens.storage.repos import FactRepo

REG = load_registry()
XBRL = Path(__file__).parent / "fixtures" / "xbrl" / "nse_consolidated_annual.xml"


def counts(con):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("document", "fact", "page", "chunk", "chunk_fts")}


def citations(con):
    return {(f.metric_id, f.period_type): (f.page, f.verified)
            for f in FactRepo(con).list_facts(["TCS"])}


def test_ingest_pdf_and_calibrate(con, report_pdf):
    ingest_xbrl(con, [XBRL], REG)
    rep = ingest_pdf(con, report_pdf, REG)
    assert rep.error is None and rep.ticker == "TCS" and rep.fy == "FY24" and rep.n_pages == 3
    reports = calibrate_db(con, ["TCS"])
    c = citations(con)
    assert c[("revenue_from_operations", "FY")] == (1, "pdf")
    assert c[("total_assets", "INSTANT")] == (1, "pdf")
    assert c[("revenue_from_operations", "Q")] == (None, "no")       # quarters are not checked
    assert reports["TCS"].unit_ok and reports["TCS"].rate >= 0.6
    assert con.execute("SELECT COUNT(*) FROM chunk_fts WHERE chunk_fts MATCH 'verticals'"
                       ).fetchone()[0] == 1


def test_reingest_is_cached_and_idempotent(con, report_pdf):
    ingest_xbrl(con, [XBRL], REG)
    ingest_pdf(con, report_pdf, REG)
    calibrate_db(con, ["TCS"])
    first, cites = counts(con), citations(con)
    assert ingest_pdf(con, report_pdf, REG).skipped
    assert not ingest_pdf(con, report_pdf, REG, force=True).skipped
    ingest_xbrl(con, [XBRL], REG)                 # rebuild clears citations ...
    calibrate_db(con, ["TCS"])                    # ... and calibration restores them
    assert counts(con) == first and citations(con) == cites
    assert first["chunk"] == first["chunk_fts"]


def test_unknown_company_and_bad_pdf(con, tmp_path):
    bad = tmp_path / "TCS_broken.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    assert "cannot read PDF" in ingest_pdf(con, bad, REG).error
    other = tmp_path / "acme.pdf"
    other.write_bytes(b"x")
    assert "unknown company" in ingest_pdf(con, other, REG).error


def test_inbox_and_cli(tmp_path, report_pdf, capsys):
    inbox = tmp_path / "inbox"
    (inbox / "TCS" / "xbrl").mkdir(parents=True)
    (inbox / "TCS" / "annual_report").mkdir()
    shutil.copy(XBRL, inbox / "TCS" / "xbrl")
    shutil.copy(report_pdf, inbox / "TCS" / "annual_report" / "annual.pdf")
    db = tmp_path / "w.db"
    assert main(["ingest", str(inbox), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "parsed" in out and "unit_check" in out and " ok" in out
    assert main(["inspect-pdf", str(report_pdf)]) == 0
    assert "financial_statements" in capsys.readouterr().out
    from filingLens.storage.db import connect
    rep = ingest_inbox(connect(db), inbox, REG)              # second run: PDF cached
    assert rep.pdfs[0].skipped and rep.calibration["TCS"].verified > 0
