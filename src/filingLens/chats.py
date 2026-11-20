"""Chats: each user's conversations on the Ask page, kept in Postgres.

A chat is named the moment it is created, from its first question (code, instant, works
without a model). When the LLM is available it is then asked for a short title, which replaces
the first one unless the user has renamed the chat. Every query is scoped to the user, so one
account never reads or changes another's chats.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from filingLens.llm.client import LLMClient
from filingLens.llm.executor import Context
from filingLens.llm.planner import named_tickers
from filingLens.storage.db import Database

TitleSource = Literal["question", "llm", "user"]
MAX_TITLE = 60
MAX_CHATS = 200                    # listed, newest first
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

TITLE_SYSTEM = """You name a chat about Indian listed companies' financials. Given its first
question, reply with a title of 2 to 6 words: the companies and the topic, e.g. "Infosys profit
drivers FY26" or "TCS vs Infosys revenue". Use only companies the question names. Reply with the
title only: no quotes, no full stop, no explanation. Write it in English."""


@dataclass
class Chat:
    id: str
    title: str
    title_source: TitleSource
    created_at: datetime
    updated_at: datetime
    turns: int = 0


@dataclass
class Turn:
    seq: int
    question: str
    answer_json: str


def title_from_question(question: str) -> str:
    """The first question, tidied: one line, no closing punctuation, at most 6 words."""
    words = re.sub(r"\s+", " ", question).strip().rstrip("?.!").split(" ")
    title = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 1].rstrip() + "…"
    return (title[:1].upper() + title[1:]) or "New chat"


def clean_title(text: str | None) -> str | None:
    """A model's title, or None when it is not usable (empty, long, chatty)."""
    if not text:
        return None
    t = _THINK.sub("", text).strip().splitlines()[0] if _THINK.sub("", text).strip() else ""
    t = re.sub(r"^(title|chat title)\s*:\s*", "", t, flags=re.IGNORECASE)
    for _ in range(2):                             # '"Title".' : quotes and stops, any order
        t = t.strip(" \"'“”‘’`*#").rstrip(".!?:;,").strip()
    if not t or len(t) > MAX_TITLE or len(t.split()) > 8:
        return None
    return t


def llm_title(llm: LLMClient, question: str, ctx: Context) -> str | None:
    """Ask the model for a title; None when the reply is unusable or names a company the
    question does not (small models fill gaps with well-known names)."""
    try:
        reply = llm.chat([{"role": "system", "content": TITLE_SYSTEM},
                          {"role": "user", "content": question}], [])
    except Exception:                              # noqa: BLE001 - keep the code title
        return None
    title = clean_title(reply.content)
    if title is None or not named_tickers(title, ctx) <= named_tickers(question, ctx):
        return None
    return title


class ChatRepo:
    def __init__(self, con: Database):
        self.con = con

    def create(self, user_id: str, title: str, source: TitleSource = "question") -> Chat:
        cid = uuid.uuid4().hex
        self.con.execute("INSERT INTO chat(id, user_id, title, title_source) VALUES (?, ?, ?, ?)",
                         (cid, user_id, title[:MAX_TITLE], source))
        chat = self.get(cid, user_id)
        assert chat is not None
        return chat

    def for_user(self, user_id: str) -> list[Chat]:
        rows = self.con.execute(
            "SELECT c.id, c.title, c.title_source, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM chat_turn t WHERE t.chat_id = c.id) AS n "
            "FROM chat c WHERE c.user_id = ? ORDER BY c.updated_at DESC LIMIT ?",
            (user_id, MAX_CHATS))
        return [Chat(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]

    def get(self, chat_id: str, user_id: str) -> Chat | None:
        r = self.con.execute(
            "SELECT c.id, c.title, c.title_source, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM chat_turn t WHERE t.chat_id = c.id) "
            "FROM chat c WHERE c.id = ? AND c.user_id = ?", (chat_id, user_id)).fetchone()
        return Chat(r[0], r[1], r[2], r[3], r[4], r[5]) if r else None

    def turns(self, chat_id: str) -> list[Turn]:
        return [Turn(r[0], r[1], r[2]) for r in self.con.execute(
            "SELECT seq, question, answer_json FROM chat_turn WHERE chat_id = ? ORDER BY seq",
            (chat_id,))]

    def add_turn(self, chat_id: str, question: str, answer_json: str) -> int:
        with self.con.transaction():
            seq = self.con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_turn "
                                   "WHERE chat_id = ?", (chat_id,)).fetchone()[0]
            self.con.execute("INSERT INTO chat_turn(chat_id, seq, question, answer_json) "
                             "VALUES (?, ?, ?, ?)", (chat_id, seq, question, answer_json))
            self.con.execute("UPDATE chat SET updated_at = now() WHERE id = ?", (chat_id,))
        return int(seq)

    def rename(self, chat_id: str, user_id: str, title: str) -> bool:
        return self.con.execute(
            "UPDATE chat SET title = ?, title_source = 'user' WHERE id = ? AND user_id = ?",
            (title[:MAX_TITLE], chat_id, user_id)).rowcount > 0

    def set_llm_title(self, chat_id: str, title: str) -> bool:
        """Only replaces the title made from the question: a name the user gave is kept."""
        return self.con.execute(
            "UPDATE chat SET title = ?, title_source = 'llm' WHERE id = ? "
            "AND title_source = 'question'", (title[:MAX_TITLE], chat_id)).rowcount > 0

    def delete(self, chat_id: str, user_id: str) -> bool:
        return self.con.execute("DELETE FROM chat WHERE id = ? AND user_id = ?",
                                (chat_id, user_id)).rowcount > 0
