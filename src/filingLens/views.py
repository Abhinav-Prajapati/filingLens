"""Typed, frontend-ready views of answers and data (Pydantic, so the API publishes a JSON schema
at /openapi.json and a frontend can generate types from it).

Every data result becomes a ``DataView``:

    table    columns (with a type and unit each) + rows of raw numbers + display strings
    chart    a suggested chart: type, unit and one ``Series`` of (period, value) points per line
    stats    headline figures: the value, CAGR, change from first to last period
    passages report text with page sources (text questions)
    sources  where each number came from: XBRL tag, filing, annual-report page

Conventions: amounts are INR crore, per-share figures INR, ratios %; every change is in % (YoY,
change vs first, CAGR) except changes of ratios, which are percentage points ("pp").
Missing values are null, never filled in.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from filingLens.analytics.derived import DERIVED
from filingLens.domain.registry import Registry
from filingLens.llm.answer import _cell
from filingLens.llm.executor import ToolResult

ColumnType = Literal["period", "number", "percent", "change", "text", "integer"]
Cell = str | float | int | None


class Column(BaseModel):
    key: str
    label: str
    type: ColumnType
    unit: str | None = None          # "INR crore", "INR", "%", "pp", "index (first = 100)"


class Table(BaseModel):
    columns: list[Column]
    rows: list[dict[str, Cell]]                # raw values (changes already in % or pp)
    display: list[dict[str, str]]              # same rows formatted: "2,40,893", "+6.85%"


class Point(BaseModel):
    period: str                                # "FY24" or "FY24 Q3"; the x axis
    value: float | None                        # null = missing (draw a gap)


class Series(BaseModel):
    key: str                                   # column key in the table
    label: str                                 # legend text
    ticker: str | None = None
    metric: str | None = None
    points: list[Point]


class Chart(BaseModel):
    type: Literal["line", "bar", "grouped_bar"]
    title: str
    unit: str
    y_label: str
    series: list[Series]


class Stat(BaseModel):
    kind: Literal["value", "cagr", "change", "change_pp"]
    label: str                                 # "TCS CAGR FY23-FY26"
    value: float | None
    unit: str
    display: str
    series: str | None = None
    period_from: str | None = None
    period_to: str | None = None


class Source(BaseModel):
    kind: Literal["xbrl", "derived", "report"]
    label: str                                 # "TCS FY24 revenue_from_operations · AR p.82"
    ticker: str | None = None
    metric: str | None = None
    period: str | None = None
    xbrl_tag: str | None = None
    doc_id: str | None = None
    page: int | None = None                    # annual-report page (PDF page number)
    section: str | None = None
    verified: str | None = None                # "pdf": value found on that page


class Passage(BaseModel):
    rank: int
    ticker: str | None = None
    page: int | None = None
    section: str | None = None
    source: str
    text: str


class DataView(BaseModel):
    tool: str
    title: str
    unit: str = ""
    table: Table | None = None
    chart: Chart | None = None
    stats: list[Stat] = Field(default_factory=list)
    passages: list[Passage] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    clarify: str | None = None                 # the system asks the user back


class AnswerView(BaseModel):
    question: str
    ok: bool
    mode: Literal["llm", "rules", "search"]    # what understood the question
    tool: str | None = None
    args: dict[str, Any] | None = None
    answer: str | None = None                  # written answer (checked); null = show data
    answer_rejected: str | None = None         # why a model-written answer was not used
    data: DataView | None = None
    notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    seconds: float = 0.0


# ---- building views ------------------------------------------------------------------------
def metric_label(metric: str, reg: Registry) -> str:
    if metric in reg.metrics:
        return reg.metrics[metric].desc
    return DERIVED[metric].desc if metric in DERIVED else metric


def _series_label(key: str, reg: Registry) -> str:
    return key if key in reg.companies else metric_label(key, reg)


_CHANGE_COLS = {"yoy": "YoY", "change_vs_first": "Change vs first", "change_pp": "Change"}


def _column(key: str, res: ToolResult, reg: Registry) -> Column:
    if key == "period":
        return Column(key=key, label="Period", type="period")
    if key in ("yoy", "change_vs_first"):
        return Column(key=key, label=_CHANGE_COLS[key], type="change", unit="%")
    if key == "change_pp":
        return Column(key=key, label=_CHANGE_COLS[key], type="change", unit="pp")
    if key in ("ticker", "metric", "source", "text"):
        return Column(key=key, label=key.capitalize(), type="text")
    if key == "rank":
        return Column(key=key, label="#", type="integer")
    pct = res.unit in ("%", "% of total")
    label = "Value" if key == "value" else _series_label(key, reg)
    return Column(key=key, label=label, type="percent" if pct else "number", unit=res.unit)


def _raw(key: str, v: Any) -> Cell:
    if isinstance(v, float) and key in ("yoy", "change_vs_first"):
        return v * 100                          # fractions -> %
    return v


def _table(res: ToolResult, reg: Registry) -> Table:
    cols = [_column(c, res, reg) for c in res.columns]
    return Table(columns=cols,
                 rows=[{c: _raw(c, r.get(c)) for c in res.columns} for r in res.rows],
                 display=[{c: _cell(c, r.get(c), res.unit) for c in res.columns}
                          for r in res.rows])


def _chart(res: ToolResult, table: Table, args: dict[str, Any], reg: Registry) -> Chart | None:
    value_cols = [c for c in table.columns if c.type in ("number", "percent")]
    if res.tool not in ("get_trend", "compare") or not value_cols or not table.rows:
        return None
    metrics = args.get("metrics") or [args.get("metric")]
    ticker = args.get("ticker") or (args.get("tickers") or [None])[0]

    def series(c: Column) -> Series:
        is_co = c.key in reg.companies
        t = c.key if is_co else ticker
        m = metrics[0] if is_co or c.key == "value" else c.key
        return Series(key=c.key, label=c.label if c.key != "value" else str(t), ticker=t,
                      metric=m, points=[Point(period=str(r["period"]), value=_num(r.get(c.key)))
                                        for r in table.rows])

    lines = [series(c) for c in value_cols]
    kind: Literal["line", "bar", "grouped_bar"] = (
        "line" if len(table.rows) > 6 else ("grouped_bar" if len(lines) > 1 else "bar"))
    if res.tool == "get_trend" and value_cols[0].type == "percent":
        kind = "line"                            # a ratio over time reads best as a line
    y = metric_label(metrics[0], reg) if len(metrics) == 1 and metrics[0] else "Value"
    return Chart(type=kind, title=res.title, unit=res.unit, y_label=y, series=lines)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _stats(res: ToolResult, args: dict[str, Any]) -> list[Stat]:
    out = []
    if res.tool == "get_value" and res.rows:
        r = res.rows[0]
        out.append(Stat(kind="value", label=res.title, value=_num(r["value"]), unit=res.unit,
                        display=_cell("value", r["value"], res.unit), series=r.get("ticker"),
                        period_to=r.get("period")))
    for s in res.stats:
        span = f"{s['from']}-{s['to']}"
        if s["kind"] == "cagr":
            out.append(Stat(kind="cagr", label=f"{s['series']} CAGR {span}", value=s["value"],
                            unit="%", display=f"{s['value']:+.2f}%", series=s["series"],
                            period_from=s["from"], period_to=s["to"]))
        elif s["kind"] == "change_pp":
            out.append(Stat(kind="change_pp", label=f"{s['series']} change {span}",
                            value=s["value"], unit="pp", display=f"{s['value']:+.2f} pp",
                            series=s["series"], period_from=s["from"], period_to=s["to"]))
        else:
            out.append(Stat(kind="change", label=f"{s['series']} change {span}",
                            value=s["value"], unit="%", display=f"{s['value']:+.2f}%",
                            series=s["series"], period_from=s["from"], period_to=s["to"]))
            if s.get("cagr") is not None:
                out.append(Stat(kind="cagr", label=f"{s['series']} CAGR {span}",
                                value=s["cagr"], unit="%", display=f"{s['cagr']:+.2f}%",
                                series=s["series"], period_from=s["from"], period_to=s["to"]))
    return out


def _sources(res: ToolResult) -> list[Source]:
    out = []
    for c in res.citations:
        page = c.get("page")
        if res.tool == "search_reports":
            out.append(Source(kind="report", ticker=c.get("ticker"), page=page,
                              section=c.get("section"), doc_id=c.get("doc_id"),
                              label=f"{c.get('ticker')} annual report p.{page}"
                                    + (f" ({c['section']})" if c.get("section") else "")))
            continue
        label = f"{c.get('ticker')} {c.get('period')} {c.get('metric')}"
        label += f" · annual report p.{page}" if page else f" · XBRL {c.get('xbrl_tag')}"
        out.append(Source(kind="derived" if c.get("method") == "derived" else "xbrl",
                          label=label, ticker=c.get("ticker"), metric=c.get("metric"),
                          period=c.get("period"), xbrl_tag=c.get("xbrl_tag"),
                          doc_id=c.get("doc_id"), page=page, verified=c.get("verified")))
    return out


def data_view(res: ToolResult, args: dict[str, Any] | None, reg: Registry) -> DataView:
    args = args or {}
    v = DataView(tool=res.tool, title=res.title, unit=res.unit, notes=list(res.notes),
                 clarify=res.clarify, sources=_sources(res))
    if res.tool == "search_reports":
        v.passages = [Passage(rank=r["rank"], ticker=c.get("ticker"), page=c.get("page"),
                              section=c.get("section"), source=r["source"], text=r["text"])
                      for r, c in zip(res.rows, res.citations, strict=True)]
        return v
    if res.rows:
        v.table = _table(res, reg)
        v.chart = _chart(res, v.table, args, reg)
    v.stats = _stats(res, args)
    return v


def answer_view(r: Any, reg: Registry) -> AnswerView:
    """``r``: a service.Response."""
    return AnswerView(question=r.question, ok=r.ok or bool(r.result and r.result.clarify),
                      mode=r.mode, tool=r.tool, args=r.args, answer=r.answer.text,
                      answer_rejected=r.answer.rejected,
                      data=data_view(r.result, r.args, reg) if r.result else None,
                      notes=list(r.notes), errors=list(r.errors), seconds=r.seconds)
