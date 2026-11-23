"""Request traces: the tracer, what the answer path records, storage, the admin routes, eval."""
import json
from types import SimpleNamespace

import pytest
from conftest import make_fact

from filingLens import trace
from filingLens.domain.models import Chunk
from filingLens.domain.registry import load_registry
from filingLens.llm.client import FakeLLM, LLMReply, ToolCall, usage_of
from filingLens.llm.executor import Context
from filingLens.service import AnswerService
from filingLens.storage.repos import FactRepo, TextRepo
from filingLens.trace import TraceRepo, start_trace, step

REG = load_registry()


def call(name, args, cid="c1"):
    return LLMReply(tool_calls=[ToolCall(name, args, cid, json.dumps(args))],
                    usage={"prompt_tokens": 3700, "completion_tokens": 30, "cached_tokens": 3650})


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


def steps(t, name=None, kind=None):
    return [s for s in sorted(t.steps, key=lambda s: s.start_ms)
            if (name is None or s.name == name) and (kind is None or s.kind == kind)]


# ---- the tracer ---------------------------------------------------------------------------------
def test_steps_are_no_ops_outside_a_trace():
    assert trace.current() is None
    with step("search", "search") as s:
        s.detail["x"] = 1                          # accepted and dropped
    trace.set_fields(mode="llm")                   # no error


def test_steps_nest_time_and_record_errors():
    with start_trace("q") as t:
        with step("outer", "route"), step("inner", "tool") as s:
            s.detail["rows"] = 3
        with pytest.raises(ValueError), step("broken", "tool"):
            raise ValueError("no data")
    inner, = steps(t, "inner")
    assert inner.parent == "outer" and inner.detail == {"rows": 3} and inner.ms >= 0
    broken, = steps(t, "broken")
    assert broken.status == "error" and "no data" in broken.detail["error"]
    assert t.fields["total_ms"] >= 0 and trace.current() is None


def test_details_are_kept_small():
    big = trace.clip({"ids": list(range(25)), "text": "x" * 1000})
    assert big["ids"][-1] == "+15 more" and len(big["ids"]) == 11
    assert len(big["text"]) == trace.MAX_TEXT


def test_token_usage_and_llama_cpp_cache_are_read():
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3712, completion_tokens=41,
                                                 prompt_tokens_details=None),
                           model_extra={"timings": {"cache_n": 3690, "prompt_ms": 80.5,
                                                    "predicted_ms": 900.2}})
    assert usage_of(resp) == {"prompt_tokens": 3712, "completion_tokens": 41,
                              "cached_tokens": 3690, "prompt_ms": 80, "predicted_ms": 900}
    assert usage_of(SimpleNamespace()) == {}


# ---- what the answer path records --------------------------------------------------------------
def test_llm_path_records_attempts_tool_answer_and_tokens(ctx):
    bad = call("get_value", {"ticker": "INFY", "metric": "revenue", "fy": "FY24"})   # ungrounded
    good = call("get_value", {"ticker": "TCS", "metric": "revenue", "fy": "FY24"}, "c2")
    llm = FakeLLM([bad, good, "TCS revenue was 2,40,893 crore in FY24."])
    with start_trace("TCS revenue FY24") as t:
        r = AnswerService(ctx, llm).answer("TCS revenue FY24")
    assert r.ok
    route, = steps(t, "route")
    assert route.detail["path"] == "llm"
    first, second = steps(t, "plan_attempt")
    assert first.status == "error" and "did not name INFY" in first.detail["error"]
    assert second.status == "ok" and second.detail["tool"] == "get_value"
    plan_calls = [s for s in steps(t, kind="llm") if s.parent == "plan_attempt"]
    assert len(plan_calls) == 2 and plan_calls[0].detail["tool_calls"] == ["get_value"]
    tool, = steps(t, "get_value")
    assert tool.kind == "tool" and tool.detail["rows"] == 1
    answer, = steps(t, "answer")
    assert answer.detail["prompt"] == "data" and answer.detail["verdict"] == "passed"
    f = t.fields
    assert (f["mode"], f["tool"], f["outcome"]) == ("llm", "get_value", "answered")
    assert f["llm_calls"] == 3 and f["prompt_tokens"] == 7400 and f["cached_tokens"] == 7300


def test_rejected_answer_and_fallback_are_recorded(ctx):
    good = call("get_value", {"ticker": "TCS", "metric": "revenue", "fy": "FY24"})
    with start_trace("TCS revenue FY24") as t:
        AnswerService(ctx, FakeLLM([good, "TCS made 9,99,999 crore."])).answer("TCS revenue FY24")
    assert t.fields["outcome"] == "rejected"
    assert "numbers not in the sources" in t.fields["reject_reason"]
    with start_trace("TCS revenue FY24") as t2:
        AnswerService(ctx, FakeLLM(["no idea", "still none"]),
                      write_answer=False).answer("TCS revenue FY24")
    fb, = steps(t2, "fallback")
    assert fb.kind == "override" and "valid data request" in fb.detail["why"]
    assert t2.fields["mode"] == "rules"


def test_report_search_records_filters_rankings_and_hits(ctx):
    with start_trace("What does TCS say about generative AI?") as t:
        r = AnswerService(ctx, None).answer("What does TCS say about generative AI?")
    assert r.result.rows
    rs, = steps(t, "report_search")
    assert rs.kind == "retrieve" and rs.detail["hits"][0]["chunk"] == "c1"
    assert rs.detail["hits"][0]["page"] == 44
    s = steps(t, "search")[0]
    assert s.parent == "report_search" and "c1" in s.detail["keyword"]
    assert s.detail["filters"]["tickers"] == ["TCS"] and s.detail["fused"][0]["chunk"] == "c1"
    assert steps(t, "route")[0].detail["path"] == "rules"


# ---- storage --------------------------------------------------------------------------------
def test_store_search_stats_and_prune(con, ctx):
    repo = TraceRepo(con)
    for q, llm in (("TCS revenue FY24", None), ("What does TCS say about generative AI?", None)):
        with start_trace(q, source="api", user_id="u1", chat_id="c9") as t:
            AnswerService(ctx, llm).answer(q)
        assert repo.save(t)
    rows = repo.search()
    assert rows[0].question == "What does TCS say about generative AI?"     # newest first
    got = repo.get(rows[0].id)
    assert got.user_id == "u1" and got.chat_id == "c9" and got.steps[0]["name"] == "prepare"
    assert [r.mode for r in repo.search(mode="rules")] == ["rules"]
    assert repo.search(q="generative")[0].tool == "search_reports"
    st = repo.stats(days=7)
    assert st["questions"] == 2 and st["llm"] == 0 and {m["mode"] for m in st["modes"]} == {
        "rules", "search"}
    assert any(s["kind"] == "search" for s in st["steps"]) and st["per_day"][0]["count"] == 2
    con.execute("UPDATE ask_trace SET created_at = now() - interval '40 days' WHERE id = ?",
                (rows[1].id,))
    assert repo.prune(30) == 1 and len(repo.search()) == 1


def test_a_failed_save_never_raises():
    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("database gone")
    with start_trace("q") as t:
        pass
    assert TraceRepo(Broken()).save(t) is False


# ---- eval ---------------------------------------------------------------------------------------
def test_eval_run_keeps_a_trace_per_case(con, ctx):
    from filingLens.evaluation import Case, run
    repo = TraceRepo(con)
    cases = [Case("v1", "TCS revenue FY24", {"tool": "get_value", "values": [240893]}),
             Case("v2", "INFY revenue FY24", {"tool": "get_value", "values": [1]})]
    results = run(cases, AnswerService(ctx, None), traces=repo, run_id="run-1")
    assert [r.passed for r in results] == [True, False] and all(r.trace_id for r in results)
    rows = repo.search(source="eval", eval_run="run-1")
    assert len(rows) == 2
    failed = repo.get(results[1].trace_id)
    ev = next(s for s in failed.steps if s["name"] == "eval")
    assert ev["status"] == "error" and ev["detail"]["case"] == "v2"
    assert repo.search(source="api") == []                 # kept apart from real use


# ---- HTTP -------------------------------------------------------------------------------------
def test_ask_saves_a_trace_that_only_admins_can_read(tmp_path):
    import asyncio

    from test_chats import make_client, sign_in

    from filingLens.api.auth import make_auth

    with make_client(tmp_path, None) as c:
        sign_in(c, "ana@example.com")
        a = c.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False}).json()
        tid = a["trace_id"]
        assert tid
        assert c.get(f"/admin/traces/{tid}").status_code == 403
        turn = c.get(f"/chats/{a['chat_id']}").json()["items"][0]
        assert turn["answer"]["trace_id"] == tid

        async def promote():
            auth = make_auth()
            await auth.create_admin("ana@example.com", None)
            await auth.close()
        asyncio.run(promote())

        d = c.get(f"/admin/traces/{tid}").json()
        assert d["question"] == "TCS revenue FY24" and d["outcome"] == "answered"
        assert d["chat_id"] == a["chat_id"] and d["chat_seq"] == 1
        assert [s["name"] for s in d["steps"]][:2] == ["prepare", "route"]
        page = c.get("/admin/traces", params={"mode": "rules"}).json()
        assert [i["id"] for i in page["items"]] == [tid] and page["next_before"] is None
        assert c.get("/admin/traces/stats").json()["questions"] == 1
        assert c.get("/admin/traces/nope").status_code == 404
