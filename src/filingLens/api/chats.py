"""Chat routes: the signed-in user's conversations (see filingLens.chats).

    GET    /chats              newest first
    GET    /chats/{id}         one chat with its turns (each the AnswerView shown)
    PATCH  /chats/{id}         rename
    DELETE /chats/{id}
    POST   /chats/import       the conversation kept in the browser before chats existed

POST /ask with ``chat_id`` adds a turn to that chat; without one it starts a chat (see app.py).
Someone else's chat is a 404, never a 403, so ids reveal nothing.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from filingLens.chats import MAX_TITLE, Chat, ChatRepo, title_from_question
from filingLens.views import AnswerView


class ChatInfo(BaseModel):
    id: str
    title: str
    title_source: str                    # "question" | "llm" | "user"
    created_at: datetime
    updated_at: datetime
    turns: int


class ChatTurn(BaseModel):
    question: str                        # as typed
    answer: AnswerView


class ChatDetail(ChatInfo):
    items: list[ChatTurn]


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)


class ImportChat(BaseModel):
    turns: list[ChatTurn] = Field(min_length=1, max_length=50)


def info(c: Chat) -> ChatInfo:
    return ChatInfo(id=c.id, title=c.title, title_source=c.title_source,
                    created_at=c.created_at, updated_at=c.updated_at, turns=c.turns)


def add_chat_routes(app: FastAPI, current_user: Any, repo: ChatRepo,
                    lock: threading.Lock) -> None:
    def uid(user: Any) -> str:
        return str(user.id)

    def owned(chat_id: str, user: Any) -> Chat:
        with lock:
            c = repo.get(chat_id, uid(user))
        if c is None:
            raise HTTPException(404, "chat not found")
        return c

    @app.get("/chats", response_model=list[ChatInfo])
    def list_chats(user: Any = Depends(current_user)) -> list[ChatInfo]:
        with lock:
            return [info(c) for c in repo.for_user(uid(user))]

    @app.get("/chats/{chat_id}", response_model=ChatDetail)
    def get_chat(chat_id: str, user: Any = Depends(current_user)) -> ChatDetail:
        c = owned(chat_id, user)
        with lock:
            turns = repo.turns(c.id)
        items = [ChatTurn(question=t.question, answer=AnswerView.model_validate_json(t.answer_json))
                 for t in turns]
        return ChatDetail(**info(c).model_dump(), items=items)

    @app.patch("/chats/{chat_id}", response_model=ChatInfo)
    def rename_chat(chat_id: str, body: Rename, user: Any = Depends(current_user)) -> ChatInfo:
        title = " ".join(body.title.split())
        if not title:
            raise HTTPException(422, "the title is empty")
        owned(chat_id, user)
        with lock:
            repo.rename(chat_id, uid(user), title)
        return info(owned(chat_id, user))

    @app.delete("/chats/{chat_id}", status_code=204)
    def delete_chat(chat_id: str, user: Any = Depends(current_user)) -> Response:
        with lock:
            gone = repo.delete(chat_id, uid(user))
        if not gone:
            raise HTTPException(404, "chat not found")
        return Response(status_code=204)

    @app.post("/chats/import", response_model=ChatInfo)
    def import_chat(body: ImportChat, user: Any = Depends(current_user)) -> ChatInfo:
        with lock:
            c = repo.create(uid(user), title_from_question(body.turns[0].question))
            for t in body.turns:
                repo.add_turn(c.id, t.question, t.answer.model_dump_json())
        return info(owned(c.id, user))
