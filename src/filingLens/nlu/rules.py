"""Rule-based planner: question -> the same tool call the LLM would make, for simple questions.

Used when no LLM is configured or it fails. Everything comes from registry.yaml (+ the company
overlay): tickers, names and aliases for companies; metric ids and synonyms (incl. Hinglish:
bikri, munafa, ...) for metrics; plus fixed patterns for years, scope and question type.

    "TCS revenue FY24"                -> get_value(TCS, revenue_from_operations, FY24)
    "Infosys net margin trend"        -> get_trend(INFY, net_margin)
    "HUL ki bikri pichle 3 saal"      -> get_trend(HINDUNILVR, revenue_from_operations, 3 years)
    "TCS vs Infosys revenue growth"   -> compare([TCS, INFY], revenue, index100)
    "Q3 FY24 revenue of TCS"          -> get_value(TCS, revenue, FY24, Q3)

Returns None when the question has no recognisable metric (the caller searches the reports
instead) and a clarifying ask_user when a metric is found but no company.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from filingLens.analytics.derived import DERIVED
from filingLens.domain.registry import Registry

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "ek": 1, "do": 2, "teen": 3, "tin": 3,
                "chaar": 4, "char": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7}
# extra phrases for derived metrics (registry synonyms cover stored metrics)
DERIVED_PHRASES = {
    "net margin": "net_margin", "net profit margin": "net_margin", "profit margin": "net_margin",
    "margin": "net_margin", "pat margin": "net_margin",
    "pbt margin": "pbt_margin", "pre-tax margin": "pbt_margin", "pretax margin": "pbt_margin",
    "effective tax rate": "effective_tax_rate", "tax rate": "effective_tax_rate",
    "employee cost ratio": "employee_cost_ratio", "staff cost ratio": "employee_cost_ratio",
}
COMPARE_WORDS = r"\b(vs|versus|compare|comparison|compared|tulna|against|between)\b"
GROWTH_WORDS = r"\b(growth|grew|grow|growing|faster|badha|badhi|badhe|increase[sd]?)\b"
TREND_WORDS = r"\b(trend|trends|history|historical|over the years|over time|years|saal|sal|since)\b"
ALL_WORDS = r"\b(all|teeno|tino|teenon|sab|sabhi|saari)\b"


@dataclass
class RulePlan:
    tool: str
    args: dict
    found: dict = field(default_factory=dict)       # what was recognised, for notes/debugging
    notes: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^\w\s\-'.&]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _find_phrases(text: str, phrases: dict[str, str]) -> list[str]:
    """Values of the phrases found in text, longest phrases first, no overlapping matches,
    returned in order of appearance."""
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for ph in sorted(phrases, key=len, reverse=True):
        for m in re.finditer(rf"(?<![\w]){re.escape(ph)}(?![\w])", text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), phrases[ph]))
    return list(dict.fromkeys(v for _, v in sorted(hits)))


def find_companies(text: str, reg: Registry) -> list[str]:
    if re.search(ALL_WORDS, text) and re.search(r"\b(compan\w*|kampani\w*|three|teeno|tino|all)\b", text):
        return list(reg.companies)
    phrases: dict[str, str] = {}
    for t, c in reg.companies.items():
        for a in (t.lower(), c.name.lower(), *c.aliases):
            if len(a) >= 2:
                phrases.setdefault(a, t)
    return _find_phrases(text, phrases)


def find_metrics(text: str, reg: Registry) -> list[str]:
    phrases = dict(DERIVED_PHRASES)
    for name in DERIVED:
        phrases.setdefault(name.replace("_", " "), name)
    for mid, m in reg.metrics.items():
        phrases.setdefault(mid.replace("_", " "), mid)
        for s in m.synonyms:
            phrases.setdefault(s.lower(), mid)
    return _find_phrases(text, phrases)


def find_years(text: str) -> dict:
    """{"fys": [...], "last_n": int, "latest": bool, "quarter": "Q3"}; only what is present."""
    out: dict = {}
    fys = []
    for m in re.finditer(r"\bfy\s?'?(\d{4}|\d{2})\b|\b(20\d{2})\s?[-/]\s?(\d{4}|\d{2})\b", text):
        yy = (m.group(1) or m.group(3))[-2:]
        fys.append(f"FY{int(yy):02d}")
    if fys:
        out["fys"] = list(dict.fromkeys(fys))
    num = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
    span = re.search(rf"\b(?:last|past|pichle|pichhle|previous)\s+{num}\s+(?:years?|yrs?|saal|sal|fy)\b",
                     text) or re.search(rf"\b{num}\s+(?:years?|saal|sal)\b", text)
    if span:
        w = span.group(1)
        out["last_n"] = int(w) if w.isdigit() else NUMBER_WORDS[w]
    if re.search(r"\b(latest|this year|current|recent|is saal|last year)\b", text):
        out["latest"] = True
    q = re.search(r"\bq([1-4])\b", text)
    if q:
        out["quarter"] = f"Q{q.group(1)}"
    return out


def plan(question: str, reg: Registry) -> RulePlan | None:
    text = _norm(question)
    companies = find_companies(text, reg)
    metrics = find_metrics(text, reg)
    years = find_years(text)
    scope = "standalone" if re.search(r"\bstandalone\b", text) else "consolidated"
    found = {"companies": companies, "metrics": metrics, "years": years, "scope": scope}
    if not metrics:
        return None                                      # caller searches the reports
    if not companies:
        names = ", ".join(c.name for c in reg.companies.values())
        return RulePlan("ask_user", {"question": f"Which company? I have data for {names}."},
                        found)
    growth = bool(re.search(GROWTH_WORDS, text))
    notes: list[str] = []
    fys = years.get("fys", [])

    if len(companies) > 1 or len(metrics) > 1 or re.search(COMPARE_WORDS, text):
        if len(companies) > 1 and len(metrics) > 1:
            notes.append(f"several companies and metrics: comparing {metrics[0]} only")
            metrics = metrics[:1]
        args: dict = {"tickers": companies, "metrics": metrics, "scope": scope}
        if len(companies) > 1 and growth and not fys:
            args["normalise"] = "index100"
        if fys:
            args["fys"] = fys
        elif years.get("last_n"):
            args["last_n_years"] = years["last_n"]
        elif years.get("latest") and not growth:
            args["fys"] = ["latest"]
        return RulePlan("compare", args, found, notes)

    base = {"ticker": companies[0], "metric": metrics[0], "scope": scope}
    if years.get("quarter") and len(fys) <= 1:
        return RulePlan("get_value", {**base, "fy": fys[0] if fys else "latest",
                                      "quarter": years["quarter"]}, found)
    if len(fys) >= 2:
        s = sorted(fys, key=lambda f: int(f[2:]))
        return RulePlan("get_trend", {**base, "from_fy": s[0], "to_fy": s[-1]}, found)
    if years.get("last_n"):
        return RulePlan("get_trend", {**base, "last_n_years": years["last_n"]}, found)
    if len(fys) == 1 and not re.search(r"\b(since|from)\b", text):
        return RulePlan("get_value", {**base, "fy": fys[0]}, found)
    if len(fys) == 1:                                         # "since FY23"
        return RulePlan("get_trend", {**base, "from_fy": fys[0]}, found)
    if growth or re.search(TREND_WORDS, text):
        return RulePlan("get_trend", base, found)
    return RulePlan("get_value", {**base, "fy": "latest"}, found,
                    ["no year given: showing the latest"])
