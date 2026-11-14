"""HTTP API over the same AnswerService and tools the CLI uses. Run: ``filinglens serve``.

    POST /ask                      question -> AnswerView (written answer + data view)
    GET  /value   /trend  /compare  /search    direct data, no LLM: DataView (table, chart...)
    GET  /companies  /metrics  /health
    GET  /                         the web UI (web/dist), when it has been built

Every response is a Pydantic model (see filingLens.views); /docs has an interactive page and
/openapi.json the schema a frontend can generate types from. One SQLite connection is shared
and access to it is serialised with a lock (a small single-user service).
"""
from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from filingLens.analytics.derived import DERIVED
from filingLens.companies import DEFAULT_LIST_PATH, registry_with_overlay
from filingLens.domain.fiscal import fy_int
from filingLens.domain.registry import Registry
from filingLens.llm.client import LLMClient, LLMConfig, make_client
from filingLens.llm.executor import execute
from filingLens.llm.tools import validate_call
from filingLens.service import AnswerService, make_context, make_guard
from filingLens.storage.db import connect
from filingLens.views import AnswerView, DataView, answer_view, data_view, metric_label

Scope = Literal["consolidated", "standalone"]
# the built web UI (cd web && npm run build); served at / when present
WEB_DIST = Path(os.environ.get("FILINGLENS_WEB", Path(__file__).resolve().parents[3] / "web" / "dist"))
DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:5173"


class AskRequest(BaseModel):
    question: str = Field(min_length=2, examples=["TCS vs Infosys revenue growth FY23-FY26"])
    use_llm: bool = True                 # false: rule-based understanding only (fast, free)
    write_answer: bool = True            # false: data only, no written summary


class CompanyInfo(BaseModel):
    ticker: str
    name: str
    sector: str
    fys: list[str]                       # full years with data, oldest first
    latest_fy: str | None
    latest_quarter: str | None           # "FY27 Q2"
    annual_reports: list[str]            # FYs of loaded annual-report PDFs (text search)
    has_data: bool


class MetricInfo(BaseModel):
    id: str
    label: str
    kind: Literal["flow", "stock", "ratio"]  # flow: over a period; stock: balance sheet date
    unit: str


class Health(BaseModel):
    status: Literal["ok"]
    facts: int
    companies_with_data: int
    chunks: int
    vector_index: bool
    llm: str | None                      # "model @ base_url", or null when not configured


def _llm_from_env() -> LLMClient | None:
    cfg = LLMConfig.from_env()
    return None if cfg.missing() else make_client(cfg)


def create_app(db: str | Path = "work/filingLens.db", vectors: str | Path | None = None,
               llm: LLMClient | None | Literal["env"] = "env", warm: bool = False,
               registry: Registry | None = None,
               equity_list: str | Path = DEFAULT_LIST_PATH,
               web_dir: str | Path | None = WEB_DIST) -> FastAPI:
    """``llm="env"`` reads the LLM settings from the environment / .env; ``warm`` loads the
    embedding model at startup instead of on the first text question."""
    db = Path(db)
    vectors = Path(vectors) if vectors else db.parent / "vectors.npy"
    reg = registry or registry_with_overlay()
    con = connect(db, threads=True)
    ctx = make_context(con, vectors, reg)
    client = _llm_from_env() if llm == "env" else llm
    guard = make_guard(reg, Path(equity_list))     # "HCL Tech is not loaded"
    llm_label = None
    if client is not None:
        cfg = getattr(client, "cfg", None)
        llm_label = f"{cfg.model} @ {cfg.base_url}" if cfg else getattr(client, "model", "llm")
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if warm and vectors.exists() and ctx.searcher:
            ctx.searcher()
        yield
        con.close()

    app = FastAPI(title="filingLens", version="0.1.0", lifespan=lifespan,
                  description="Verified financial data and annual-report answers for "
                              "NSE-listed companies.")
    origins = os.environ.get("FILINGLENS_CORS", DEFAULT_ORIGINS).split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins if o.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    def run_tool(tool: str, params: dict[str, Any]) -> DataView:
        params = {k: v for k, v in params.items() if v not in (None, [])}
        try:
            args = validate_call(tool, params, reg)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        with lock:
            res = execute(tool, args, ctx)
        return data_view(res, args.model_dump(), reg)

    @app.post("/ask", response_model=AnswerView)
    def ask(req: AskRequest) -> AnswerView:
        reason = "use_llm=false" if not req.use_llm else "not configured"
        svc = AnswerService(ctx, client if req.use_llm else None, req.write_answer, reason,
                            guard)
        with lock:
            r = svc.answer(req.question)
        return answer_view(r, reg)

    @app.get("/value", response_model=DataView)
    def value(ticker: str, metric: str, fy: str = "latest",
              quarter: Literal["Q1", "Q2", "Q3", "Q4"] | None = None,
              scope: Scope = "consolidated") -> DataView:
        return run_tool("get_value", {"ticker": ticker, "metric": metric, "fy": fy,
                                      "quarter": quarter, "scope": scope})

    @app.get("/trend", response_model=DataView)
    def trend(ticker: str, metric: str, last_n_years: int | None = None,
              from_fy: str | None = None, to_fy: str | None = None,
              period: Literal["FY", "Q"] = "FY", scope: Scope = "consolidated") -> DataView:
        return run_tool("get_trend", {"ticker": ticker, "metric": metric,
                                      "last_n_years": last_n_years, "from_fy": from_fy,
                                      "to_fy": to_fy, "period": period, "scope": scope})

    @app.get("/compare", response_model=DataView)
    def compare(tickers: Annotated[list[str], Query()], metrics: Annotated[list[str], Query()],
                fys: Annotated[list[str] | None, Query()] = None,
                last_n_years: int | None = None,
                normalise: Literal["none", "index100", "share"] = "none",
                scope: Scope = "consolidated") -> DataView:
        return run_tool("compare", {"tickers": tickers, "metrics": metrics, "fys": fys,
                                    "last_n_years": last_n_years, "normalise": normalise,
                                    "scope": scope})

    @app.get("/search", response_model=DataView)
    def search(q: str, tickers: Annotated[list[str] | None, Query()] = None,
               fy: str | None = None, section: str | None = None, k: int = 5) -> DataView:
        return run_tool("search_reports", {"query": q, "tickers": tickers, "fy": fy,
                                           "section": section, "k": k})

    @app.get("/companies", response_model=list[CompanyInfo])
    def companies() -> list[CompanyInfo]:
        with lock:
            return [_company(t, reg, con) for t in sorted(reg.companies)]

    @app.get("/metrics", response_model=list[MetricInfo])
    def metrics() -> list[MetricInfo]:
        out = [MetricInfo(id=m.metric_id, label=m.desc, kind=m.kind,  # type: ignore[arg-type]
                          unit="INR per share" if m.unit_kind == "per_share" else "INR crore")
               for m in reg.metrics.values()]
        return out + [MetricInfo(id=k, label=metric_label(k, reg), kind="ratio", unit="%")
                      for k in DERIVED]

    @app.get("/health", response_model=Health)
    def health() -> Health:
        with lock:
            facts, cos = con.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM fact").fetchone()
            chunks = con.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
        return Health(status="ok", facts=facts, companies_with_data=cos, chunks=chunks,
                      vector_index=vectors.exists(), llm=llm_label)

    # the web UI last, so every API route above takes precedence over its files
    if web_dir and (Path(web_dir) / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


def _company(t: str, reg: Registry, con: Any) -> CompanyInfo:
    q = lambda sql: [r[0] for r in con.execute(sql, (t,)).fetchall()]
    fys = sorted(q("SELECT DISTINCT fy FROM fact WHERE ticker=? AND period_type='FY'"),
                 key=fy_int)
    quarters = [tuple(r) for r in con.execute(
        "SELECT DISTINCT fy, quarter FROM fact WHERE ticker=? AND period_type='Q'", (t,))]
    lq = max(quarters, key=lambda x: (fy_int(x[0]), x[1]), default=None)
    reports = sorted({r or "?" for r in q("SELECT fy FROM document WHERE ticker=? AND "
                                          "doc_type='annual_report'")})
    c = reg.companies[t]
    return CompanyInfo(ticker=t, name=c.name, sector=c.sector, fys=fys,
                       latest_fy=fys[-1] if fys else None,
                       latest_quarter=f"{lq[0]} {lq[1]}" if lq else None,
                       annual_reports=reports, has_data=bool(fys or quarters))
