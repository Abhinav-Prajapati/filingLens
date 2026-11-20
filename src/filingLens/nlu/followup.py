"""Follow-ups: read a short message together with the previous question.

    previous answer asked back ("Which company?")  + "Wipro"            -> answer to it
    "TCS revenue FY24"                             + "and Infosys?"     -> compare both
    "TCS revenue FY24"                             + "what about FY25"  -> both years

    "TCS net profit FY24"                          + "what was the reason for that growth"
                                                                        -> why-question about it

Short messages (up to 6 words) are joined; so is a longer one that names no company but points
back ("that", "it", "its", "uska"...) or asks why. Never joined: complete questions of their own
("Infosys net margin FY25") and ones naming a different company than the previous question
("tcs growth reason" after a Titan question). The answer says how it was read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from filingLens.domain.registry import Registry
from filingLens.nlu.rules import _norm, find_companies, find_metrics, find_years

MAX_WORDS = 6
MAX_REFER_WORDS = 20               # "what was the reason for this increase in profit vs last year"
# words that only make sense with something said before
REFER = re.compile(r"\b(that|this|these|those|it|its|they|them|their|same|above|"
                   r"uska|uski|uske|iska|iski|iske|unka|unki|unke|inka|inki|inke|wahi|wohi|yahi)\b")
WHY = re.compile(r"\b(why|reason|reasons|explain|kyun|kyon|kyu)\b")
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
    t = _norm(text)
    words = len(t.split())
    companies = find_companies(t, reg)
    pointing = _norm(re.sub(r"\bIT\b", " ", text))          # "IT industry" is not "it"
    refers = not companies and bool(REFER.search(pointing) or WHY.search(t))
    if words > (MAX_REFER_WORDS if refers else MAX_WORDS):
        return text, None
    metrics, years = find_metrics(t, reg), find_years(t)
    if companies and metrics:
        return text, None                                # a complete question of its own
    before = find_companies(_norm(previous.question), reg)
    if companies and before and set(companies) != set(before) and not FOLLOW.match(t):
        return text, None             # another company, no "and"/"what about": a new question
    if previous.tool == "ask_user":
        return (f"{previous.question.strip()} ({reply})",
                f"read as your answer to the question asked: “{previous.question.strip()}”")
    if not (companies or metrics or years.get("fys") or years.get("last_n")
            or FOLLOW.match(t) or refers):
        return text, None                                # "thanks", "ok"...
    return (f"{previous.question.strip()}, {reply}",
            f"read together with your previous question: “{previous.question.strip()}”")
