"""The tools the LLM may call: JSON schemas (built from registry.yaml, so the allowed tickers and
metrics always match the data) and validation of the arguments it sends back.

Validation is forgiving where the meaning is unambiguous ("Infosys" -> INFY, "profit" ->
net_profit, "2023-24" -> FY24) and strict otherwise; the error text is sent back to the model.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from filingLens.analytics.derived import DERIVED
from filingLens.domain.registry import Registry

SCOPES = ("consolidated", "standalone")
SECTIONS = ("mdna", "directors_report", "notice", "corporate_governance", "brsr",
            "auditor_report", "financial_statements", "sustainability", "front_matter")


# ---- normalisation (uses the registry passed as validation context) ---------------------------
def norm_ticker(v: Any, reg: Registry) -> str:
    s = str(v).strip()
    if s.upper() in reg.companies:
        return s.upper()
    low = s.lower()
    for t, c in reg.companies.items():
        if low == c.name.lower() or low in c.aliases:
            return t
    raise ValueError(f"unknown company {s!r}; use one of {sorted(reg.companies)}")


def metric_names(reg: Registry) -> list[str]:
    return [*reg.metrics, *DERIVED]


def norm_metric(v: Any, reg: Registry) -> str:
    s = str(v).strip().lower().replace(" ", "_")
    if s in reg.metrics or s in DERIVED:
        return s
    phrase = s.replace("_", " ")
    for mid, m in reg.metrics.items():
        if phrase in m.synonyms:
            return mid
    raise ValueError(f"unknown metric {v!r}; use one of {metric_names(reg)}")


def norm_fy(v: Any) -> str:
    s = str(v).strip().upper().replace(" ", "")
    if s in ("LATEST", "CURRENT"):
        return "latest"
    if m := re.fullmatch(r"FY(\d{2}|\d{4})", s):
        return f"FY{m.group(1)[-2:]}"
    if m := re.fullmatch(r"(\d{4})[-/](\d{2}|\d{4})", s):          # 2023-24 -> FY24
        return f"FY{m.group(2)[-2:]}"
    raise ValueError(f"bad fiscal year {v!r}; use FY24 style or 'latest'")


def _reg(info: ValidationInfo) -> Registry:
    assert info.context and "registry" in info.context, "validate with context={'registry': ...}"
    return info.context["registry"]


# ---- argument models ----------------------------------------------------------------------------
class _Base(BaseModel):
    model_config = {"extra": "ignore"}


class GetValue(_Base):
    ticker: str
    metric: str
    fy: str = "latest"
    quarter: Literal["Q1", "Q2", "Q3", "Q4"] | None = None
    scope: Literal["consolidated", "standalone"] = "consolidated"

    _t = field_validator("ticker")(lambda cls, v, info: norm_ticker(v, _reg(info)))
    _m = field_validator("metric")(lambda cls, v, info: norm_metric(v, _reg(info)))
    _f = field_validator("fy")(lambda cls, v: norm_fy(v))


class GetTrend(_Base):
    ticker: str
    metric: str
    last_n_years: int | None = Field(None, ge=1, le=15)
    from_fy: str | None = None
    to_fy: str | None = None
    period: Literal["FY", "Q"] = "FY"
    scope: Literal["consolidated", "standalone"] = "consolidated"

    _t = field_validator("ticker")(lambda cls, v, info: norm_ticker(v, _reg(info)))
    _m = field_validator("metric")(lambda cls, v, info: norm_metric(v, _reg(info)))
    _f = field_validator("from_fy", "to_fy")(lambda cls, v: None if v is None else norm_fy(v))


class Compare(_Base):
    tickers: list[str] = Field(min_length=1, max_length=5)
    metrics: list[str] = Field(min_length=1, max_length=5)
    fys: list[str] = Field(default_factory=list)
    last_n_years: int | None = Field(None, ge=1, le=15)
    normalise: Literal["none", "index100", "share"] = "none"
    scope: Literal["consolidated", "standalone"] = "consolidated"

    _t = field_validator("tickers")(lambda cls, v, info: [norm_ticker(x, _reg(info)) for x in v])
    _m = field_validator("metrics")(lambda cls, v, info: [norm_metric(x, _reg(info)) for x in v])
    _f = field_validator("fys")(lambda cls, v: [norm_fy(x) for x in v])

    @model_validator(mode="after")
    def _one_axis(self) -> Compare:
        if len(self.tickers) > 1 and len(self.metrics) > 1:
            raise ValueError("compare several companies on ONE metric, or several metrics for "
                             "ONE company; make separate calls otherwise")
        return self


class SearchReports(_Base):
    query: str = Field(min_length=2)
    tickers: list[str] = Field(default_factory=list)
    fy: str | None = None
    section: Literal[SECTIONS] | None = None  # type: ignore[valid-type]
    k: int = Field(5, ge=1, le=10)

    _t = field_validator("tickers")(lambda cls, v, info: [norm_ticker(x, _reg(info)) for x in v])
    _f = field_validator("fy")(lambda cls, v: None if v is None else norm_fy(v))


class AskUser(_Base):
    question: str = Field(min_length=3)


ARGS: dict[str, type[_Base]] = {"get_value": GetValue, "get_trend": GetTrend,
                                "compare": Compare, "search_reports": SearchReports,
                                "ask_user": AskUser}


def validate_call(name: str, arguments: dict[str, Any], reg: Registry) -> _Base:
    """Parsed, normalised arguments; raises ValueError with a message meant for the model."""
    if name not in ARGS:
        raise ValueError(f"unknown tool {name!r}; use one of {list(ARGS)}")
    try:
        return ARGS[name].model_validate(arguments, context={"registry": reg})
    except ValidationError as e:
        msgs = [f"{'.'.join(map(str, err['loc'])) or name}: {err['msg']}" for err in e.errors()]
        raise ValueError(f"invalid arguments for {name}: " + "; ".join(msgs)) from None


# ---- JSON schemas shown to the model ---------------------------------------------------------------
def tool_schemas(reg: Registry) -> list[dict]:
    tickers = sorted(reg.companies)
    metrics = metric_names(reg)
    fy = {"type": "string", "description": "fiscal year like FY24 (Apr 2023-Mar 2024), or 'latest'"}
    scope = {"type": "string", "enum": list(SCOPES), "default": "consolidated"}
    t1 = {"type": "string", "enum": tickers}
    m1 = {"type": "string", "enum": metrics}

    def fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props, "required": required,
            "additionalProperties": False}}}

    return [
        fn("get_value", "One number for one company, metric and year (or quarter).",
           {"ticker": t1, "metric": m1, "fy": fy,
            "quarter": {"type": "string", "enum": ["Q1", "Q2", "Q3", "Q4"]}, "scope": scope},
           ["ticker", "metric"]),
        fn("get_trend", "One metric of one company over several years (or quarters), with growth.",
           {"ticker": t1, "metric": m1,
            "last_n_years": {"type": "integer", "minimum": 1, "maximum": 15},
            "from_fy": fy, "to_fy": fy, "period": {"type": "string", "enum": ["FY", "Q"]},
            "scope": scope}, ["ticker", "metric"]),
        fn("compare", "Several companies on ONE metric, or several metrics of ONE company.",
           {"tickers": {"type": "array", "items": t1, "minItems": 1},
            "metrics": {"type": "array", "items": m1, "minItems": 1},
            "fys": {"type": "array", "items": fy},
            "last_n_years": {"type": "integer", "minimum": 1, "maximum": 15},
            "normalise": {"type": "string", "enum": ["none", "index100", "share"],
                          "description": "index100 = growth from a common base; share = % of total"},
            "scope": scope}, ["tickers", "metrics"]),
        fn("search_reports", "Search annual-report text for qualitative questions (strategy, "
           "risks, people, ESG, governance, explanations).",
           {"query": {"type": "string", "description": "short English search phrase"},
            "tickers": {"type": "array", "items": t1}, "fy": fy,
            "section": {"type": "string", "enum": list(SECTIONS)},
            "k": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
        fn("ask_user", "Ask a short clarifying question when the company or what is wanted is "
           "genuinely unclear, or the company is not covered.",
           {"question": {"type": "string"}}, ["question"]),
    ]
