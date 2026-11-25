"""迅雷网盘适配器。

凭证：access_token / refresh_token（扫码授权后自动写入，四家网盘里唯一不依赖 Cookie 的）。
扫码走的是 OAuth Device Code 流程：后台展示一个授权链接对应的二维码，
用户在迅雷 App / 网页确认后，服务端轮询换取 token。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .base import BaseDrive, DirEntry, DriveError, NotSupported, QRStart, QRPoll, SaveResult

API = "https://api-pan.xunlei.com/drive/v1"
CLIENT_ID = "Xqp0k6fs6q6nf9m6wj3w"  # 迅雷网页端公开 client_id
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"
SHARE_RE = re.compile(r"pan\.xunlei\.com/s/([0-9a-zA-Z_\-]+)")


class XunleiDrive(BaseDrive):
    key = "xunlei"
    name = "迅雷网盘"
    domains = ("pan.xunlei.com", "xunlei.com")
    required_cookie = ()
    supports_qr = False
    qr_note = "迅雷授权接口已变更，请在 Cookie 导入里粘贴包含 access_token / refresh_token 的 JSON"

    # ---------- 凭证导入 ----------
    # 迅雷网页端把 OAuth 凭证存在 localStorage 的 credentials_<clientId>，
    # 形如 {"token":"...","refresh_token":"...","expires_in":...}。
    # 扫码设备码接口已下线（2026-11 实测全 404），所以这里以粘贴凭证为主。
    @classmethod
    def parse_import(cls, raw: str) -> tuple[dict[str, str], dict[str, Any]]:
        raw = (raw or "").strip()
        extra: dict[str, Any] = {}
        if not raw:
            return {}, {}

        if raw.startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                token = data.get("access_token") or data.get("token") or data.get("accessToken")
                refresh = data.get("refresh_token") or data.get("refreshToken")
                if token:
                    extra["access_token"] = str(token)
                if refresh:
                    extra["refresh_token"] = str(refresh)
                expires_in = data.get("expires_in") or data.get("expiresIn")
                if expires_in:
                    try:
                        extra["expires_at"] = int(time.time()) + int(expires_in)
                    except (TypeError, ValueError):
                        pass
        elif raw.lower().startswith("bearer "):
            extra["access_token"] = raw[7:].strip()
        elif "=" not in raw and len(raw) > 20:
            extra["access_token"] = raw

        if not extra:
            return {}, {}
        # 标记已绑定（cookie 字段对迅雷没意义，但前端靠它判断 bound）
        return {"IMPORTED": "1"}, extra

    # ---------- 基础设施 ----------
    @property
    def device_id(self) -> str:
        did = self.extra.get("device_id")
        if not did:
            did = uuid.uuid4().hex
            self.extra["device_id"] = did
        return did

    def _headers(self) -> dict[str, str]:
        headers = {
            "Referer": "https://pan.xunlei.com/",
            "Origin": "https://pan.xunlei.com",
            "X-Device-Id": self.device_id,
        }
        token = self.extra.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _refresh(self) -> None:
        refresh_token = self.extra.get("refresh_token")
        if not refresh_token:
            raise DriveError("迅雷授权已失效，请重新扫码登录")
        async with self.client(headers=self._headers()) as client:
            data = self._pick_json(
                await client.post(
                    f"{API}/credential/token",
                    params={"client_id": CLIENT_ID},
                    json={
                        "client_id": CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
            )
        token = (data or {}).get("access_token")
        if not token:
            raise DriveError("迅雷 token 刷新失败，请重新扫码登录")
        self.extra["access_token"] = token
        self.extra["refresh_token"] = (data or {}).get("refresh_token") or refresh_token
        self.extra["expires_at"] = int(time.time()) + int((data or {}).get("expires_in") or 3600)

    async def _ensure_token(self) -> None:
        if not self.extra.get("access_token"):
            if self.extra.get("refresh_token"):
                await self._refresh()  # 只给了 refresh_token 也能用
                return
            raise DriveError("迅雷未授权：请在后台用「Cookie 导入」粘贴 access_token / refresh_token")
        expires_at = int(self.extra.get("expires_at") or 0)
        if expires_at and expires_at - 120 < time.time():
            await self._refresh()

    async def _api(self, method: str, path: str, **kwargs) -> Any:
        await self._ensure_token()
        async with self.client(headers=self._headers()) as client:
            resp = await client.request(method, f"{API}{path}", **kwargs)
            if resp.status_code == 401:
                await self._refresh()
                async with self.client(headers=self._headers()) as retry_client:
                    resp = await retry_client.request(method, f"{API}{path}", **kwargs)
            return self._pick_json(resp)

    # ---------- 账号 ----------
    async def account_info(self) -> dict[str, Any]:
        await self._ensure_token()
        for path in ("/member/profile", "/about"):
            try:
                data = await self._api("GET", path)
            except DriveError:
                continue
            payload = data if isinstance(data, dict) else {}
            payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            name = (
                payload.get("nickname")
                or payload.get("username")
                or payload.get("name")
                or payload.get("userName")
            )
            if name:
                return {"name": str(name)}
        return {"name": "迅雷账号"}

    # ---------- 目录 ----------
    async def list_dirs(self, parent_id: str = "0") -> list[DirEntry]:
        parent = "" if parent_id in ("", "0", None) else str(parent_id)
        filters = json.dumps(
            {"trashed": {"eq": False}, "phase": {"eq": "PHASE_TYPE_COMPLETE"}}, separators=(",", ":")
        )
        data = await self._api(
            "GET",
            "/files",
            params={"parent_id": parent, "page_token": "", "limit": 200, "filters": filters},
        )
        files = (data or {}).get("files") or []
        dirs: list[DirEntry] = []
        for item in files:
            kind = (item.get("kind") or {}).get("#type")
            if kind == "folder":
                dirs.append(DirEntry(id=str(item.get("id")), name=str(item.get("name") or "未命名")))
        return dirs

    # ---------- 转存 ----------
    async def save_share(self, share_url: str, passcode: str, dir_id: str) -> SaveResult:
        match = SHARE_RE.search(share_url)
        if not match:
            raise DriveError("无法从链接中解析出迅雷分享 ID")
        share_id = match.group(1)
        parent_id = "" if dir_id in ("", "0", None) else str(dir_id)

        info = await self._api("GET", "/share", params={"share_id": share_id, "pass_code": passcode or ""})
        share = (info or {}).get("share") or {}
        files = (info or {}).get("files") or []
        if not share:
            return SaveResult(ok=False, message="解析分享失败（提取码错误或链接已失效）")
        file_ids = [str(f.get("id")) for f in files if f.get("id")]
        if not file_ids:
            return SaveResult(ok=False, message="分享中没有可转存的文件")

        payload = {
            "parent_id": parent_id,
            "share_id": share_id,
            "pass_code": passcode or "",
            "file_ids": file_ids,
            "pass_code_token": share.get("pass_code_token") or "",
        }
        restored = await self._api("POST", "/share/restore", json=payload)
        task_id = (restored or {}).get("restore_task_id") or (restored or {}).get("task_id")
        if not task_id:
            return SaveResult(ok=True, message="转存已提交", files=len(file_ids))
        return SaveResult(ok=True, message="转存成功", files=len(file_ids), extra={"task_id": task_id})

    # ---------- 扫码（Device Code） ----------
    async def qr_start(self) -> QRStart:
        raise NotSupported(self.qr_note)

    async def _qr_start_legacy(self) -> QRStart:  # pragma: no cover - 接口已 404，留作参考
        async with self.client(headers=self._headers()) as client:
            data = self._pick_json(
                await client.post(f"{API}/credential/device_code", params={"client_id": CLIENT_ID})
            )
        device_code = (data or {}).get("device_code")
        verify_url = (data or {}).get("verification_uri_complete") or (data or {}).get("verification_uri")
        if not device_code or not verify_url:
            raise DriveError("获取迅雷授权码失败")
        return QRStart(
            state={
                "device_code": device_code,
                "interval": float((data or {}).get("interval") or 2),
                "created_at": time.time(),
            },
            mode="content",
            content=str(verify_url),
            tip=f"{self.qr_note}；也可以直接打开链接：{verify_url}",
            expires_in=int((data or {}).get("expires_in") or 300),
            interval=float((data or {}).get("interval") or 2),
        )

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        async with self.client(headers=self._headers()) as client:
            resp = await client.post(
                f"{API}/credential/token",
                params={"client_id": CLIENT_ID},
                json={
                    "client_id": CLIENT_ID,
                    "grant_type": GRANT_DEVICE,
                    "device_code": state.get("device_code"),
                },
            )
            body = self._pick_json(resp)
            if resp.status_code in (400, 428) or (isinstance(body, dict) and body.get("error")):
                error = str((body or {}).get("error") or "")
                if "authorization_pending" in error or "slow_down" in error:
                    return QRPoll(status="pending", message="等待授权")
                if "expired" in error:
                    return QRPoll(status="expired", message="授权码已过期，请刷新二维码")
                return QRPoll(status="pending", message="等待授权")
            token = (body or {}).get("access_token")
            if not token:
                return QRPoll(status="pending", message="等待授权")
            return QRPoll(
                status="success",
                message="授权成功",
                account_name="迅雷账号",
                extra={
                    "access_token": token,
                    "refresh_token": (body or {}).get("refresh_token") or "",
                    "expires_at": int(time.time()) + int((body or {}).get("expires_in") or 3600),
                    "device_id": self.device_id,
                },
            )
