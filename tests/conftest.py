"""测试夹具：临时数据库 + Mock 网盘 + 未配置 Bot 的 FastAPI 客户端。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP_DIR = Path(tempfile.mkdtemp(prefix="pansaver-test-"))
os.environ.update(
    {
        "DATA_DIR": str(TMP_DIR),
        "DATABASE_URL": f"sqlite+aiosqlite:///{TMP_DIR / 'test.db'}",
        "ENABLE_MOCK": "1",
        "TELEGRAM_BOT_TOKEN": "",
        "BOT_ENABLED": "false",
        "ALLOW_OPEN_SETUP": "true",
        "SESSION_TTL_HOURS": "24",
    }
)

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://pansaver.test") as http:
        yield http


@pytest_asyncio.fixture
async def admin(client: AsyncClient):
    resp = await client.post(
        "/api/auth/setup", json={"username": "admin", "password": "S3cret-pass"}
    )
    assert resp.status_code == 200, resp.text
    return client
