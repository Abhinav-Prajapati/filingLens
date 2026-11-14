"""Integrated filings, any listed company (equity list + overlay), and data availability."""
import datetime as dt

from conftest import make_fact

from filingLens.acquire import INTEGRATED_API, NSEClient, acquire, parse_integrated
from filingLens.catalog import local_status, mark_have, summarise_remote
from filingLens.companies import add_companies, aliases_for, load_equity_list, search
from filingLens.domain.registry import load_registry
from filingLens.storage.repos import DocumentRepo, FactRepo

INTEGRATED = [
    {"type": "Integrated Filing- Financials", "consolidated": "Consolidated",
     "qe_Date": "31-MAR-2026", "xbrl": "https://x/INTEGRATED_FILING_INDAS_1.xml"},
    {"type": "Integrated Filing- Financials", "consolidated": "Standalone",
     "qe_Date": "30-SEP-2026", "xbrl": "https://x/INTEGRATED_FILING_INDAS_2.xml"},
    {"type": "Integrated Filing- Governance", "consolidated": None,
     "qe_Date": "30-SEP-2026", "xbrl": "https://x/INTEGRATED_FILING_GOVE_3.xml"},
]


class Resp:
    def __init__(self, payload):
        self.status_code, self._p, self.headers, self.content = 200, payload, {}, b"x"

    def json(self):
        return self._p


class PagedSession:
    """Integrated API split over 2 pages (size 2); the other endpoints return nothing."""

    def __init__(self):
        self.headers, self.pages = {}, []

    def get(self, url, timeout=None, params=None):
        if url == INTEGRATED_API:
            page, size = params["page"], params["size"]
            self.pages.append(page)
            rows = INTEGRATED[(page - 1) * size: page * size]
            return Resp({"data": rows, "totalCount": len(INTEGRATED)})
        if "annual-reports" in url:
            return Resp([{"toYr": "2026", "fileName": "https://x/AR_2026.pdf"},
                         {"toYr": "2012", "fileName": "https://x/AR_2012.pdf"}])
        return Resp({"data": []})


def test_parse_integrated_keeps_financials_with_quarter_and_scope():
    fs = parse_integrated(INTEGRATED, "TCS")
    assert [(f.label, f.scope, f.period) for f in fs] == [
        ("FY26 Q4", "consolidated", "Integrated"), ("FY27 Q2", "standalone", "Integrated")]
    assert fs[0].filename == "TCS_Integrated_INTEGRATED_FILING_INDAS_1.xml"


def test_integrated_paging_and_annual_report_window(tmp_path):
    from filingLens.acquire import _integrated_pages
    s = PagedSession()
    c = NSEClient(delay=0, session=s, sleep=lambda x: None, log=lambda m: None)
    assert len(_integrated_pages(c, "TCS", {}, size=2)) == 2 and s.pages == [1, 2]  # stops at total
    rep = acquire(c, tmp_path, ["TCS"], sources=["integrated"], annual_reports=True,
                  dry_run=True, to_date=dt.date(2026, 11, 1))
    assert [f.label for f in rep.found] == ["FY26 Q4", "FY27 Q2", "FY26"]   # FY12 report: too old


def test_equity_list_search_and_overlay(tmp_path):
    csv = tmp_path / "eq.csv"
    csv.write_text("SYMBOL,NAME OF COMPANY, SERIES, ISIN NUMBER\n"
                   "WIPRO,Wipro Limited,EQ,INE075A01022\nTCS,Tata Consultancy Services Limited,EQ,"
                   "INE467B01029\nWIPROX,Wiproxy Ltd,EQ,INE000000001\n")
    listed = load_equity_list(csv)
    assert [c.symbol for c in search(listed, "wipro")] == ["WIPRO", "WIPROX"]
    assert aliases_for(listed[0]) == ["wipro", "wipro limited"]
    overlay = tmp_path / "companies.yaml"
    assert add_companies(listed, overlay) == ["WIPRO", "WIPROX"]   # TCS already built in
    assert add_companies(listed, overlay) == []                    # idempotent
    reg = load_registry(overlay=overlay)
    assert reg.companies["WIPRO"].isin == "INE075A01022" and "TCS" in reg.companies
    assert "wipro" not in reg.companies["WIPROX"].aliases          # no alias collisions
    assert set(load_registry().companies) == {"TCS", "INFY", "HINDUNILVR"}   # default unchanged


def test_local_status_and_remote_summary(con, tmp_path):
    FactRepo(con).upsert_facts([make_fact(fy=fy, value=v) for fy, v in
                                [("FY22", 1.0), ("FY24", 2.0)]])
    DocumentRepo(con).upsert({"doc_id": "d1", "ticker": "TCS", "doc_type": "annual_report",
                              "fy": "FY25", "original_name": "my_name.pdf"})
    row = next(r for r in local_status(con, load_registry()) if r["ticker"] == "TCS")
    assert row["fy_facts"] == "FY22-FY24 (2) missing FY23" and row["annual_reports"] == "FY25"
    assert row["metrics"] == "1/14"
    fs = parse_integrated(INTEGRATED, "TCS")
    from filingLens.acquire import Filing
    fs.append(Filing("TCS", "annual_report", "https://x/AR.pdf", "", "FY25"))
    (tmp_path / "TCS" / "xbrl").mkdir(parents=True)
    fs[0].dest(tmp_path).write_text("x")
    status = mark_have(fs, tmp_path, con)
    assert status == ["have", "new", "have"]      # in inbox / new / report ingested under other name
    s = summarise_remote(fs, status)[0]
    assert (s["full_years"], s["files"], s["new"]) == ("FY26-FY26 (1)", 3, 1)
    assert s["quarters"] == "FY26 Q4 .. FY27 Q2 (2)" and s["annual_reports"] == "FY25"
