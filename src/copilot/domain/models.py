"""Core typed contracts shared by every layer. No I/O, no dependencies on other layers."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """What the user wants done with a question. The executor routes on this value."""

    LOOKUP = "lookup"      # one value: "TCS revenue FY24"
    TREND = "trend"        # one series over time
    COMPARE = "compare"    # across companies, metrics or periods (see CompareAxis)
    DERIVED = "derived"    # computed figure: growth, margin, ratio
    TEXT_QA = "text_qa"    # answer from annual-report text, not from facts
    CLARIFY = "clarify"    # question is missing or ambiguous; ask the user back


class CompareAxis(str, Enum):
    """What varies in a COMPARE intent; everything else is held fixed."""

    COMPANIES = "companies"  # same metric and period, different tickers
    METRICS = "metrics"      # same ticker and period, different metrics
    PERIODS = "periods"      # same ticker and metric, different periods


class PeriodSpec(BaseModel):
    """Time range the user asked for. Holds the intent ('last 5 years');
    code resolves it to concrete FYs using the latest data loaded."""

    kind: Literal["explicit", "last_n", "latest", "all"] = "all"  # how to read the fields below
    fy_from: str | None = None   # e.g. "FY22" (explicit)
    fy_to: str | None = None     # e.g. "FY24" (explicit)
    n: int | None = None         # number of periods (last_n)
    period_type: Literal["FY", "Q"] = "FY"  # annual or quarterly


class QueryPlan(BaseModel):
    """Structured form of one question, produced by the planner (rules or LLM) and
    validated here. The executor works only from this, never from raw text."""

    intent: Intent
    tickers: list[str] = Field(default_factory=list)   # registry tickers, e.g. ["TCS"]
    metrics: list[str] = Field(default_factory=list)   # registry metric ids
    period: PeriodSpec = Field(default_factory=PeriodSpec)
    scope: Literal["consolidated", "standalone"] = "consolidated"
    compare_axis: CompareAxis | None = None            # only set for COMPARE
    transform: Literal["none", "yoy", "cagr", "margin", "index100", "share"] = "none"
    chart_type: Literal["bar", "line", "table", "none"] = "none"
    text_query: str | None = None       # search text for TEXT_QA
    clarification: str | None = None    # question to ask the user for CLARIFY
    assumptions: list[str] = Field(default_factory=list)  # defaults applied, shown to the user
    confidence: float = 1.0             # planner's certainty; low values trigger the LLM fallback
    language: Literal["en", "hinglish", "hi"] = "en"


class Fact(BaseModel):
    """One verified number from a filing, with everything needed to cite and audit it.
    Stored in the `fact` table."""

    ticker: str
    metric_id: str
    period_type: str  # Q, 6M, 9M, FY, or INSTANT (balance-sheet date)
    fy: str           # fiscal year label, e.g. "FY24"
    quarter: str | None = None   # Q1..Q4, set only for quarterly facts
    period_start: str = ""       # empty for INSTANT facts
    period_end: str
    scope: str = "consolidated"
    value: float | None = None   # canonical: INR crore, or INR per share
    unit: str = ""
    orig_value: float            # number exactly as in the XBRL file, before unit conversion
    orig_unit: str = ""          # declared rounding of the filing, e.g. "Crores"
    xbrl_tag: str = ""           # source tag the value came from
    doc_id: str = ""             # sha256 of the source XBRL file
    pdf_doc_id: str | None = None  # annual report where the value was found
    page: int | None = None        # page in that report (the citation)
    verified: Literal["no", "pdf", "pdf_scope_mismatch"] = "no"  # cross-check against the PDF
    qa: Literal["ok", "warn", "fail"] = "ok"  # result of accounting checks
    method: str = "xbrl"         # how the fact was extracted


class Citation(BaseModel):
    """A source reference attached to an answer so the user can check it.
    `fact` citations point to a filing and page; `text` citations carry a snippet."""

    id: int                      # number shown next to the claim, e.g. [1]
    kind: Literal["fact", "text"]
    ticker: str | None = None
    period: str | None = None
    xbrl_file: str | None = None
    xbrl_tag: str | None = None
    pdf_file: str | None = None
    pdf_page: int | None = None
    verified: str | None = None  # copied from Fact.verified
    qa: str | None = None        # copied from Fact.qa
    snippet: str | None = None   # excerpt, for text citations


class ResultBundle(BaseModel):
    """Everything the executor found for one plan: data, sources and caveats.
    The presenter and narrator may only state numbers that appear here."""

    intent: Intent
    plan: QueryPlan                  # the plan that produced this result
    rows: list[dict] = Field(default_factory=list)       # table of values
    citations: list[Citation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)       # gaps, e.g. "FY23 missing (not interpolated)"
    assumptions: list[str] = Field(default_factory=list)
    confidence: Literal["high", "partial", "not_found", "evidence_only"] = "high"