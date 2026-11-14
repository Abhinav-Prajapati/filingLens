"""Second LLM step: write a short answer from a tool result, then check it.

* Text questions (search_reports): answer ONLY from the retrieved passages, citing pages
  like [p.44]. Numbers must be copied from the passages; cited pages must be ones retrieved.
* Data questions (get_value / get_trend / compare): a 1-2 sentence summary of the table,
  using only the table's numbers (rounding allowed).

Anything that fails a check is rejected and the caller shows the table or passages instead,
so a fabricated number never reaches the user.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from filingLens.domain.units import fmt_change, fmt_value
from filingLens.llm.client import LLMClient
from filingLens.llm.executor import ToolResult

NOT_FOUND = "NOT_IN_SOURCES"
_NUM = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")
_CITE = re.compile(r"\[p\.?\s*(\d+)(?:\s*,\s*p?\.?\s*(\d+))*\]", re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_FY = re.compile(r"\bFY\s?'?(\d{2})\b", re.IGNORECASE)
SMALL_INT = 12      # whole numbers up to this are counts/months ("12 months"), not figures

SEARCH_SYSTEM = f"""You answer questions about Indian companies using ONLY the passages given,
which come from their annual reports.
Rules:
- Use only facts stated in the passages. Do not use outside knowledge.
- After each sentence, cite the page it comes from in square brackets, e.g. [p.44].
- Copy numbers exactly as they appear in the passages.
- Write 2 to 5 sentences. Answer in the language of the question (English or Hinglish).
- If the passages do not answer the question, reply with exactly: {NOT_FOUND}"""

DATA_SYSTEM = """You summarise a small table of verified financial data in 1 or 2 sentences.
Rules:
- Use only numbers that appear in the table, copied exactly (you may round a percentage).
- State what the numbers show (levels, growth, which is higher). Do not give reasons or
  causes, and do not add any outside facts.
- Answer in the language of the question (English or Hinglish)."""


@dataclass
class Answer:
    text: str | None = None              # None: show the table / passages instead
    used_llm: bool = False
    rejected: str | None = None          # why the model's answer was not used
    pages: list[int] = field(default_factory=list)


# ---- evidence given to the model ----------------------------------------------------------
def _cell(col: str, v: object, unit: str) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and col in ("yoy", "change_vs_first"):
        return fmt_change(v)
    if isinstance(v, float) and col == "change_pp":
        return fmt_change(v, pp=True)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return fmt_value(float(v), "%" if unit in ("%", "% of total") else unit)
    return str(v)


def evidence(res: ToolResult) -> str | None:
    """The tool result as plain text for the model; None when there is nothing to summarise."""
    if res.clarify or not res.rows:
        return None
    if res.tool == "search_reports":
        return "\n\n".join(f"[p.{c['page']}] {row['source']}\n{row['text']}"
                           for row, c in zip(res.rows, res.citations, strict=True))
    lines = [f"{res.title}, unit: {res.unit}", " | ".join(res.columns)]
    lines += [" | ".join(_cell(c, r.get(c), res.unit) for c in res.columns) for r in res.rows]
    lines += [f"note: {n}" for n in res.notes]
    return "\n".join(lines)


# ---- checks ----------------------------------------------------------------------------------
def _numbers(text: str) -> list[str]:
    return [m.group(0).rstrip(",.") for m in _NUM.finditer(text)]


def _year_forms(text: str) -> list[float]:
    """FY24 in the evidence allows 24, 2024 and 2023 in the answer (FY 24, 2023-24)."""
    out: list[float] = []
    for yy in {int(m.group(1)) for m in _FY.finditer(text)}:
        out += [yy, 2000 + yy, 2000 + yy - 1]
    return out


def _matches(a: str, pool: list[float]) -> bool:
    """a (as written in the answer) equals some evidence number, allowing rounding.
    Small whole numbers (<= SMALL_INT) are always allowed."""
    try:
        x = float(a.replace(",", ""))
    except ValueError:
        return True
    dec = len(a.split(".")[1]) if "." in a else 0
    if dec == 0 and x <= SMALL_INT:
        return True
    return any(x == e or round(e, dec) == x for e in pool)


def unsupported_numbers(text: str, evidence_text: str, question: str = "") -> list[str]:
    """Numbers in text (citations ignored) that do not appear in the evidence or question,
    allowing rounding, year forms and small whole numbers."""
    pool = [float(n.replace(",", "")) for n in _numbers(evidence_text + " " + question)
            if n.replace(",", "").replace(".", "", 1).isdigit()]
    pool += _year_forms(evidence_text + " " + question)
    return [n for n in _numbers(_CITE.sub("", text)) if not _matches(n, pool)]


def check_answer(text: str, evidence_text: str, question: str, pages: set[int],
                 need_citations: bool) -> str | None:
    """Reason to reject, or None if the answer passes."""
    cited = {int(p) for m in _CITE.finditer(text) for p in m.groups() if p}
    if need_citations and not cited:
        return "no page citations"
    if cited - pages:
        return f"cites pages that were not retrieved: {sorted(cited - pages)}"
    bad = unsupported_numbers(text, evidence_text, question)
    if bad:
        return f"numbers not in the sources: {bad}"
    return None


# ---- the call ------------------------------------------------------------------------------
def compose_answer(question: str, res: ToolResult, llm: LLMClient) -> Answer:
    ev = evidence(res)
    if ev is None:
        return Answer()
    is_text = res.tool == "search_reports"
    pages = {int(c["page"]) for c in res.citations if c.get("page")}
    messages = [{"role": "system", "content": SEARCH_SYSTEM if is_text else DATA_SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\n"
                                            f"{'Passages' if is_text else 'Table'}:\n{ev}"}]
    try:
        reply = llm.chat(messages, [])
    except Exception as e:                       # noqa: BLE001 - fall back to raw evidence
        return Answer(used_llm=True, rejected=f"LLM request failed: {type(e).__name__}")
    text = _THINK.sub("", reply.content or "").strip()
    if not text or NOT_FOUND in text:
        return Answer(used_llm=True, rejected="the sources do not answer the question")
    reason = check_answer(text, ev, question, pages, need_citations=is_text)
    if reason:
        return Answer(used_llm=True, rejected=reason)
    cited = sorted({int(p) for m in _CITE.finditer(text) for p in m.groups() if p})
    return Answer(text, used_llm=True, pages=cited)
