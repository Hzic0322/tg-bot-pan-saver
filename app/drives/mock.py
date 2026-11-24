"""Mock 网盘：用于本地演示与自动化测试，不访问任何真实接口。

启用方式：环境变量 PANSAVER_ENABLE_MOCK=1
它只出现在后台的调试入口，不会出现在正式的四家网盘列表里（除非被明确启用）。
"""

from __future__ import annotations

import re
import time
from typing import Any

from .base import BaseDrive, DirEntry, DriveError, QRStart, QRPoll, SaveResult

SHARE_RE = re.compile(r"mockdrive\.test/s/([0-9a-zA-Z]+)")

FAKE_DIRS = [
    DirEntry(id="100", name="电影"),
    DirEntry(id="200", name="剧集"),
    DirEntry(id="300", name="音乐"),
]


class MockDrive(BaseDrive):
    key = "mock"
    name = "Mock 网盘（调试）"
    domains = ("mockdrive.test",)
    supports_qr = True
    qr_note = "Mock 登录：3 秒后自动成功"
    is_mock = True

    async def account_info(self) -> dict[str, Any]:
        if not self.cookie.get("MOCK_SESSION"):
            raise DriveError("Mock Cookie 无效，请重新扫码")
        return {"name": "Mock 用户"}

    async def list_dirs(self, parent_id: str = "0") -> list[DirEntry]:
        await self.account_info()
        if parent_id in ("", "0", None):
            return list(FAKE_DIRS)
        return [DirEntry(id=f"{parent_id}1", name=f"子目录-{parent_id}")]

    async def save_share(self, share_url: str, passcode: str, dir_id: str) -> SaveResult:
        await self.account_info()
        match = SHARE_RE.search(share_url)
        if not match:
            raise DriveError("不是合法的 Mock 分享链接")
        if passcode and passcode != "mock":
            return SaveResult(ok=False, message="提取码错误（Mock 提取码为 mock）")
        if dir_id not in ("", "0", None) and not any(d.id == dir_id for d in FAKE_DIRS):
            return SaveResult(ok=False, message="目标目录不存在")
        return SaveResult(ok=True, message="转存成功", files=2)

    async def qr_start(self) -> QRStart:
        return QRStart(
            state={"created_at": time.time()},
            mode="content",
            content="mock://login",
            tip=self.qr_note,
            expires_in=60,
            interval=1.0,
        )

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        if time.time() - float(state.get("created_at", 0)) < 3:
            return QRPoll(status="pending", message="等待扫码")
        return QRPoll(
            status="success",
            message="登录成功",
            cookie={"MOCK_SESSION": "mock-token"},
            account_name="Mock 用户",
        )
