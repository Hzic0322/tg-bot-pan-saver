"""后台登录 / 首次初始化相关测试。"""

from __future__ import annotations


async def test_setup_required_on_fresh_install(client):
    resp = await client.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setup_required"] is True
    assert data["authenticated"] is False


async def test_setup_then_authenticated(client):
    resp = await client.post("/api/auth/setup", json={"username": "admin", "password": "S3cret-pass"})
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True

    status = (await client.get("/api/auth/status")).json()
    assert status["setup_required"] is False
    assert status["authenticated"] is True

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


async def test_second_setup_rejected(client):
    first = await client.post("/api/auth/setup", json={"username": "admin", "password": "S3cret-pass"})
    assert first.status_code == 200
    second = await client.post("/api/auth/setup", json={"username": "other", "password": "Another-pass1"})
    assert second.status_code == 409


async def test_weak_password_rejected(client):
    resp = await client.post("/api/auth/setup", json={"username": "admin", "password": "12345678"})
    assert resp.status_code == 400
    resp = await client.post("/api/auth/setup", json={"username": "admin", "password": "short"})
    assert resp.status_code == 400


async def test_login_flow_and_guard(client):
    await client.post("/api/auth/setup", json={"username": "admin", "password": "S3cret-pass"})
    await client.post("/api/auth/logout")

    # 登出后受保护接口应 401
    guarded = await client.get("/api/drives")
    assert guarded.status_code == 401

    bad = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert bad.status_code == 401

    good = await client.post("/api/auth/login", json={"username": "admin", "password": "S3cret-pass"})
    assert good.status_code == 200
    assert (await client.get("/api/drives")).status_code == 200


async def test_change_password(admin):
    resp = await admin.post(
        "/api/auth/password", json={"old_password": "S3cret-pass", "new_password": "Even-better-2"}
    )
    assert resp.status_code == 200
    await admin.post("/api/auth/logout")
    old = await admin.post("/api/auth/login", json={"username": "admin", "password": "S3cret-pass"})
    assert old.status_code == 401
    new = await admin.post("/api/auth/login", json={"username": "admin", "password": "Even-better-2"})
    assert new.status_code == 200


async def test_setup_blocked_when_disabled_without_token(client, monkeypatch):
    """ALLOW_OPEN_SETUP=false 且没配 SETUP_TOKEN 时，不能被人抢注管理员。"""
    from app.api import auth as auth_api

    monkeypatch.setattr(auth_api.settings, "allow_open_setup", False)
    monkeypatch.setattr(auth_api.settings, "setup_token", "")

    resp = await client.post("/api/auth/setup", json={"username": "attacker", "password": "S3cret-pass"})
    assert resp.status_code == 403
    assert "SETUP_TOKEN" in resp.json()["detail"]
    assert (await client.get("/api/auth/status")).json()["setup_required"] is True


async def test_setup_requires_matching_setup_token(client, monkeypatch):
    from app.api import auth as auth_api

    monkeypatch.setattr(auth_api.settings, "allow_open_setup", False)
    monkeypatch.setattr(auth_api.settings, "setup_token", "let-me-in")

    wrong = await client.post("/api/auth/setup", json={"username": "admin", "password": "S3cret-pass"})
    assert wrong.status_code == 403
    assert "口令错误" in wrong.json()["detail"]

    ok = await client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "S3cret-pass", "setup_token": "let-me-in"},
    )
    assert ok.status_code == 200
    assert ok.json()["authenticated"] is True


async def test_setup_records_last_login(client):
    await client.post("/api/auth/setup", json={"username": "admin", "password": "S3cret-pass"})
    from sqlalchemy import select

    from app.db import SessionFactory
    from app.models import Admin

    async with SessionFactory() as session:
        admin = (await session.execute(select(Admin))).scalars().first()
    assert admin.last_login_at is not None
