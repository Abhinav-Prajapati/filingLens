"""Recall with the real BGE-M3 model on the real TCS report. Local only: `pytest -m embed`.
Skipped when torch/sentence-transformers or the PDF are missing."""
from pathlib import Path

import pytest
import yaml

from filingLens.ingest.pdf import parse_pdf
from filingLens.retrieval.search import Searcher, SearchFilters
from filingLens.storage.repos import TextRepo

ROOT = Path(__file__).parents[1]
SPEC = yaml.safe_load((Path(__file__).parent / "golden" / "recall.yaml").read_text())
pytestmark = pytest.mark.embed


@pytest.fixture(scope="module")
def searcher(tmp_path_factory):
    pytest.importorskip("sentence_transformers")
    pdf = ROOT / SPEC["pdf"]
    if not pdf.exists():
        pytest.skip(f"PDF not present: {pdf}")
    from filingLens.retrieval.embedder import BgeM3Embedder
    from filingLens.retrieval.index import update_index
    from filingLens.storage.db import connect

    con = connect()
    parsed = parse_pdf(pdf, "ar", SPEC["ticker"], "Tata Consultancy Services")
    TextRepo(con).replace("ar", parsed.pages, parsed.chunks)
    emb = BgeM3Embedder()
    index, _ = update_index(con, emb, tmp_path_factory.mktemp("v") / "vectors.npy")
    return Searcher(con, index, emb)


def test_recall_at_5(searcher):
    filters = SearchFilters(tickers=(SPEC["ticker"],))
    misses = []
    for item in SPEC["questions"]:
        pages = [h.page for h in searcher.search(item["q"], filters, k=5)]
        if not set(pages) & set(item["pages"]):
            misses.append(f"{item['q']!r}: got pages {pages}, want any of {item['pages']}")
    rate = 1 - len(misses) / len(SPEC["questions"])
    print(f"\nrecall@5 = {rate:.0%}" + "".join(f"\n  MISS {m}" for m in misses))
    assert rate >= SPEC["min_hit_rate"], "\n".join(misses)
