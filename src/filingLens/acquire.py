"""Download filings from NSE into the inbox layout that `filinglens ingest` reads:

    <inbox>/<TICKER>/xbrl/<TICKER>_<Source>_<name>.xml      results (XBRL)
    <inbox>/<TICKER>/annual_report/<TICKER>_annual_FY25.pdf  annual reports
    <inbox>/sources.json                                     file name -> source URL

Sources: "annual" and "quarterly" results (the older listing, up to about FY24/FY25) and
"integrated" (SEBI Integrated Filing - Financials, every quarter from Dec 2024 to the latest;
the March quarter also carries the full year). Default: annual + integrated.

NSE has no official API. This uses the JSON endpoints its website calls, with a browser
session (cookies from the home page), as open-source clients such as nsepython do.
NSE's terms of use restrict automated access: keep volumes small and personal. Built in:
a pause between requests, retries with back-off, files already on disk are never fetched
again, and the run stops after repeated refusals (403/429): then download by hand from
nseindia.com -> Corporate Filings -> Financial Results.

The API's row fields are not documented; parsing is tolerant, and filters on scope or FY
use whatever fields are present. The downloaded files are authoritative: ingestion reads
scope, period and company from the XBRL itself.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filingLens.domain.fiscal import fy_int, fy_label, quarter_of

BASE = "https://www.nseindia.com"
RESULTS_API = f"{BASE}/api/corporates-financial-results"
INTEGRATED_API = f"{BASE}/api/integrated-filing-results"
SOURCES = ("annual", "quarterly", "integrated")
ANNUAL_REPORTS_API = f"{BASE}/api/annual-reports"
WARMUP = (BASE, f"{BASE}/companies-listing/corporate-filings-financial-results")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/",
}


class BlockedError(RuntimeError):
    """NSE refused repeatedly; stop instead of hammering it."""


@dataclass
class Filing:
    """One downloadable file found in an NSE listing."""

    ticker: str
    kind: str                         # "xbrl" | "annual_report"
    url: str
    period: str = ""                  # "Annual" | "Quarterly" | "Integrated" (results)
    fy: str | None = None             # FY of the period end, when the row says
    scope: str = ""                   # "consolidated" | "standalone" | "" (unknown)
    row: dict = field(default_factory=dict, repr=False)
    quarter: str | None = None        # Q1..Q4 for integrated filings

    @property
    def filename(self) -> str:
        name = self.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if self.kind == "annual_report":
            ext = Path(name).suffix.lower() or ".pdf"
            return f"{self.ticker}_annual_{self.fy or 'unknown'}{ext}"
        return f"{self.ticker}_{self.period}_{name}"

    def dest(self, inbox: Path) -> Path:
        return inbox / self.ticker / self.kind / self.filename

    @property
    def label(self) -> str:
        """FY24, FY26 Q4, ... for listings."""
        return (self.fy or "?") + (f" {self.quarter}" if self.quarter else "")


# ------------------------------------------------------------------------------------------
# parsing NSE rows (pure)
# ------------------------------------------------------------------------------------------
def _rows(payload: Any) -> list[dict]:
    rows = payload if isinstance(payload, list) else (payload or {}).get("data") or []
    return [r for r in rows if isinstance(r, dict)]


def _date(text: Any) -> dt.date | None:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return dt.datetime.strptime(str(text).strip()[:11], fmt).date()
        except ValueError:
            continue
    return None


def _scope(row: dict) -> str:
    text = " ".join(str(row.get(k, "")) for k in ("consolidated", "resultType", "type")).lower()
    if ("non" in text and "consol" in text) or "standalone" in text:
        return "standalone"
    return "consolidated" if "consol" in text else ""


def _urls(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        if obj.startswith("http"):
            yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _urls(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _urls(v)


def parse_results(payload: Any, ticker: str, period: str) -> list[Filing]:
    out = []
    for r in _rows(payload):
        url = str(r.get("xbrl") or "")
        if not url.startswith("http") or url.rstrip("/").endswith("/-"):
            continue
        end = _date(r.get("toDate") or r.get("to_date") or r.get("periodEnded"))
        out.append(Filing(ticker, "xbrl", url, period, fy_label(end) if end else None,
                          _scope(r), r))
    return out


def parse_integrated(payload: Any, ticker: str) -> list[Filing]:
    """Integrated Filing rows; only "Financials" (results) are kept, not "Governance"."""
    out = []
    for r in _rows(payload):
        url = str(r.get("xbrl") or "")
        if "financ" not in str(r.get("type", "")).lower() or not url.startswith("http"):
            continue
        end = _date(r.get("qe_Date") or r.get("qeDate"))
        out.append(Filing(ticker, "xbrl", url, "Integrated", fy_label(end) if end else None,
                          _scope(r), r, quarter_of(end) if end else None))
    return out


def parse_annual_reports(payload: Any, ticker: str) -> list[Filing]:
    out = []
    for r in _rows(payload):
        to_yr = str(r.get("toYr") or r.get("toYear") or "")[-4:]
        fy = f"FY{int(to_yr) % 100:02d}" if to_yr.isdigit() else None
        urls = [u for u in _urls(r) if u.lower().split("?")[0].endswith((".pdf", ".zip"))]
        if urls:
            out.append(Filing(ticker, "annual_report", urls[0], "", fy, "", r))
    return out


def select(filings: Iterable[Filing], fys: Iterable[str] = (), scope: str = "both") -> list[Filing]:
    """Keep filings in the wanted FYs and scope. Unknown FY or scope is kept (ingest checks)."""
    want = set(fys)
    out, seen = [], set()
    for f in filings:
        if want and f.fy and f.fy not in want:
            continue
        if scope != "both" and f.scope and f.scope != scope:
            continue
        if f.url not in seen:
            seen.add(f.url)
            out.append(f)
    return out


# ------------------------------------------------------------------------------------------
# network
# ------------------------------------------------------------------------------------------
class NSEClient:
    """Polite session: warm-up for cookies, delay between calls, retries, stop when blocked.
    ``session`` and ``sleep`` can be replaced in tests."""

    def __init__(self, delay: float = 2.0, retries: int = 3, max_refusals: int = 3,
                 session: Any = None, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print):
        if session is None:
            import requests
            session = requests.Session()
        self.s, self.delay, self.retries = session, delay, retries
        self.max_refusals, self.refusals = max_refusals, 0
        self.sleep, self.log, self._warm = sleep, log, False
        self.s.headers.update(HEADERS)

    def _get(self, url: str, **kw: Any) -> Any:
        if not self._warm:
            for u in WARMUP:
                try:
                    self.s.get(u, timeout=20)
                except Exception as e:                 # noqa: BLE001 - warm-up is best effort
                    self.log(f"warm-up {u}: {type(e).__name__}")
                self.sleep(self.delay)
            self._warm = True
        for attempt in range(self.retries):
            try:
                r = self.s.get(url, timeout=60, **kw)
            except Exception as e:                     # noqa: BLE001 - network errors retried
                self.log(f"{type(e).__name__} on {url}")
                r = None
            self.sleep(self.delay * (attempt + 1))
            if r is not None and r.status_code == 200:
                self.refusals = 0
                return r
            if r is not None and r.status_code in (401, 403, 429):
                self.refusals += 1
                self.log(f"NSE refused ({r.status_code}) {url}")
                if self.refusals >= self.max_refusals:
                    raise BlockedError("NSE is refusing requests; wait a while or download by "
                                       "hand from nseindia.com -> Corporate Filings")
        return None

    def get_json(self, url: str, params: dict) -> Any:
        r = self._get(url, params=params)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            self.log(f"not JSON from {url} (bot check page?)")
            return None

    def download(self, url: str, dest: Path) -> bool:
        r = self._get(url)
        if r is None:
            return False
        if "text/html" in r.headers.get("content-type", "") or not r.content:
            self.log(f"got an HTML page instead of a file: {url}")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(dest)                              # no half-written files in the inbox
        return True


# ------------------------------------------------------------------------------------------
# orchestration
# ------------------------------------------------------------------------------------------
@dataclass
class AcquireReport:
    found: list[Filing] = field(default_factory=list)
    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)       # already on disk
    failed: list[str] = field(default_factory=list)
    blocked: str | None = None


def _integrated_pages(client: NSEClient, ticker: str, dates: dict, max_pages: int = 10,
                      size: int = 20) -> list[Filing]:
    out: list[Filing] = []
    for page in range(1, max_pages + 1):
        payload = client.get_json(INTEGRATED_API, {"index": "equities", "symbol": ticker,
                                                   **dates, "page": page, "size": size})
        rows = _rows(payload)
        out += parse_integrated(rows, ticker)
        total = (payload or {}).get("totalCount", 0) if isinstance(payload, dict) else 0
        if not rows or page * size >= int(total or 0):
            break
    return out


def list_filings(client: NSEClient, tickers: Iterable[str], sources: Iterable[str],
                 from_date: dt.date, to_date: dt.date, annual_reports: bool) -> list[Filing]:
    dates = {"from_date": from_date.strftime("%d-%m-%Y"), "to_date": to_date.strftime("%d-%m-%Y")}
    out: list[Filing] = []
    for t in tickers:
        for src in sources:
            if src == "integrated":
                out += _integrated_pages(client, t, dates)
                continue
            per = src.capitalize()                        # "Annual" | "Quarterly"
            payload = client.get_json(RESULTS_API, {"index": "equities", "symbol": t,
                                                    "period": per, **dates})
            out += parse_results(payload, t, per)
        if annual_reports:
            out += parse_annual_reports(
                client.get_json(ANNUAL_REPORTS_API, {"index": "equities", "symbol": t}), t)
    return out


def acquire(client: NSEClient, inbox: str | Path, tickers: Iterable[str],
            sources: Iterable[str] = ("annual", "integrated"), fys: Iterable[str] = (),
            scope: str = "both",
            from_date: dt.date | None = None, to_date: dt.date | None = None,
            annual_reports: bool = False, dry_run: bool = False,
            limit: int | None = None) -> AcquireReport:
    inbox = Path(inbox)
    to_date = to_date or dt.date.today()
    from_date = from_date or to_date.replace(year=to_date.year - 5)
    rep = AcquireReport()
    try:
        bad = [s for s in sources if s not in SOURCES]
        if bad:
            raise ValueError(f"unknown source(s) {bad}; use {SOURCES}")
        oldest = fy_int(fy_label(from_date))
        rep.found = [f for f in select(list_filings(client, [t.upper() for t in tickers], sources,
                                                    from_date, to_date, annual_reports), fys, scope)
                     if f.kind != "annual_report" or not f.fy or fy_int(f.fy) >= oldest]
        todo = rep.found[:limit] if limit else rep.found
        if dry_run:
            return rep
        sources_path = inbox / "sources.json"
        provenance = json.loads(sources_path.read_text()) if sources_path.exists() else {}
        for f in todo:
            dest = f.dest(inbox)
            if dest.exists() and dest.stat().st_size > 0:
                rep.skipped.append(dest)
                continue
            if client.download(f.url, dest):
                rep.downloaded.append(dest)
                provenance[dest.name] = f.url
                sources_path.parent.mkdir(parents=True, exist_ok=True)
                sources_path.write_text(json.dumps(provenance, indent=1, sort_keys=True))
            else:
                rep.failed.append(f.url)
    except BlockedError as e:
        rep.blocked = str(e)
    return rep
