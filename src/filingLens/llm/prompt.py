"""System prompt for the question -> tool-call step. Built from registry.yaml (companies,
aliases, metrics, synonyms) and llm/examples.yaml, so it never lists something the data lacks."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from filingLens.analytics.derived import DERIVED
from filingLens.domain.registry import Registry
from filingLens.llm.tools import SECTIONS

EXAMPLES_PATH = Path(__file__).with_name("examples.yaml")

HINGLISH = {
    "revenue / sales": "aay, bikri, kamai, revenue",
    "profit": "munafa, nafa, profit",
    "expenses": "kharcha",
    "how much": "kitna, kitni, kitne",
    "last N years": "pichle N saal (ek=1, do=2, teen=3, chaar=4, paanch=5)",
    "compare": "compare karo, tulna, vs, ya",
    "which / whose": "kaun sa, kiska, kiski",
    "grew fastest": "sabse tez badha",
    "in / of": "mein, ka, ki, ke",
}


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _call(tool: str, args: dict) -> str:
    return json.dumps({"name": tool, "arguments": args}, ensure_ascii=False)


# well-known listed companies used in the "not covered" examples: the first ones the registry
# does not cover are picked, so an example never claims a loaded company is missing
UNCOVERED_CANDIDATES = ("Reliance", "HCL Tech", "Maruti", "ITC", "Bajaj Finance", "Titan",
                        "Asian Paints", "Larsen & Toubro", "Sun Pharma", "Wipro", "Infosys")


def join_names(names: list[str], last: str = "or") -> str:
    """["A"] -> "A"; ["A", "B", "C"] -> "A, B or C"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {last} {names[-1]}"


def uncovered(reg: Registry, n: int = 2) -> list[str]:
    known = {a for t, c in reg.companies.items() for a in (t.lower(), c.name.lower(), *c.aliases)}
    out = [c for c in UNCOVERED_CANDIDATES
           if not any(w in known for w in (c.lower(), c.lower().split()[0]))]
    return (out + ["Acme Industries", "Example Corp"])[:n]


def short_name(name: str) -> str:
    """"Wipro Limited" -> "Wipro"; "Tata Consultancy Services" stays."""
    return re.sub(r"\s+(limited|ltd\.?)$", "", name, flags=re.IGNORECASE).strip()


def fill_examples(examples: list[dict], loaded: list[str], reg: Registry) -> list[dict]:
    """Replace {loaded}, {loaded_and}, {uncovered1}, {uncovered2} in the examples."""
    u1, u2 = uncovered(reg)
    short = [short_name(n) for n in loaded]
    either, both = join_names(short) or "none yet", join_names(short, "and") or "none yet"
    sub = lambda v: (v.replace("{loaded_and}", both).replace("{loaded}", either)
                     .replace("{uncovered1}", u1).replace("{uncovered2}", u2)
                     ) if isinstance(v, str) else v
    return [{**e, "q": sub(e["q"]), "args": {k: sub(v) for k, v in e["args"].items()}}
            for e in examples]


def build_system_prompt(reg: Registry, latest_fy: str | None,
                        examples: list[dict] | None = None,
                        loaded: list[str] | None = None) -> str:
    """``loaded``: names of the companies that have data (default: every registered one)."""
    loaded = loaded if loaded is not None else [c.name for c in reg.companies.values()]
    examples = fill_examples(load_examples() if examples is None else examples, loaded, reg)
    companies = "\n".join(f"- {t}: {c.name} (also: {', '.join(c.aliases)})"
                          for t, c in reg.companies.items())
    metrics = "\n".join(f"- {mid}: {m.desc} (also: {', '.join(m.synonyms[:6])})"
                        for mid, m in reg.metrics.items())
    derived = "\n".join(f"- {name}: {f.desc} = {f.numerator} / {f.denominator}, in %"
                        for name, f in DERIVED.items())
    glossary = "\n".join(f"- {en}: {hi}" for en, hi in HINGLISH.items())
    shots = "\n".join(f"Q: {e['q']}\nA: {_call(e['tool'], e['args'])}" for e in examples)
    return f"""You turn questions about Indian listed companies' financial filings into exactly ONE tool call.
You never answer with numbers or facts yourself: the tool fetches verified data from filings.
Questions may be in English, Hindi or Hinglish (Hindi written in Latin letters).

COMPANIES (use the ticker):
{companies}

STORED METRICS (use the id):
{metrics}

DERIVED METRICS (ratios computed from stored metrics):
{derived}

TEXT SECTIONS for search_reports: {', '.join(SECTIONS)}

FISCAL YEARS: Indian fiscal year April-March. FY24 = 1 Apr 2023 to 31 Mar 2024; "2023-24" = FY24.
Latest year with data: {latest_fy or 'unknown'}. "this year", "latest", "current" -> fy "latest".
Quarters: Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar.

HINGLISH WORDS:
{glossary}

RULES:
1. Call exactly one tool. Use get_value for one number, get_trend for one metric over time,
   compare for several companies on one metric OR several metrics of one company,
   search_reports for qualitative questions (why, how, strategy, risks, people, ESG, governance).
2. Use only the tickers and metric ids listed above. "profit" alone means net_profit;
   "sales", "turnover" and "income from operations" mean revenue_from_operations; "margin"
   alone means net_margin.
3. Scope is consolidated unless the question says standalone.
4. If the company is missing, or not in the list (e.g. {', '.join(uncovered(reg))}), call
   ask_user. Never substitute a listed company for one the user did not name. Companies
   with data: {join_names([short_name(n) for n in loaded], 'and') or 'none yet'}.
5. "growth", "grew", "badha", "faster" when comparing companies: compare with
   normalise "index100" and no fys (growth needs several years).
6. search_reports query: a short English phrase with the key terms, even for Hinglish questions.
7. If you cannot call tools natively, reply with ONLY this JSON and nothing else:
   {{"name": "<tool>", "arguments": {{...}}}}

EXAMPLES:
{shots}
"""
