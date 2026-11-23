"""Admin routes over request traces (see filingLens.trace).

    GET /admin/traces            newest first; filters: source, mode, outcome, q, eval_run, days
    GET /admin/traces/stats      totals, rates, latency per step kind, questions per day
    GET /admin/traces/{id}       one trace with its steps
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from filingLens.trace import TraceRepo, TraceRow

Source = Literal["api", "cli", "eval", "title"]


class TraceSummary(BaseModel):
    id: str
    created_at: datetime
    source: str
    eval_run: str | None
    user_id: str | None
    chat_id: str | None
    chat_seq: int | None
    question: str
    read_as: str | None
    mode: str | None
    tool: str | None
    outcome: str | None
    reject_reason: str | None
    llm_model: str | None
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_ms: float
    error: str | None


class TraceStep(BaseModel):
    name: str
    kind: str
    start_ms: float
    ms: float
    status: str
    parent: str | None = None
    detail: dict[str, Any] = {}


class TraceDetail(TraceSummary):
    steps: list[TraceStep]


class TracePage(BaseModel):
    items: list[TraceSummary]
    next_before: datetime | None          # pass as ?before= for the next page


def _summary(r: TraceRow) -> TraceSummary:
    d = {k: v for k, v in r.__dict__.items() if k != "steps"}
    return TraceSummary(**d)


def add_trace_routes(app: FastAPI, admin: Any, repo: TraceRepo, lock: threading.Lock) -> None:
    deps = [Depends(admin)]

    @app.get("/admin/traces", response_model=TracePage, dependencies=deps)
    def list_traces(source: Source | None = "api", mode: str | None = None,
                    outcome: str | None = None, q: str | None = None,
                    eval_run: str | None = None, days: int | None = Query(None, ge=1, le=365),
                    before: datetime | None = None,
                    limit: int = Query(50, ge=1, le=200)) -> TracePage:
        with lock:
            rows = repo.search(source=source, mode=mode, outcome=outcome, q=q,
                               eval_run=eval_run, days=days, before=before, limit=limit)
        return TracePage(items=[_summary(r) for r in rows],
                         next_before=rows[-1].created_at if len(rows) == limit else None)

    @app.get("/admin/traces/stats", dependencies=deps)
    def trace_stats(days: int = Query(7, ge=1, le=365), source: Source = "api") -> dict[str, Any]:
        with lock:
            return repo.stats(days=days, source=source)

    @app.get("/admin/traces/{trace_id}", response_model=TraceDetail, dependencies=deps)
    def get_trace(trace_id: str) -> TraceDetail:
        with lock:
            r = repo.get(trace_id)
        if r is None:
            raise HTTPException(404, "trace not found")
        return TraceDetail(**_summary(r).model_dump(),
                           steps=[TraceStep(**s) for s in r.steps or []])
