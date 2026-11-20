"""Admin data page: every NSE-listed company with local holdings, the per-company NSE check,
and downloads into the bucket (fake NSE session and fake S3 store: no network)."""
import time
from pathlib import Path

import pytest
from conftest import make_fact

pytest.importorskip("fastapi_users")
from fastapi.testclient import TestClient

from filingLens.api import admin as admin_mod
from filingLens.api.app import create_app
from filingLens.storage.db import connect
from filingLens.storage.repos import FactRepo

PW = "correct horse 42"
CSV = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1
INFY,Infosys Limited,EQ,08-FEB-1995,5,1,INE009A01021,5
ZYDUSWELL,Zydus Wellness Limited,EQ,05-JUN-2009,10,1,INE768C01028,10
"""


XBRL = (Path(__file__).parent / "fixtures" / "xbrl" / "nse_consolidated_annual.xml").read_bytes()


class FakeResponse:
    def __init__(self, payload, content=b"", status=200, ctype="application/json"):
        self.payload, self.content, self.status_code = payload, content, status
        self.headers = {"content-type": ctype}

    def json(self):
        return self.payload


class FakeNSE:
    """Answers the three listing endpoints the check calls."""

    def __init__(self):
        self.headers, self.calls, self.empty = {}, [], set()     # empty: symbols with no filings

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith(".xml"):                     # a filing: the TCS fixture's bytes
            return FakeResponse(None, XBRL, ctype="application/xml")
        if url.endswith(".pdf"):
            return FakeResponse(None, status=404)
        if (params or {}).get("symbol") in self.empty:
            return FakeResponse({"data": []})
        if "integrated-filing-results" in url:
            return FakeResponse({"totalCount": 2, "data": [
                {"type": "Integrated Filing- Financials", "qe_Date": "31-Mar-2026",
                 "xbrl": "https://nsearchives.example/INTEGRATED_FY26Q4.xml",
                 "consolidated": "Consolidated"},
                {"type": "Integrated Filing- Financials", "qe_Date": "30-Sep-2026",
                 "xbrl": "https://nsearchives.example/INTEGRATED_FY27Q2.xml",
                 "consolidated": "Consolidated"}]})
        if "corporates-financial-results" in url:
            return FakeResponse([{"xbrl": "https://nsearchives.example/ANNUAL_FY25.xml",
                                  "toDate": "31-Mar-2025", "consolidated": "Consolidated"}])
        if "annual-reports" in url:
            return FakeResponse({"data": [{"toYr": "2025",
                                           "fileName": "https://nsearchives.example/AR_FY25.pdf"}]})
        return FakeResponse({})                       # warm-up pages


class FakeStore:
    """In-memory stand-in for ObjectStore."""

    def __init__(self):
        self.objects = {}

    def put_file(self, path, key):
        self.objects[key] = Path(path).read_bytes()
        return f"s3://test/{key}"

    def exists(self, key):
        return key in self.objects

    def list(self, prefix):
        from filingLens.storage.objects import StoredFile
        return [StoredFile(k, len(v), "2026-11-14T00:00:00") for k, v in sorted(self.objects.items())
                if k.startswith(prefix)]

    def download_url(self, key, seconds=900):
        return f"http://s3.test/{key}?sig"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FILINGLENS_COMPANIES", str(tmp_path / "companies.yaml"))
    con = connect()
    FactRepo(con).upsert_facts([make_fact(fy="FY24"), make_fact(fy="FY25")])
    con.execute("INSERT INTO document(doc_id, ticker, doc_type, fy, original_name) VALUES "
                "('d1','TCS','xbrl_result','FY24','TCS_a.xml'), "
                "('d2','TCS','xbrl_result','FY25','TCS_b.xml'), "
                "('d3','TCS','annual_report','FY25','TCS_annual_FY25.pdf')")
    con.commit()
    con.close()
    (tmp_path / "inbox" / "INFY" / "xbrl").mkdir(parents=True)
    (tmp_path / "inbox" / "INFY" / "xbrl" / "INFY_Annual_x.xml").write_text("<x/>")
    (tmp_path / "inbox" / "TCS" / "xbrl").mkdir(parents=True)
    (tmp_path / "inbox" / "TCS" / "xbrl" / "TCS_a.xml").write_text("<x/>")
    (tmp_path / "list.csv").write_text(CSV)
    fake = FakeNSE()
    real = admin_mod.add_admin_routes

    def with_fake(*a, **kw):                          # create_app builds the routes; give it
        kw["nse_client"] = lambda: admin_mod._CountingClient(session=fake, sleep=lambda _: None,
                                                             log=lambda _: None)
        return real(*a, **kw)
    monkeypatch.setattr("filingLens.api.app.add_admin_routes", with_fake)
    store = FakeStore()
    app = create_app(None, data_dir=tmp_path, store=store, llm=None, registry=None,
                     equity_list=tmp_path / "list.csv", web_dir=None)
    app.state.fake_store = store
    return app, tmp_path, fake


def sign_in(c, email):
    c.post("/auth/register", json={"email": email, "password": PW})
    assert c.post("/auth/login", data={"username": email, "password": PW}).status_code == 204


def make_admin(tmp_path, email):
    import asyncio

    from filingLens.api.auth import make_auth

    auth = make_auth()

    async def run():
        await auth.create_admin(email, None)
        await auth.close()
    asyncio.run(run())


def test_admin_routes_need_the_admin_role(setup):
    app, _, _ = setup
    with TestClient(app) as c:
        assert c.get("/admin/companies").status_code == 401
        sign_in(c, "ana@example.com")
        assert c.get("/admin/companies").status_code == 403
        assert c.get("/admin/companies/TCS/nse").status_code == 403


def test_lists_every_nse_company_with_local_holdings(setup):
    app, tmp_path, _ = setup
    with TestClient(app) as c:
        sign_in(c, "boss@example.com")
        make_admin(tmp_path, "boss@example.com")
        v = c.get("/admin/companies").json()
    assert v["total"] == 3 and v["list_updated"]
    rows = {r["symbol"]: r for r in v["rows"]}
    tcs = rows["TCS"]
    assert tcs["tracked"] and tcs["xbrl_files"] == 2 and tcs["fys"] == ["FY24", "FY25"]
    assert tcs["annual_reports"] == ["FY25"] and tcs["listed_on"] == "2004-08-25"
    assert rows["INFY"]["xbrl_files"] == 0 and rows["INFY"]["pending_xbrl"] == 1
    assert tcs["pending_xbrl"] == 0                   # its inbox file is already ingested
    z = rows["ZYDUSWELL"]
    assert not z["tracked"] and z["fys"] == [] and z["latest_quarter"] is None


def test_nse_check_for_one_company(setup):
    app, tmp_path, fake = setup
    with TestClient(app) as c:
        sign_in(c, "boss@example.com")
        make_admin(tmp_path, "boss@example.com")
        a = c.get("/admin/companies/zydusWELL/nse").json()
        assert a["symbol"] == "ZYDUSWELL" and a["xbrl_filings"] == 3
        assert a["full_years"] == ["FY25", "FY26"] and a["quarters"] == ["FY26 Q4", "FY27 Q2"]
        assert a["annual_reports"] == ["FY25"] and a["new_files"] == 4 and a["warning"] is None
        n = len(fake.calls)
        assert c.get("/admin/companies/ZYDUSWELL/nse").json() == a        # cached
        assert len(fake.calls) == n
        assert c.get("/admin/companies/NOPE/nse").status_code == 404


def wait_for(c, symbol, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/admin/companies/{symbol}/download").json()
        if job["stage"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"download of {symbol} did not finish: {job}")


def test_download_saves_to_the_bucket_and_ingests(setup):
    app, tmp_path, _ = setup
    with TestClient(app) as c:
        sign_in(c, "boss@example.com")
        make_admin(tmp_path, "boss@example.com")
        assert c.get("/admin/companies/INFY/download").status_code == 404
        r = c.post("/admin/companies/infy/download")
        assert r.status_code == 202 and r.json()["symbol"] == "INFY"
        job = wait_for(c, "INFY")
        assert job["stage"] == "done", job
        # 3 XBRL filings saved to the bucket; the annual report was refused by NSE (404)
        assert (job["total"], job["downloaded"], job["stored"], job["failed_files"]) == (4, 3, 3, 1)
        assert job["facts"] > 0 and job["years"]       # ingested: answerable at once
        keys = sorted(app.state.fake_store.objects)
        assert all(k.startswith("filings/INFY/xbrl/INFY_") for k in keys) and len(keys) == 3
        files = c.get("/admin/companies/INFY/files").json()
        assert [f["kind"] for f in files] == ["xbrl"] * 3 and files[0]["url"].startswith("http")
        row = next(x for x in c.get("/admin/companies").json()["rows"] if x["symbol"] == "INFY")
        assert row["stored"] == 3 and row["tracked"]
        assert [j["symbol"] for j in c.get("/admin/jobs").json()] == ["INFY"]
        r = c.post("/admin/companies/INFY/download")            # again: nothing new to fetch
        assert wait_for(c, "INFY")["stored"] == 0


def test_download_adds_an_untracked_company(setup):
    app, tmp_path, fake = setup
    fake.empty.add("ZYDUSWELL")
    with TestClient(app) as c:
        sign_in(c, "boss@example.com")
        make_admin(tmp_path, "boss@example.com")
        assert not next(x for x in c.get("/admin/companies").json()["rows"]
                        if x["symbol"] == "ZYDUSWELL")["tracked"]
        c.post("/admin/companies/ZYDUSWELL/download")
        job = wait_for(c, "ZYDUSWELL")
        assert job["stage"] == "done" and job["added_company"] and job["total"] == 0
        assert "ZYDUSWELL" in (tmp_path / "companies.yaml").read_text()
        assert next(x for x in c.get("/admin/companies").json()["rows"]
                    if x["symbol"] == "ZYDUSWELL")["tracked"]
        assert "ZYDUSWELL" in {x["ticker"] for x in c.get("/companies").json()}   # no restart


def test_download_needs_admin_and_a_listed_symbol(setup):
    app, tmp_path, _ = setup
    with TestClient(app) as c:
        sign_in(c, "ana@example.com")
        assert c.post("/admin/companies/TCS/download").status_code == 403
        make_admin(tmp_path, "ana@example.com")
        assert c.post("/admin/companies/NOPE/download").status_code == 404


# ---- embeddings ------------------------------------------------------------------------------
@pytest.fixture
def with_reports(setup, monkeypatch):
    """Two annual reports with passages (TCS FY24, FY25); embeddings with the hash model."""
    from filingLens.domain.models import Chunk
    from filingLens.retrieval.embedder import HashEmbedder
    from filingLens.storage.repos import TextRepo

    app, tmp_path, _ = setup
    con = connect()
    for fy, n in (("FY24", 3), ("FY25", 2)):
        doc = f"ar-{fy}"
        con.execute("INSERT INTO document(doc_id, ticker, doc_type, fy, original_name) VALUES "
                    "(?, 'TCS', 'annual_report', ?, ?)", (doc, fy, f"TCS_annual_{fy}.pdf"))
        TextRepo(con).replace(doc, [], [Chunk(f"{doc}-{i}", doc, "TCS", fy, "annual_report",
                                              "none", "text", "mdna", i, i, f"text {fy} {i}",
                                              f"TCS | Annual Report {fy} | page {i}")
                                        for i in range(n)])
    con.close()
    app.state.embeddings.factory = lambda model, device: HashEmbedder()
    return app, tmp_path


def wait_embed(c, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get("/admin/embeddings").json()
        if job and job["state"] not in ("loading", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"embedding did not finish: {job}")


def test_lists_reports_with_their_embedding_status(with_reports):
    app, tmp_path = with_reports
    with TestClient(app) as c:
        sign_in(c, "boss@example.com")
        make_admin(tmp_path, "boss@example.com")
        v = c.get("/admin/companies").json()
        assert v["index"] == {"model": "BAAI/bge-m3", "passages": 5, "embedded": 0,
                              "reports_pending": 2, "companies_pending": 1}
        tcs = next(r for r in v["rows"] if r["symbol"] == "TCS")
        assert [(r["fy"], r["passages"], r["embedded"]) for r in tcs["reports"]] == [
            ("FY24", 3, 0), ("FY25", 2, 0)]


def test_embedding_job_runs_report_by_report(with_reports):
    app, tmp_path = with_reports
    with TestClient(app) as c:
        sign_in(c, "boss@example.com")
        make_admin(tmp_path, "boss@example.com")
        assert c.get("/admin/embeddings").json() is None
        r = c.post("/admin/embeddings", json={"tickers": ["tcs"], "device": "cpu"})
        assert r.status_code == 202 and [i["fy"] for i in r.json()["items"]] == ["FY24", "FY25"]
        job = wait_embed(c)
        assert job["state"] == "done" and job["embedded"] == 5 and job["model"] == "hash-512"
        assert [(i["state"], i["done"], i["passages"]) for i in job["items"]] == [
            ("done", 3, 3), ("done", 2, 2)]
        v = c.get("/admin/companies").json()
        assert v["index"]["embedded"] == 5 and v["index"]["reports_pending"] == 0
        assert c.post("/admin/embeddings", json={}).status_code == 400     # nothing left
        assert c.delete("/admin/embeddings").status_code == 409            # nothing running
    assert (tmp_path / "vectors.npy").exists()


def test_embedding_needs_admin(with_reports):
    app, _ = with_reports
    with TestClient(app) as c:
        sign_in(c, "ana@example.com")
        assert c.post("/admin/embeddings", json={}).status_code == 403
        assert c.get("/admin/embeddings").status_code == 403
