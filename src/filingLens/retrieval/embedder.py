"""Text -> unit vectors. Two implementations behind one interface:

* ``BgeM3Embedder``: BAAI/bge-m3 (multilingual: English, Hindi, Hinglish), dense vectors,
  fp16 on a CUDA GPU (~1.1 GB VRAM, fits a 4 GB card), fp32 on CPU otherwise.
  FILINGLENS_EMBED_DEVICE=auto|cpu|cuda; "auto" falls back to CPU when the GPU is full
  (e.g. a local LLM server holds it). One query on CPU takes well under a second.
* ``HashEmbedder``: deterministic bag-of-words hashing. Not semantic; for tests and CI,
  so nothing is downloaded there.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Protocol

import numpy as np

BGE_M3 = "BAAI/bge-m3"


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """float32 array of shape (len(texts), dim), rows L2-normalised."""
        ...


class HashEmbedder:
    def __init__(self, dim: int = 512):
        self.dim, self.name = dim, f"hash-{dim}"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, np.float32)
        for w in re.findall(r"\w+", text.lower()):
            h = int.from_bytes(hashlib.md5(w.encode()).digest()[:8], "little")
            v[h % self.dim] += 1.0 if (h >> 40) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim),
                                                                              np.float32)


class BgeM3Embedder:
    """Needs the ``embed`` extra: ``uv sync --extra embed``. The model (~2.3 GB) is
    downloaded once into the Hugging Face cache on first use."""

    def __init__(self, model: str = BGE_M3, device: str | None = None, batch_size: int = 16,
                 max_tokens: int = 512):
        import torch

        want = device or os.environ.get("FILINGLENS_EMBED_DEVICE", "auto")
        if want == "auto":
            want = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self.model = self._load(model, want)
        except torch.OutOfMemoryError:              # GPU busy (e.g. a local LLM server)
            torch.cuda.empty_cache()
            want = "cpu"
            self.model = self._load(model, want)
        device = want
        self.model.max_seq_length = max_tokens       # chunks are <= 1,100 chars (~300 tokens)
        self.batch_size, self.device = batch_size, device
        self.name = model
        self.dim = int(self.model.get_embedding_dimension() or 1024)

    @staticmethod
    def _load(model: str, device: str):  # type: ignore[no-untyped-def]
        import torch
        from sentence_transformers import SentenceTransformer

        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        return SentenceTransformer(model, device=device, model_kwargs={"torch_dtype": dtype})

    def embed(self, texts: list[str]) -> np.ndarray:
        v = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                              convert_to_numpy=True, show_progress_bar=len(texts) > 64)
        return np.asarray(v, dtype=np.float32).reshape(len(texts), self.dim)


def get_embedder(name: str = "bge-m3") -> Embedder:
    if name == "hash":
        return HashEmbedder()
    if name in ("bge-m3", BGE_M3):
        return BgeM3Embedder()
    return BgeM3Embedder(model=name)                 # any sentence-transformers model id
