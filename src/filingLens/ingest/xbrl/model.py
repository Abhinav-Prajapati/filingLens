"""Data shapes produced by the XBRL parser."""
from __future__ import annotations

from dataclasses import dataclass, field

from filingLens.domain.models import RawFact
from filingLens.ingest.xbrl.nse import is_root_context


@dataclass(frozen=True)
class Context:
    """One xbrli:context: the period a fact belongs to, plus any dimensions."""

    id: str
    start: str | None
    end: str | None
    instant: str | None
    dims: tuple[tuple[str, str], ...] = ()
    period_source: str = "context"   # "context" | "reporting_period_facts" (NSE fix applied)

    @property
    def has_dim(self) -> bool:
        return bool(self.dims)

    @property
    def is_root(self) -> bool:
        """True for a column's main context (OneD, FourD, OneI)."""
        return is_root_context(self.id)


@dataclass
class ParsedFiling:
    """Everything read from one file, before any mapping to metrics."""

    path: str
    format: str                                  # "xbrl" | "ixbrl"
    contexts: dict[str, Context]
    units: dict[str, str]
    facts: list[RawFact]
    entity_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
