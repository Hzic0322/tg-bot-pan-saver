"""网盘适配器基类与通用数据结构。

新增网盘只需要实现 BaseDrive 的子类并注册到 registry.DRIVES。
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from ..config import settings


class DriveError(Exception):
    """适配器可预期的业务错误（会原样展示给用户）。"""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class NotSupported(DriveError):
    """该网盘不支持此操作（例如扫码接口不可用）。"""


@dataclass
class DirEntry:
    id: str
    name: str


@dataclass
class SaveResult:
    ok: bool
    message: str = ""
    files: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class QRStart:
    """扫码会话的初始状态。"""

    state: dict[str, Any]
    mode: str  # image（直接给图片地址）| content（本地渲染二维码）
    image_url: str = ""
    content: str = ""
    tip: str = ""
    expires_in: int = 180
    interval: float = 1.5


@dataclass
class QRPoll:
    status: str  # pending | scanned | success | expired | failed
    message: str = ""
    tip: str = ""
    account_name: str = ""
    cookie: dict[str, str] | None = None
    extra: dict[str, Any] | None = None


class BaseDrive(abc.ABC):
    key: ClassVar[str]
    name: ClassVar[str]
    domains: ClassVar[tuple[str, ...]] = ()
    required_cookie: ClassVar[tuple[str, ...]] = ()
    supports_qr: ClassVar[bool] = False
    qr_note: ClassVar[str] = ""

    def __init__(self, cookie: dict[str, str] | None = None, extra: dict[str, Any] | None = None) -> None:
        self.cookie = cookie or {}
        self.extra = extra or {}

    # ---------- 公共工具 ----------
    @staticmethod
    def parse_cookie(raw: str) -> dict[str, str]:
        """接受 `k=v; k2=v2`、裸 cookie 串或 JSON 对象。"""
        raw = (raw or "").strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {str(k).strip(): str(v) for k, v in data.items()}
            except json.JSONDecodeError:
                pass
        jar: dict[str, str] = {}
        for part in raw.replace("\n", ";").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            jar[k.strip()] = v.strip()
        return jar

    @classmethod
    def parse_import(cls, raw: str) -> tuple[dict[str, str], dict[str, Any]]:
        """把用户粘贴的内容解析成 (cookie, extra)。

        默认按 Cookie 串解析；像迅雷这种不用 Cookie 的网盘可以覆写成 token。
        """
        return cls.parse_cookie(raw), {}

    def require_cookie(self) -> None:
        missing = [k for k in self.required_cookie if not self.cookie.get(k)]
        if missing:
            raise DriveError(f"Cookie 缺少字段：{', '.join(missing)}，请重新扫码或粘贴完整 Cookie")

    def client(self, *, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None) -> httpx.AsyncClient:
        base_headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            base_headers.update(headers)
        return httpx.AsyncClient(
            headers=base_headers,
            cookies=cookies if cookies is not None else self.cookie,
            timeout=settings.http_timeout,
            follow_redirects=True,
        )

    @staticmethod
    def _pick_json(resp: httpx.Response) -> Any:
        """尽量解析 JSON；失败时抛出带原文片段的错误。"""
        text = resp.text or ""
        try:
            return resp.json()
        except Exception:
            snippet = text.strip().replace("\n", " ")[:200]
            raise DriveError(f"接口返回非 JSON（HTTP {resp.status_code}）：{snippet}") from None

    @staticmethod
    def cookie_header(cookies: dict[str, str]) -> str:
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    # ---------- 需要子类实现 ----------
    @abc.abstractmethod
    async def account_info(self) -> dict[str, Any]:
        """校验凭证并返回 {'name': 昵称}；失败抛 DriveError。"""

    @abc.abstractmethod
    async def list_dirs(self, parent_id: str = "0") -> list[DirEntry]:
        """列出某个目录下的子目录；parent_id 为 '0' 表示根目录。"""

    @abc.abstractmethod
    async def save_share(self, share_url: str, passcode: str, dir_id: str) -> SaveResult:
        """把分享链接转存到 dir_id。"""

    async def qr_start(self) -> QRStart:
        raise NotSupported(f"{self.name}暂不支持扫码登录，请使用 Cookie 导入")

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        raise NotSupported(f"{self.name}暂不支持扫码登录，请使用 Cookie 导入")
