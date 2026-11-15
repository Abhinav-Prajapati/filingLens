"""Follow-ups: read a short message together with the previous question.

    previous answer asked back ("Which company?")  + "Wipro"            -> answer to it
    "TCS revenue FY24"                             + "and Infosys?"     -> compare both
    "TCS revenue FY24"                             + "what about FY25"  -> both years

Only short messages (up to 6 words) that are not complete questions on their own are joined:
"Infosys net margin FY25" after anything is a new question. The answer says how it was read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from filingLens.domain.registry import Registry
from filingLens.nlu.rules import _norm, find_companies, find_metrics, find_years

MAX_WORDS = 6
FOLLOW = re.compile(r"^(and|also|what about|how about|same for|same|now|then|aur|or|for|in|"
                    r"with|vs|versus|only|just)\b")


@dataclass(frozen=True)
class Previous:
    question: str                  # the previous question as it was answered
    tool: str | None = None        # "ask_user" when the previous answer asked back


def combine(previous: Previous | None, text: str, reg: Registry) -> tuple[str, str | None]:
    """(question to answer, note) — the note says the message was read with the previous one;
    None when the message stands on its own."""
    if previous is None or not previous.question.strip() or not text.strip():
        return text, None
    reply = text.strip().rstrip("?.! ")
    if previous.tool == "ask_user":
        return (f"{previous.question.strip()} ({reply})",
                f"read as your answer to the question asked: “{previous.question.strip()}”")
    t = _norm(text)
    if len(t.split()) > MAX_WORDS:
        return text, None
    companies, metrics, years = find_companies(t, reg), find_metrics(t, reg), find_years(t)
    if companies and metrics:
        return text, None                                # a complete question of its own
    if not (companies or metrics or years.get("fys") or years.get("last_n")
            or FOLLOW.match(t)):
        return text, None                                # "thanks", "ok"...
    return (f"{previous.question.strip()}, {reply}",
            f"read together with your previous question: “{previous.question.strip()}”")
