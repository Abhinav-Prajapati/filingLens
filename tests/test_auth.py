"""Accounts: sign-up, sign-in, sign-out, roles, and that the data routes need a session."""
import asyncio

import pytest

pytest.importorskip("fastapi_users")
from fastapi.testclient import TestClient

from filingLens.api.app import create_app
from filingLens.api.auth import COOKIE_NAME, make_auth
from filingLens.domain.registry import load_registry
from filingLens.storage.db import connect

PW = "correct horse 42"
DATA_ROUTES = [("get", "/companies"), ("get", "/metrics"),
               ("get", "/value?ticker=TCS&metric=revenue"),
               ("get", "/trend?ticker=TCS&metric=revenue"),
               ("get", "/compare?tickers=TCS&metrics=revenue"), ("get", "/search?q=attrition")]


@pytest.fixture
def app(tmp_path):
    connect().close()
    return create_app(None, data_dir=tmp_path, store=None, llm=None, registry=load_registry(),
                      equity_list=tmp_path / "none.csv", web_dir=None)


def register(c, email="ana@example.com", password=PW, **extra):
    return c.post("/auth/register", json={"email": email, "password": password, **extra})


def login(c, email="ana@example.com", password=PW):
    return c.post("/auth/login", data={"username": email, "password": password})


def test_signed_out_gets_401_except_health(app):
    with TestClient(app) as c:
        for method, path in DATA_ROUTES:
            assert getattr(c, method)(path).status_code == 401, path
        assert c.post("/ask", json={"question": "TCS revenue"}).status_code == 401
        assert c.get("/auth/me").status_code == 401
        assert c.get("/health").status_code == 200


def test_sign_up_sign_in_sign_out(app):
    with TestClient(app) as c:
        r = register(c)
        assert r.status_code == 201
        assert r.json()["role"] == "user" and "hashed_password" not in r.json()
        assert login(c, password="wrong password").status_code == 400
        r = login(c, email="ANA@example.com")                 # email is case-insensitive
        assert r.status_code == 204
        cookie = r.headers["set-cookie"].lower()
        assert COOKIE_NAME in cookie and "httponly" in cookie and "samesite=lax" in cookie
        me = c.get("/auth/me").json()
        assert me["email"] == "ana@example.com" and me["role"] == "user"
        assert c.get("/companies").status_code == 200
        assert c.post("/auth/logout").status_code == 204
        assert c.get("/companies").status_code == 401


def test_logout_ends_the_session_on_the_server(app):
    with TestClient(app) as c:
        register(c)
        login(c)
        token = c.cookies[COOKIE_NAME]
        c.post("/auth/logout")
        c.cookies.set(COOKIE_NAME, token)                     # replaying the old cookie
        assert c.get("/auth/me").status_code == 401


def test_sign_up_cannot_choose_a_role_or_privileges(app):
    with TestClient(app) as c:
        r = register(c, role="admin", is_superuser=True, is_verified=True)
        assert r.status_code == 201
        u = r.json()
        assert u["role"] == "user" and not u["is_superuser"] and not u["is_verified"]


def test_sign_up_rules(app):
    with TestClient(app) as c:
        assert register(c, password="short").status_code == 400
        assert register(c, email="not-an-email").status_code == 422
        assert register(c).status_code == 201
        r = register(c)
        assert r.status_code == 400 and r.json()["detail"] == "REGISTER_USER_ALREADY_EXISTS"


def test_create_admin_and_require_role(tmp_path):
    auth = make_auth()

    async def run():
        user, created = await auth.create_admin("boss@example.com", PW)
        assert created and user.role == "admin"
        with pytest.raises(ValueError):
            await auth.create_admin("new@example.com", None)    # new account needs a password
        await auth.close()
    asyncio.run(run())

    from fastapi import Depends, FastAPI

    from filingLens.api.auth import add_auth_routes

    app = FastAPI()
    add_auth_routes(app, auth)

    @app.get("/admin-only", dependencies=[Depends(auth.require_role("admin"))])
    def admin_only():
        return {"ok": True}

    with TestClient(app) as c:
        assert c.get("/admin-only").status_code == 401
        register(c)
        login(c)
        assert c.get("/admin-only").status_code == 403
        c.post("/auth/logout")
        login(c, email="boss@example.com")
        assert c.get("/admin-only").json() == {"ok": True}

    async def promote():                                      # promoting an existing user
        user, created = await auth.create_admin("ana@example.com", None)
        assert not created and user.role == "admin"
        await auth.close()
    asyncio.run(promote())
