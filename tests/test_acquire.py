"""NSE downloader with a fake HTTP session: no network."""
import datetime as dt
import json
from pathlib import Path

from filingLens.acquire import (
    RESULTS_API,
    Filing,
    NSEClient,
    acquire,
    parse_annual_reports,
    parse_results,
    select,
)

ROWS = [
    {"xbrl": "https://nsearchives.nseindia.com/a/INDAS_1.xml", "toDate": "31-Mar-2024",
     "consolidated": "Consolidated"},
    {"xbrl": "https://nsearchives.nseindia.com/a/INDAS_2.xml", "toDate": "31-Mar-2024",
     "consolidated": "Non-Consolidated"},
    {"xbrl": "https://nsearchives.nseindia.com/a/INDAS_3.xml", "toDate": "31-Mar-2025",
     "consolidated": "Consolidated"},
    {"xbrl": "-", "toDate": "31-Mar-2023"},                      # no XBRL attached
]


class Resp:
    def __init__(self, status=200, payload=None, content=b"", ctype="application/json"):
        self.status_code, self._payload = status, payload
        self.content, self.headers = content, {"content-type": ctype}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Routes: results API -> ROWS; .xml -> XBRL bytes; everything else -> 200 HTML."""

    def __init__(self, status=200, file_ctype="application/xml"):
        self.headers, self.calls, self.status, self.file_ctype = {}, [], status, file_ctype

    def get(self, url, timeout=None, params=None):
        self.calls.append(url)
        if url == RESULTS_API:
            return Resp(self.status, {"data": ROWS})
        if url.endswith(".xml"):
            return Resp(self.status, content=b"<xbrl/>", ctype=self.file_ctype)
        return Resp(200, content=b"<html>", ctype="text/html")


def client(session):
    return NSEClient(delay=0, session=session, sleep=lambda s: None, log=lambda m: None)


def test_parse_rows_fy_scope_and_names():
    fs = parse_results({"data": ROWS}, "TCS", "Annual")
    assert [(f.fy, f.scope) for f in fs] == [("FY24", "consolidated"), ("FY24", "standalone"),
                                             ("FY25", "consolidated")]
    assert fs[0].filename == "TCS_Annual_INDAS_1.xml"
    assert fs[0].dest(Path("in")) == Path("in/TCS/xbrl/TCS_Annual_INDAS_1.xml")
    ar = parse_annual_reports([{"toYr": "2025", "fileName": "https://x/AR_2025.pdf"}], "TCS")
    assert ar[0].fy == "FY25" and ar[0].filename == "TCS_annual_FY25.pdf"
    assert parse_results(None, "TCS", "Annual") == []


def test_select_filters_and_keeps_unknowns():
    fs = parse_results(ROWS, "TCS", "Annual") + [Filing("TCS", "xbrl", "https://x/u.xml")]
    got = select(fs, fys=["FY24"], scope="consolidated")
    assert [f.url.rsplit("/", 1)[-1] for f in got] == ["INDAS_1.xml", "u.xml"]


def test_acquire_downloads_once_and_records_sources(tmp_path):
    s = FakeSession()
    rep = acquire(client(s), tmp_path, ["tcs"], fys=["FY24"], scope="consolidated",
                  to_date=dt.date(2025, 6, 1))
    dest = tmp_path / "TCS" / "xbrl" / "TCS_Annual_INDAS_1.xml"
    assert rep.downloaded == [dest] and dest.read_bytes() == b"<xbrl/>"
    assert json.loads((tmp_path / "sources.json").read_text())[dest.name].endswith("INDAS_1.xml")
    rep = acquire(client(FakeSession()), tmp_path, ["TCS"], fys=["FY24"], scope="consolidated")
    assert rep.skipped == [dest] and rep.downloaded == []          # never fetched twice


def test_dry_run_downloads_nothing(tmp_path):
    s = FakeSession()
    rep = acquire(client(s), tmp_path, ["TCS"], dry_run=True)
    assert len(rep.found) == 3 and rep.downloaded == [] and not any(".xml" in c for c in s.calls)


def test_html_instead_of_file_is_a_failure(tmp_path):
    rep = acquire(client(FakeSession(file_ctype="text/html")), tmp_path, ["TCS"], fys=["FY25"])
    assert rep.failed and not list(tmp_path.rglob("*.xml"))


def test_stops_when_nse_refuses(tmp_path):
    s = FakeSession(status=403)
    rep = acquire(client(s), tmp_path, ["TCS", "INFY"])
    assert rep.blocked and "download by hand" in rep.blocked
    assert len([c for c in s.calls if c == RESULTS_API]) == 3      # gave up, no hammering
