"""网盘绑定、目录、转存、日志的端到端测试（走 Mock 网盘，不联网）。"""

from __future__ import annotations

from typing import Any

from app.drives.mock import MockDrive
from app.drives.base import QRPoll


async def _bind_mock(client) -> None:
    resp = await client.post("/api/drives/mock/cookie", json={"cookie": "MOCK_SESSION=mock-token"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


async def test_drive_list_contains_four_official_drives(admin):
    data = (await admin.get("/api/drives")).json()
    keys = [item["key"] for item in data["items"]]
    for key in ("115", "baidu", "quark", "xunlei"):
        assert key in keys
    mock = next(item for item in data["items"] if item["key"] == "mock")
    assert mock["bound"] is False


async def test_cookie_bind_and_dirs(admin):
    await _bind_mock(admin)
    drives = (await admin.get("/api/drives")).json()["items"]
    mock = next(item for item in drives if item["key"] == "mock")
    assert mock["bound"] is True
    assert mock["status"] == "ok"
    assert mock["account_name"] == "Mock 用户"

    dirs = (await admin.get("/api/drives/mock/dirs?parent_id=0")).json()
    assert [d["name"] for d in dirs["items"]] == ["电影", "剧集", "音乐"]


async def test_set_default_dir_and_manual_save(admin):
    await _bind_mock(admin)
    resp = await admin.put("/api/drives/mock/default-dir", json={"dir_id": "100", "dir_name": "电影"})
    assert resp.json()["default_dir_name"] == "电影"

    save = await admin.post(
        "/api/drives/save",
        json={"drive": "mock", "share_url": "https://mockdrive.test/s/abc123", "passcode": "mock"},
    )
    body = save.json()
    assert body["ok"] is True
    assert body["files"] == 2

    logs = (await admin.get("/api/logs")).json()
    assert logs["total"] == 1
    assert logs["items"][0]["status"] == "success"
    assert logs["items"][0]["dir_id"] == "100"
    assert logs["items"][0]["source"] == "web"


async def test_save_failure_logged(admin):
    await _bind_mock(admin)
    save = await admin.post(
        "/api/drives/save",
        json={"drive": "mock", "share_url": "https://mockdrive.test/s/abc123", "passcode": "nope"},
    )
    assert save.json()["ok"] is False
    logs = (await admin.get("/api/logs?status=failed")).json()
    assert logs["total"] == 1
    assert logs["items"][0]["status"] == "failed"


async def test_unbound_drive_returns_error(admin):
    resp = await admin.post(
        "/api/drives/save",
        json={"drive": "quark", "share_url": "https://pan.quark.cn/s/abc"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "未绑定" in resp.json()["message"]


async def test_qr_login_flow(admin, monkeypatch):
    async def fake_poll(self: MockDrive, state: dict[str, Any]) -> QRPoll:
        return QRPoll(
            status="success",
            message="登录成功",
            cookie={"MOCK_SESSION": "mock-token"},
            account_name="Mock 用户",
        )

    monkeypatch.setattr(MockDrive, "qr_poll", fake_poll)

    start = await admin.post("/api/drives/mock/qr")
    assert start.status_code == 200, start.text
    qr_id = start.json()["qr_id"]
    assert start.json()["mode"] == "content"

    png = await admin.get(f"/api/drives/mock/qr/{qr_id}/png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"

    polled = await admin.get(f"/api/drives/mock/qr/{qr_id}")
    body = polled.json()
    assert body["status"] == "success"

    drives = (await admin.get("/api/drives")).json()["items"]
    mock = next(item for item in drives if item["key"] == "mock")
    assert mock["bound"] is True


async def test_qr_image_proxy_blocks_unknown_host(admin):
    resp = await admin.get("/api/drives/qr-image", params={"url": "https://evil.example.com/a.png"})
    assert resp.status_code == 400


async def test_stats_and_allowlist(admin):
    await _bind_mock(admin)
    await admin.post(
        "/api/drives/save",
        json={"drive": "mock", "share_url": "https://mockdrive.test/s/abc123", "passcode": "mock"},
    )
    stats = (await admin.get("/api/logs/stats")).json()
    assert stats["today"] == 1
    assert stats["today_success"] == 1
    assert stats["success_rate"] == 100.0

    added = await admin.post("/api/settings/allows", json={"tg_user_id": 123456, "note": "我自己"})
    assert added.status_code == 200
    settings = (await admin.get("/api/settings")).json()
    assert settings["allows"][0]["tg_user_id"] == 123456

    deleted = await admin.delete(f"/api/settings/allows/{settings['allows'][0]['id']}")
    assert deleted.json()["ok"] is True
