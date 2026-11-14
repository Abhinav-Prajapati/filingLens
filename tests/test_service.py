"""Rule-based planner and the answer service (LLM path, fallbacks, search). No network."""
import json

import pytest
from conftest import make_fact

from filingLens.domain.models import Chunk
from filingLens.domain.registry import load_registry
from filingLens.llm.client import FakeLLM, LLMReply, ToolCall
from filingLens.llm.executor import Context
from filingLens.nlu.rules import plan
from filingLens.service import AnswerService
from filingLens.storage.repos import FactRepo, TextRepo

REG = load_registry()


@pytest.mark.parametrize("q,tool,expect", [
    ("TCS revenue FY24", "get_value",
     {"ticker": "TCS", "metric": "revenue_from_operations", "fy": "FY24"}),
    ("Infosys net margin trend", "get_trend", {"ticker": "INFY", "metric": "net_margin"}),
    ("HUL ki bikri pichle 3 saal", "get_trend",
     {"ticker": "HINDUNILVR", "metric": "revenue_from_operations", "last_n_years": 3}),
    ("TCS ka munafa do saal", "get_trend", {"metric": "net_profit", "last_n_years": 2}),
    ("TCS vs Infosys revenue growth", "compare",
     {"tickers": ["TCS", "INFY"], "normalise": "index100"}),
    ("Q3 FY24 revenue of TCS", "get_value", {"fy": "FY24", "quarter": "Q3"}),
    ("standalone EPS of TCS for 2022-23", "get_value",
     {"metric": "eps_basic", "fy": "FY23", "scope": "standalone"}),
    ("teeno companies ka PBT margin", "compare",
     {"tickers": ["TCS", "INFY", "HINDUNILVR"], "metrics": ["pbt_margin"]}),
    ("TCS revenue, profit and employee cost", "compare",
     {"tickers": ["TCS"], "metrics": ["revenue_from_operations", "net_profit",
                                      "employee_expense"]}),
    ("TCS revenue from FY23 to FY26", "get_trend", {"from_fy": "FY23", "to_fy": "FY26"}),
    ("effective tax rate of Infosys since FY23", "get_trend",
     {"metric": "effective_tax_rate", "from_fy": "FY23"}),
    ("what should I do with TCS revenue", "get_value", {"fy": "latest"}),   # "do" is not 2
    ("profit kitna hua?", "ask_user", {}),
])
def test_rule_plans(q, tool, expect):
    p = plan(q, REG)
    assert p is not None and p.tool == tool, (q, p)
    assert {k: p.args.get(k) for k in expect} == expect, (q, p.args)


def test_rules_leave_questions_without_a_metric_to_search():
    assert plan("What does TCS say about AI?", REG) is None
    assert plan("Who is the CEO of Infosys?", REG) is None


# ---- service -------------------------------------------------------------------------------
@pytest.fixture
def ctx(con):
    FactRepo(con).upsert_facts([make_fact(t, "revenue_from_operations", fy, v)
                                for t, vals in {"TCS": (225458, 240893),
                                                "INFY": (146767, 153670)}.items()
                                for fy, v in zip(("FY23", "FY24"), vals, strict=True)])
    TextRepo(con).replace("ar", [], [Chunk("c1", "ar", "TCS", "FY25", "annual_report", "none",
                                           "text", "mdna", 44, 44,
                                           "TCS is investing in generative AI across services.",
                                           "TCS | Annual Report FY25 | mdna | page 44")])
    return Context(con, REG)


def call(name, args, cid="c1"):
    return LLMReply(tool_calls=[ToolCall(name, args, cid, json.dumps(args))])


def test_llm_path_with_written_answer(ctx):
    llm = FakeLLM([call("get_value", {"ticker": "TCS", "metric": "revenue", "fy": "FY24"}),
                   "TCS revenue was ₹2,40,893 crore in FY24."])
    r = AnswerService(ctx, llm).answer("TCS revenue FY24")
    assert r.mode == "llm" and r.ok and r.answer.text and r.notes == []
    assert r.result.rows[0]["value"] == 240893


def test_no_llm_uses_rules_with_a_note(ctx):
    r = AnswerService(ctx, None, no_llm_reason="--no-llm").answer("TCS revenue FY24")
    assert r.mode == "rules" and r.ok and r.answer.text is None
    assert r.notes[0] == "answered without the LLM (--no-llm)"
    assert r.to_dict()["result"]["rows"][0]["value"] == 240893


def test_llm_down_or_confused_falls_back_to_rules(ctx):
    class Down:
        model = "down"

        def chat(self, messages, tools):
            raise ConnectionError("429 rate limited")

    r = AnswerService(ctx, Down()).answer("Infosys revenue FY23")
    assert r.mode == "rules" and r.ok and "LLM request failed" in r.notes[0]
    assert r.result.rows[0]["value"] == 146767
    confused = AnswerService(ctx, FakeLLM(["no idea", "still none"])).answer("TCS revenue FY24")
    assert confused.mode == "rules" and "valid data request" in confused.notes[0]
    assert confused.attempts == 2 and confused.ok


def test_model_clarification_for_uncovered_company_is_kept(ctx):
    swap = call("get_value", {"ticker": "INFY", "metric": "revenue", "fy": "FY24"})
    r = AnswerService(ctx, FakeLLM([swap, swap])).answer("Wipro revenue FY24")
    assert r.mode == "llm" and "only have data for" in r.result.clarify


def test_no_metric_goes_to_search_and_tool_errors_do_not_crash(ctx):
    r = AnswerService(ctx, None).answer("What does TCS say about generative AI?")
    assert r.mode == "search" and r.args["tickers"] == ["TCS"]
    assert r.result.rows[0]["text"].startswith("TCS is investing") and r.ok

    def boom():
        raise RuntimeError("index corrupted")
    ctx.searcher = boom
    bad = AnswerService(ctx, None).answer("What does TCS say about AI?")
    assert not bad.ok and "index corrupted" in bad.errors[0]
