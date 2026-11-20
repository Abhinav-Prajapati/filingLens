"""Embeddings over HTTP and the llama.cpp router: a fake server stands in for llama.cpp."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from filingLens.llm.router import Router, RouterError
from filingLens.retrieval.embedder import HttpEmbedder, get_embedder, target_index_name


class FakeLlama:
    """/v1/embeddings (deterministic vectors), /v1/models, /models/load, /models/unload."""

    def __init__(self, loaded=("qwen", "bge-m3-cpu")):
        self.state = {"qwen": "unloaded", "bge-m3-cpu": "unloaded", "bge-m3-gpu": "unloaded"}
        self.state.update({m: "loaded" for m in loaded})
        self.calls, self.inputs = [], []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, code=200):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/v1/models":
                    self._send({"data": [{"id": m, "status": {"value": s}}
                                         for m, s in fake.state.items()]})
                else:
                    self._send({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.calls.append((self.path, body.get("model")))
                if self.path == "/v1/embeddings":
                    if fake.state.get(body["model"]) != "loaded":
                        return self._send({"error": "model not loaded"}, 503)
                    fake.inputs.append(body["input"])
                    vecs = [[float(len(t)), 1.0, 2.0] for t in body["input"]]
                    return self._send({"data": [{"index": i, "embedding": v}
                                                for i, v in reversed(list(enumerate(vecs)))]})
                if self.path in ("/models/load", "/models/unload"):
                    fake.state[body["model"]] = "loaded" if self.path.endswith("load") and \
                        not self.path.endswith("unload") else "unloaded"
                    return self._send({"success": True})
                self._send({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def llama():
    f = FakeLlama()
    yield f
    f.close()


def test_http_embedder_batches_orders_and_normalises(llama):
    emb = HttpEmbedder(llama.url + "/v1", "bge-m3-cpu", dim=3, batch=2)
    v = emb.embed(["a", "bbb", "cc"])
    assert v.shape == (3, 3) and np.allclose(np.linalg.norm(v, axis=1), 1)
    raw = np.array([[1, 1, 2], [3, 1, 2], [2, 1, 2]], np.float32)
    assert np.allclose(v, raw / np.linalg.norm(raw, axis=1, keepdims=True))   # input order kept
    assert llama.inputs == [["a", "bbb"], ["cc"]] and emb.embed([]).shape == (0, 3)
    assert emb.name == "bge-m3-gguf-q8" and emb.device == "cpu"


def test_embedder_selection_follows_the_settings(monkeypatch, llama):
    assert target_index_name() == "BAAI/bge-m3"                       # nothing configured
    monkeypatch.setenv("FILINGLENS_EMBED_URL", llama.url + "/v1")
    assert target_index_name() == "bge-m3-gguf-q8"
    e = get_embedder("bge-m3")
    assert isinstance(e, HttpEmbedder) and e.model == "bge-m3-cpu"     # questions: CPU preset
    assert isinstance(get_embedder("bge-m3-gguf-q8"), HttpEmbedder)    # the index's own name
    assert get_embedder("hash").name.startswith("hash")


def test_router_load_unload_wait(llama):
    r = Router(llama.url)
    assert r.status()["qwen"] == "loaded"
    r.unload("qwen")
    r.wait("qwen", "unloaded", timeout=2)
    r.load("bge-m3-gpu")
    r.wait("bge-m3-gpu", timeout=2)
    r.load("bge-m3-gpu")                         # already loaded: no second request
    assert [c for c in llama.calls if c[1] == "bge-m3-gpu"] == [("/models/load", "bge-m3-gpu")]
    with pytest.raises(RouterError):
        r.wait("qwen", "loaded", timeout=0.3)
    with pytest.raises(RouterError):
        Router("http://127.0.0.1:9", timeout=1).status()                 # nothing listening


def test_router_url_from_the_embeddings_url(monkeypatch):
    assert Router.from_env() is None
    monkeypatch.setenv("FILINGLENS_EMBED_URL", "http://127.0.0.1:8081/v1")
    assert Router.from_env().url == "http://127.0.0.1:8081"


# ---- the GPU embedding job: swap the chat model out and back, pause the LLM meanwhile ----------
def _app_with_router(tmp_path, llama, monkeypatch):
    from conftest import make_fact

    from filingLens.api.app import create_app
    from filingLens.domain.models import Chunk
    from filingLens.domain.registry import load_registry
    from filingLens.storage.db import connect
    from filingLens.storage.repos import FactRepo, TextRepo

    monkeypatch.setenv("FILINGLENS_EMBED_URL", llama.url + "/v1")
    monkeypatch.setenv("FILINGLENS_LLM_MODEL", "qwen")
    con = connect()
    FactRepo(con).upsert_facts([make_fact(fy="FY24")])
    con.execute("INSERT INTO document(doc_id, ticker, doc_type, fy, original_name) VALUES "
                "('ar', 'TCS', 'annual_report', 'FY24', 'TCS_annual_FY24.pdf')")
    TextRepo(con).replace("ar", [], [Chunk(f"c{i}", "ar", "TCS", "FY24", "annual_report", "none",
                                           "text", "mdna", i, i, f"text {i}", f"TCS p{i}")
                                     for i in range(3)])
    con.close()
    return create_app(None, llm=None, registry=load_registry(), web_dir=None, data_dir=tmp_path,
                      store=None, equity_list=tmp_path / "none.csv")


def test_gpu_job_unloads_the_llm_and_reloads_it(tmp_path, llama, monkeypatch):
    from fastapi.testclient import TestClient

    app = _app_with_router(tmp_path, llama, monkeypatch)
    emb = app.state.embeddings
    assert (emb.llm_model, emb.bulk_model) == ("qwen", "bge-m3-gpu")
    seen = {}
    real = emb.factory

    def factory(model, device):              # look at the state while the job runs
        seen["paused"] = app.state.embeddings.st.llm_paused
        seen["models"] = dict(llama.state)
        return real(model, device)
    emb.factory = factory
    with TestClient(app):
        emb.start(__import__("filingLens.api.embeddings", fromlist=["EmbedRequest"])
                  .EmbedRequest(device="auto"))
        for _ in range(100):
            if not emb.running:
                break
            time.sleep(0.05)
    job = emb.job
    assert job.state == "done" and job.embedded == 3 and job.model == "bge-m3-gguf-q8"
    assert seen["paused"] and seen["models"]["qwen"] == "unloaded"
    assert seen["models"]["bge-m3-gpu"] == "loaded"
    assert llama.state["bge-m3-gpu"] == "unloaded" and llama.state["qwen"] == "loaded"   # back
    assert emb.st.llm_paused is None and not job.llm_paused
    order = [c for c in llama.calls if c[0].startswith("/models")]
    assert order == [("/models/unload", "qwen"), ("/models/load", "bge-m3-gpu"),
                     ("/models/unload", "bge-m3-gpu"), ("/models/load", "qwen")]


def test_cpu_job_leaves_the_llm_alone(tmp_path, llama, monkeypatch):
    from fastapi.testclient import TestClient

    from filingLens.api.embeddings import EmbedRequest

    app = _app_with_router(tmp_path, llama, monkeypatch)
    emb = app.state.embeddings
    with TestClient(app):
        emb.start(EmbedRequest(device="cpu"))
        for _ in range(100):
            if not emb.running:
                break
            time.sleep(0.05)
    assert emb.job.state == "done" and emb.job.device == "cpu"
    assert not [c for c in llama.calls if c[0].startswith("/models")]


def test_failed_gpu_job_still_unpauses_and_reloads_the_llm(tmp_path, llama, monkeypatch):
    from fastapi.testclient import TestClient

    from filingLens.api.embeddings import EmbedRequest

    app = _app_with_router(tmp_path, llama, monkeypatch)
    emb = app.state.embeddings

    def broken(model, device):
        raise RuntimeError("boom")
    emb.factory = broken
    with TestClient(app):
        emb.start(EmbedRequest(device="auto"))
        for _ in range(100):
            if not emb.running:
                break
            time.sleep(0.05)
    assert emb.job.state == "failed" and "boom" in emb.job.error
    assert emb.st.llm_paused is None and llama.state["qwen"] == "loaded"


def test_questions_are_answered_without_the_llm_while_paused(tmp_path, llama, monkeypatch):
    from fastapi.testclient import TestClient

    from filingLens.llm.client import FakeLLM

    monkeypatch.setenv("FILINGLENS_EMBED_URL", llama.url + "/v1")
    from filingLens.api.app import create_app
    from filingLens.domain.registry import load_registry
    from filingLens.storage.db import connect

    connect().close()
    app = create_app(None, llm=FakeLLM([]), registry=load_registry(), web_dir=None,
                     data_dir=tmp_path, store=None, equity_list=tmp_path / "none.csv")
    with TestClient(app) as c:
        c.post("/auth/register", json={"email": "a@example.com", "password": "correct horse 42"})
        c.post("/auth/login", data={"username": "a@example.com", "password": "correct horse 42"})
        app.state.embeddings.st.llm_paused = "reports are being embedded on the GPU"
        a = c.post("/ask", json={"question": "TCS revenue FY24"}).json()
    assert a["mode"] == "rules"
    assert any("paused: reports are being embedded on the GPU" in n for n in a["notes"])
