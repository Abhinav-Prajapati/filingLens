"""Any NSE-listed company: the official equity list, search, and adding companies.

NSE publishes every listed equity (symbol, name, ISIN) as one CSV. It is downloaded once to
``work/nse_equity_list.csv``. Added companies go to an overlay file (default
``work/companies.yaml``, or FILINGLENS_COMPANIES) that ``load_registry`` merges with the
built-in ``registry.yaml``.

The 14 metrics follow the Ind-AS taxonomy for non-financial companies. Banks and NBFCs file
with a different taxonomy, so most of their metrics will show as missing after ingest.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from filingLens.domain.registry import DEFAULT_PATH, Registry, load_registry

EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
DEFAULT_LIST_PATH = Path("work/nse_equity_list.csv")


def default_overlay() -> Path:
    return Path(os.environ.get("FILINGLENS_COMPANIES", "work/companies.yaml"))


def registry_with_overlay() -> Registry:
    return load_registry(DEFAULT_PATH, default_overlay())


@dataclass(frozen=True)
class ListedCompany:
    symbol: str
    name: str
    isin: str
    series: str = "EQ"


def fetch_equity_list(client, path: Path = DEFAULT_LIST_PATH) -> bool:  # type: ignore[no-untyped-def]
    """Download NSE's list of listed equities (one request, ~2,000 rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(client.download(EQUITY_LIST_URL, path))


def load_equity_list(path: Path = DEFAULT_LIST_PATH) -> list[ListedCompany]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip().upper(): (v or "").strip() for k, v in r.items() if k}
                for r in csv.DictReader(f)]
    return [ListedCompany(r.get("SYMBOL", ""), r.get("NAME OF COMPANY", ""),
                          r.get("ISIN NUMBER", ""), r.get("SERIES", ""))
            for r in rows if r.get("SYMBOL")]


def search(listed: list[ListedCompany], text: str, limit: int = 20) -> list[ListedCompany]:
    """Exact symbol first, then symbols / names containing the text."""
    q = text.strip().lower()
    exact = [c for c in listed if c.symbol.lower() == q]
    rest = [c for c in listed if c not in exact and (q in c.symbol.lower() or q in c.name.lower())]
    return (exact + rest)[:limit]


def aliases_for(c: ListedCompany) -> list[str]:
    """symbol, full name, and the name without 'Limited'/'Ltd' (all lowercase)."""
    name = c.name.lower().strip()
    short = re.sub(r"\s+(limited|ltd\.?)$", "", name).strip()
    return list(dict.fromkeys(a for a in (c.symbol.lower(), name, short) if a))


def add_companies(listed: list[ListedCompany], overlay: Path | None = None,
                  registry: Registry | None = None) -> list[str]:
    """Add companies to the overlay file; returns the tickers actually added (already known
    tickers are skipped, and aliases already used by another company are left out)."""
    overlay = overlay or default_overlay()
    registry = registry or load_registry(DEFAULT_PATH, overlay)
    data = yaml.safe_load(overlay.read_text()) if overlay.exists() else None
    data = data or {"companies": {}}
    used = {a for comp in registry.companies.values() for a in comp.aliases}
    added = []
    for c in listed:
        if c.symbol in registry.companies or c.symbol in data["companies"]:
            continue
        aliases = [a for a in aliases_for(c) if a not in used]
        used.update(aliases)
        data["companies"][c.symbol] = {"isin": c.isin, "name": c.name, "sector": "unknown",
                                       "aliases": aliases or [c.symbol.lower()]}
        added.append(c.symbol)
    if added:
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True))
    return added
