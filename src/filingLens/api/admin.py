"""Admin-only routes (role ``admin``).

    GET  /admin/companies                    every NSE-listed equity, with what this install
                                             holds for it: tracked or not, XBRL filings and
                                             their years, annual reports, report passages,
                                             files in the S3 bucket, inbox files not ingested
    GET  /admin/companies/{symbol}/nse       what NSE has for one company now (XBRL results and
                                             annual reports of the last 5 years), have / new
    POST /admin/companies/{symbol}/download  queue a download: NSE -> S3 bucket -> ingest
    GET  /admin/companies/{symbol}/download  that job's progress (404 when there is none)
    GET  /admin/jobs                         recent download jobs, newest first
    GET  /admin/companies/{symbol}/files     the company's files in the bucket, with
                                             short-lived download links

The list is local (NSE's equity list CSV, the database, the bucket listing), so it is quick.
NSE is asked politely and one request at a time (see filingLens.acquire), so downloads run
one company after another from a queue. A download adds an untracked company to the company
list, fetches every file NSE has that isn't held yet, keeps each in the bucket (the inbox
folder is the local working copy the parsers read), then ingests the company: XBRL figures,
annual-report text (searchable by keyword at once) and calibration. Semantic-search embedding
stays a separate step (`filinglens index`): it needs the GPU, which llama-server shares.
"""
from __future__ import annotations

import datetime as dt
import queue
import threading
import time
import traceback
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from filingLens.acquire import BlockedError, NSEClient, acquire
from filingLens.api.embeddings import index_model
from filingLens.catalog import mark_have
from filingLens.companies import add_companies, load_equity_list
from filingLens.domain.fiscal import fy_int
from filingLens.ingest.errors import IngestError
from filingLens.ingest.pdf_pipeline import ingest_inbox
from filingLens.retrieval.index import pending_by_document
from filingLens.storage.db import Database, connect
from filingLens.storage.objects import ObjectStore, filing_key

if TYPE_CHECKING:
    from filingLens.api.app import State

NSE_CACHE_SECONDS = 3600
JOBS_KEPT = 50


class ReportDoc(BaseModel):
    doc_id: str
    fy: str | None
    passages: int                        # text chunks of the PDF
    embedded: int                        # of those, with a vector (semantic search)


class IndexInfo(BaseModel):
    model: str | None                    # what the vector file was built with
    passages: int                        # report passages in the database
    embedded: int                        # of those, with a vector
    reports_pending: int                 # PDFs with passages not embedded
    companies_pending: int


class ListedRow(BaseModel):
    symbol: str
    name: str
    series: str                          # EQ, BE, BZ, SM ... (NSE trading series)
    isin: str
    listed_on: str | None                # ISO date
    tracked: bool                        # in the company registry (can be asked about)
    xbrl_files: int                      # XBRL results ingested
    fys: list[str]                       # full years with figures, oldest first
    latest_quarter: str | None           # "FY27 Q2"
    annual_reports: list[str]            # FYs of ingested annual-report PDFs
    passages: int                        # report text chunks (keyword-searchable)
    embedded: int                        # of those, with a vector (semantic search)
    reports: list[ReportDoc]             # each ingested annual-report PDF
    stored: int                          # files in the S3 bucket
    pending_xbrl: int                    # in the inbox, not ingested yet
    pending_reports: int


class ListedCompanies(BaseModel):
    total: int
    list_updated: str | None             # when NSE's equity list was downloaded (ISO)
    storage: bool                        # an S3 bucket is configured
    index: IndexInfo
    rows: list[ListedRow]


class NSEFile(BaseModel):
    kind: Literal["xbrl", "annual_report"]
    period: str                          # "FY25", "FY26 Q2", "annual report FY25"
    fy: str | None
    scope: str | None
    status: Literal["have", "new"]


class NSEAvailability(BaseModel):
    symbol: str
    checked_at: str
    xbrl_filings: int
    full_years: list[str]                # years with annual results (or a Q4 integrated filing)
    quarters: list[str]
    annual_reports: list[str]
    new_files: int
    files: list[NSEFile]
    warning: str | None                  # NSE didn't answer some requests


Stage = Literal["queued", "listing", "downloading", "ingesting", "done", "failed"]


class Job(BaseModel):
    symbol: str
    name: str
    stage: Stage
    done: int = 0                        # files handled so far (downloading)
    total: int = 0                       # files to handle
    downloaded: int = 0                  # fetched from NSE in this job
    stored: int = 0                      # put in the bucket in this job
    failed_files: int = 0
    added_company: bool = False          # it was not tracked before
    message: str = ""                    # what is happening now, or the outcome
    error: str | None = None
    queued_at: str
    started_at: str | None = None
    finished_at: str | None = None
    facts: int | None = None             # after ingest: figures held for the company
    years: list[str] = []
    passages: int | None = None


class StoredFileView(BaseModel):
    key: str
    name: str
    kind: str                            # xbrl | annual_report
    size: int
    modified: str
    url: str                             # short-lived download link


class _CountingClient(NSEClient):
    """Counts requests NSE didn't answer, so "nothing found" and "NSE was down" differ."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls = self.failed = 0

    def get_json(self, url: str, params: dict) -> Any:
        self.calls += 1
        out = super().get_json(url, params)
        self.failed += out is None
        return out


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _iso_date(text: str) -> str | None:
    try:
        return dt.datetime.strptime(text.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _empty() -> dict[str, Any]:
    return {"fys": set(), "quarters": set(), "xbrl_files": 0, "annual_reports": set(),
            "passages": 0, "embedded": 0, "reports": [], "stored": 0, "pending_xbrl": 0,
            "pending_reports": 0}


def local_holdings(con: Database, inbox: Path, stored: dict[str, int] | None = None,
                   vectors: Path | None = None,
                   model: str | None = None) -> dict[str, dict[str, Any]]:
    """Per ticker: FYs, latest quarter, XBRL files, annual reports (each with passages and
    embedded passages), files in the bucket, and files in the inbox not ingested yet."""
    h: dict[str, dict[str, Any]] = defaultdict(_empty)
    for t, fy in con.execute("SELECT DISTINCT ticker, fy FROM fact WHERE period_type='FY'"):
        h[t]["fys"].add(fy)
    for t, fy, q in con.execute("SELECT DISTINCT ticker, fy, quarter FROM fact "
                                "WHERE period_type='Q'"):
        h[t]["quarters"].add((fy, q))
    ingested: set[str] = set()
    # passages count as embedded only with vectors of ``model`` (the one new vectors use)
    progress = pending_by_document(con, vectors, model) if vectors is not None else {}
    for doc_id, t, kind, fy, name in con.execute(
            "SELECT doc_id, ticker, doc_type, fy, original_name FROM document"):
        ingested.add(name)
        if kind == "xbrl_result":
            h[t]["xbrl_files"] += 1
        elif kind == "annual_report":
            h[t]["annual_reports"].add(fy or "?")
            p = progress.get(doc_id)
            if p is not None:
                h[t]["reports"].append(ReportDoc(doc_id=doc_id, fy=fy, passages=p.total,
                                                 embedded=p.done))
                h[t]["embedded"] += p.done
    for t, n in con.execute("SELECT ticker, COUNT(*) FROM chunk GROUP BY ticker"):
        h[t]["passages"] = n
    for t, n in (stored or {}).items():
        h[t]["stored"] = n
    if inbox.is_dir():
        for d in inbox.iterdir():
            if d.is_dir():
                for key, sub, pat in (("pending_xbrl", "xbrl", "*.xml"),
                                      ("pending_reports", "annual_report", "*.pdf")):
                    n = sum(f.name not in ingested for f in (d / sub).glob(pat))
                    if n:
                        h[d.name][key] = n
    return h


class Downloads:
    """A FIFO of download jobs and the one worker thread that runs them."""

    def __init__(self, st: State, lock: threading.Lock, inbox: Path,
                 store: ObjectStore | None, nse: Callable[[], _CountingClient],
                 nse_lock: threading.Lock, database_url: str | None = None):
        self.st, self.lock, self.inbox, self.store = st, lock, inbox, store
        self.nse, self.nse_lock, self.database_url = nse, nse_lock, database_url
        self.jobs: dict[str, Job] = {}
        self.q: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._guard = threading.Lock()

    def submit(self, symbol: str, name: str) -> Job:
        with self._guard:
            job = self.jobs.get(symbol)
            if job and job.stage not in ("done", "failed"):
                return job                               # already queued or running
            job = Job(symbol=symbol, name=name, stage="queued", message="Waiting to start",
                      queued_at=_now())
            self.jobs[symbol] = job
            for old in sorted(self.jobs.values(), key=lambda j: j.queued_at)[:-JOBS_KEPT]:
                self.jobs.pop(old.symbol, None)
            self.q.put(symbol)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="downloads", daemon=True)
                self._worker.start()
            return job

    def _run(self) -> None:
        while True:
            try:
                symbol = self.q.get(timeout=5)
            except queue.Empty:
                with self._guard:
                    if self.q.empty():
                        self._worker = None
                        return
                continue
            job = self.jobs.get(symbol)
            if job is None:
                continue
            try:
                self._download(job)
            except Exception as e:                       # noqa: BLE001 - reported on the job
                job.stage, job.error = "failed", f"{type(e).__name__}: {e}"
                job.message = "Stopped by an error"
                traceback.print_exc()
            finally:
                job.finished_at = _now()

    def _download(self, job: Job) -> None:
        t = job.symbol
        job.started_at, job.stage, job.message = _now(), "listing", "Asking NSE what it has"
        if t not in self.st.reg.companies:
            listed = {c.symbol: c for c in load_equity_list(self.st.equity_list)}
            job.added_company = bool(add_companies([listed[t]]))
            with self.lock:
                self.st.reload()
            if t not in self.st.reg.companies:
                raise RuntimeError(f"{t} could not be added to the company list")
        with self.nse_lock:
            client = self.nse()
            rep = acquire(client, self.inbox, [t], sources=("annual", "integrated"),
                          annual_reports=True, dry_run=True)
            if rep.blocked:
                raise BlockedError(rep.blocked)
            if client.calls and client.failed == client.calls:
                raise RuntimeError("NSE did not answer; try again in a minute")
            job.total, job.stage = len(rep.found), "downloading"
            for f in rep.found:
                dest = f.dest(self.inbox)
                job.message = f"{'Annual report' if f.kind == 'annual_report' else 'XBRL'} {f.label}"
                if not (dest.exists() and dest.stat().st_size > 0):
                    if client.download(f.url, dest):
                        job.downloaded += 1
                    else:
                        job.failed_files += 1
                        job.done += 1
                        continue
                if self.store is not None:
                    key = filing_key(t, f.kind, dest.name)
                    if not self.store.exists(key):
                        self.store.put_file(dest, key)
                        job.stored += 1
                job.done += 1
        job.stage, job.message = "ingesting", "Reading figures and report text"
        if (self.inbox / t).is_dir():
            con = connect(self.database_url)      # its own connection: the API stays responsive
            try:
                try:
                    ingest_inbox(con, self.inbox, self.st.reg, tickers=[t])
                except IngestError as e:
                    if "no files" not in str(e):
                        raise
                job.facts = con.execute("SELECT COUNT(*) FROM fact WHERE ticker=?",
                                        (t,)).fetchone()[0]
                job.years = sorted((r[0] for r in con.execute(
                    "SELECT DISTINCT fy FROM fact WHERE ticker=? AND period_type='FY'", (t,))),
                    key=fy_int)
                job.passages = con.execute("SELECT COUNT(*) FROM chunk WHERE ticker=?",
                                           (t,)).fetchone()[0]
            finally:
                con.close()
        with self.lock:
            self.st.reload()
        job.stage = "done"
        job.message = ("Nothing new on NSE" if not job.total else
                       f"{job.downloaded} downloaded, {job.stored} saved to the bucket"
                       + (f", {job.failed_files} failed" if job.failed_files else ""))


def add_admin_routes(app: Any, require_admin: Any, st: State, lock: threading.Lock,
                     inbox: Path, store: ObjectStore | None,
                     nse_client: Callable[[], _CountingClient] | None = None,
                     database_url: str | None = None) -> Downloads:
    """``nse_client`` makes the NSE session (tests pass one with a fake HTTP session)."""
    admin = [Depends(require_admin)]
    make_client = nse_client or (lambda: _CountingClient(delay=1.5, log=lambda _: None))
    nse_lock = threading.Lock()                      # one NSE conversation at a time
    nse_cache: dict[str, tuple[float, NSEAvailability]] = {}
    clients: list[_CountingClient] = []              # one warm session, reused

    def nse() -> _CountingClient:
        if not clients:
            clients.append(make_client())
        c = clients[0]
        c.calls = c.failed = 0
        return c

    downloads = Downloads(st, lock, inbox, store, nse, nse_lock, database_url)

    def listed_by_symbol(symbol: str) -> Any:
        found = [c for c in load_equity_list(st.equity_list) if c.symbol == symbol.upper()]
        if not found:
            raise HTTPException(404, f"{symbol.upper()} is not in NSE's equity list")
        return found[0]

    def stored_counts() -> dict[str, int]:
        if store is None:
            return {}
        counts: dict[str, int] = defaultdict(int)
        try:
            for f in store.list("filings/"):
                counts[f.key.split("/")[1]] += 1
        except Exception:                            # noqa: BLE001 - bucket down: list still works
            return {}
        return counts

    @app.get("/admin/companies", response_model=ListedCompanies, dependencies=admin,
             tags=["admin"])
    def listed_companies() -> ListedCompanies:
        listed = load_equity_list(st.equity_list)
        stored = stored_counts()
        with lock:
            held = local_holdings(st.con, inbox, stored, st.vectors, index_model(st))
        rows = []
        for c in listed:
            x = held.get(c.symbol) or _empty()
            lq = max(x["quarters"], key=lambda p: (fy_int(p[0]), p[1]), default=None)
            rows.append(ListedRow(
                symbol=c.symbol, name=c.name, series=c.series, isin=c.isin,
                listed_on=_iso_date(c.listed_on), tracked=c.symbol in st.reg.companies,
                xbrl_files=x["xbrl_files"], fys=sorted(x["fys"], key=fy_int),
                latest_quarter=f"{lq[0]} {lq[1]}" if lq else None,
                annual_reports=sorted(x["annual_reports"]), passages=x["passages"],
                embedded=x["embedded"], reports=sorted(x["reports"], key=lambda r: r.fy or ""),
                stored=x["stored"], pending_xbrl=x["pending_xbrl"],
                pending_reports=x["pending_reports"]))
        updated = (dt.datetime.fromtimestamp(st.equity_list.stat().st_mtime).isoformat(
            timespec="seconds") if st.equity_list.exists() else None)
        pending = [r for x in held.values() for r in x["reports"] if r.embedded < r.passages]
        index = IndexInfo(model=index_model(st),
                          passages=sum(x["passages"] for x in held.values()),
                          embedded=sum(x["embedded"] for x in held.values()),
                          reports_pending=len(pending),
                          companies_pending=sum(any(r.embedded < r.passages for r in x["reports"])
                                                for x in held.values()))
        return ListedCompanies(total=len(rows), list_updated=updated, storage=store is not None,
                               index=index, rows=rows)

    @app.get("/admin/companies/{symbol}/nse", response_model=NSEAvailability,
             dependencies=admin, tags=["admin"])
    def nse_availability(symbol: str, refresh: bool = False) -> NSEAvailability:
        symbol = listed_by_symbol(symbol).symbol
        hit = nse_cache.get(symbol)
        if hit and not refresh and time.time() - hit[0] < NSE_CACHE_SECONDS:
            return hit[1]
        with nse_lock:
            client = nse()
            rep = acquire(client, inbox, [symbol], sources=("annual", "integrated"),
                          annual_reports=True, dry_run=True)
        if rep.blocked:
            raise HTTPException(503, rep.blocked)
        if client.calls and client.failed == client.calls:
            raise HTTPException(502, "NSE did not answer; try again in a minute")
        with lock:
            status = mark_have(rep.found, inbox, st.con)
        files, years, quarters, reports = [], set(), set(), set()
        for f, stt in zip(rep.found, status, strict=True):
            if f.kind == "annual_report":
                reports.add(f.fy or "?")
                period = f"annual report {f.fy or '?'}"
            else:
                period = f.label
                if f.quarter:
                    quarters.add(f.label)
                if f.fy and (f.period == "Annual" or f.quarter == "Q4"):
                    years.add(f.fy)
            files.append(NSEFile(kind=f.kind, period=period, fy=f.fy,  # type: ignore[arg-type]
                                 scope=f.scope or None, status=stt))  # type: ignore[arg-type]
        out = NSEAvailability(
            symbol=symbol, checked_at=_now(),
            xbrl_filings=sum(f.kind == "xbrl" for f in files),
            full_years=sorted(years, key=fy_int),
            quarters=sorted(quarters, key=lambda s: (fy_int(s[:4]), s[-2:])),
            annual_reports=sorted(reports), new_files=sum(f.status == "new" for f in files),
            files=files,
            warning=(f"NSE didn't answer {client.failed} of {client.calls} requests; the list "
                     "may be incomplete") if client.failed else None)
        nse_cache[symbol] = (time.time(), out)
        return out

    @app.post("/admin/companies/{symbol}/download", response_model=Job, status_code=202,
              dependencies=admin, tags=["admin"])
    def start_download(symbol: str) -> Job:
        c = listed_by_symbol(symbol)
        nse_cache.pop(c.symbol, None)                # its have/new status is about to change
        return downloads.submit(c.symbol, c.name)

    @app.get("/admin/companies/{symbol}/download", response_model=Job, dependencies=admin,
             tags=["admin"])
    def download_status(symbol: str) -> Job:
        job = downloads.jobs.get(symbol.upper())
        if job is None:
            raise HTTPException(404, "No download for this company in this session")
        return job

    @app.get("/admin/jobs", response_model=list[Job], dependencies=admin, tags=["admin"])
    def jobs() -> list[Job]:
        return sorted(downloads.jobs.values(), key=lambda j: j.queued_at, reverse=True)

    @app.get("/admin/companies/{symbol}/files", response_model=list[StoredFileView],
             dependencies=admin, tags=["admin"])
    def stored_files(symbol: str) -> list[StoredFileView]:
        if store is None:
            raise HTTPException(503, "No S3 bucket configured (FILINGLENS_S3_*)")
        symbol = listed_by_symbol(symbol).symbol
        return [StoredFileView(key=f.key, name=f.key.rsplit("/", 1)[-1],
                               kind=f.key.split("/")[2], size=f.size, modified=f.modified,
                               url=store.download_url(f.key))
                for f in sorted(store.list(f"filings/{symbol}/"), key=lambda f: f.key)]

    return downloads
