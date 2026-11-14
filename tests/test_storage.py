import pytest
from conftest import make_fact

from filingLens.storage.db import SCHEMA_VERSION, connect


def test_schema_created_and_versioned(con):
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
    assert {"fact", "document", "chunk", "page", "validation", "schema_info"} <= tables
    assert con.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION


def test_init_is_idempotent():
    connect().close()
    connect().close()  # no error on second init


def test_newer_schema_rejected():
    c = connect()
    c.execute("UPDATE schema_info SET version = 99")
    c.close()
    with pytest.raises(RuntimeError):
        connect()


def test_rows_by_position_name_and_dict(con):
    r = con.execute("SELECT 1 AS a, 'x' AS b").fetchone()
    assert r[0] == 1 and r["b"] == "x" and dict(r) == {"a": 1, "b": "x"}
    assert con.execute("SELECT ? || '%' AS p", ("50",)).fetchone()["p"] == "50%"


def test_upsert_idempotent(repo, con):
    f = make_fact()
    repo.upsert_facts([f])
    repo.upsert_facts([f])
    assert con.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == 1
    assert repo.validation.list("restatement") == []


def test_restatement_overwrites_and_logs(repo):
    repo.upsert_facts([make_fact(value=100.0)])
    repo.upsert_facts([make_fact(value=110.0)])
    s = repo.get_series("TCS", "revenue_from_operations")
    assert len(s) == 1 and s[0].value == 110.0
    log = repo.validation.list("restatement")
    assert len(log) == 1 and "100 -> 110" in log[0]["detail"]


def test_tiny_float_difference_not_a_restatement(repo):
    repo.upsert_facts([make_fact(value=100.0)])
    repo.upsert_facts([make_fact(value=100.0000000001)])
    assert repo.validation.list("restatement") == []


def test_get_series_ordered_and_filtered(seeded):
    s = seeded.get_series("TCS", "revenue_from_operations")
    assert [f.fy for f in s] == ["FY22", "FY23", "FY24"]
    s = seeded.get_series("TCS", "revenue_from_operations", fy_from="FY23", fy_to="FY23")
    assert [f.fy for f in s] == ["FY23"]


def test_get_series_scope_and_period_type(seeded):
    assert seeded.get_series("TCS", "revenue_from_operations", scope="standalone")[0].value == 100.0
    q = seeded.get_series("TCS", "revenue_from_operations", period_type="Q")
    assert [f.quarter for f in q] == ["Q1", "Q2", "Q3", "Q4"]
    assert seeded.get_series("TCS", "total_assets", period_type="INSTANT")[0].value == 143000


def test_get_series_company_isolation(seeded):
    assert {f.ticker for f in seeded.get_series("INFY", "revenue_from_operations")} == {"INFY"}
    assert seeded.get_series("HINDUNILVR", "revenue_from_operations") == []


def test_get_series_skips_null_values(repo):
    f = make_fact()
    f.value = None
    repo.upsert_facts([f])
    assert repo.get_series("TCS", "revenue_from_operations") == []


def test_latest_fy_and_coverage(seeded):
    assert seeded.latest_fy("TCS") == "FY24"
    assert seeded.latest_fy("INFY") == "FY24"
    assert seeded.latest_fy("HINDUNILVR") is None
    cov = {(c["ticker"], c["metric_id"], c["period_type"], c["scope"]): c for c in seeded.coverage()}
    c = cov[("TCS", "revenue_from_operations", "FY", "consolidated")]
    assert c["n"] == 3 and c["first_fy"] == "FY22" and c["last_fy"] == "FY24"


def test_isin_stored(repo, con):
    repo.upsert_facts([make_fact()], isin_by_ticker={"TCS": "INE467B01029"})
    assert con.execute("SELECT isin FROM fact").fetchone()[0] == "INE467B01029"
