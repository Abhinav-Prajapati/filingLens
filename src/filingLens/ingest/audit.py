"""Tag audit: what each filing gave us, and which big numbers we are not mapping yet.

Use it to grow ``registry.yaml``: if a metric is missing for a filing, the largest
unmapped tags usually show the name that filer used.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from filingLens.domain.fiscal import ptype, to_date
from filingLens.domain.models import RawFact
from filingLens.domain.registry import Registry
from filingLens.ingest.xbrl import FilingFacts


def period_type_counts(rows: list[RawFact]) -> dict[str, int]:
    """Phase 0 diagnostic: how many non-dimensional numeric facts fall in each period type."""
    c: Counter[str] = Counter()
    for r in rows:
        if r.has_dim or r.value_num is None:
            continue
        if r.instant:
            c["INSTANT"] += 1
            continue
        s, e = to_date(r.period_start), to_date(r.period_end)
        c[ptype(s, e) if s and e else "no_period"] += 1
    return dict(sorted(c.items()))


def metric_coverage(filing: FilingFacts, registry: Registry) -> list[dict]:
    """One row per metric: which period types were found, and from which tag."""
    found: dict[str, list[str]] = defaultdict(list)
    tags: dict[str, str] = {}
    for f in filing.facts:
        found[f.metric_id].append(f.period_type)
        tags[f.metric_id] = f.xbrl_tag
    return [{"metric_id": m, "found": ",".join(sorted(set(found.get(m, [])))) or "MISSING",
                 "tag": tags.get(m, "")} for m in registry.metrics]


def missing_metrics(filing: FilingFacts, registry: Registry) -> list[str]:
    have = {f.metric_id for f in filing.facts}
    return [m for m in registry.metrics if m not in have]


def unmapped_tags(rows: list[RawFact], registry: Registry, top: int = 25) -> list[dict]:
    """Largest non-dimensional numeric tags not used by any metric (by max absolute value)."""
    mapped = registry.result_tags()
    best: dict[str, dict] = {}
    for r in rows:
        if r.has_dim or r.value_num is None or r.tag.lower() in mapped:
            continue
        kind = "instant" if r.instant else "duration"
        cur = best.get(r.tag)
        if cur is None:
            best[r.tag] = cur = {"tag": r.tag, "kind": kind, "n": 0, "max_abs": 0.0, "unit": r.unit}
        cur["n"] += 1
        cur["max_abs"] = max(cur["max_abs"], abs(r.value_num))
    return sorted(best.values(), key=lambda d: -d["max_abs"])[:top]
