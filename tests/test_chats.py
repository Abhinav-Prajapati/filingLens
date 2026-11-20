"""Chats: titles, the store, and the HTTP routes (each user sees only their own chats)."""
import pytest
from conftest import make_fact

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from filingLens.api.app import create_app
from filingLens.chats import ChatRepo, clean_title, llm_title, title_from_question
from filingLens.domain.registry import load_registry
from filingLens.llm.client import FakeLLM
from filingLens.llm.executor import Context
from filingLens.storage.db import connect
from filingLens.storage.repos import FactRepo

REG = load_registry()


# ---- titles -------------------------------------------------------------------------------------
@pytest.mark.parametrize("q,title", [
    ("why did infosys profit fall?", "Why did infosys profit fall"),
    ("  TCS   revenue FY24 ", "TCS revenue FY24"),
    ("Compare revenue of TCS, Infosys and HUL over the last 4 years",
     "Compare revenue of TCS, Infosys and…"),
    ("Infosys ka diluted EPS FY23 se FY26 tak kaise badla?", "Infosys ka diluted EPS FY23 se…"),
])
def test_title_from_the_first_question(q, title):
    assert title_from_question(q) == title


@pytest.mark.parametrize("reply,title", [
    ('"Infosys profit drivers FY26".', "Infosys profit drivers FY26"),
    ("Title: TCS vs Infosys revenue", "TCS vs Infosys revenue"),
    ("<think>hmm</think>\nTitan margins\nextra line", "Titan margins"),
    ("", None),
    ("Here is a short and descriptive title that you could use for this chat", None),
])
def test_model_titles_are_cleaned_or_refused(reply, title):
    assert clean_title(reply) == title


def test_model_title_naming_another_company_is_refused(con):
    ctx = Context(con, REG)
    assert llm_title(FakeLLM(["TCS revenue growth"]), "TCS revenue FY24", ctx) == "TCS revenue growth"
    assert llm_title(FakeLLM(["TCS vs Infosys revenue"]), "TCS revenue FY24", ctx) is None


# ---- store --------------------------------------------------------------------------------------
def test_store_is_per_user_and_keeps_a_users_own_name(con):
    repo = ChatRepo(con)
    c = repo.create("ana", "TCS revenue FY24")
    repo.add_turn(c.id, "TCS revenue FY24", "{}")
    assert repo.add_turn(c.id, "and Infosys?", "{}") == 2
    assert [x.id for x in repo.for_user("ana")] == [c.id] and repo.for_user("bob") == []
    assert repo.get(c.id, "bob") is None and not repo.rename(c.id, "bob", "x")
    assert repo.set_llm_title(c.id, "TCS revenue")              # replaces the question title
    assert repo.rename(c.id, "ana", "My TCS notes")
    assert not repo.set_llm_title(c.id, "Model title")          # never over a user's name
    assert repo.get(c.id, "ana").title == "My TCS notes"
    assert not repo.delete(c.id, "bob") and repo.delete(c.id, "ana")
    assert repo.turns(c.id) == []                               # turns go with the chat


# ---- HTTP ---------------------------------------------------------------------------------------
def make_client(tmp_path, llm):
    con = connect()
    FactRepo(con).upsert_facts([make_fact(fy=fy, value=v)
                                for fy, v in {"FY23": 225458.0, "FY24": 240893.0}.items()]
                               + [make_fact(ticker="INFY", fy="FY24", value=153670.0)])
    con.close()
    return TestClient(create_app(None, data_dir=tmp_path, store=None, llm=llm, registry=REG,
                                 equity_list=tmp_path / "none.csv", web_dir=None))


def sign_in(c, email):
    c.post("/auth/register", json={"email": email, "password": "correct horse 42"})
    assert c.post("/auth/login", data={"username": email,
                                       "password": "correct horse 42"}).status_code == 204


def test_ask_starts_a_chat_and_follow_ups_use_its_last_turn(tmp_path):
    with make_client(tmp_path, None) as c:
        sign_in(c, "ana@example.com")
        a = c.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False}).json()
        cid = a["chat_id"]
        assert cid
        (chat,) = c.get("/chats").json()
        assert chat["id"] == cid and chat["title"] == "TCS revenue FY24" and chat["turns"] == 1
        b = c.post("/ask", json={"question": "and Infosys?", "use_llm": False,
                                 "chat_id": cid}).json()
        assert b["chat_id"] == cid and b["tool"] == "compare"      # read with the chat's turn
        d = c.get(f"/chats/{cid}").json()
        assert [t["question"] for t in d["items"]] == ["TCS revenue FY24", "and Infosys?"]
        assert d["items"][1]["answer"]["question"] == "TCS revenue FY24, and Infosys"
        r = c.patch(f"/chats/{cid}", json={"title": "  TCS   and Infosys "}).json()
        assert r["title"] == "TCS and Infosys" and r["title_source"] == "user"
        assert c.patch(f"/chats/{cid}", json={"title": ""}).status_code == 422
        assert c.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False,
                                    "chat_id": "nope"}).status_code == 404


def test_another_user_cannot_see_or_change_a_chat(tmp_path):
    with make_client(tmp_path, None) as c:
        sign_in(c, "ana@example.com")
        cid = c.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False}).json()["chat_id"]
        c.post("/auth/logout")
        sign_in(c, "bob@example.com")
        assert c.get("/chats").json() == []
        assert c.get(f"/chats/{cid}").status_code == 404
        assert c.patch(f"/chats/{cid}", json={"title": "mine"}).status_code == 404
        assert c.delete(f"/chats/{cid}").status_code == 404
        assert c.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False,
                                    "chat_id": cid}).status_code == 404
        c.post("/auth/logout")
        sign_in(c, "ana@example.com")
        assert c.delete(f"/chats/{cid}").status_code == 204 and c.get("/chats").json() == []


def test_model_names_the_chat_after_the_first_answer(tmp_path):
    # the model plans the question, writes the answer, then names the chat
    from filingLens.llm.client import LLMReply, ToolCall
    call = LLMReply(tool_calls=[ToolCall("get_value", {"ticker": "TCS", "metric": "revenue",
                                                       "fy": "FY24"}, "c1", "{}")])
    with make_client(tmp_path, FakeLLM([call, "TCS made 2,40,893 crore.", "TCS revenue FY24 check"])) as c:
        sign_in(c, "ana@example.com")
        cid = c.post("/ask", json={"question": "tcs ka revenue fy24 kitna tha"}).json()["chat_id"]
        (chat,) = c.get("/chats").json()
        assert chat["id"] == cid and chat["title"] == "TCS revenue FY24 check"
        assert chat["title_source"] == "llm"


def test_rules_mode_keeps_the_question_title_and_import_brings_old_turns(tmp_path):
    llm = FakeLLM([])
    with make_client(tmp_path, llm) as c:
        sign_in(c, "ana@example.com")
        a = c.post("/ask", json={"question": "TCS revenue FY24", "use_llm": False}).json()
        assert llm.requests == []                                   # LLM off: not asked to name
        imported = c.post("/chats/import", json={"turns": [
            {"question": "TCS revenue FY24", "answer": a}]}).json()
        assert imported["turns"] == 1 and imported["title"] == "TCS revenue FY24"
        assert c.post("/chats/import", json={"turns": []}).status_code == 422
