"""Request traces: what happened inside one question, kept in Postgres (table ``ask_trace``).

    with start_trace(question, source="api", user_id=..., chat_id=...) as t:
        with step("search", "search") as s:
            s.detail["query"] = "..."          # small, JSON-friendly facts about the step
        t.set(mode="llm", tool="compare")      # columns of the trace row

The current trace lives in a context variable, so code deep in the call stack records steps
without a trace being passed around. Outside ``start_trace`` every call is a no-op: tests,
the CLI and background jobs are unaffected. A trace is saved once, at the end, and saving
never fails the request.

Kept small on purpose: ids are capped at ``MAX_IDS``, text at ``MAX_TEXT`` characters.
Users are stored by id, never by email.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from filingLens.storage.db import Database

log = logging.getLogger(__name__)

MAX_IDS = 10
MAX_TEXT = 400
RETENTION_DAYS = 30
STEP_KINDS = ("prepare", "route", "llm", "plan", "override", "tool", "retrieve", "search",
              "answer", "title")


def clip(v: Any) -> Any:
    """Make a detail value small and JSON-safe."""
    if isinstance(v, str):
        return v if len(v) <= MAX_TEXT else v[:MAX_TEXT - 1] + "…"
    if isinstance(v, dict):
        return {str(k): clip(x) for k, x in list(v.items())[:30]}
    if isinstance(v, (list, tuple, set)):
        items = list(v)
        out = [clip(x) for x in items[:MAX_IDS]]
        return out + [f"+{len(items) - MAX_IDS} more"] if len(items) > MAX_IDS else out
    if isinstance(v, float):
        return round(v, 4)
    if v is None or isinstance(v, (bool, int)):
        return v
    return clip(str(v))


@dataclass
class Step:
    name: str
    kind: str
    start_ms: float
    ms: float = 0.0
    status: str = "ok"                      # ok | error
    parent: str | None = None               # the step it ran inside (an llm call's purpose)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "start_ms": round(self.start_ms, 1),
                "ms": round(self.ms, 1), "status": self.status, "parent": self.parent,
                "detail": clip(self.detail)}


@dataclass
class Trace:
    question: str
    source: str = "api"                     # api | cli | eval | title
    user_id: str | None = None
    chat_id: str | None = None
    eval_run: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    steps: list[Step] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _open: list[Step] = field(default_factory=list, repr=False)

    def now_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def set(self, **kw: Any) -> None:
        """Columns of the trace row: mode, tool, read_as, outcome, reject_reason, chat_id..."""
        for k, v in kw.items():
            if k == "chat_id":
                self.chat_id = v
            else:
                self.fields[k] = v

    def add_llm(self, model: str, ms: float, usage: dict[str, int], detail: dict[str, Any]
                ) -> None:
        """One model call: a step under the step it ran in, and the token totals."""
        parent = self._open[-1].name if self._open else None
        s = Step("llm", "llm", self.now_ms() - ms, ms, "error" if detail.get("error") else "ok",
                 parent, {"model": model, **usage, **detail})
        self.steps.append(s)
        f = self.fields
        f["llm_model"] = model
        f["llm_calls"] = f.get("llm_calls", 0) + 1
        for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
            f[k] = f.get(k, 0) + int(usage.get(k) or 0)


_current: ContextVar[Trace | None] = ContextVar("filinglens_trace", default=None)


def current() -> Trace | None:
    return _current.get()


def set_fields(**kw: Any) -> None:
    t = _current.get()
    if t is not None:
        t.set(**kw)


class _NoStep:
    """What ``step`` yields outside a trace: accepts details and drops them."""

    def __init__(self) -> None:
        self.detail: dict[str, Any] = {}
        self.status = "ok"


@contextmanager
def start_trace(question: str, source: str = "api", **kw: Any) -> Iterator[Trace]:
    t = Trace(question, source, **kw)
    token = _current.set(t)
    try:
        yield t
    except Exception as e:
        t.fields.setdefault("outcome", "failed")
        t.fields.setdefault("error", f"{type(e).__name__}: {e}")
        raise
    finally:
        t.fields["total_ms"] = round(t.now_ms(), 1)
        _current.reset(token)


@contextmanager
def step(name: str, kind: str) -> Iterator[Step | _NoStep]:
    """Time a step and let it record details. A no-op outside a trace."""
    t = _current.get()
    if t is None:
        yield _NoStep()
        return
    s = Step(name, kind, t.now_ms(), parent=t._open[-1].name if t._open else None)
    t._open.append(s)
    try:
        yield s
    except Exception as e:
        s.status = "error"
        s.detail["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        s.ms = t.now_ms() - s.start_ms
        t._open.pop()
        t.steps.append(s)


# ---- storage ----------------------------------------------------------------------------------
COLUMNS = ("mode", "tool", "read_as", "outcome", "reject_reason", "llm_model", "llm_calls",
           "prompt_tokens", "completion_tokens", "cached_tokens", "total_ms", "chat_seq",
           "error")


@dataclass
class TraceRow:
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
    steps: list[dict[str, Any]] | None = None


_SELECT = ("SELECT id, created_at, source, eval_run, user_id, chat_id, chat_seq, question, "
           "read_as, mode, tool, outcome, reject_reason, llm_model, llm_calls, prompt_tokens, "
           "completion_tokens, cached_tokens, total_ms, error")


def _row(r: Any, steps: bool = False) -> TraceRow:
    out = TraceRow(*r[:20])
    if steps:
        raw = r[20]                         # psycopg decodes jsonb; a string from other drivers
        out.steps = (json.loads(raw) if isinstance(raw, str) else raw) or []
    return out


class TraceRepo:
    def __init__(self, con: Database):
        self.con = con

    def save(self, t: Trace) -> bool:
        """Write the trace; False (and a log line) when it could not be saved."""
        f = t.fields
        try:
            self.con.execute(
                "INSERT INTO ask_trace(id, source, eval_run, user_id, chat_id, chat_seq, question, "
                "read_as, mode, tool, outcome, reject_reason, llm_model, llm_calls, prompt_tokens, "
                "completion_tokens, cached_tokens, total_ms, error, steps) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t.id, t.source, t.eval_run, t.user_id, t.chat_id, f.get("chat_seq"),
                 clip(t.question), clip(f.get("read_as")), f.get("mode"), f.get("tool"),
                 f.get("outcome"), clip(f.get("reject_reason")), f.get("llm_model"),
                 f.get("llm_calls", 0), f.get("prompt_tokens", 0), f.get("completion_tokens", 0),
                 f.get("cached_tokens", 0), f.get("total_ms", 0.0), clip(f.get("error")),
                 json.dumps([s.to_dict() for s in sorted(t.steps, key=lambda s: s.start_ms)])))
            return True
        except Exception:
            log.exception("could not save trace %s", t.id)
            return False

    def get(self, trace_id: str) -> TraceRow | None:
        r = self.con.execute(f"{_SELECT}, steps FROM ask_trace WHERE id = ?",
                             (trace_id,)).fetchone()
        return _row(r, steps=True) if r else None

    def search(self, *, source: str | None = None, mode: str | None = None,
               outcome: str | None = None, q: str | None = None, eval_run: str | None = None,
               days: int | None = None, before: datetime | None = None,
               limit: int = 50) -> list[TraceRow]:
        """Newest first. ``before``: the created_at of the last row seen (next page)."""
        where, params = _filters(source=source, mode=mode, outcome=outcome, q=q,
                                 eval_run=eval_run, days=days)
        if before is not None:
            where.append("created_at < ?")
            params.append(before)
        sql = (f"{_SELECT} FROM ask_trace WHERE {' AND '.join(where)} "
               "ORDER BY created_at DESC LIMIT ?")
        return [_row(r) for r in self.con.execute(sql, [*params, limit])]

    def stats(self, days: int = 7, source: str = "api") -> dict[str, Any]:
        """Totals, rates, latency percentiles per step kind, and questions per day."""
        where, params = _filters(source=source, days=days)
        w = " AND ".join(where)
        r = self.con.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE mode = 'llm'), "
            "COUNT(*) FILTER (WHERE outcome = 'rejected'), "
            "COUNT(*) FILTER (WHERE outcome = 'failed'), "
            "COUNT(*) FILTER (WHERE outcome = 'clarify'), "
            "COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms), 0), "
            "COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms), 0), "
            "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), "
            "COALESCE(SUM(cached_tokens), 0) "
            f"FROM ask_trace WHERE {w}", params).fetchone()
        total = int(r[0])
        fallback = self.con.execute(
            "SELECT COUNT(*) FROM ask_trace WHERE " + w + " AND steps @> ?::jsonb",
            [*params, json.dumps([{"name": "fallback"}])]).fetchone()[0]
        retried = self.con.execute(
            "SELECT COUNT(*) FROM ask_trace WHERE " + w + " AND "
            "jsonb_array_length(jsonb_path_query_array(steps, '$[*] ? (@.name == \"plan_attempt\")')) > 1",
            params).fetchone()[0]
        steps = [{"kind": k, "count": int(n), "p50_ms": round(float(p50), 1),
                  "p95_ms": round(float(p95), 1)} for k, n, p50, p95 in self.con.execute(
            "SELECT s->>'kind', COUNT(*), "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY (s->>'ms')::float), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY (s->>'ms')::float) "
            f"FROM ask_trace, jsonb_array_elements(steps) s WHERE {w} "
            "GROUP BY 1 ORDER BY 1", params)]
        reasons = [{"reason": k, "count": int(n)} for k, n in self.con.execute(
            "SELECT COALESCE(reject_reason, 'unknown'), COUNT(*) FROM ask_trace "
            f"WHERE {w} AND outcome = 'rejected' GROUP BY 1 ORDER BY 2 DESC LIMIT 8", params)]
        modes = [{"mode": k or "none", "count": int(n)} for k, n in self.con.execute(
            f"SELECT mode, COUNT(*) FROM ask_trace WHERE {w} GROUP BY 1 ORDER BY 2 DESC",
            params)]
        per_day = [{"day": d.isoformat(), "count": int(n), "p50_ms": round(float(p), 1)}
                   for d, n, p in self.con.execute(
            "SELECT (created_at AT TIME ZONE 'UTC')::date, COUNT(*), "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) "
            f"FROM ask_trace WHERE {w} GROUP BY 1 ORDER BY 1", params)]
        prompt, cached = int(r[7]), int(r[9])
        return {"days": days, "questions": total, "llm": int(r[1]), "rejected": int(r[2]),
                "failed": int(r[3]), "clarify": int(r[4]), "fallback": int(fallback),
                "retried": int(retried), "p50_ms": round(float(r[5]), 1),
                "p95_ms": round(float(r[6]), 1), "prompt_tokens": prompt,
                "completion_tokens": int(r[8]), "cached_tokens": cached,
                "cache_rate": round(cached / prompt, 3) if prompt else None,
                "steps": steps, "reject_reasons": reasons, "modes": modes, "per_day": per_day}

    def prune(self, days: int = RETENTION_DAYS) -> int:
        return self.con.execute("DELETE FROM ask_trace WHERE created_at < now() - "
                                "make_interval(days => ?)", (days,)).rowcount


def _filters(**kw: Any) -> tuple[list[str], list[Any]]:
    where, params = ["1=1"], []
    for col in ("source", "mode", "outcome", "eval_run"):
        if kw.get(col):
            where.append(f"{col} = ?")
            params.append(kw[col])
    if kw.get("q"):
        where.append("(question ILIKE ? OR read_as ILIKE ?)")
        params += [f"%{kw['q']}%"] * 2
    if kw.get("days"):
        where.append("created_at >= now() - make_interval(days => ?)")
        params.append(int(kw["days"]))
    return where, params
