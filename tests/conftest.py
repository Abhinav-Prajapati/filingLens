import os
import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest

from filingLens.config import database_url
from filingLens.domain.models import Fact
from filingLens.storage.db import connect
from filingLens.storage.repos import FactRepo

# Tests run against Postgres (docker compose up -d postgres), in a database of their own
# (FILINGLENS_TEST_DATABASE_URL, default: the app's server with database "filinglens_test").
# Every test gets an empty schema, dropped afterwards; connect() with no URL lands in it.


def _test_url() -> str:
    url = os.environ.get("FILINGLENS_TEST_DATABASE_URL")
    if url:
        return url
    parts = urlsplit(database_url())
    return urlunsplit(parts._replace(path="/filinglens_test"))


@pytest.fixture(scope="session")
def _test_db() -> str:
    url = _test_url()
    try:
        psycopg.connect(url, connect_timeout=5).close()
    except psycopg.OperationalError as e:
        if "does not exist" not in str(e):
            pytest.exit(f"Postgres not reachable for tests ({e}). Start it: "
                        "docker compose up -d postgres", returncode=3)
        admin = urlunsplit(urlsplit(url)._replace(path="/postgres"))
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{urlsplit(url).path.lstrip("/")}"')
    return url


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Tests never read the developer's .env (LLM, embeddings server, S3...): only what they set."""
    from filingLens import config

    monkeypatch.setattr(config, "_dotenv", lambda path=".env": {})
    for k in [k for k in __import__("os").environ if k.startswith("FILINGLENS_")
              and k != "FILINGLENS_TEST_DATABASE_URL"]:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _pg_schema(_test_db, _no_dotenv, monkeypatch):
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_test_db, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
    sep = "&" if "?" in _test_db else "?"
    monkeypatch.setenv("FILINGLENS_DATABASE_URL",
                       f"{_test_db}{sep}options={quote(f'-csearch_path={schema}')}")
    yield schema
    with psycopg.connect(_test_db, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {schema} CASCADE")


def make_fact(ticker="TCS", metric="revenue_from_operations", fy="FY24", value=100.0,
              ptype="FY", scope="consolidated", start=None, end=None, quarter=None, **kw):
    y = 2000 + int(fy[2:])
    return Fact(ticker=ticker, metric_id=metric, period_type=ptype, fy=fy, quarter=quarter,
                period_start=start if start is not None else f"{y-1}-04-01",
                period_end=end or f"{y}-03-31", scope=scope, value=value, unit="INR crore",
                orig_value=value, **kw)


@pytest.fixture
def con():
    c = connect()
    yield c
    c.close()


@pytest.fixture
def repo(con):
    return FactRepo(con)


@pytest.fixture
def seeded(repo):
    """~20 known facts: 3 FYs x 2 metrics for TCS, plus INFY, standalone, and quarters."""
    facts = []
    for fy, rev, pat in [("FY22", 191754, 38327), ("FY23", 225458, 42147), ("FY24", 240893, 45908)]:
        facts.append(make_fact("TCS", "revenue_from_operations", fy, rev))
        facts.append(make_fact("TCS", "net_profit", fy, pat))
    for fy, rev in [("FY23", 146767), ("FY24", 153670)]:
        facts.append(make_fact("INFY", "revenue_from_operations", fy, rev))
    facts.append(make_fact("TCS", "revenue_from_operations", "FY24", 100.0, scope="standalone"))
    for q, (s, e) in {"Q1": ("2023-04-01", "2023-06-30"), "Q2": ("2023-07-01", "2023-09-30"),
                      "Q3": ("2023-10-01", "2023-12-31"), "Q4": ("2024-01-01", "2024-03-31")}.items():
        facts.append(make_fact("TCS", "revenue_from_operations", "FY24", 60000.0, ptype="Q",
                               start=s, end=e, quarter=q))
    facts.append(make_fact("TCS", "total_assets", "FY24", 143000, ptype="INSTANT", start=""))
    repo.upsert_facts(facts)
    return repo


# ---- generated annual-report PDF (no binary fixtures in git) ---------------------------------
# Matches tests/fixtures/xbrl/nse_consolidated_annual.xml (TCS FY24, crore): page 1 is a
# two-page spread with the consolidated statements, page 2 an MD&A page, page 3 a standalone
# statement printing the same revenue figure.
_SPREAD = {
    "L": ["Integrated Annual Report 2023-24", "Consolidated Financial Statements 2023-24", "178",
          "Consolidated Balance Sheet", "As at March 31, 2024",
          "Total assets 2,000", "Total equity 1,500", "Notes 1 to 30 form part of these statements."],
    "R": ["Integrated Annual Report 2023-24", "Consolidated Financial Statements 2023-24", "179",
          "Consolidated Statement of Profit and Loss", "Year ended March 31, 2024 (` crore)",
          "Revenue from operations 1,000", "Other income 50", "Total income 1,050",
          "Total expenses 800", "Profit before tax 250", "Tax expense 60",
          "Profit for the year 190", "Basic earnings per share 12.50"],
}
_MDNA = ["Management Discussion and Analysis", "86",
         "Revenue for the year 2023-24 grew to 1,000 crore, driven by strong demand across",
         "all industry verticals and markets. Employee costs were 600 crore."]
_STANDALONE = ["Standalone Financial Statements 2023-24", "240", "Standalone Statement of Profit",
               "Revenue from operations 1,000", "Other income 50 and other standalone items."]


def write_lines(page, x, lines):
    for i, line in enumerate(lines):
        page.insert_text((x, 40 + 14 * i), line, fontsize=9)


@pytest.fixture
def report_pdf(tmp_path):
    import pymupdf

    path = tmp_path / "TCS_annual_report_2023-24.pdf"
    doc = pymupdf.open()
    spread = doc.new_page(width=1188, height=756)
    write_lines(spread, 40, _SPREAD["L"])
    write_lines(spread, 634, _SPREAD["R"])
    write_lines(doc.new_page(width=594, height=756), 40, _MDNA)
    write_lines(doc.new_page(width=594, height=756), 40, _STANDALONE)
    doc.save(path)
    return path
