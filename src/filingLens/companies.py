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
    listed_on: str = ""               # "06-OCT-2008", as NSE writes it


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
                          r.get("ISIN NUMBER", ""), r.get("SERIES", ""),
                          r.get("DATE OF LISTING", ""))
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


# ---- companies named in a question that are not loaded ----------------------------------------
_LEGAL = {"limited", "ltd", "the", "and", "&", "of", "co", "company", "corporation", "corp"}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9&\-]*")


class CompanyGuard:
    """Finds listed companies a text names that the registry does not cover, so "HCL Tech vs
    TCS" gets "HCL Tech is not loaded" instead of an answer about TCS alone (or TCS's numbers
    under HCL's name). Covered companies are removed from the text first.

    Named means (case-insensitive) the first word of a company's name followed by the start of
    its second word ("HCL Tech", "Reliance Ind", "Tata Steel"), or a single word that begins
    one or more names and is written with a capital ("Reliance", "HCL", "Wipro") or is the
    NSE symbol in capitals ("HCLTECH"). Words in ``ignore`` (ordinary question words, metric
    names, months...) never count on their own, so "What is ..." or "Net profit" are safe."""

    def __init__(self, listed: list[ListedCompany], registry: Registry,
                 ignore: set[str] | frozenset[str] = frozenset()):
        self.covered = set(registry.companies)
        self.aliases = sorted({a for t, c in registry.companies.items()
                               for a in (t.lower(), c.name.lower(), *c.aliases) if len(a) >= 2},
                              key=len, reverse=True)
        self.ignore = {w.lower() for w in ignore}
        self.by_symbol = {c.symbol: c for c in listed}
        self.by_first: dict[str, list[tuple[list[str], ListedCompany]]] = {}
        for c in listed:
            words = [w for w in _TOKEN.findall(c.name.lower()) if w not in _LEGAL]
            if words:
                self.by_first.setdefault(words[0], []).append((words, c))

    def _uncovered(self, cands: list[ListedCompany]) -> list[ListedCompany]:
        """All candidates, unless one of them is covered (then the text means that one)."""
        return [] if any(c.symbol in self.covered for c in cands) else cands

    def find(self, text: str) -> list[ListedCompany]:
        t = text.replace("’", "'")
        for a in self.aliases:                        # "tata consultancy services" is TCS
            t = re.sub(rf"(?<![\w]){re.escape(a)}(?![\w])", " ", t, flags=re.IGNORECASE)
        toks = [re.sub(r"'s$", "", w) for w in _TOKEN.findall(t.replace("'s ", " "))]
        found: dict[str, ListedCompany] = {}
        used: set[int] = set()                        # second words of two-word names
        for i, tok in enumerate(toks):
            if i in used:
                continue
            w = tok.lower()
            nxt = toks[i + 1].lower() if i + 1 < len(toks) else ""
            names = self.by_first.get(w, [])
            two = [c for n, c in names if len(n) > 1 and len(nxt) >= 3 and n[1].startswith(nxt)]
            if two and not (w in self.ignore and nxt in self.ignore):
                hits = self._uncovered(two)
                used.add(i + 1)
            elif names and w not in self.ignore and len(w) >= 3 and tok[0].isupper():
                hits = self._uncovered([c for _, c in names])
            elif tok.isupper() and tok in self.by_symbol and w not in self.ignore:
                hits = self._uncovered([self.by_symbol[tok]])
            else:
                hits = []
            for c in hits[:3]:
                found.setdefault(c.symbol, c)
        return list(found.values())
