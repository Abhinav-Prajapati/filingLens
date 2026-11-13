"""Company and metric registry, loaded from YAML (the single place to edit glossary/tag maps)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("registry.yaml")


@dataclass(frozen=True)
class Company:
    ticker: str
    isin: str
    name: str
    sector: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Metric:
    metric_id: str
    desc: str
    kind: str  # flow | stock
    unit_kind: str  # money | per_share
    tags: tuple[str, ...]
    synonyms: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Registry:
    companies: dict[str, Company]
    metrics: dict[str, Metric]

    def result_tags(self) -> set[str]:
        return {t for m in self.metrics.values() for t in m.tags}


def load_registry(path: Path | str = DEFAULT_PATH) -> Registry:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    companies = {
        t: Company(t, c["isin"], c["name"], c["sector"], tuple(c["aliases"]))
        for t, c in raw["companies"].items()
    }
    metrics = {
        mid: Metric(mid, m["desc"], m["kind"], m["unit_kind"], tuple(m["tags"]),
                    tuple(m.get("synonyms", [])))
        for mid, m in raw["metrics"].items()
    }
    return Registry(companies, metrics)
