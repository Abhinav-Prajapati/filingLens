"""Embedding annual-report text for semantic search, from the admin UI (role ``admin``).

    GET    /admin/embeddings        the current (or last) job, or null
    POST   /admin/embeddings        start a job: {"doc_ids": [...]} or {"tickers": [...]} or {}
                                    (= every report with passages not embedded yet), and
                                    "device": "auto" | "cpu" | "cuda"
    DELETE /admin/embeddings        stop after the current batch (finished reports are kept)

One job at a time, one report after another; the vector file is saved after each report, so a
stopped or crashed job keeps what it finished and the next run continues from there. When the
search model is already loaded (someone searched), it is reused rather than loaded twice: the
GPU is small and shared with a local LLM server. "auto" uses the GPU and falls back to the CPU
when the GPU is full. When the job ends the app reloads the index, so search uses it at once.
"""
from __future__ import annotations

import datetime as dt
import gc
import threading
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from filingLens.retrieval.embedder import BGE_M3, Embedder
from filingLens.retrieval.index import VectorIndex, embed_documents, pending_by_document
from filingLens.storage.db import connect

if TYPE_CHECKING:
    from filingLens.api.app import State

Device = Literal["auto", "cpu", "cuda"]
PROGRESS_BATCH = 16          # passages per progress step (a few seconds on a laptop CPU)


class EmbedItem(BaseModel):
    doc_id: str
    ticker: str
    fy: str | None
    passages: int
    done: int                            # passages with a vector
    state: Literal["waiting", "running", "done", "stopped", "failed"] = "waiting"


class EmbedJob(BaseModel):
    state: Literal["loading", "running", "done", "stopped", "failed"]
    device: str | None = None            # where the model runs: cuda / cpu
    model: str | None = None
    items: list[EmbedItem]
    embedded: int = 0                    # passages embedded by this job
    message: str = ""
    error: str | None = None
    started_at: str
    finished_at: str | None = None
    stop_requested: bool = False


class EmbedRequest(BaseModel):
    doc_ids: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    device: Device = "auto"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def index_model(st: State) -> str:
    idx = VectorIndex.load(st.vectors)
    return idx.model if idx is not None else BGE_M3


def default_embedder(model: str, device: Device) -> Embedder:
    from filingLens.retrieval.embedder import BgeM3Embedder, HashEmbedder

    if model.startswith("hash"):
        return HashEmbedder()
    return BgeM3Embedder(model=model, device=None if device == "auto" else device)


class Embeddings:
    def __init__(self, st: State, lock: threading.Lock, database_url: str | None,
                 factory: Callable[[str, Device], Embedder] = default_embedder):
        self.st, self.lock, self.database_url, self.factory = st, lock, database_url, factory
        self.job: EmbedJob | None = None
        self._guard = threading.Lock()

    @property
    def running(self) -> bool:
        return self.job is not None and self.job.state in ("loading", "running")

    def plan(self, req: EmbedRequest) -> list[EmbedItem]:
        """The reports to embed, oldest company/year first: the ones asked for that still
        have passages without a vector."""
        with self.lock:
            progress = pending_by_document(self.st.con, self.st.vectors, index_model(self.st))
            docs = {r[0]: (r[1], r[2]) for r in self.st.con.execute(
                "SELECT doc_id, ticker, fy FROM document WHERE doc_type='annual_report'")}
        want_docs, want_t = set(req.doc_ids), {t.upper() for t in req.tickers}
        items = []
        for doc_id, p in progress.items():
            if doc_id not in docs or p.done >= p.total:
                continue
            ticker, fy = docs[doc_id]
            if (want_docs and doc_id not in want_docs) or (want_t and ticker not in want_t):
                continue
            items.append(EmbedItem(doc_id=doc_id, ticker=ticker, fy=fy, passages=p.total,
                                   done=p.done))
        return sorted(items, key=lambda i: (i.ticker, i.fy or ""))

    def start(self, req: EmbedRequest) -> EmbedJob:
        with self._guard:
            if self.running:
                raise HTTPException(409, "An embedding job is already running")
            items = self.plan(req)
            if not items:
                raise HTTPException(400, "Nothing to embed: every selected report is done")
            self.job = EmbedJob(state="loading", items=items, started_at=_now(),
                                message="Loading the embedding model")
            threading.Thread(target=self._run, args=(self.job, req.device), daemon=True,
                             name="embeddings").start()
            return self.job

    def stop(self) -> EmbedJob:
        if not self.running or self.job is None:
            raise HTTPException(409, "No embedding job is running")
        self.job.stop_requested = True
        self.job.message = "Stopping after the current batch"
        return self.job

    def _shared(self, model: str, device: Device) -> Embedder | None:
        """The search model, when the app has already loaded it (same model and device)."""
        searcher: Any = self.st.ctx.searcher           # functools.cache: loaded at most once
        if searcher is None or not hasattr(searcher, "cache_info") \
                or searcher.cache_info().currsize == 0:
            return None
        emb = getattr(searcher(), "embedder", None)
        if emb is None or emb.name != model:
            return None
        dev = getattr(emb, "device", "cpu")
        return emb if device == "auto" or str(dev).startswith(device) else None

    def _run(self, job: EmbedJob, device: Device) -> None:
        con = None
        embedder: Embedder | None = None
        try:
            model = index_model(self.st)
            embedder = self._shared(model, device) or self.factory(model, device)
            job.model, job.device = embedder.name, str(getattr(embedder, "device", "cpu"))
            job.state = "running"
            con = connect(self.database_url)          # its own connection: the API stays free
            for item in job.items:
                if job.stop_requested:
                    break
                item.state = "running"
                job.message = f"{item.ticker} annual report {item.fy or ''}".strip()

                def progress(_doc: str, done: int, total: int, item: EmbedItem = item) -> None:
                    item.done, item.passages = done, total

                job.embedded += embed_documents(con, embedder, self.st.vectors, [item.doc_id],
                                                progress, lambda: job.stop_requested,
                                                batch=PROGRESS_BATCH)
                item.state = "done" if item.done >= item.passages else "stopped"
            stopped = [i for i in job.items if i.state != "done"]
            for i in stopped:
                i.state = "stopped"
            job.state = "stopped" if job.stop_requested and stopped else "done"
            job.message = (f"{job.embedded:,} passages embedded"
                           + (f"; stopped with {len(stopped)} report(s) left" if stopped else ""))
        except Exception as e:                        # noqa: BLE001 - reported on the job
            job.state, job.error = "failed", f"{type(e).__name__}: {e}"
            job.message = "Stopped by an error; finished reports are kept"
            for i in job.items:
                if i.state == "running":
                    i.state = "failed"
            traceback.print_exc()
        finally:
            job.finished_at = _now()
            if con is not None:
                con.close()
            with self.lock:
                self.st.reload()                      # search uses the new vectors now
            embedder = None
            gc.collect()
            _free_gpu()


def _free_gpu() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def add_embedding_routes(app: Any, require_admin: Any, st: State, lock: threading.Lock,
                         database_url: str | None = None,
                         factory: Callable[[str, Device], Embedder] | None = None) -> Embeddings:
    admin = [Depends(require_admin)]
    emb = Embeddings(st, lock, database_url, factory or default_embedder)

    @app.get("/admin/embeddings", response_model=EmbedJob | None, dependencies=admin,
             tags=["admin"])
    def embedding_job() -> EmbedJob | None:
        return emb.job

    @app.post("/admin/embeddings", response_model=EmbedJob, status_code=202, dependencies=admin,
              tags=["admin"])
    def start_embedding(req: EmbedRequest) -> EmbedJob:
        return emb.start(req)

    @app.delete("/admin/embeddings", response_model=EmbedJob, dependencies=admin,
                tags=["admin"])
    def stop_embedding() -> EmbedJob:
        return emb.stop()

    return emb
