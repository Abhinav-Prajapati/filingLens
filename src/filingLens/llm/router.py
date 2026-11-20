"""The llama.cpp router (docker-compose.yml, service "llm"): load and unload models on demand.

Used to run BGE-M3 on the GPU for bulk embedding: the chat model is unloaded first (4 GB of
VRAM holds one of them) and loaded again afterwards. URL: FILINGLENS_LLM_ROUTER_URL, default
FILINGLENS_EMBED_URL without its "/v1".
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from filingLens.config import setting


class RouterError(RuntimeError):
    pass


class Router:
    def __init__(self, url: str, timeout: float = 30.0):
        self.url, self.timeout = url.rstrip("/"), timeout

    @classmethod
    def from_env(cls) -> Router | None:
        url = setting("FILINGLENS_LLM_ROUTER_URL")
        if not url:
            embed = setting("FILINGLENS_EMBED_URL")
            url = embed[:-3] if embed.rstrip("/").endswith("/v1") else embed
        return cls(url) if url else None

    def _call(self, path: str, body: dict | None = None) -> Any:
        req = urllib.request.Request(
            self.url + path, json.dumps(body).encode() if body is not None else None,
            {"content-type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise RouterError(f"llama.cpp router at {self.url}: {e}") from None

    def status(self) -> dict[str, str]:
        """model -> "loaded" | "loading" | "unloaded" (as the router reports it)."""
        return {m["id"]: m.get("status", {}).get("value", "?")
                for m in self._call("/v1/models").get("data", [])}

    def load(self, model: str) -> None:
        if self.status().get(model) not in ("loaded", "loading"):
            self._call("/models/load", {"model": model})

    def unload(self, model: str) -> None:
        if self.status().get(model) in ("loaded", "loading"):
            self._call("/models/unload", {"model": model})

    def wait(self, model: str, state: str = "loaded", timeout: float = 180.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.status().get(model) == state:
                return
            time.sleep(0.25)
        raise RouterError(f"{model} did not become {state} within {timeout:.0f} s")
