"""管理员 CLI 测试：忘记密码时的救援手段。"""

from __future__ import annotations

import pytest

from app.cli import CliError, create_admin, delete_admin, list_admins, reset_password
from app.db import SessionFactory
from app.models import Admin, AuthSession
from app.security import verify_password


async def test_create_admin_and_list(client):
    assert await list_admins() == []
    message = await create_admin("admin", "S3cret-pass")
    assert "已创建" in message

    rows = await list_admins()
    assert rows[0][0] == "admin"

    async with SessionFactory() as session:
        admin = (await session.execute(__import__("sqlalchemy").select(Admin))).scalars().first()
    assert verify_password("S3cret-pass", admin.password_hash)
    assert not verify_password("wrong", admin.password_hash)


async def test_duplicate_admin_rejected_unless_reset(client):
    await create_admin("admin", "S3cret-pass")
    with pytest.raises(CliError, match="已存在"):
        await create_admin("admin", "Another-pass")

    message = await create_admin("admin", "Another-pass", reset=True)
    assert "已重置" in message
    async with SessionFactory() as session:
        admin = (await session.execute(__import__("sqlalchemy").select(Admin))).scalars().first()
    assert verify_password("Another-pass", admin.password_hash)


async def test_weak_password_rejected():
    with pytest.raises(CliError, match="至少 8 位"):
        await create_admin("admin2", "short")
    with pytest.raises(CliError, match="纯数字"):
        await create_admin("admin2", "12345678")


async def test_reset_password_invalidates_sessions(client):
    await create_admin("admin", "S3cret-pass")
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "S3cret-pass"})
    assert login.status_code == 200
    assert (await client.get("/api/drives")).status_code == 200

    message = await reset_password("admin", "Brand-new-pass")
    assert "失效" in message

    async with SessionFactory() as session:
        remaining = (await session.execute(__import__("sqlalchemy").select(AuthSession))).scalars().all()
    assert remaining == []
    assert (await client.get("/api/drives")).status_code == 401

    relogin = await client.post("/api/auth/login", json={"username": "admin", "password": "Brand-new-pass"})
    assert relogin.status_code == 200


async def test_reset_unknown_user(client):
    with pytest.raises(CliError, match="找不到"):
        await reset_password("nobody", "Brand-new-pass")


async def test_delete_admin_allows_reinit(client):
    await create_admin("admin", "S3cret-pass")
    await delete_admin("admin")
    assert await list_admins() == []
    status = (await client.get("/api/auth/status")).json()
    assert status["setup_required"] is True
