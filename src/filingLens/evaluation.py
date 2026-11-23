"""Evaluation harness: run a fixed set of questions through the real AnswerService and score
the responses. See evals/questions.yaml for the format.

Per question: right tool, right arguments, right values in the result, right page cited,
correct refusal, no number when data is missing, written answer contains the key facts, and
no fabricated numbers in the written answer (re-checked here, independently of the answer
step's own guard). A question passes when every check that applies to it passes.

Runs are saved as JSON (work/eval/<timestamp>_<mode>.json) so runs can be compared.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from filingLens.llm.answer import evidence, unsupported_numbers
from filingLens.service import AnswerService, Response
from filingLens.trace import TraceRepo, start_trace, step

DEFAULT_CASES = Path("evals/questions.yaml")
CHECKS = ("tool", "args", "values", "pages", "refuse", "no_value", "answer")


@dataclass
class Case:
    id: str
    q: str
    expect: dict[str, Any]
    tags: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    id: str
    q: str
    tags: list[str]
    mode: str                                   # path that answered: llm | rules | search
    tool: str | None
    args: dict[str, Any] | None
    checks: dict[str, bool | None]              # None = not applicable
    fabricated: list[str] = field(default_factory=list)
    answer: str | None = None
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    trace_id: str | None = None                 # Admin -> Traces, when traces are kept

    @property
    def passed(self) -> bool:
        return not self.fabricated and all(v for v in self.checks.values() if v is not None)


def load_cases(path: Path = DEFAULT_CASES) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Case(c["id"], c["q"], c.get("expect", {}), c.get("tags", [])) for c in raw]


# ---- checks -----------------------------------------------------------------------------------
def _same(expected: Any, actual: Any) -> bool:
    if isinstance(actual, list):                        # tickers / metrics / fys: same set
        return isinstance(expected, list) and set(map(str, expected)) == set(map(str, actual))
    if isinstance(expected, list):                      # scalar: one of the alternatives
        return actual in expected
    return bool(expected == actual)


def args_match(expected: dict | list[dict], actual: dict | None) -> bool:
    if actual is None:
        return False
    options = expected if isinstance(expected, list) else [expected]
    return any(all(_same(v, actual.get(k)) for k, v in opt.items()) for opt in options)


def _numbers_in(rows: list[dict]) -> list[float]:
    return [float(v) for r in rows for v in r.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def value_found(expected: float, numbers: list[float]) -> bool:
    text = repr(expected)
    dec = len(text.split(".")[1]) if "." in text and not text.endswith(".0") else 0
    return any(round(n, dec) == round(float(expected), dec) for n in numbers)


def score(case: Case, r: Response) -> CaseResult:
    e, res = case.expect, r.result
    rows = res.rows if res else []
    checks: dict[str, bool | None] = dict.fromkeys(CHECKS)
    refused = bool(res and res.clarify)
    if "tool" in e:
        tools = e["tool"] if isinstance(e["tool"], list) else [e["tool"]]
        checks["tool"] = r.tool in tools
    if "args" in e:
        checks["args"] = bool(checks.get("tool") is not False and args_match(e["args"], r.args))
    if "values" in e:
        nums = _numbers_in(rows)
        checks["values"] = all(value_found(v, nums) for v in e["values"])
    if "pages" in e or ("ticker" in e and r.tool == "search_reports"):
        cites = [c for c in (res.citations if res else [])
                 if "ticker" not in e or c.get("ticker") == e["ticker"]]
        checks["pages"] = bool(cites) and ("pages" not in e or any(
            c.get("page") in e["pages"] for c in cites))
    if e.get("refuse"):
        checks["refuse"] = refused
    if e.get("no_value"):
        checks["no_value"] = not any(v is not None for v in _numbers_in(rows))
    if e.get("answer_contains") and (r.mode == "llm" or r.answer.used_llm):
        text = (r.answer.text or "").lower()
        checks["answer"] = bool(text) and all(s.lower() in text for s in e["answer_contains"])
    fabricated = (unsupported_numbers(r.answer.text, evidence(res) or "", case.q)
                  if r.answer.text and res else [])
    return CaseResult(case.id, case.q, case.tags, r.mode, r.tool, r.args, checks, fabricated,
                      r.answer.text, list(r.errors), r.seconds)


# ---- running and reporting -------------------------------------------------------------------
def run(cases: list[Case], service: AnswerService,
        progress: Callable[[int, CaseResult], None] | None = None,
        traces: TraceRepo | None = None, run_id: str | None = None) -> list[CaseResult]:
    """``traces``: keep one trace per question (source "eval", ``run_id``), so a failed case
    can be opened in Admin -> Traces to see which step went wrong."""
    out = []
    for i, c in enumerate(cases, 1):
        t0 = time.monotonic()
        with contextlib.ExitStack() as stack:
            t = stack.enter_context(start_trace(c.q, source="eval", eval_run=run_id)) \
                if traces is not None else None
            try:
                res = score(c, service.answer(c.q))
            except Exception as e:                  # noqa: BLE001 - one bad case must not stop a run
                res = CaseResult(c.id, c.q, c.tags, "error", None, None, {"tool": False},
                                 errors=[f"{type(e).__name__}: {e}"])
                if t is not None:
                    t.set(outcome="failed", error=res.errors[0])
            if t is not None:
                with step("eval", "eval") as st:
                    st.detail.update(case=c.id, passed=res.passed, checks=res.checks,
                                     fabricated=res.fabricated)
                    st.status = "ok" if res.passed else "error"
                res.trace_id = t.id
        if traces is not None and t is not None:
            traces.save(t)
        res.seconds = round(time.monotonic() - t0, 2)
        out.append(res)
        if progress:
            progress(i, res)
    return out


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    def rate(name: str) -> dict[str, Any]:
        vals = [r.checks.get(name) for r in results if r.checks.get(name) is not None]
        return {"ok": sum(bool(v) for v in vals), "n": len(vals)}

    tags: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        for t in r.tags:
            tags[t].append(r.passed)
    return {
        "questions": len(results),
        "passed": sum(r.passed for r in results),
        "checks": {name: rate(name) for name in CHECKS},
        "fabricated_numbers": sum(len(r.fabricated) for r in results),
        "written_answers": sum(r.answer is not None for r in results),
        "answered_by": {m: sum(r.mode == m for r in results)
                        for m in sorted({r.mode for r in results})},
        "avg_seconds": round(sum(r.seconds for r in results) / max(len(results), 1), 2),
        "by_tag": {t: {"ok": sum(v), "n": len(v)} for t, v in sorted(tags.items())},
    }


def save(results: list[CaseResult], summary: dict, meta: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}_{meta.get('mode', 'run')}.json"
    path.write_text(json.dumps({"meta": meta, "summary": summary,
                                "results": [asdict(r) | {"passed": r.passed} for r in results]},
                               indent=1, default=str, ensure_ascii=False))
    return path


def previous_summary(out_dir: Path, mode: str, before: Path | None = None) -> dict | None:
    runs = sorted(p for p in out_dir.glob(f"*_{mode}.json") if p != before)
    return json.loads(runs[-1].read_text())["summary"] if runs else None
