"""Question -> one validated tool call -> result.

    1. system prompt (tools, companies, metrics, Hinglish glossary, examples) + the question
    2. the model replies with a tool call (native, or JSON text for servers without tools)
    3. arguments are validated and normalised against registry.yaml
    4. on a missing or invalid call, the exact error goes back to the model once
    5. the tool runs on verified data; if the model still fails, the user is asked to rephrase

The model never produces numbers: every value in the result comes from the database.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from filingLens.llm.client import LLMClient, LLMReply
from filingLens.llm.executor import Context, ToolResult, execute
from filingLens.llm.prompt import build_system_prompt, join_names, short_name
from filingLens.llm.tools import tool_schemas, validate_call
from filingLens.nlu.rules import _norm, month_quarter

ALL_COMPANIES = re.compile(r"\b(all|three|3|teeno|tino|teenon|sab|sabhi|saari|every|each|"
                           r"companies|kampaniyon|kampaniyan)\b", re.IGNORECASE)

FALLBACK = ("Sorry, I could not turn that into a data request. Try naming the company, the "
            "metric and the year, e.g. 'TCS revenue FY24' or 'Infosys net margin last 2 years'.")


@dataclass
class AskResult:
    question: str
    tool: str | None = None
    args: dict[str, Any] | None = None            # validated, normalised
    result: ToolResult | None = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    replies: list[LLMReply] = field(default_factory=list, repr=False)

    @property
    def ok(self) -> bool:
        """True when the model produced a valid tool call (the result may still be empty)."""
        return self.tool is not None


def _assistant_turn(reply: LLMReply) -> dict:
    """Echo the model's reply into the conversation so a correction can refer to it."""
    calls = [c for c in reply.tool_calls if c.id]
    if calls:
        return {"role": "assistant", "content": reply.content or None, "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": c.raw or json.dumps(c.arguments)}}
            for c in calls]}
    return {"role": "assistant", "content": reply.content or "(no tool call)"}


def _correction(reply: LLMReply, error: str) -> dict:
    msg = f"Error: {error}. Reply again with exactly one valid tool call."
    call = next((c for c in reply.tool_calls if c.id), None)
    if call:
        return {"role": "tool", "tool_call_id": call.id, "content": msg}
    return {"role": "user", "content": msg}


def ungrounded_tickers(question: str, tickers: list[str], ctx: Context) -> list[str]:
    """Tickers in the call that the question never names (by ticker, name or alias).
    Small models swap an uncovered company ("Wipro") for a covered one; this catches it.
    Questions about "all"/"teeno"/"sab" companies may use any covered ticker."""
    if ALL_COMPANIES.search(question):
        return []
    q = question.lower()
    named = {t for t, c in ctx.registry.companies.items()
             if any(re.search(rf"\b{re.escape(a)}\b", q)
                    for a in (t.lower(), c.name.lower(), *c.aliases))}
    return [t for t in tickers if t not in named]


def fix_month_quarter(question: str, tool: str, args: Any) -> tuple[Any, str | None]:
    """Models get "September 2026 quarter" wrong (Q3, Q1, FY26...). The quarter a month ends is
    fixed by the calendar, so code decides it and the model's guess is overridden."""
    mq = month_quarter(_norm(question))
    if tool != "get_value" or mq is None or (args.fy, args.quarter) == mq:
        return args, None
    note = (f"'{question}' means {mq[0]} {mq[1]} (Indian fiscal year April-March); "
            f"the model asked for {args.fy} {args.quarter or ''}".rstrip())
    return args.model_copy(update={"fy": mq[0], "quarter": mq[1]}), note


def loaded_names(ctx: Context) -> list[str]:
    """Names of the covered companies that have figures (all covered ones when none do yet)."""
    have = {r[0] for r in ctx.con.execute("SELECT DISTINCT ticker FROM fact")}
    names = [c.name for t, c in ctx.registry.companies.items() if t in have]
    return names or [c.name for c in ctx.registry.companies.values()]


def _tickers(args: dict) -> list[str]:
    return list(args.get("tickers") or ([args["ticker"]] if args.get("ticker") else []))


def ask(question: str, llm: LLMClient, ctx: Context, max_attempts: int = 2,
        system_prompt: str | None = None) -> AskResult:
    t0 = time.monotonic()
    reg = ctx.registry
    loaded = loaded_names(ctx)
    system = system_prompt or build_system_prompt(reg, ctx.latest_fy(), loaded=loaded)
    messages: list[dict] = [{"role": "system", "content": system},
                            {"role": "user", "content": question}]
    tools = tool_schemas(reg)
    out = AskResult(question)
    for attempt in range(1, max_attempts + 1):
        out.attempts = attempt
        try:
            reply = llm.chat(messages, tools)
        except Exception as e:                      # noqa: BLE001 - network/server errors
            out.errors.append(f"LLM request failed: {type(e).__name__}: {e}")
            break
        out.replies.append(reply)
        if reply.error or not reply.tool_calls:
            err = reply.error or "no tool call in the reply"
        elif len(reply.tool_calls) > 1:
            err = f"{len(reply.tool_calls)} tool calls; exactly one is allowed"
        else:
            call = reply.tool_calls[0]
            try:
                args = validate_call(call.name, call.arguments, reg)
            except ValueError as e:
                err = str(e)
            else:
                bad = ungrounded_tickers(question, _tickers(args.model_dump()), ctx)
                if bad:
                    err = (f"the user did not name {', '.join(bad)}. Use only companies named "
                           "in the question; if they named a company not in the list, call "
                           "ask_user")
                else:
                    args, fixed = fix_month_quarter(question, call.name, args)
                    out.tool, out.args = call.name, args.model_dump()
                    try:
                        out.result = execute(call.name, args, ctx)
                    except Exception as e:      # noqa: BLE001 - report, never crash the caller
                        out.errors.append(f"tool {call.name} failed: {type(e).__name__}: {e}")
                        out.result = ToolResult(call.name, "the data lookup failed",
                                                notes=[f"{type(e).__name__}: {e}"])
                    if fixed:
                        out.result.notes.insert(0, fixed)
                    break
        out.errors.append(err)
        messages += [_assistant_turn(reply), _correction(reply, err)]
    if out.result is None:
        if any("did not name" in e for e in out.errors):
            names = join_names([short_name(n) for n in loaded], "and")
            out.result = ToolResult("ask_user", "company not covered", clarify=(
                f"I only have data for {names}. Which of these would you like?"))
        else:
            out.result = ToolResult("ask_user", "could not understand the question",
                                    clarify=FALLBACK)
    out.seconds = round(time.monotonic() - t0, 2)
    return out
