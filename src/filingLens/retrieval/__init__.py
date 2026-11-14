"""Retrieval: embed chunks, keep vectors on disk, hybrid (dense + keyword) search.

    embedder.py  BgeM3Embedder (GPU fp16 / CPU), HashEmbedder (tests)
    index.py     vectors.npy + vectors.json (row -> chunk_id), incremental update
    search.py    filters before ranking, dense + Postgres full-text keyword, RRF fusion
"""
