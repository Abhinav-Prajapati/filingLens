"""Text -> unit vectors. Two implementations behind one interface:

* ``BgeM3Embedder``: BAAI/bge-m3 (multilingual: English, Hindi, Hinglish), dense vectors,
  fp16 on a CUDA GPU (~1.1 GB VRAM, fits a 4 GB card), fp32 on CPU otherwise.
  FILINGLENS_EMBED_DEVICE=auto|cpu|cuda; "auto" falls back to CPU when the GPU is full
  (e.g. a local LLM server holds it). One query on CPU takes well under a second.
* ``HttpEmbedder``: an OpenAI-compatible ``/v1/embeddings`` server, here the llama.cpp
  router of docker-compose.yml (BGE-M3 as GGUF, CLS pooling). FILINGLENS_EMBED_URL turns it on;
  FILINGLENS_EMBED_MODEL is the preset for questions (CPU), FILINGLENS_EMBED_BULK_MODEL the one
  for bulk embedding (GPU). Both run the same GGUF, so their vectors share one index, named
  FILINGLENS_EMBED_NAME (default "bge-m3-gguf-q8"). No PyTorch needed.
* ``HashEmbedder``: deterministic bag-of-words hashing. Not semantic; for tests and CI,
  so nothing is downloaded there.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from typing import Protocol

import numpy as np

from filingLens.config import setting

BGE_M3 = "BAAI/bge-m3"
HTTP_INDEX_NAME = "bge-m3-gguf-q8"


class Embedder(Protocol):
    name: str

    @property
    def dim(self) -> int: ...

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
        except (torch.OutOfMemoryError, RuntimeError) as e:   # GPU busy (e.g. a local LLM)
            # newer torch raises AcceleratorError (a RuntimeError) when the CUDA context itself
            # cannot be created; only out-of-memory errors fall back to the CPU
            if want == "cpu" or "out of memory" not in str(e).lower():
                raise
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


class HttpEmbedder:
    """Vectors from an OpenAI-compatible embeddings endpoint, L2-normalised here."""

    def __init__(self, url: str, model: str, name: str | None = None, dim: int | None = None,
                 batch: int = 32, timeout: float = 600.0):
        """``dim``: vector size; asked from the server on first use when not given."""
        self.url, self.model = url.rstrip("/"), model
        self.name = name or setting("FILINGLENS_EMBED_NAME", HTTP_INDEX_NAME)
        self._dim, self.batch, self.timeout = dim, batch, timeout
        self.device = "gpu" if "gpu" in model else "cpu"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = int(self._post(["dimension probe"]).shape[1])
        return self._dim

    def _post(self, texts: list[str]) -> np.ndarray:
        req = urllib.request.Request(
            f"{self.url}/embeddings", json.dumps({"model": self.model, "input": texts}).encode(),
            {"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = sorted(json.load(r)["data"], key=lambda d: d["index"])
        return np.asarray([d["embedding"] for d in data], dtype=np.float32)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        v = np.concatenate([self._post(texts[i:i + self.batch])
                            for i in range(0, len(texts), self.batch)])
        self._dim = int(v.shape[1])
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return (v / np.where(n == 0, 1, n)).astype(np.float32)


def http_embedder(bulk: bool = False) -> HttpEmbedder | None:
    """The configured embeddings server (questions, or ``bulk`` jobs), or None."""
    url = setting("FILINGLENS_EMBED_URL")
    if not url:
        return None
    model = (setting("FILINGLENS_EMBED_BULK_MODEL", "bge-m3-gpu") if bulk
             else setting("FILINGLENS_EMBED_MODEL", "bge-m3-cpu"))
    return HttpEmbedder(url, model)


def target_index_name() -> str:
    """The model name new vectors are stored under: the HTTP backend's when one is
    configured, else BGE-M3 via sentence-transformers."""
    return setting("FILINGLENS_EMBED_NAME", HTTP_INDEX_NAME) if setting("FILINGLENS_EMBED_URL") \
        else BGE_M3


def get_embedder(name: str = "bge-m3") -> Embedder:
    """``name``: "hash", "bge-m3" (the HTTP server when FILINGLENS_EMBED_URL is set, else
    sentence-transformers), the HTTP index name, or a sentence-transformers model id."""
    if name == "hash":
        return HashEmbedder()
    http = http_embedder()
    if http is not None and name in ("bge-m3", http.name):
        return http
    if name in ("bge-m3", BGE_M3):
        return BgeM3Embedder()
    return BgeM3Embedder(model=name)                 # any sentence-transformers model id
