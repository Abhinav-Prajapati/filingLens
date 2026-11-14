"""The answer service: one function from question to complete response, used by the CLI, the
API and the eval, so all behave identically.

    question names a listed company that is not loaded -> say so (no partial answer)
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
from filingLens.llm.answer import Answer, compose_answer
from filingLens.llm.client import LLMClient
from filingLens.llm.executor import Context, ToolResult, execute
from filingLens.llm.planner import ask
from filingLens.llm.tools import validate_call
from filingLens.nlu import rules
from filingLens.storage.db import Database


@dataclass
class Response:
    question: str
    mode: str                                   # "llm" | "rules" | "search"
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: ToolResult | None = None
    answer: Answer = field(default_factory=Answer)
    notes: list[str] = field(default_factory=list)      # how the question was handled
    errors: list[str] = field(default_factory=list)     # model/tool errors along the way
    attempts: int = 0
    seconds: float = 0.0

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
# text questions the rules may still find a metric in ("why did revenue fall")
EXPLAIN = re.compile(r"\b(why|reason|reasons|explain|kyun|kyon|driver|drivers|drove|"
                           r"strategy|outlook|guidance|risk|risks|commentary|plan|plans)\b")


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

    def answer(self, question: str) -> Response:
        t0 = time.monotonic()
        missing = self.guard.find(question) if self.guard else []
        if missing:
            r = Response(question, "rules", "ask_user", {}, notes=[
                "the question names a company that is not loaded"])
            r.result = ToolResult("ask_user", "company not loaded",
                                  clarify=not_loaded_message(missing, self.ctx.registry))
            r.seconds = round(time.monotonic() - t0, 2)
            return r
        via_llm = self._via_llm(question) if self.llm is not None else None
        if via_llm is None:
            r = self._via_rules(question)
            if self.llm is None:
                r.notes.insert(0, f"answered without the LLM ({self.no_llm_reason})")
        else:
            r = via_llm
        r.seconds = round(time.monotonic() - t0, 2)
        return r

    # ---- LLM path -------------------------------------------------------------------------
    def _via_llm(self, question: str) -> Response | None:
        assert self.llm is not None
        res = ask(question, self.llm, self.ctx)
        out = Response(question, "llm", res.tool, res.args, res.result, attempts=res.attempts,
                       errors=list(res.errors))
        if res.ok and res.tool in ("ask_user", "search_reports"):
            override = self._data_instead(question, res.tool)
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
        tickers = rules.find_companies(rules._norm(question), self.ctx.registry)
        args = validate_call("search_reports", {"query": question, "tickers": tickers},
                             self.ctx.registry)
        r = Response(question, "search", "search_reports", args.model_dump(),
                     notes=["no metric recognised: showing the most relevant report passages"])
        r.result = self._run("search_reports", args, r)
        return r

    def _run(self, tool: str, args: Any, r: Response) -> ToolResult:
        try:
            return execute(tool, args, self.ctx)
        except Exception as e:                      # noqa: BLE001 - report, never crash
            r.errors.append(f"tool {tool} failed: {type(e).__name__}: {e}")
            return ToolResult(tool, "the data lookup failed", notes=[f"{type(e).__name__}: {e}"])
