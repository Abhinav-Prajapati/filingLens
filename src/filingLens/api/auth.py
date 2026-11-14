"""Accounts and sign-in for the HTTP API (fastapi-users).

    POST /auth/register   {"email", "password"}             open sign-up; new accounts are "user"
    POST /auth/login      form: username=<email>, password    sets an HttpOnly session cookie
    POST /auth/logout                                         ends the session (server side)
    GET  /auth/me                                             the signed-in user, with role

Accounts live in Postgres (tables ``user`` and ``access_token``, next to the filings data).
Sessions are rows there (fastapi-users' database strategy), so signing out ends a session for
good. Passwords are hashed with argon2 (pwdlib).

Roles: ``admin`` and ``user``. Sign-up always makes a ``user``; an admin is made with
``filinglens users create-admin EMAIL``. ``Auth.require_role`` is the check routes use.
"""
from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import Depends, HTTPException
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users import schemas as fu_schemas
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import (
    SQLAlchemyAccessTokenDatabase,
    SQLAlchemyBaseAccessTokenTableUUID,
)
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from filingLens.config import database_url as default_database_url
from filingLens.config import setting

SESSION_SECONDS = 7 * 24 * 3600
COOKIE_NAME = "filinglens_session"
MIN_PASSWORD = 8


class Role(str, Enum):
    admin = "admin"
    user = "user"


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "user"
    role: Mapped[str] = mapped_column(String(16), default=Role.user.value, nullable=False)


class AccessToken(SQLAlchemyBaseAccessTokenTableUUID, Base):
    __tablename__ = "access_token"


class UserRead(fu_schemas.BaseUser[uuid.UUID]):
    role: Role


class UserCreate(fu_schemas.BaseUserCreate):
    """Sign-up body. There is no role field: every new account is a plain user, and
    fastapi-users drops is_superuser / is_active / is_verified from public sign-ups."""


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def validate_password(self, password: str, user: Any) -> None:
        if len(password) < MIN_PASSWORD:
            raise InvalidPasswordException(f"Use at least {MIN_PASSWORD} characters")
        if user.email and user.email.split("@")[0].lower() in password.lower():
            raise InvalidPasswordException("The password must not contain your email name")


@dataclass
class Auth:
    """Everything the app needs, bound to one accounts database."""
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    users: FastAPIUsers[User, uuid.UUID]
    backend: AuthenticationBackend[User, uuid.UUID]
    current_user: Callable[..., Any]          # dependency: the signed-in, active user (or 401)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    def require_role(self, *roles: Role | str) -> Callable[..., Any]:
        """Dependency: 401 when signed out, 403 unless the user has one of ``roles``."""
        allowed = {Role(r).value for r in roles}
        current = self.current_user

        async def check(user: User = Depends(current)) -> User:
            if user.role not in allowed:
                raise HTTPException(403, "You don't have access to this")
            return user
        return check

    async def create_admin(self, email: str, password: str | None) -> tuple[User, bool]:
        """Make ``email`` an admin; creates the account when it doesn't exist (then
        ``password`` is required). Returns (user, created)."""
        await self.init()
        async with self.sessions() as s:
            db: SQLAlchemyUserDatabase[User, uuid.UUID] = SQLAlchemyUserDatabase(s, User)
            manager = UserManager(db)
            existing = (await s.execute(select(User).where(func.lower(User.email) == email.lower())))
            user = existing.scalar_one_or_none()
            if user is not None:
                user = await db.update(user, {"role": Role.admin.value})
                return user, False
            if not password:
                raise ValueError("a password is needed to create a new account")
            user = await manager.create(UserCreate(email=email, password=password))
            user = await db.update(user, {"role": Role.admin.value, "is_verified": True})
            return user, True


def make_auth(database_url: str | None = None, cookie_secure: bool | None = None) -> Auth:
    """Accounts in Postgres (default FILINGLENS_DATABASE_URL), tables ``user`` and
    ``access_token``. ``cookie_secure``: send the session cookie over HTTPS only. Default from
    FILINGLENS_COOKIE_SECURE (off, because the app is usually served over plain HTTP on
    localhost); turn it on behind HTTPS."""
    if cookie_secure is None:
        cookie_secure = setting("FILINGLENS_COOKIE_SECURE").lower() in {"1", "true"}
    # signs fastapi-users' reset/verify tokens (not used yet); sessions don't depend on it
    secret = setting("FILINGLENS_AUTH_SECRET") or secrets.token_urlsafe(32)

    url = database_url or default_database_url()
    engine = create_async_engine(url.replace("postgresql://", "postgresql+psycopg://", 1),
                                 pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def get_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    async def get_user_db(s: AsyncSession = Depends(get_session)) -> AsyncIterator[Any]:
        yield SQLAlchemyUserDatabase(s, User)

    async def get_token_db(s: AsyncSession = Depends(get_session)) -> AsyncIterator[Any]:
        yield SQLAlchemyAccessTokenDatabase(s, AccessToken)

    async def get_manager(db: Any = Depends(get_user_db)) -> AsyncIterator[UserManager]:
        m = UserManager(db)
        m.reset_password_token_secret = m.verification_token_secret = secret
        yield m

    def get_strategy(tokens: Any = Depends(get_token_db)) -> DatabaseStrategy[Any, Any, Any]:
        return DatabaseStrategy(tokens, lifetime_seconds=SESSION_SECONDS)

    transport = CookieTransport(cookie_name=COOKIE_NAME, cookie_max_age=SESSION_SECONDS,
                                cookie_secure=cookie_secure, cookie_httponly=True,
                                cookie_samesite="lax")
    backend = AuthenticationBackend(name="cookie", transport=transport,
                                    get_strategy=get_strategy)
    users = FastAPIUsers[User, uuid.UUID](get_manager, [backend])
    return Auth(engine=engine, sessions=sessions, users=users, backend=backend,
                current_user=users.current_user(active=True))


def add_auth_routes(app: Any, auth: Auth) -> None:
    app.include_router(auth.users.get_auth_router(auth.backend), prefix="/auth", tags=["auth"])
    app.include_router(auth.users.get_register_router(UserRead, UserCreate), prefix="/auth",
                       tags=["auth"])

    @app.get("/auth/me", response_model=UserRead, tags=["auth"])
    async def me(user: User = Depends(auth.current_user)) -> User:
        return user
