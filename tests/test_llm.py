"""LLM layer with FakeLLM (scripted replies): no network, no credentials."""
import json

import pytest
from conftest import make_fact

from filingLens.domain.models import Chunk
from filingLens.domain.registry import load_registry
from filingLens.llm.client import FakeLLM, LLMConfig, LLMReply, ToolCall, parse_text_call
from filingLens.llm.executor import Context
from filingLens.llm.planner import FALLBACK, ask
from filingLens.llm.prompt import build_system_prompt, load_examples
from filingLens.llm.tools import tool_schemas, validate_call
from filingLens.storage.repos import FactRepo, TextRepo

REG = load_registry()


@pytest.fixture
def ctx(con):
    facts = []
    for t, revs in {"TCS": (225458, 240893), "INFY": (146767, 153670)}.items():
        for fy, v in zip(("FY23", "FY24"), revs, strict=True):
            facts.append(make_fact(t, "revenue_from_operations", fy, v))
            facts.append(make_fact(t, "net_profit", fy, v * 0.19))
    FactRepo(con).upsert_facts(facts)
    TextRepo(con).replace("ar", [], [Chunk("c1", "ar", "TCS", "FY25", "annual_report", "none",
                                           "text", "mdna", 43, 43,
                                           "IT services attrition was 13.3% for FY 2025.",
                                           "TCS | Annual Report FY25 | mdna | page 43")])
    return Context(con, REG)


def native(name, args, cid="call_1"):
    return LLMReply(tool_calls=[ToolCall(name, args, cid, json.dumps(args))])


# ---- prompt, schemas, examples ---------------------------------------------------------------
def test_examples_validate_and_prompt_is_built_from_registry():
    for e in load_examples():
        validate_call(e["tool"], e["args"], REG)
    p = build_system_prompt(REG, "FY24")
    assert "Latest year with data: FY24" in p and "HINDUNILVR: Hindustan Unilever" in p
    assert "net_margin: Net profit margin" in p and "munafa" in p and "Q: HUL ki bikri" in p
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in tool_schemas(REG)}
    assert set(schemas) == {"get_value", "get_trend", "compare", "search_reports", "ask_user"}
    assert schemas["get_value"]["properties"]["ticker"]["enum"] == sorted(REG.companies)
    assert "net_margin" in schemas["get_value"]["properties"]["metric"]["enum"]


# ---- parsing replies from small models ----------------------------------------------------------
@pytest.mark.parametrize("text", [
    '<tool_call>{"name": "get_value", "arguments": {"ticker": "TCS"}}</tool_call>',
    'Sure!\n```json\n{"name": "get_value", "arguments": {"ticker": "TCS"}}\n```',
    '{"tool": "get_value", "args": {"ticker": "TCS"}}',
    '{"name": "get_value", "arguments": "{\\"ticker\\": \\"TCS\\"}"}',
    'I will call {"name": "get_value", "arguments": {"ticker": "T}C{S"}} now',
])
def test_parse_text_call_variants(text):
    call = parse_text_call(text)
    assert call is not None and call.name == "get_value" and call.arguments["ticker"] in (
        "TCS", "T}C{S")


def test_parse_text_call_none_for_plain_text():
    assert parse_text_call("TCS revenue was 2,40,893 crore") is None
    assert parse_text_call('{"not": "a call"}') is None


# ---- validation -----------------------------------------------------------------------------------
def test_validation_normalises_and_explains():
    a = validate_call("get_value", {"ticker": "Infosys", "metric": "profit", "fy": "2023-24"}, REG)
    assert (a.ticker, a.metric, a.fy) == ("INFY", "net_profit", "FY24")
    assert validate_call("get_trend", {"ticker": "hul", "metric": "Net Margin",
                                       "last_n_years": 3}, REG).ticker == "HINDUNILVR"
    for name, args, msg in [
        ("get_value", {"ticker": "Reliance", "metric": "revenue"}, "unknown company"),
        ("get_value", {"ticker": "TCS", "metric": "ebitda"}, "unknown metric"),
        ("get_value", {"ticker": "TCS", "metric": "revenue", "fy": "next year"}, "bad fiscal year"),
        ("compare", {"tickers": ["TCS", "INFY"], "metrics": ["revenue", "net_profit"]}, "ONE"),
        ("delete_all", {}, "unknown tool"),
    ]:
        with pytest.raises(ValueError, match=msg):
            validate_call(name, args, REG)


# ---- planner -------------------------------------------------------------------------------------
def test_ask_native_tool_call_runs_on_verified_data(ctx):
    llm = FakeLLM([native("get_trend", {"ticker": "TCS", "metric": "revenue",
                                        "last_n_years": 2})])
    r = ask("TCS ka revenue pichle 2 saal", llm, ctx)
    assert r.ok and r.tool == "get_trend" and r.attempts == 1 and r.errors == []
    assert [(x["period"], x["value"]) for x in r.result.rows] == [
        ("FY23", 225458), ("FY24", 240893)]
    assert r.result.rows[1]["yoy"] == pytest.approx(0.06846, abs=1e-5)
    sent = llm.requests[0]
    assert sent[0]["role"] == "system" and sent[1] == {
        "role": "user", "content": "TCS ka revenue pichle 2 saal"}


def test_ask_invalid_call_is_corrected_once(ctx):
    llm = FakeLLM([native("get_value", {"ticker": "Reliance", "metric": "revenue"}, "c9"),
                   native("ask_user", {"question": "Which company: TCS, Infosys or HUL?"})])
    r = ask("Reliance revenue", llm, ctx)
    assert r.ok and r.attempts == 2 and "unknown company" in r.errors[0]
    retry = llm.requests[1]
    assert retry[-2]["role"] == "assistant" and retry[-2]["tool_calls"][0]["id"] == "c9"
    assert retry[-1]["role"] == "tool" and retry[-1]["tool_call_id"] == "c9"
    assert "unknown company" in retry[-1]["content"]
    assert r.result.clarify == "Which company: TCS, Infosys or HUL?"


def test_ask_json_text_reply_and_fallback(ctx):
    reply = ('{"name": "compare", "arguments": {"tickers": ["TCS", "INFY"], '
             '"metrics": ["revenue_from_operations"], "fys": ["FY24"]}}')
    r = ask("TCS vs Infosys revenue FY24", FakeLLM([reply]), ctx)
    assert r.ok and [row["TCS"] for row in r.result.rows] == [240893]
    bad = ask("??", FakeLLM(["I think TCS did well.", "Still no tool."]), ctx)
    assert not bad.ok and bad.attempts == 2 and bad.result.clarify == FALLBACK
    assert bad.errors == ["no tool call in the reply"] * 2


def test_ask_survives_llm_errors(ctx):
    class Down:
        model = "down"

        def chat(self, messages, tools):
            raise ConnectionError("refused")

    r = ask("TCS revenue", Down(), ctx)
    assert not r.ok and "ConnectionError" in r.errors[0] and r.result.clarify == FALLBACK


# ---- executor ------------------------------------------------------------------------------------
def test_executor_tools(ctx):
    def run(name, args):
        return ask("TCS and Infosys", FakeLLM([native(name, args)]), ctx).result

    v = run("get_value", {"ticker": "INFY", "metric": "revenue", "fy": "latest"})
    assert v.rows[0]["period"] == "FY24" and v.rows[0]["value"] == 153670 and v.citations
    miss = run("get_value", {"ticker": "INFY", "metric": "revenue", "fy": "FY20"})
    assert miss.rows == [] and "available: FY23, FY24" in miss.notes[-1]
    m = run("get_trend", {"ticker": "TCS", "metric": "net_margin"})
    assert m.columns == ["period", "value", "change_pp"] and m.unit == "%"
    c = run("compare", {"tickers": ["TCS", "INFY"], "metrics": ["revenue"], "last_n_years": 1})
    assert [r["period"] for r in c.rows] == ["FY24"]
    s = run("search_reports", {"query": "attrition", "tickers": ["TCS"]})
    assert s.rows[0]["text"].startswith("IT services attrition") and s.citations[0]["page"] == 43
    FactRepo(ctx.con).upsert_facts([make_fact("TCS", "eps_basic", "FY24", 125.88)
                                    .model_copy(update={"unit": "INR per share"})])
    mixed = run("compare", {"tickers": ["TCS"], "metrics": ["revenue", "eps_basic"]})
    assert mixed.rows == [] and "different units" in mixed.notes[-1]


# ---- config ----------------------------------------------------------------------------------------
def test_config_from_dotenv_env_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FILINGLENS_LLM_BASE_URL=http://localhost:8000/v1\n"
                   "FILINGLENS_LLM_MODEL='Qwen/Qwen2.5-7B-Instruct'\n# comment\n"
                   "FILINGLENS_LLM_API_KEY=secret-key\n")
    monkeypatch.setenv("FILINGLENS_LLM_TOOLS", "json")
    cfg = LLMConfig.from_env(env)
    assert cfg.model == "Qwen/Qwen2.5-7B-Instruct" and cfg.tools_mode == "json"
    assert cfg.missing() == [] and "secr…" in cfg.masked() and "secret-key" not in cfg.masked()
    assert LLMConfig.from_env(tmp_path / "none").missing()


# ---- company grounding (small models swap an uncovered company for a covered one) ----------
def test_uncovered_company_is_not_swapped_for_a_covered_one(ctx):
    swap = native("get_value", {"ticker": "INFY", "metric": "revenue", "fy": "FY24"})
    r = ask("Wipro revenue FY24", FakeLLM([swap, swap]), ctx)
    assert not r.ok and all("did not name INFY" in e for e in r.errors)
    assert "only have data for Tata Consultancy Services, Infosys" in r.result.clarify
    fixed = ask("Wipro revenue FY24", FakeLLM([swap, native("ask_user", {
        "question": "I only cover TCS, Infosys and HUL."}, "c2")]), ctx)
    assert fixed.ok and fixed.attempts == 2 and fixed.result.clarify.startswith("I only cover")


def test_grounding_accepts_aliases_and_all_company_questions(ctx):
    call = native("compare", {"tickers": ["TCS", "INFY"], "metrics": ["revenue"]})
    for q in ("tcs vs infosys revenue", "Tata Consultancy aur Infy ka revenue",
              "teeno companies ka revenue", "compare all three on revenue"):
        assert ask(q, FakeLLM([call]), ctx).ok, q


def test_tool_failure_is_reported_not_raised(ctx):
    def boom():
        raise RuntimeError("CUDA out of memory")
    ctx.searcher = boom
    r = ask("TCS attrition", FakeLLM([native("search_reports", {"query": "attrition",
                                                               "tickers": ["TCS"]})]), ctx)
    assert r.ok and r.result.rows == [] and "out of memory" in r.result.notes[0]


# ---- answer step ---------------------------------------------------------------------------
def _search_result(ctx):
    return ask("TCS attrition", FakeLLM([native("search_reports", {
        "query": "attrition", "tickers": ["TCS"]})]), ctx).result


def test_text_answer_must_cite_retrieved_pages_and_copy_numbers(ctx):
    from filingLens.llm.answer import compose_answer
    res = _search_result(ctx)
    good = compose_answer("TCS attrition?", res, FakeLLM(
        ["TCS reported IT services attrition of 13.3% for FY 2025 [p.43]."]))
    assert good.text and good.pages == [43] and good.rejected is None
    sent = FakeLLM(["x"])
    compose_answer("TCS attrition?", res, sent)
    assert "[p.43]" in sent.requests[0][1]["content"] and "ONLY the passages" in (
        sent.requests[0][0]["content"])
    for reply, why in [
        ("TCS attrition was 12.1% [p.43].", "numbers not in the sources"),
        ("TCS attrition was 13.3% [p.99].", "not retrieved"),
        ("TCS attrition was 13.3%.", "no page citations"),
        ("NOT_IN_SOURCES", "do not answer"),
        ("<think>hmm</think>", "do not answer"),
    ]:
        a = compose_answer("TCS attrition?", res, FakeLLM([reply]))
        assert a.text is None and why in a.rejected, reply


def test_data_summary_allows_rounding_but_not_new_numbers(ctx):
    from filingLens.llm.answer import compose_answer, evidence
    res = ask("TCS revenue pichle 2 saal", FakeLLM([native("get_trend", {
        "ticker": "TCS", "metric": "revenue", "last_n_years": 2})]), ctx).result
    ev = evidence(res)
    assert "FY24 | 2,40,893 | +6.85%" in ev
    ok = compose_answer("TCS revenue?", res, FakeLLM(
        ["TCS revenue rose about 7% from ₹2,25,458 crore in FY23 to ₹2,40,893 crore in FY24."]))
    assert ok.text and ok.rejected is None                      # 6.85 rounded to 7 is fine
    years = compose_answer("TCS revenue?", res, FakeLLM(
        ["In FY 24 (2023-24) TCS earned ₹2,40,893 crore over 12 months."]))
    assert years.text and years.rejected is None               # year forms + small counts ok
    bad = compose_answer("TCS revenue?", res, FakeLLM(["TCS revenue grew 15% to ₹2,40,893 crore."]))
    assert bad.text is None and "['15']" in bad.rejected


def test_no_answer_call_for_clarifications_or_empty_results(ctx):
    from filingLens.llm.answer import compose_answer
    from filingLens.llm.executor import ToolResult
    never = FakeLLM([])                                          # would raise if called
    assert compose_answer("q", ToolResult("ask_user", "x", clarify="Which?"), never).text is None
    assert compose_answer("q", ToolResult("get_value", "x"), never).used_llm is False


def test_compare_states_growth_so_the_answer_can_quote_it(ctx):
    from filingLens.llm.answer import compose_answer
    res = ask("Which grew revenue faster, Infosys or TCS?", FakeLLM([native("compare", {
        "tickers": ["INFY", "TCS"], "metrics": ["revenue"], "normalise": "index100"})]), ctx).result
    assert "change FY23-FY24: INFY +4.70%, TCS +6.85%" in res.notes
    ans = compose_answer("Which grew faster?", res, FakeLLM(
        ["TCS grew faster: revenue rose 6.85% versus 4.70% for Infosys (FY23-FY24)."]))
    assert ans.text and ans.rejected is None
    margin = ask("TCS vs Infosys net margin", FakeLLM([native("compare", {
        "tickers": ["TCS", "INFY"], "metrics": ["net_margin"]})]), ctx).result
    assert any(n.startswith("change FY23-FY24: TCS +0.00 pp") for n in margin.notes)
