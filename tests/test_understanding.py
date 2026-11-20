"""How questions are read: misspelt company names, follow-ups, year ranges, "why" questions,
and few-shot examples built from the companies that have data. No network."""
import json

import pytest
from conftest import make_fact

from filingLens.domain.models import Chunk
from filingLens.domain.registry import load_registry
from filingLens.llm.client import FakeLLM, LLMReply, ToolCall
from filingLens.llm.executor import Context
from filingLens.llm.prompt import build_system_prompt, fill_examples, load_examples, uncovered
from filingLens.nlu.followup import Previous, combine
from filingLens.nlu.rules import _norm, find_years, question_words
from filingLens.nlu.spelling import correct_companies
from filingLens.service import AnswerService, metric_words
from filingLens.storage.repos import FactRepo, TextRepo

REG = load_registry()
COMMON = question_words(REG)


# ---- spelling ----------------------------------------------------------------------------------
@pytest.mark.parametrize("q,fixed", [
    ("infossys revenue FY24", "infosys revenue FY24"),
    ("Hindustn Unilver profit", "hindustan unilever profit"),
    ("TCS vs Infosis margin", "TCS vs infosys margin"),
])
def test_close_misspellings_of_covered_companies_are_fixed(q, fixed):
    out, fixes = correct_companies(q, REG, COMMON)
    assert out == fixed and fixes


@pytest.mark.parametrize("q", [
    "TCS profit FY24",                  # correct already
    "Reliance revenue",                 # another company, not a typo of a covered one
    "what is the margin trend",         # ordinary words are never "corrected"
    "profit kitna hua",
    "TSC revenue",                      # 3-letter names are never guessed at
])
def test_other_words_are_left_alone(q):
    assert correct_companies(q, REG, COMMON) == (q, [])


# ---- year ranges -------------------------------------------------------------------------------
@pytest.mark.parametrize("q,fys", [
    ("net profit from fy 23 to 24", ["FY23", "FY24"]),
    ("revenue FY23-24", ["FY23", "FY24"]),
    ("revenue fy23 se fy25 tak", ["FY23", "FY25"]),
    ("revenue 2023-24", ["FY24"]),                  # one fiscal year, written as two
    ("revenue FY24 and 2 more", ["FY24"]),
])
def test_year_ranges(q, fys):
    assert find_years(_norm(q))["fys"] == fys


# ---- follow-ups --------------------------------------------------------------------------------
def test_a_new_question_after_a_clarification_stands_alone():
    """Regression: every message after a clarifying question used to be glued onto it."""
    prev = Previous("what is Infosys doing in North America", "ask_user")
    assert combine(prev, "tcs growth reason", REG) == ("tcs growth reason", None)
    assert combine(prev, "TCS net profit FY24", REG)[1] is None          # complete question
    assert combine(prev, "fy24", REG)[0] == "what is Infosys doing in North America (fy24)"
    assert combine(Previous("TCS revenue FY24", "get_value"), "and Infosys?", REG)[1]


def test_answer_to_a_clarifying_question_continues_it():
    q, note = combine(Previous("why did net profit fall from FY23 to FY24", "ask_user"),
                      "Infosys", REG)
    assert q == "why did net profit fall from FY23 to FY24 (Infosys)" and note


@pytest.mark.parametrize("prev,text,joined", [
    ("TCS revenue FY24", "and Infosys?", True),
    ("TCS revenue FY24", "what about FY23", True),
    ("TCS revenue FY24", "net profit", True),
    ("TCS revenue FY24", "Infosys net margin FY24", False),     # a full question of its own
    ("TCS revenue FY24", "thanks", False),
    ("TCS revenue FY24", "what does the company say about its plans for the next five years",
     True),                                     # long, but "its" points back to TCS
    ("TCS revenue FY24", "what does Infosys say about its plans for the next five years",
     False),                                    # names its own company: a new question
    ("TCS revenue FY24", "what are the biggest risks for the Indian IT industry next year",
     False),                                    # long, nothing points back
])
def test_short_follow_ups_join_the_previous_question(prev, text, joined):
    q, note = combine(Previous(prev, "get_value"), text, REG)
    assert (q != text) is joined and (note is not None) is joined


@pytest.mark.parametrize("text", [
    "what was reason for that growth",
    "why did it grow",
    "what was the reason for this increase in profit compared to last year",
    "uska reason kya tha",
])
def test_follow_ups_that_point_back_are_read_with_the_previous_question(text):
    q, note = combine(Previous("what was tcs profit in fy24", "get_value"), text, REG)
    assert q == f"what was tcs profit in fy24, {text}" and note


def test_why_question_naming_another_company_stands_alone():
    prev = Previous("what was tcs profit in fy24", "get_value")
    assert combine(prev, "why did infosys profit fall", REG) == ("why did infosys profit fall", None)


# ---- examples are built from the data ---------------------------------------------------------
def test_examples_name_only_loaded_companies_and_really_uncovered_ones():
    raw = json.dumps(load_examples())
    assert "Hindustan Unilever" not in raw and "Wipro" not in raw      # no hard-coded lists
    filled = json.dumps(fill_examples(load_examples(), ["Tata Consultancy Services",
                                                        "Wipro Limited"], REG))
    assert "Which company: Tata Consultancy Services or Wipro?" in filled
    assert "I only have data for Tata Consultancy Services and Wipro." in filled
    assert "{" + "loaded" not in filled and "Infosys or" not in filled
    for name in uncovered(REG):
        assert name.lower() not in {a for c in REG.companies.values()
                                    for a in (c.name.lower(), *c.aliases)}
    prompt = build_system_prompt(REG, "FY26", loaded=["Infosys Limited"])
    assert "Companies\n   with data: Infosys." in prompt


# ---- service: everything together --------------------------------------------------------------
@pytest.fixture
def ctx(con):
    FactRepo(con).upsert_facts([make_fact("INFY", "net_profit", fy, v)
                                for fy, v in (("FY23", 24095.0), ("FY24", 26233.0))]
                               + [make_fact("TCS", "net_profit", "FY24", 45908.0)])
    TextRepo(con).replace("ar24", [], [
        Chunk("p1", "ar24", "INFY", "FY24", "annual_report", "none", "text", "mdna", 12, 12,
              "Net profit grew on higher revenue and better operating margins.",
              "INFY | Annual Report FY24 | mdna | page 12"),
        Chunk("p2", "ar24", "INFY", "FY24", "annual_report", "none", "text", "notice", 90, 90,
              "Notice of the annual general meeting and the venue.",
              "INFY | Annual Report FY24 | notice | page 90"),
        Chunk("p3", "ar24", "INFY", "FY24", "annual_report", "consolidated", "text",
              "financial_statements", 150, 150,
              "Loss allowances for trade receivables are measured at lifetime expected loss.",
              "INFY | Annual Report FY24 | financial_statements | page 150")])
    return Context(con, REG)


def test_why_question_shows_figures_and_the_reports_reasons(ctx):
    r = AnswerService(ctx).answer("why did infossys net profit rise from fy 23 to 24")
    assert r.mode == "explain" and "read “infossys” as “infosys”" in r.notes
    assert [x["value"] for x in r.support.rows] == [24095.0, 26233.0]          # the figures
    pages = [c["page"] for c in r.result.citations]
    assert pages[0] == 12 and 90 not in pages      # the management discussion first; the
    assert r.args["fy"] == "FY24"                  # notice never (it doesn't mention profit)


def test_why_question_written_answer_uses_figures_and_cites_pages(ctx):
    llm = FakeLLM([("Infosys net profit rose from 24,095 to 26,233 crore, on higher revenue "
                   "and better operating margins [p.12].")])
    r = AnswerService(ctx, llm).answer("Why did Infosys net profit rise from FY23 to FY24?")
    assert r.mode == "explain" and r.answer.text and r.answer.pages == [12]


def test_clarification_answer_is_read_with_the_question(ctx):
    r = AnswerService(ctx).answer("Infosys", Previous("net profit FY24", "ask_user"))
    assert r.question == "net profit FY24 (Infosys)" and r.tool == "get_value"
    assert r.result.rows[0]["value"] == 26233.0


def test_misspelt_company_passes_the_llm_grounding_check(ctx):
    """The model maps the misspelling to the right company; that is not a "swap"."""
    args = {"ticker": "INFY", "metric": "net_profit", "fy": "FY24"}
    llm = FakeLLM([LLMReply(tool_calls=[ToolCall("get_value", args, "c1", json.dumps(args))]),
                   "Infosys net profit was ₹26,233 crore in FY24."])
    r = AnswerService(ctx, llm).answer("infosis net profit FY24")
    assert r.mode == "llm" and r.ok and not r.errors


def test_metric_words():
    assert metric_words(["net_profit"], REG) == ["profit"]
    assert "margin" in metric_words(["net_margin"], REG)


def test_why_answer_without_a_checked_explanation_states_the_figures(ctx):
    r = AnswerService(ctx).answer("why did Infosys net profit rise from FY23 to FY24")
    assert r.answer.text.startswith("Infosys's net profit went from ₹24,095 crore in FY23 "
                                    "to ₹26,233 crore in FY24 (+8.9%).")
    assert not r.answer.used_llm                     # written by code from the figures
    bad = FakeLLM(["Profit rose because of a big new deal worth 5,000 crore."])   # no page
    r = AnswerService(ctx, bad).answer("why did Infosys net profit rise from FY23 to FY24")
    assert r.answer.text.startswith("Infosys's net profit went from")
    assert any("explanation was not used" in n for n in r.notes)


def test_figures_only_answer_saying_no_reason_needs_no_citation(ctx):
    llm = FakeLLM([("Net profit rose from 24,095 to 26,233 crore. The report pages found do not "
                   "state the reason.")])
    r = AnswerService(ctx, llm).answer("Why did Infosys net profit rise from FY23 to FY24?")
    assert r.answer.used_llm and "do not state the reason" in r.answer.text


def test_topic_outside_the_metrics_is_searched_when_the_model_asks_back(ctx):
    """Small models answer "I don't have data on debentures" for topics that are not stored
    figures; the question names a covered company, so its reports are searched instead."""
    TextRepo(ctx.con).replace("ar24d", [], [Chunk(
        "d1", "ar24d", "INFY", "FY24", "annual_report", "none", "text", "notes", 70, 70,
        "Redeemable non-convertible debentures of face value 1,00,000 each were issued.",
        "INFY | Annual Report FY24 | notes | page 70")])
    ask_back = {"question": "I don't have data on debentures. Debt instead?"}
    llm = FakeLLM([LLMReply(tool_calls=[ToolCall("ask_user", ask_back, "c1",
                                                 json.dumps(ask_back))]),
                   "Infosys issued redeemable non-convertible debentures [p.70]."])
    r = AnswerService(ctx, llm).answer("infosys redeemable non convertible debentures fy24")
    assert r.tool == "search_reports" and r.result.citations[0]["page"] == 70
    assert r.answer.text and "asked back" in r.notes[0]
    assert r.args["query"] == "redeemable non convertible debentures" and r.args["fy"] == "FY24"
