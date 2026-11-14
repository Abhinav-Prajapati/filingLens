"""Retrieval with the hash embedder: no model download, no GPU."""
import numpy as np
import pytest

from filingLens.cli import main
from filingLens.domain.models import Chunk
from filingLens.retrieval.embedder import HashEmbedder
from filingLens.retrieval.index import VectorIndex, chunk_passage, update_index
from filingLens.retrieval.search import Searcher, SearchFilters, fts_query, keyword_search, rrf
from filingLens.storage.repos import TextRepo

TEXTS = {
    "attr": "Attrition in IT services was 13.3%, among the lowest in the industry.",
    "div": "The Board recommended a final dividend of 30 rupees per share.",
    "ai": "Generative AI deployments grew across banking and retail customers.",
}


def chunk(cid, ticker, text, fy="FY25", section="mdna", scope="none", doc=None):
    return Chunk(cid, doc or f"doc-{ticker}", ticker, fy, "annual_report", scope, "text", section,
                 1, 1, text, f"{ticker} | Annual Report {fy} | {section} | page 1")


@pytest.fixture
def db(con):
    """Same three texts for TCS and INFY, plus one TCS FY24 chunk."""
    for t in ("TCS", "INFY"):
        TextRepo(con).replace(f"doc-{t}", [], [chunk(f"{t}-{k}", t, v) for k, v in TEXTS.items()])
    TextRepo(con).replace("doc-old", [], [chunk("TCS-old", "TCS", TEXTS["attr"], fy="FY24",
                                                doc="doc-old")])
    return con


class CountingEmbedder(HashEmbedder):
    def __init__(self, name="hash-512"):
        super().__init__()
        self.name, self.calls = name, 0

    def embed(self, texts):
        self.calls += len(texts)
        return super().embed(texts)


def test_hash_embedder_unit_rows_and_deterministic():
    e = HashEmbedder()
    v = e.embed(["revenue grew", "revenue grew", ""])
    assert v.shape == (3, 512) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v[:2], axis=1), 1.0) and np.array_equal(v[0], v[1])
    assert not v[2].any()


def test_rrf_exact_order():
    fused = rrf({"dense": ["x", "y", "z"], "keyword": ["y", "w"]}, k=60)
    assert [c for c, _, _ in fused] == ["y", "x", "w", "z"]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)
    # small k: a #1 in one list beats a #3 that appears in both lists
    first = next(iter(rrf({"d": ["top", "a", "both"], "k": ["b", "c", "both"]}, k=1)))
    assert first[0] in ("top", "b")
    assert fused[0][2] == {"dense": 2, "keyword": 1}
    assert [c for c, _, _ in rrf({"a": ["p"], "b": ["q"]})] == ["p", "q"]   # tie -> by id


def test_fts_query_is_safe_for_any_text(db):
    assert fts_query('TCS\'s "attrition" (FY25)?') == "tcs | attrition | fy25"
    assert fts_query("How many people does TCS employ?", frozenset({"tcs"})) == (
        "people | employ")                               # stopwords + filtered ticker dropped
    assert fts_query("kitne log kaam karte hain") == "log | kaam"   # Hinglish
    assert fts_query("how is it") == "how | is | it"     # only stopwords: keep all
    assert fts_query("net_profit & margin") == "net | profit | margin"   # no query syntax
    assert fts_query("?? !!") is None
    # query operators typed by a user are plain words, never syntax: no error. These three are
    # also English stopwords, which Postgres leaves out of the index, so nothing matches.
    assert keyword_search(db, 'AND OR NOT "( & | !', SearchFilters(), 5) == []
    assert set(keyword_search(db, "attrition & (AND)", SearchFilters(), 5)) >= {"TCS-attr"}


def test_index_incremental_and_row_mapping(db, tmp_path):
    path, emb = tmp_path / "v.npy", CountingEmbedder()
    idx, rep = update_index(db, emb, path)
    assert (rep.total, rep.added, rep.removed, rep.rebuilt) == (7, 7, 0, True)
    row = db.execute("SELECT context_line, text FROM chunk WHERE chunk_id='TCS-ai'").fetchone()
    assert np.allclose(idx.vectors[idx.row_of["TCS-ai"]], emb.embed([chunk_passage(*row)])[0])

    emb.calls = 0
    _, rep = update_index(db, emb, path)                           # nothing changed
    assert (rep.added, rep.removed, emb.calls) == (0, 0, 0)
    TextRepo(db).replace("doc-old", [], [])                        # one chunk deleted
    TextRepo(db).replace("doc-new", [], [chunk("NEW", "TCS", "Cloud revenue.", doc="doc-new")])
    idx, rep = update_index(db, emb, path)
    assert (rep.total, rep.added, rep.removed, emb.calls) == (7, 1, 1, 1)
    assert "TCS-old" not in idx.row_of and VectorIndex.load(path).chunk_ids == idx.chunk_ids
    _, rep = update_index(db, CountingEmbedder("other-model"), path)
    assert rep.rebuilt and rep.added == 7


def test_filters_apply_before_ranking(db, tmp_path):
    idx, _ = update_index(db, HashEmbedder(), tmp_path / "v.npy")
    s = Searcher(db, idx, HashEmbedder())
    hits = s.search("attrition", SearchFilters(tickers=("TCS",)), k=10)
    assert hits and {h.ticker for h in hits} == {"TCS"}
    assert hits[0].chunk_id in ("TCS-attr", "TCS-old") and set(hits[0].ranks) == {"dense", "keyword"}
    hits = s.search("attrition", SearchFilters(tickers=("TCS",), fys=("FY24",)), k=10)
    assert [h.chunk_id for h in hits] == ["TCS-old"]
    assert s.search("attrition", SearchFilters(sections=("notice",))) == []


def test_keyword_only_fallback_and_model_mismatch(db, tmp_path):
    hits = Searcher(db).search("dividend per share", SearchFilters(tickers=("INFY",)))
    assert hits[0].chunk_id == "INFY-div" and set(hits[0].ranks) == {"keyword"}
    idx, _ = update_index(db, HashEmbedder(), tmp_path / "v.npy")
    with pytest.raises(ValueError, match="index built with"):
        Searcher(db, idx, CountingEmbedder("bge"))


def test_cli_index_and_search(tmp_path, capsys):
    from filingLens.storage.db import connect

    (tmp_path / "w").mkdir()
    con = connect()
    TextRepo(con).replace("doc-TCS", [], [chunk(f"TCS-{k}", "TCS", v) for k, v in TEXTS.items()])
    con.close()
    vec = str(tmp_path / "w" / "vectors.npy")
    assert main(["index", "--vectors", vec, "--model", "hash"]) == 0
    assert (tmp_path / "w" / "vectors.npy").exists()
    assert main(["search", "final dividend", "--vectors", vec, "--ticker", "tcs"]) == 0
    out = capsys.readouterr().out
    assert "[1] TCS | Annual Report FY25" in out and "dividend" in out
