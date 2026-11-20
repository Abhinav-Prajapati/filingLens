"""The answer service: one function from question to complete response, used by the CLI, the
API and the eval, so all behave identically.

    short follow-up / answer to a clarifying question -> read with the previous question
    close misspelling of a covered company ("wifpro")  -> read as that company, and say so
    question names a listed company that is not loaded -> say so (no partial answer)
    "why" question with a company and a metric  -> the figures + the reasons from that year's
                                                   annual report (management discussion first)
    LLM configured and working  -> LLM picks the tool -> run -> LLM writes a checked answer
    no LLM / LLM fails / no valid call -> rule-based plan -> run (table, no written answer)
    rules find no metric        -> search the reports -> passages with page sources

The tools and data are the same on every path; only the understanding step differs. When the
model asks back or searches the reports although the rules find covered companies and a known
metric, the checked data is used instead (models refuse covered companies or read a figure
off a PDF page). A written answer that names a company not in the data is rejected.
"""
from __future__ import annotations

import functools
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filingLens.companies import (
    DEFAULT_LIST_PATH,
    CompanyGuard,
    ListedCompany,
    load_equity_list,
)
from filingLens.domain.registry import Registry
from filingLens.domain.units import fmt_value
from filingLens.llm.answer import Answer, compose_answer
from filingLens.llm.client import LLMClient
from filingLens.llm.executor import Context, ToolResult, execute, search_passages
from filingLens.llm.planner import ask, loaded_names
from filingLens.llm.prompt import join_names, short_name
from filingLens.llm.tools import validate_call
from filingLens.nlu import rules
from filingLens.nlu.followup import Previous, combine
from filingLens.nlu.spelling import correct_companies
from filingLens.retrieval.search import STOPWORDS
from filingLens.storage.db import Database


@dataclass
class Response:
    question: str
    mode: str                                   # "llm" | "rules" | "search" | "explain"
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: ToolResult | None = None
    answer: Answer = field(default_factory=Answer)
    notes: list[str] = field(default_factory=list)      # how the question was handled
    errors: list[str] = field(default_factory=list)     # model/tool errors along the way
    attempts: int = 0
    seconds: float = 0.0
    support: ToolResult | None = None           # "why" questions: the figures behind the text

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.result.empty

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def make_context(con: Database, vectors: Path, registry: Registry) -> Context:
    """Context whose searcher loads the vector index and embedding model once, on first use
    (keyword-only search when there is no index or the embed extra is not installed)."""

    @functools.cache
    def searcher():  # type: ignore[no-untyped-def]
        from filingLens.retrieval.index import VectorIndex
        from filingLens.retrieval.search import Searcher

        index = VectorIndex.load(vectors)
        if index is None:
            return Searcher(con)
        try:
            from filingLens.retrieval.embedder import get_embedder
            emb = get_embedder("hash" if index.model.startswith("hash") else index.model)
        except ImportError:
            return Searcher(con)
        return Searcher(con, index, emb)

    return Context(con, registry, searcher)


def make_guard(registry: Registry, path: Path = DEFAULT_LIST_PATH) -> CompanyGuard | None:
    """Guard over NSE's equity list (``filinglens companies`` downloads it); None without it."""
    listed = load_equity_list(path)
    return CompanyGuard(listed, registry, rules.question_words(registry)) if listed else None


DATA_TOOLS = ("get_value", "get_trend", "compare")
# where annual reports explain results: management discussion, directors' report, the letters
NARRATIVE = ["mdna", "directors_report", "front_matter"]
FELL = "decline decrease decreased lower fell drop reduced pressure"
ROSE = "growth increase increased higher rose improved driven"
# the direction a question assumes ("why did profit fall"); the filings may say otherwise
SAID_FELL = re.compile(r"\b(fall|fell|fallen|falling|drop|dropped|decline|declined|decrease|"
                       r"decreased|down|lower|shrink|shrank|ghata|ghat|gira|giri|kam)\b")
SAID_ROSE = re.compile(r"\b(rise|rose|risen|rising|grow|grew|grown|growth|increase|increased|up|"
                       r"higher|jump|jumped|improve|improved|badha|badhi|zyada)\b")
# text questions the rules may still find a metric in ("why did revenue fall")
EXPLAIN = re.compile(r"\b(why|reason|reasons|explain|kyun|kyon|driver|drivers|drove|"
                           r"strategy|outlook|guidance|risk|risks|commentary|plan|plans)\b")


def figures_sentence(figures: ToolResult, metrics: list[str], reg: Registry) -> str:
    """A plain summary written by code from the verified figures (no model): first and last
    value of each series and the change. Used when no checked explanation is available."""
    from filingLens.views import metric_label

    label = " and ".join(metric_label(m, reg).split(" (")[0] for m in metrics)
    pct = figures.unit == "%"
    parts = []
    for st in figures.stats or []:
        if st.get("kind") not in ("change", "change_pp") or st.get("value") is None:
            continue
        series = str(st.get("series") or "")
        first = next((r for r in figures.rows if r.get("period") == st.get("from")), None)
        last = next((r for r in figures.rows if r.get("period") == st.get("to")), None)
        col = series if first is not None and series in first else "value"
        v0, v1 = (first or {}).get(col), (last or {}).get(col)
        if not isinstance(v0, (int, float)) or not isinstance(v1, (int, float)):
            continue
        who = short_company(reg.companies[series].name) if series in reg.companies else ""
        what = (f"{who}'s {label[0].lower()}{label[1:]}" if who else label)
        show = (lambda v: f"{v:.2f}%") if pct else (lambda v: f"₹{fmt_value(v, 'INR crore')} crore")
        ch = st["value"]
        change = f"{ch:+.2f} pp" if st["kind"] == "change_pp" else f"{ch:+.1f}%"
        parts.append(f"{what} went from {show(v0)} in {st['from']} to {show(v1)} in "
                     f"{st['to']} ({change.replace('-', '−')}).")
    lead = " ".join(parts) or "The figures are below."
    return f"{lead} The reasons the annual report gives are in the passages below."


def short_company(name: str) -> str:
    return re.sub(r"\s+(limited|ltd\.?)$", "", name, flags=re.IGNORECASE).strip()


GENERIC = {"from", "operations", "total", "other", "basic", "diluted", "for", "the", "per"}


def metric_words(metrics: list[str], reg: Registry) -> list[str]:
    """Words a passage must mention to be about the metric: "Net profit margin" -> profit,
    margin (ratios add their parts: net margin -> profit, revenue)."""
    from filingLens.analytics.derived import DERIVED

    words: list[str] = []
    for m in metrics:
        if m in DERIVED:
            d = DERIVED[m]
            parts = [d.desc, *(reg.metrics[x].desc for x in (d.numerator, d.denominator)
                               if x in reg.metrics)]
        else:
            parts = [reg.metrics[m].desc if m in reg.metrics else m.replace("_", " ")]
        for label in parts:
            words += [w for w in re.findall(r"[a-z]+", label.lower().split("(")[0])
                      if len(w) > 3 and w not in GENERIC]
    return list(dict.fromkeys(words))


def topic_words(question: str, reg: Registry) -> str:
    """The question without company names, years and filler: what to search the reports for.
    Falls back to the whole question when nothing is left."""
    drop = set(STOPWORDS) | {"fy", "tell", "about", "show", "give", "details", "detail",
                             "please", "what", "which", "who", "how", "much", "many"}
    for t, c in reg.companies.items():
        for a in (t.lower(), c.name.lower(), *c.aliases):
            drop.update(a.split())
    norm = rules._norm(question)
    words = [w for w in re.findall(r"[a-z][a-z\-]+", norm) if w not in drop and len(w) > 1
             and not re.fullmatch(r"fy\d*", w)]
    return " ".join(dict.fromkeys(words)) or question


def explain_query(question: str, metrics: list[str], change: float | None,
                  reg: Registry) -> str:
    """Search words for a "why" question: its own content words, the metric's name, and words
    reports use for the direction of the change."""
    drop = set(STOPWORDS) | {"why", "reason", "reasons", "explain", "kyun", "kyon", "fy",
                             "from", "between", "during", "was", "were", "the"}
    for t, c in reg.companies.items():
        for a in (t.lower(), c.name.lower(), *c.aliases):
            drop.update(a.split())
    words = [w for w in re.findall(r"[a-z]+", question.lower()) if w not in drop and len(w) > 2
             and not (change is not None and (SAID_FELL.fullmatch(w) or SAID_ROSE.fullmatch(w)))]
    for m in metrics:
        label = reg.metrics[m].desc if m in reg.metrics else m.replace("_", " ")
        words += re.findall(r"[a-z]+", label.lower().split("(")[0])
    if change is not None:                 # the direction the filings show, not the one assumed
        words += (FELL if change < 0 else ROSE).split()
    return " ".join(dict.fromkeys(words)) or question


# "thanks", "ok", "hi": no question to answer, and nothing to look up
SMALL_TALK = re.compile(r"(thanks?|thank you|thanks a lot|thx|ty|ok|okay|okk|cool|great|nice|"
                        r"got it|perfect|awesome|hi|hello|hey|bye|good|fine|sure|"
                        r"shukriya|dhanyavaad|dhanyavad|accha|acha|theek hai|thik hai)[ !.]*")
SMALL_TALK_REPLY = ("Ask about a company's figures (\"TCS revenue FY24\"), a comparison, or "
                    "what its annual report says.")


def as_change(tool: str, args: dict) -> tuple[str, dict]:
    """A "why" question about one year ("why did Infosys profit fall") is about the change
    into that year: ask for the year before too, so there is a change to explain."""
    if tool != "get_value" or args.get("quarter"):
        return tool, args
    fy = args.get("fy") or "latest"
    span = ({"last_n_years": 2} if fy == "latest"
            else {"from_fy": f"FY{int(fy[2:]) - 1:02d}", "to_fy": fy})
    return "get_trend", {"ticker": args["ticker"], "metric": args["metric"],
                         "scope": args.get("scope", "consolidated"), **span}


def premise_note(question: str, change: float | None, what: str) -> str | None:
    """"Why did profit fall" when it rose: say so first, from the figures."""
    if change is None or abs(change) < 0.05:
        return None
    norm = rules._norm(question)
    fell, rose = bool(SAID_FELL.search(norm)), bool(SAID_ROSE.search(norm))
    if fell and not rose and change > 0:
        return f"{what} did not fall, it rose."
    if rose and not fell and change < 0:
        return f"{what} did not rise, it fell."
    return None


def not_loaded_message(missing: list[ListedCompany], reg: Registry) -> str:
    names = ", ".join(f"{c.name} ({c.symbol})" for c in missing)
    have = ", ".join(c.name for c in reg.companies.values())
    syms = " ".join(c.symbol for c in missing)
    return (f"{names} {'is' if len(missing) == 1 else 'are'} not loaded, so I can't answer "
            f"about {'it' if len(missing) == 1 else 'them'}. Loaded: {have}. To add: "
            f"filinglens companies --add {syms} && filinglens download {syms} && "
            "filinglens ingest work/inbox")


class AnswerService:
    def __init__(self, ctx: Context, llm: LLMClient | None = None, write_answer: bool = True,
                 no_llm_reason: str = "none configured", guard: CompanyGuard | None = None):
        self.ctx, self.llm, self.write_answer = ctx, llm, write_answer
        self.no_llm_reason, self.guard = no_llm_reason, guard

    def answer(self, question: str, previous: Previous | None = None) -> Response:
        t0 = time.monotonic()
        reg = self.ctx.registry
        read: list[str] = []                          # how the question was read
        if SMALL_TALK.fullmatch(question.strip().lower()):
            r = Response(question, "rules", "ask_user", {})
            r.result = ToolResult("ask_user", "nothing to look up", clarify=SMALL_TALK_REPLY)
            r.seconds = round(time.monotonic() - t0, 2)
            return r
        question, follow = combine(previous, question, reg)
        if follow:
            read.append(follow)
        question, fixes = correct_companies(question, reg, rules.question_words(reg))
        read += [f"read “{f.typed}” as “{f.read_as}”" for f in fixes]
        # a message read on its own still goes to the model with the turn before it
        r = self._answer(question, None if follow else previous, joined=bool(follow))
        r.notes[:0] = read
        r.seconds = round(time.monotonic() - t0, 2)
        return r

    def _answer(self, question: str, previous: Previous | None = None,
                joined: bool = False) -> Response:
        missing = self.guard.find(question) if self.guard else []
        if missing:
            r = Response(question, "rules", "ask_user", {}, notes=[
                "the question names a company that is not loaded"])
            r.result = ToolResult("ask_user", "company not loaded",
                                  clarify=not_loaded_message(missing, self.ctx.registry))
            return r
        explain = self._explain(question)
        if explain is not None:
            return explain
        if joined and self.llm is not None:
            # "TCS revenue FY24, and Infosys" is text the code put together for the rules, and
            # small models misread it (two calls, one company): plan it by rules when they can
            p = rules.plan(question, self.ctx.registry)
            if p is not None and p.tool in DATA_TOOLS:
                r = self._via_rules(question)
                if r.result is not None and not r.result.empty:
                    if self.write_answer:
                        r.answer = self._checked_answer(question, r.result)
                    return r
        via_llm = self._via_llm(question, previous) if self.llm is not None else None
        if via_llm is None:
            r = self._via_rules(question)
            if self.llm is None:
                r.notes.insert(0, f"answered without the LLM ({self.no_llm_reason})")
        else:
            r = via_llm
        return r

    # ---- "why" questions ----------------------------------------------------------------------
    def _explain(self, question: str) -> Response | None:
        """"Why did Wipro's net profit fall from FY23 to FY24": the figures (rules plan) and
        the reasons from the annual report of the last year asked about, management
        discussion and directors' report first. None when it is not such a question."""
        norm = rules._norm(question)
        if not EXPLAIN.search(norm):
            return None
        reg = self.ctx.registry
        p = rules.plan(question, reg)
        if p is None or p.tool not in DATA_TOOLS:
            return None
        tool, raw = as_change(p.tool, p.args)
        try:
            args = validate_call(tool, raw, reg)
        except ValueError:
            return None
        r = Response(question, "explain", "search_reports", notes=[
            "a “why” question: the figures from the filings, and what the annual report says"])
        figures = self._run(tool, args, r)
        a = args.model_dump()
        tickers = a.get("tickers") or [a["ticker"]]
        metrics = a.get("metrics") or [a["metric"]]
        years = rules.find_years(norm).get("fys") or []
        report_fy = max(years, key=lambda f: int(f[2:])) if years else None
        if report_fy is None and figures.rows:
            report_fy = str(figures.rows[-1].get("period", ""))[:4] or None
        change = next((st["value"] for st in figures.stats
                       if st.get("kind") in ("change", "change_pp") and st.get("value") is not None),
                      None) if figures.stats else None
        query = explain_query(question, metrics, change, reg)
        fy = [report_fy] if report_fy and report_fy.startswith("FY") else []
        res = search_passages(self.ctx, query, tickers, fy, NARRATIVE, k=5,
                              tiers=[(fy, NARRATIVE), (fy, []), ([], NARRATIVE)],
                              must=metric_words(metrics, reg))
        if fy:
            res.notes.insert(0, f"searched the {report_fy} annual report"
                             + (f"{'s' if len(tickers) > 1 else ''} of {', '.join(tickers)}")
                             + ": management discussion and directors' report first")
        r.args = {"query": query, "tickers": tickers, "fy": fy[0] if fy else None,
                  "section": None, "k": 5, "figures": {"tool": tool, **a}}
        r.result, r.support = res, (figures if figures.rows else None)
        if self.llm is not None and self.write_answer and res.rows:
            r.answer = compose_answer(question, res, self.llm, figures=r.support)
        if r.answer.text is None and r.support is not None:
            if r.answer.rejected:                    # say why, then use the checked figures
                r.notes.append(f"the model's explanation was not used ({r.answer.rejected})")
            elif self.llm is None:
                r.notes.append(f"answered without the LLM ({self.no_llm_reason})")
            r.answer = Answer(figures_sentence(r.support, metrics, reg))
        from filingLens.views import metric_label

        unit = "pp" if any(st.get("kind") == "change_pp" for st in figures.stats or []) else "%"
        name = short_company(reg.companies[tickers[0]].name) if tickers[0] in reg.companies else ""
        label = metric_label(metrics[0], reg).split(" (")[0]
        note = premise_note(question, change, f"{name}'s {label[0].lower()}{label[1:]}"
                            if name else label) if len(tickers) == 1 and unit == "%" else None
        if note and r.answer.text:
            r.answer.text = f"{note} {r.answer.text}"
        return r

    # ---- LLM path -------------------------------------------------------------------------
    def _via_llm(self, question: str, previous: Previous | None = None) -> Response | None:
        assert self.llm is not None
        res = ask(question, self.llm, self.ctx, previous=previous.question if previous else None,
                  previous_tool=previous.tool if previous else None)
        out = Response(question, "llm", res.tool, res.args, res.result, attempts=res.attempts,
                       errors=list(res.errors))
        if res.ok and res.tool in ("ask_user", "search_reports"):
            override = self._data_instead(question, res.tool)
            if override is None and res.tool == "ask_user":
                override = self._search_instead(question)
            if override is not None:
                out = override
                out.errors, out.attempts = list(res.errors), res.attempts
        if res.ok:
            if self.write_answer and out.result is not None:
                out.answer = self._checked_answer(question, out.result)
            return out
        if any("did not name" in e for e in res.errors):
            return out                   # company not covered: keep the model's clarification
        return self._fallback(question, out.errors, out.attempts)

    def _data_instead(self, question: str, model_tool: str) -> Response | None:
        """The model asked back or searched the reports, but the question names covered
        companies and a known metric: answer from the checked data instead."""
        p = rules.plan(question, self.ctx.registry)
        if p is None or p.tool not in DATA_TOOLS:
            return None
        if model_tool == "search_reports" and EXPLAIN.search(rules._norm(question)):
            return None                              # "why did revenue fall": a text question
        r = self._via_rules(question)
        if r.mode != "rules" or r.result is None or r.result.empty:
            return None
        what = "asked back" if model_tool == "ask_user" else "searched the report text"
        r.notes.insert(0, f"the model {what}; the question names covered companies and a known "
                          "metric, so the checked data was used")
        return r

    def _search_instead(self, question: str) -> Response | None:
        """The model asked back, but the question names covered companies and no metric:
        a topic like debentures or dividends is in the annual reports, so search them."""
        norm = rules._norm(question)
        if not rules.find_companies(norm, self.ctx.registry) or rules.plan(question,
                                                                            self.ctx.registry):
            return None
        r = self._search(question)
        if r.result is None or not r.result.rows:
            return None
        r.mode = "llm"
        r.notes[:] = [("the model asked back; the question names a covered company and a topic "
                      "that is not a stored figure, so its annual reports were searched")]
        return r

    def _checked_answer(self, question: str, result: ToolResult) -> Answer:
        assert self.llm is not None
        a = compose_answer(question, result, self.llm)
        named = self.guard.find(a.text) if self.guard and a.text else []
        if named:
            a = Answer(None, True, "the answer named " + ", ".join(c.name for c in named)
                       + ", which is not in the data")
        return a

    def _fallback(self, question: str, errors: list[str], attempts: int) -> Response:
        r = self._via_rules(question)
        why = "the LLM request failed" if any("request failed" in e for e in errors) else \
            "the LLM did not produce a valid data request"
        r.notes.insert(0, f"answered without the LLM ({why})")
        r.errors, r.attempts = errors, attempts
        return r

    # ---- rules / search path ------------------------------------------------------------------
    def _via_rules(self, question: str) -> Response:
        reg = self.ctx.registry
        p = rules.plan(question, reg)
        if p is None:
            return self._search(question)
        if p.tool == "ask_user":                     # name the companies that have data
            names = join_names([short_name(n) for n in loaded_names(self.ctx)], "and")
            p.args["question"] = f"Which company? I have data for {names}."
        try:
            args = validate_call(p.tool, p.args, reg)
        except ValueError as e:
            r = self._search(question)
            r.errors.append(f"rule plan invalid: {e}")
            return r
        r = Response(question, "rules", p.tool, args.model_dump(), notes=list(p.notes))
        r.result = self._run(p.tool, args, r)
        return r

    def _search(self, question: str) -> Response:
        """Report passages for a question with no stored metric: the topic words only (no
        company names or years), the report of the year asked about first, then the others."""
        reg = self.ctx.registry
        norm = rules._norm(question)
        tickers = rules.find_companies(norm, reg)
        fys = rules.find_years(norm).get("fys") or []
        query = topic_words(question, reg)
        args = validate_call("search_reports", {"query": query, "tickers": tickers,
                                                "fy": fys[-1] if len(fys) == 1 else None},
                             reg)
        r = Response(question, "search", "search_reports", args.model_dump(),
                     notes=["no metric recognised: showing the most relevant report passages"])
        try:
            r.result = search_passages(self.ctx, query, tickers, fys, [], k=5,
                                       tiers=[(fys, []), ([], [])] if fys else None)
        except Exception as e:                      # noqa: BLE001 - report, never crash
            r.errors.append(f"tool search_reports failed: {type(e).__name__}: {e}")
            r.result = ToolResult("search_reports", "the search failed",
                                  notes=[f"{type(e).__name__}: {e}"])
        if fys and r.result.rows:
            r.result.notes.insert(0, f"searched the {', '.join(fys)} annual report first")
        return r

    def _run(self, tool: str, args: Any, r: Response) -> ToolResult:
        try:
            return execute(tool, args, self.ctx)
        except Exception as e:                      # noqa: BLE001 - report, never crash
            r.errors.append(f"tool {tool} failed: {type(e).__name__}: {e}")
            return ToolResult(tool, "the data lookup failed", notes=[f"{type(e).__name__}: {e}"])
