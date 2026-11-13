import pytest

from copilot.domain.models import Fact
from copilot.storage.db import connect
from copilot.storage.repos import FactRepo


def make_fact(ticker="TCS", metric="revenue_from_operations", fy="FY24", value=100.0,
              ptype="FY", scope="consolidated", start=None, end=None, quarter=None, **kw):
    y = 2000 + int(fy[2:])
    return Fact(ticker=ticker, metric_id=metric, period_type=ptype, fy=fy, quarter=quarter,
                period_start=start if start is not None else f"{y-1}-04-01",
                period_end=end or f"{y}-03-31", scope=scope, value=value, unit="INR crore",
                orig_value=value, **kw)


@pytest.fixture
def con():
    c = connect(":memory:")
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
