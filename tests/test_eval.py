"""The evaluation harness: case-file consistency, scoring rules, run + save + compare."""
from pathlib import Path

import pytest
from conftest import make_fact

from filingLens.domain.registry import load_registry
from filingLens.evaluation import (
    Case,
    args_match,
    load_cases,
    previous_summary,
    run,
    save,
    score,
    summarise,
    value_found,
)
from filingLens.llm.answer import Answer
from filingLens.llm.executor import Context, ToolResult
from filingLens.llm.tools import ARGS, norm_fy, norm_metric, norm_ticker
from filingLens.service import AnswerService, Response
from filingLens.storage.repos import FactRepo

REG = load_registry()
REG.companies.setdefault("WIPRO", REG.companies["TCS"].__class__(
    "WIPRO", "", "Wipro Limited", "unknown", ("wipro",)))       # the eval set mentions Wipro
CASES = load_cases(Path(__file__).parents[1] / "evals" / "questions.yaml")


def test_eval_file_is_consistent():
    assert len(CASES) >= 60 and len({c.id for c in CASES}) == len(CASES)
    for c in CASES:
        e = c.expect
        tools = e.get("tool", [])
        for t in tools if isinstance(tools, list) else [tools]:
            assert t in ARGS, (c.id, t)
        if not e.get("args"):
            continue
        tool = tools[0] if isinstance(tools, list) else tools
        for opt in e["args"] if isinstance(e["args"], list) else [e["args"]]:
            for k, v in opt.items():
                assert k in ARGS[tool].model_fields, (c.id, k)
                vals = v if isinstance(v, list) else [v]
                if k in ("ticker", "tickers"):
                    assert all(norm_ticker(x, REG) == x for x in vals), (c.id, v)
                if k in ("metric", "metrics"):
                    assert all(norm_metric(x, REG) == x for x in vals), (c.id, v)
                if k in ("fy", "fys", "from_fy", "to_fy"):
                    assert all(norm_fy(x) in (x, "latest") for x in vals), (c.id, v)


def resp(tool="get_value", args=None, rows=(), clarify=None, text=None, cites=(), mode="llm"):
    res = ToolResult(tool, "t", "INR crore", ["period", "value"], list(rows), [], list(cites),
                     clarify)
    return Response("q", mode, tool, args or {}, res, Answer(text, used_llm=bool(text)))


def test_scoring_values_args_and_fabrication():
    case = Case("x", "TCS revenue FY24", {"tool": "get_value", "values": [240893],
                                          "args": {"ticker": "TCS", "fy": ["FY24", "latest"]}})
    good = score(case, resp(args={"ticker": "TCS", "fy": "latest"},
                            rows=[{"period": "FY24", "value": 240893.0}],
                            text="TCS revenue was ₹2,40,893 crore in FY24."))
    assert good.passed and good.checks["values"] and good.fabricated == []
    wrong = score(case, resp(args={"ticker": "INFY", "fy": "FY24"},
                             rows=[{"period": "FY24", "value": 153670.0}],
                             text="Revenue was ₹2,50,000 crore."))
    assert not wrong.passed and wrong.checks["args"] is False and wrong.checks["values"] is False
    assert wrong.fabricated == ["2,50,000"]


def test_scoring_refusal_missing_data_pages_and_rules_mode():
    refuse = Case("r", "Reliance revenue", {"refuse": True})
    assert score(refuse, resp("ask_user", clarify="Which company?")).passed
    assert not score(refuse, resp(rows=[{"value": 1.0}])).passed
    missing = Case("m", "TCS FY19", {"tool": "get_value", "no_value": True})
    assert score(missing, resp(rows=[])).passed
    assert not score(missing, resp(rows=[{"period": "FY19", "value": 9.0}])).passed
    text = Case("s", "attrition", {"tool": "search_reports", "pages": [43], "ticker": "TCS",
                                   "answer_contains": ["13.3"]})
    passage = [{"rank": 1, "source": "TCS | AR | page 43", "text": "attrition was 13.3%"}]
    hit = resp("search_reports", rows=passage, cites=[{"ticker": "TCS", "page": 43}],
               text="13.3% [p.43]")
    assert score(text, hit).passed
    assert not score(text, resp("search_reports", cites=[{"ticker": "INFY", "page": 43}])).passed
    rules = score(text, resp("search_reports", cites=[{"ticker": "TCS", "page": 43}],
                             mode="search"))
    assert rules.checks["answer"] is None and rules.passed      # no written answer expected


def test_args_and_value_helpers():
    assert args_match({"tickers": ["INFY", "TCS"]}, {"tickers": ["TCS", "INFY"], "fys": []})
    assert not args_match({"tickers": ["TCS"]}, {"tickers": ["TCS", "INFY"]})
    assert args_match([{"last_n_years": 3}, {"from_fy": "FY24"}], {"from_fy": "FY24"})
    assert value_found(18.52, [18.5203]) and value_found(240893, [240893.0])
    assert not value_found(18.52, [18.6]) and not value_found(240893, [240894.2])


def test_run_save_and_compare(con, tmp_path):
    FactRepo(con).upsert_facts([make_fact(fy="FY24", value=240893.0)])
    svc = AnswerService(Context(con, REG), None)
    cases = [Case("a", "TCS revenue FY24", {"tool": "get_value", "values": [240893]}, ["value"]),
             Case("b", "Reliance revenue", {"refuse": True}, ["refuse"])]
    seen = []
    results = run(cases, svc, lambda i, r: seen.append(r.id))
    s = summarise(results)
    assert seen == ["a", "b"] and s["passed"] == 2 and s["fabricated_numbers"] == 0
    assert s["checks"]["values"] == {"ok": 1, "n": 1} and s["by_tag"]["refuse"]["ok"] == 1
    first = save(results, s, {"mode": "rules"}, tmp_path)
    assert previous_summary(tmp_path, "rules", before=first) is None
    assert previous_summary(tmp_path, "rules")["passed"] == 2


@pytest.mark.parametrize("tag", ["value", "compare", "text", "refuse", "missing", "hinglish"])
def test_eval_set_covers_each_category(tag):
    assert sum(tag in c.tags for c in CASES) >= 4
