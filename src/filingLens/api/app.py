"""HTTP API over the same AnswerService and tools the CLI uses. Run: ``filinglens serve``.

    POST /ask                      question -> AnswerView (written answer + data view)
    GET  /value   /trend  /compare  /search    direct data, no LLM: DataView (table, chart...)
    GET  /companies  /metrics  /health
    POST /auth/register /auth/login /auth/logout   GET /auth/me    accounts (see api.auth)
    GET  /admin/companies  /admin/companies/{symbol}/nse    admin only (see api.admin)
    GET  /                         the web UI (web/dist), when it has been built

Every data route needs a signed-in user (session cookie); /health, the auth routes and the
web UI's files are public.

Every response is a Pydantic model (see filingLens.views); /docs has an interactive page and
/openapi.json the schema a frontend can generate types from. One Postgres connection serves
the requests, serialised with a lock; downloads ingest on a connection of their own.
"""
from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from filingLens.analytics.derived import DERIVED
from filingLens.api.admin import add_admin_routes
from filingLens.api.auth import add_auth_routes, make_auth
from filingLens.api.embeddings import add_embedding_routes
from filingLens.companies import DEFAULT_LIST_PATH, registry_with_overlay
from filingLens.domain.fiscal import fy_int
from filingLens.domain.registry import Registry
from filingLens.llm.client import LLMClient, LLMConfig, make_client
from filingLens.llm.executor import execute
from filingLens.llm.tools import validate_call
from filingLens.nlu.followup import Previous
from filingLens.service import AnswerService, make_context, make_guard
from filingLens.storage.db import Database, connect
from filingLens.storage.objects import ObjectStore
from filingLens.views import AnswerView, DataView, answer_view, data_view, metric_label

Scope = Literal["consolidated", "standalone"]
# the built web UI (cd web && npm run build); served at / when present
WEB_DIST = Path(os.environ.get("FILINGLENS_WEB", Path(__file__).resolve().parents[3] / "web" / "dist"))
DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:5173"


class PreviousTurn(BaseModel):
    question: str                        # the previous answer's ``question`` (as answered)
    tool: str | None = None              # its ``tool``: "ask_user" when it asked back


class AskRequest(BaseModel):
    question: str = Field(min_length=1, examples=["TCS vs Infosys revenue growth FY23-FY26"])
    use_llm: bool = True                 # false: rule-based understanding only (fast, free)
    write_answer: bool = True            # false: data only, no written summary
    previous: PreviousTurn | None = None  # the turn before: short follow-ups are read with it


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


class State:
    """The registry and what is built from it. ``reload()`` swaps all three after a company is
    added (Admin -> Data -> Download), so no restart is needed."""

    def __init__(self, con: Database, vectors: Path, equity_list: Path,
                 registry: Registry | None) -> None:
        self.con, self.vectors, self.equity_list = con, vectors, equity_list
        self._fixed = registry                       # tests pass a registry; never reloaded
        self.llm_paused: str | None = None           # why the chat model is unavailable
        self.reload()

    def reload(self) -> None:
        reg = self._fixed or registry_with_overlay()
        self.reg, self.ctx = reg, make_context(self.con, self.vectors, reg)
        self.guard = make_guard(reg, self.equity_list)     # "HCL Tech is not loaded"


def create_app(database_url: str | None = None, vectors: str | Path | None = None,
               llm: LLMClient | None | Literal["env"] = "env", warm: bool = False,
               registry: Registry | None = None,
               equity_list: str | Path = DEFAULT_LIST_PATH,
               web_dir: str | Path | None = WEB_DIST,
               data_dir: str | Path = "work",
               store: ObjectStore | None | Literal["env"] = "env") -> FastAPI:
    """``database_url``: Postgres (default FILINGLENS_DATABASE_URL); ``llm="env"`` reads the
    LLM settings from the environment / .env; ``warm`` loads the embedding model at startup
    instead of on the first text question; ``data_dir`` holds the inbox and the vector file;
    ``store`` is the S3 bucket downloaded filings are kept in (``"env"``: FILINGLENS_S3_*)."""
    data_dir = Path(data_dir)
    vectors = Path(vectors) if vectors else data_dir / "vectors.npy"
    con = connect(database_url)
    st = State(con, vectors, Path(equity_list), registry)
    client = _llm_from_env() if llm == "env" else llm
    objects = ObjectStore.from_env() if store == "env" else store
    llm_label = None
    if client is not None:
        cfg = getattr(client, "cfg", None)
        llm_label = f"{cfg.model} @ {cfg.base_url}" if cfg else getattr(client, "model", "llm")
    lock = threading.Lock()
    auth = make_auth(database_url)
    signed_in = [Depends(auth.current_user)]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if warm and vectors.exists() and st.ctx.searcher:
            st.ctx.searcher()
        await auth.init()
        yield
        await auth.close()
        con.close()

    app = FastAPI(title="filingLens", version="0.1.0", lifespan=lifespan,
                  description="Verified financial data and annual-report answers for "
                              "NSE-listed companies.")
    origins = os.environ.get("FILINGLENS_CORS", DEFAULT_ORIGINS).split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins if o.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    add_auth_routes(app, auth)
    app.state.downloads = add_admin_routes(app, auth.require_role("admin"), st, lock,
                                           data_dir / "inbox", objects,
                                           database_url=database_url)
    app.state.embeddings = add_embedding_routes(app, auth.require_role("admin"), st, lock,
                                                database_url=database_url)

    def run_tool(tool: str, params: dict[str, Any]) -> DataView:
        params = {k: v for k, v in params.items() if v not in (None, [])}
        try:
            args = validate_call(tool, params, st.reg)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        with lock:
            res = execute(tool, args, st.ctx)
        return data_view(res, args.model_dump(), st.reg)

    @app.post("/ask", response_model=AnswerView, dependencies=signed_in)
    def ask(req: AskRequest) -> AnswerView:
        paused = st.llm_paused
        reason = ("use_llm=false" if not req.use_llm else f"paused: {paused}" if paused
                  else "not configured")
        use = client if req.use_llm and not paused else None
        svc = AnswerService(st.ctx, use, req.write_answer, reason, st.guard)
        with lock:
            prev = Previous(req.previous.question, req.previous.tool) if req.previous else None
            r = svc.answer(req.question, prev)
        return answer_view(r, st.reg)

    @app.get("/value", response_model=DataView, dependencies=signed_in)
    def value(ticker: str, metric: str, fy: str = "latest",
              quarter: Literal["Q1", "Q2", "Q3", "Q4"] | None = None,
              scope: Scope = "consolidated") -> DataView:
        return run_tool("get_value", {"ticker": ticker, "metric": metric, "fy": fy,
                                      "quarter": quarter, "scope": scope})

    @app.get("/trend", response_model=DataView, dependencies=signed_in)
    def trend(ticker: str, metric: str, last_n_years: int | None = None,
              from_fy: str | None = None, to_fy: str | None = None,
              period: Literal["FY", "Q"] = "FY", scope: Scope = "consolidated") -> DataView:
        return run_tool("get_trend", {"ticker": ticker, "metric": metric,
                                      "last_n_years": last_n_years, "from_fy": from_fy,
                                      "to_fy": to_fy, "period": period, "scope": scope})

    @app.get("/compare", response_model=DataView, dependencies=signed_in)
    def compare(tickers: Annotated[list[str], Query()], metrics: Annotated[list[str], Query()],
                fys: Annotated[list[str] | None, Query()] = None,
                last_n_years: int | None = None,
                normalise: Literal["none", "index100", "share"] = "none",
                scope: Scope = "consolidated") -> DataView:
        return run_tool("compare", {"tickers": tickers, "metrics": metrics, "fys": fys,
                                    "last_n_years": last_n_years, "normalise": normalise,
                                    "scope": scope})

    @app.get("/search", response_model=DataView, dependencies=signed_in)
    def search(q: str, tickers: Annotated[list[str] | None, Query()] = None,
               fy: str | None = None, section: str | None = None, k: int = 5) -> DataView:
        return run_tool("search_reports", {"query": q, "tickers": tickers, "fy": fy,
                                           "section": section, "k": k})

    @app.get("/companies", response_model=list[CompanyInfo], dependencies=signed_in)
    def companies() -> list[CompanyInfo]:
        with lock:
            return [_company(t, st.reg, con) for t in sorted(st.reg.companies)]

    @app.get("/metrics", response_model=list[MetricInfo], dependencies=signed_in)
    def metrics() -> list[MetricInfo]:
        out = [MetricInfo(id=m.metric_id, label=m.desc, kind=m.kind,  # type: ignore[arg-type]
                          unit="INR per share" if m.unit_kind == "per_share" else "INR crore")
               for m in st.reg.metrics.values()]
        return out + [MetricInfo(id=k, label=metric_label(k, st.reg), kind="ratio", unit="%")
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
