"""HTTP API + typed views, on a small hand-made database (no LLM, no embedding model)."""
import pytest
from conftest import make_fact

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from filingLens.api.app import create_app
from filingLens.domain.registry import load_registry
from filingLens.storage.db import connect
from filingLens.storage.repos import FactRepo

REV = {"FY23": 225458.0, "FY24": 240893.0}
NP = {"FY23": 42303.0, "FY24": 46099.0}


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "f.db"
    con = connect(db)
    FactRepo(con).upsert_facts(
        [make_fact(fy=fy, value=v) for fy, v in REV.items()]
        + [make_fact(metric="net_profit", fy=fy, value=v) for fy, v in NP.items()]
        + [make_fact(ticker="INFY", fy="FY24", value=153670.0)])
    con.close()
    with TestClient(create_app(db, llm=None, registry=load_registry())) as c:
        yield c


def test_trend_gives_table_chart_and_stats(client):
    v = client.get("/trend", params={"ticker": "tcs", "metric": "revenue"}).json()
    cols = {c["key"]: c for c in v["table"]["columns"]}
    assert cols["value"]["type"] == "number" and cols["value"]["unit"] == "INR crore"
    assert cols["yoy"] == {"key": "yoy", "label": "YoY", "type": "change", "unit": "%"}
    assert v["table"]["rows"][1]["yoy"] == pytest.approx(6.846, abs=1e-3)     # in %, not 0.068
    assert v["table"]["display"][1] == {"period": "FY24", "value": "2,40,893", "yoy": "+6.85%"}
    assert v["chart"]["type"] == "bar" and v["chart"]["y_label"] == "Revenue from operations"
    (s,) = v["chart"]["series"]
    assert s["ticker"] == "TCS" and [p["value"] for p in s["points"]] == list(REV.values())
    assert [x["kind"] for x in v["stats"]] == ["change"]            # one year apart: no CAGR
    ratio = client.get("/trend", params={"ticker": "TCS", "metric": "net_margin"}).json()
    assert ratio["chart"]["type"] == "line" and ratio["stats"][0]["unit"] == "pp"
    assert ratio["table"]["columns"][1]["type"] == "percent"


def test_compare_series_per_company_and_gaps_are_null(client):
    v = client.get("/compare", params={"tickers": ["TCS", "INFY"],
                                       "metrics": ["revenue_from_operations"]}).json()
    assert [s["key"] for s in v["chart"]["series"]] == ["TCS", "INFY"]
    assert v["chart"]["type"] == "grouped_bar"
    infy = v["chart"]["series"][1]["points"]
    assert infy == [{"period": "FY23", "value": None}, {"period": "FY24", "value": 153670.0}]
    assert v["table"]["display"][0]["INFY"] == "-"
    assert {s["kind"] for s in v["sources"]} == {"xbrl"} and "INFY FY23 missing" in v["notes"]


def test_value_ask_and_errors(client):
    v = client.get("/value", params={"ticker": "TCS", "metric": "revenue_from_operations",
                                     "fy": "FY24"}).json()
    assert v["stats"][0]["value"] == 240893.0 and v["stats"][0]["display"] == "2,40,893"
    assert v["chart"] is None
    a = client.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False}).json()
    assert a["ok"] and a["mode"] == "rules" and a["data"]["stats"][0]["value"] == 240893.0
    assert a["answer"] is None and "use_llm=false" in a["notes"][0]
    r = client.get("/trend", params={"ticker": "XYZ", "metric": "revenue"})
    assert r.status_code == 422 and "unknown company" in r.json()["detail"]
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_catalog_endpoints_and_schema(client):
    cos = {c["ticker"]: c for c in client.get("/companies").json()}
    assert cos["TCS"]["fys"] == ["FY23", "FY24"] and cos["TCS"]["latest_fy"] == "FY24"
    assert cos["HINDUNILVR"]["has_data"] is False
    mets = {m["id"]: m for m in client.get("/metrics").json()}
    assert mets["net_margin"]["kind"] == "ratio" and mets["eps_basic"]["unit"] == "INR per share"
    h = client.get("/health").json()
    assert h["facts"] == 5 and h["companies_with_data"] == 2 and h["llm"] is None
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert {"AnswerView", "DataView", "Table", "Chart", "Series", "Stat", "Source"} <= set(schemas)
