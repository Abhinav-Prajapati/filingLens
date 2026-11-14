"""The answer service: one function from question to complete response, used by the CLI and
(later) the API, so both behave identically.

    LLM configured and working  -> LLM picks the tool -> run -> LLM writes a checked answer
    no LLM / LLM fails / no valid call -> rule-based plan -> run (table, no written answer)
    rules find no metric        -> search the reports -> passages with page sources

The tools and data are the same on every path; only the understanding step differs. A
deliberate clarification from the model (e.g. "I only have TCS, Infosys...") is returned as
is, not overridden by the rules.
"""
from __future__ import annotations

import functools
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filingLens.domain.registry import Registry
from filingLens.llm.answer import Answer, compose_answer
from filingLens.llm.client import LLMClient
from filingLens.llm.executor import Context, ToolResult, execute
from filingLens.llm.planner import ask
from filingLens.llm.tools import validate_call
from filingLens.nlu import rules


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


def make_context(con: sqlite3.Connection, vectors: Path, registry: Registry) -> Context:
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


class AnswerService:
    def __init__(self, ctx: Context, llm: LLMClient | None = None, write_answer: bool = True,
                 no_llm_reason: str = "none configured"):
        self.ctx, self.llm, self.write_answer = ctx, llm, write_answer
        self.no_llm_reason = no_llm_reason

    def answer(self, question: str) -> Response:
        t0 = time.monotonic()
        r = self._via_llm(question) if self.llm is not None else None
        if r is None:
            r = self._via_rules(question)
            if self.llm is None:
                r.notes.insert(0, f"answered without the LLM ({self.no_llm_reason})")
        r.seconds = round(time.monotonic() - t0, 2)
        return r

    # ---- LLM path -------------------------------------------------------------------------
    def _via_llm(self, question: str) -> Response | None:
        assert self.llm is not None
        res = ask(question, self.llm, self.ctx)
        out = Response(question, "llm", res.tool, res.args, res.result, attempts=res.attempts,
                       errors=list(res.errors))
        if res.ok:
            if self.write_answer and res.result is not None:
                out.answer = compose_answer(question, res.result, self.llm)
            return out
        if any("did not name" in e for e in res.errors):
            return out                   # company not covered: keep the model's clarification
        return self._fallback(question, out.errors, out.attempts)

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
