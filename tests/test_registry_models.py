from collections import defaultdict

import pytest
from pydantic import ValidationError

from filingLens.domain.models import Fact, Intent, PeriodSpec, QueryPlan
from filingLens.domain.registry import load_registry

REG = load_registry()


def test_three_companies_fourteen_metrics():
    assert set(REG.companies) == {"TCS", "INFY", "HINDUNILVR"}
    assert len(REG.metrics) == 14


def test_every_metric_complete():
    for m in REG.metrics.values():
        assert m.tags, m.metric_id
        assert m.synonyms, m.metric_id
        assert m.kind in {"flow", "stock"}
        assert m.unit_kind in {"money", "per_share"}
        assert all(t == t.lower() for t in m.tags)


def test_no_synonym_maps_to_two_metrics():
    seen = defaultdict(set)
    for m in REG.metrics.values():
        for s in m.synonyms:
            seen[s].add(m.metric_id)
    assert {s: v for s, v in seen.items() if len(v) > 1} == {}


def test_no_alias_collisions():
    seen = defaultdict(set)
    for c in REG.companies.values():
        for a in c.aliases:
            seen[a].add(c.ticker)
    assert {a: v for a, v in seen.items() if len(v) > 1} == {}


def test_result_tags_cover_all_metrics():
    assert "revenuefromoperations" in REG.result_tags()


def test_queryplan_defaults_and_validation():
    p = QueryPlan(intent=Intent.LOOKUP, tickers=["TCS"], metrics=["net_profit"])
    assert p.scope == "consolidated" and p.period.kind == "all"
    with pytest.raises(ValidationError):
        QueryPlan(intent="nonsense")
    with pytest.raises(ValidationError):
        PeriodSpec(kind="whenever")


def test_queryplan_json_roundtrip():
    p = QueryPlan(intent=Intent.TREND, tickers=["INFY"], period=PeriodSpec(kind="last_n", n=5))
    assert QueryPlan.model_validate_json(p.model_dump_json()) == p


def test_fact_defaults():
    f = Fact(ticker="TCS", metric_id="net_profit", period_type="FY", fy="FY24",
             period_end="2024-03-31", orig_value=1.0)
    assert f.qa == "ok" and f.verified == "no"
