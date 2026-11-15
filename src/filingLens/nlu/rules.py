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
# everyday phrases for stored metrics (registry synonyms are the formal names)
EXTRA_PHRASES = {
    "bottom line": "net_profit", "top line": "revenue_from_operations",
    "make from operations": "revenue_from_operations",
    "made from operations": "revenue_from_operations",
    "earn from operations": "revenue_from_operations",
    "staff cost": "employee_expense", "staff costs": "employee_expense",
    "staff expenses": "employee_expense", "salary bill": "employee_expense",
    "profitable": "net_margin", "profitability": "net_margin",
    "employee cost as a share of revenue": "employee_cost_ratio",
    "employee cost as a percentage of revenue": "employee_cost_ratio",
    "employee costs as a share of revenue": "employee_cost_ratio",
}
QUARTERLY = r"\b(quarterly|quarter-wise|quarter wise|every quarter|har quarter|by quarter)\b"
COMPARE_WORDS = r"\b(vs|versus|compare|comparison|compared|tulna|against|between)\b"
GROWTH_WORDS = r"\b(growth|grew|grow|growing|faster|badha|badhi|badhe|increase[sd]?)\b"
TREND_WORDS = (r"\b(trend|trends|history|historical|over the years|over time|years|saal|sal|"
               r"since|changed|evolved)\b")
ALL_WORDS = r"\b(all|teeno|tino|teenon|sab|sabhi|saari)\b"
MONTHS = {m: i for i, ms in enumerate(
    ["jan january", "feb february", "mar march", "apr april", "may", "jun june", "jul july",
     "aug august", "sep sept september", "oct october", "nov november", "dec december"], 1)
    for m in ms.split()}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
# "September 2026 quarter", "quarter ended 30 September 2026", "Dec'25 quarter", "Sep-26 qtr"
_MONTH_QUARTER = re.compile(
    rf"\b(?:(?:quarter|qtr|q)\s+(?:ended|ending)\s+(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?"
    rf"(?P<m1>{_MONTH})\.?\s*(?:\d{{1,2}}(?:st|nd|rd|th)?\s*)?'?\s*-?(?P<y1>20\d{{2}}|\d{{2}})\b"
    rf"|(?P<m2>{_MONTH})\.?\s*'?\s*-?(?P<y2>20\d{{2}}|\d{{2}})\s+(?:quarter|qtr)\b)")


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
    phrases = dict(DERIVED_PHRASES) | EXTRA_PHRASES
    for name in DERIVED:
        phrases.setdefault(name.replace("_", " "), name)
    for mid, m in reg.metrics.items():
        phrases.setdefault(mid.replace("_", " "), mid)
        for s in m.synonyms:
            phrases.setdefault(s.lower(), mid)
    return _find_phrases(text, phrases)


def month_quarter(text: str) -> tuple[str, str] | None:
    """Quarter named by its last month -> (FY, quarter) of the Indian fiscal year (April-March):
    "September 2026 quarter" -> ("FY27", "Q2"); "quarter ended March 2025" -> ("FY25", "Q4").
    The month must end a quarter (Jun, Sep, Dec, Mar); ``text`` is lowercase."""
    m = _MONTH_QUARTER.search(text.lower().replace("’", "'"))
    if not m:
        return None
    month = MONTHS[(m.group("m1") or m.group("m2"))]
    year = int((m.group("y1") or m.group("y2"))[-2:])
    if month % 3:
        return None
    fy = year if month <= 3 else year + 1
    return f"FY{fy % 100:02d}", f"Q{(month - 4) % 12 // 3 + 1}"


def find_years(text: str) -> dict:
    """{"fys": [...], "last_n": int, "latest": bool, "quarter": "Q3"}; only what is present."""
    out: dict = {}
    fys = []
    for m in re.finditer(r"\bfy\s?'?(\d{4}|\d{2})\b|\b(20\d{2})\s?[-/]\s?(\d{4}|\d{2})\b", text):
        yy = (m.group(1) or m.group(3))[-2:]
        fys.append(f"FY{int(yy):02d}")
        # "fy 23 to 24", "FY23-24", "fy23 se 24 tak": the bare second year is an FY too
        if m.group(1):
            end = re.match(r"\s*(?:to|till|until|-|–|se|and|vs)\s*(?:fy\s?'?)?(\d{2}|20\d{2})\b",
                           text[m.end():])
            if end:
                fys.append(f"FY{int(end.group(1)[-2:]):02d}")
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
    if mq := month_quarter(text):                    # the calendar year is not an FY
        out["fys"], out["quarter"] = [mq[0]], mq[1]
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
    if re.search(QUARTERLY, text) and not fys:
        return RulePlan("get_trend", {**base, "period": "Q"}, found)
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


# words that are never a company name on their own (for companies.CompanyGuard)
QUESTION_WORDS = frozenset("""
show tell give list find get compare comparison vs versus between against which who whose what
how why when much many more most less least higher lower highest lowest best worst better worse
than over under last past previous next latest current recent year years quarter quarterly
annual yearly trend trends growth grew grow growing faster slower increase decrease rise fall
change changes share ratio margin rate total net gross value data report reports annual
consolidated standalone please about same each every all both three teeno tino sab sabhi saari
kya hai tha thi the kitna kitni kitne kiska kiski kiske kaun kaunsi kaisa kaise kyun mein me ka
ki ke ko se aur ya pichle pichhle saal sal badha badhi badhe zyada kam sabse tulna batao dikhao
india indian inr crore crores lakh rupees rs percent first second third fourth one two
company companies firm group business money make made earn earned earnings income sales
profit profits loss revenue revenues cost costs expense expenses tax assets equity eps
""".split())


def question_words(reg: Registry) -> frozenset[str]:
    from filingLens.retrieval.search import STOPWORDS

    words = set(QUESTION_WORDS) | set(STOPWORDS) | set(NUMBER_WORDS) | set(MONTHS)
    phrases = [*DERIVED_PHRASES, *DERIVED, *reg.metrics]
    for m in reg.metrics.values():
        phrases += [*m.synonyms, m.desc]
    for ph in phrases:
        words.update(re.findall(r"[a-z0-9]+", ph.lower().replace("_", " ")))
    return frozenset(words)
