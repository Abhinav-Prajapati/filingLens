"""Chat client for any OpenAI-compatible endpoint (vLLM, Ollama, llama.cpp server, hosted
providers), plus FakeLLM for tests.

Configuration (environment variables, or a `.env` file in the working directory):

    FILINGLENS_LLM_BASE_URL   e.g. http://localhost:8000/v1   (Ollama: http://localhost:11434/v1)
    FILINGLENS_LLM_API_KEY    any string for local servers
    FILINGLENS_LLM_MODEL      e.g. Qwen/Qwen2.5-7B-Instruct
    FILINGLENS_LLM_TOOLS      "native" (default) or "json": servers without tool-calling
                              support get the same instructions and answer in plain JSON

Small models often return a tool call as text instead of a structured tool_call, e.g.
`<tool_call>{"name": ..., "arguments": {...}}</tool_call>`; `parse_text_call` accepts that.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ENV_PREFIX = "FILINGLENS_LLM_"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None          # set for native tool calls (needed to send errors back)
    raw: str = ""                  # arguments as received, for error messages


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None       # unparseable tool call, etc.


class LLMClient(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply: ...


def _load_dotenv(path: Path = Path(".env")) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    tools_mode: str = "native"      # "native" | "json"
    temperature: float = 0.0
    max_tokens: int = 400
    timeout: float = 60.0

    @classmethod
    def from_env(cls, dotenv: Path = Path(".env")) -> LLMConfig:
        env: dict[str, str] = {**_load_dotenv(dotenv), **os.environ}

        def get(key: str, default: str = "") -> str:
            return env.get(ENV_PREFIX + key, default)

        return cls(get("BASE_URL"), get("API_KEY"), get("MODEL"), get("TOOLS", "native"),
                   float(get("TEMPERATURE", "0")), int(get("MAX_TOKENS", "400")),
                   float(get("TIMEOUT", "60")))

    def missing(self) -> list[str]:
        return [ENV_PREFIX + k for k, v in (("BASE_URL", self.base_url),
                                            ("MODEL", self.model)) if not v]

    def masked(self) -> str:
        key = f"{self.api_key[:4]}…" if self.api_key else "(none)"
        return f"{self.base_url}  model={self.model}  key={key}  tools={self.tools_mode}"


_TAGGED = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _first_json_object(text: str) -> str | None:
    """The first balanced {...} in text (models often add prose or ``` fences)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc, in_str = (False, in_str) if esc else (c == "\\", c != '"')
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def parse_text_call(content: str) -> ToolCall | None:
    """A tool call written as text: {"name": ..., "arguments": {...}} (or "tool"/"args"),
    optionally inside <tool_call> tags or a code fence. None if there is none."""
    m = _TAGGED.search(content)
    blob = m.group(1) if m else _first_json_object(content)
    if blob is None:
        return None
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return None
    name = obj.get("name") or obj.get("tool") if isinstance(obj, dict) else None
    args = obj.get("arguments", obj.get("args", obj.get("parameters", {}))) if name else None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return ToolCall(str(name), {}, raw=args)
    return ToolCall(str(name), args if isinstance(args, dict) else {}, raw=blob) if name else None


class OpenAICompatClient:
    """Thin wrapper over the `openai` SDK pointed at any compatible base URL."""

    def __init__(self, cfg: LLMConfig):
        from openai import OpenAI

        if cfg.missing():
            raise RuntimeError(f"LLM not configured; set {', '.join(cfg.missing())}")
        self.cfg, self.model = cfg, cfg.model
        self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "none",
                              timeout=cfg.timeout)

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        kw: dict[str, Any] = {"model": self.cfg.model, "messages": messages,
                              "temperature": self.cfg.temperature,
                              "max_tokens": self.cfg.max_tokens}
        if tools and self.cfg.tools_mode == "native":
            kw |= {"tools": tools, "tool_choice": "auto"}
        msg = self._client.chat.completions.create(**kw).choices[0].message
        reply = LLMReply(content=msg.content or "")
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError as e:
                reply.error = f"arguments are not valid JSON: {e}"
                args = {}
            reply.tool_calls.append(ToolCall(tc.function.name, args if isinstance(args, dict)
                                             else {}, tc.id, raw))
        if not reply.tool_calls:
            call = parse_text_call(reply.content)
            if call:
                reply.tool_calls.append(call)
        return reply


class FakeLLM:
    """Replays scripted replies (LLMReply or text) and records every request. For tests."""

    def __init__(self, replies: list[LLMReply | str], model: str = "fake"):
        self.replies, self.model = list(replies), model
        self.requests: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        self.requests.append([dict(m) for m in messages])
        r = self.replies.pop(0)
        if isinstance(r, str):
            call = parse_text_call(r)
            return LLMReply(content=r, tool_calls=[call] if call else [])
        return r


def make_client(cfg: LLMConfig | None = None) -> OpenAICompatClient:
    return OpenAICompatClient(cfg or LLMConfig.from_env())
