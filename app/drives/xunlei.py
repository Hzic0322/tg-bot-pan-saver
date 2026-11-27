"""迅雷网盘适配器。

认证有两条路：
1. **扫码（RFC 8628 设备码）**：`POST https://xluser-ssl.xunlei.com/v1/auth/device/code`
   拿到 device_code + verification_url，把它渲染成二维码；用户在迅雷 App/网页确认后，
   轮询 `POST /v1/auth/token` 换 access_token / refresh_token。
   端点与 client_id 来自开源项目 lib-x/thunder-task-rs（其 client_id 取自官方 NAS 客户端）。
2. **粘贴凭证**：迅雷网页端把 OAuth 凭证放在 localStorage 的 credentials_<clientId>。

业务接口走 api-pan.xunlei.com/drive/v1。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .base import BaseDrive, DirEntry, DriveError, NotSupported, QRStart, QRPoll, SaveResult

API = "https://api-pan.xunlei.com/drive/v1"
AUTH = "https://xluser-ssl.xunlei.com"
CLIENT_ID_NAS = "X9ibISwpIp8jQ4Ya"      # 官方 NAS 客户端 pan-xunlei-com 的公开 client_id（设备码用）
CLIENT_ID_WEB = "Xqp0kJBXWhwaTpB6"      # 迅雷网页端 client_id（localStorage 凭证用）
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"
SHARE_RE = re.compile(r"pan\.xunlei\.com/s/([0-9a-zA-Z_\-]+)")


class XunleiDrive(BaseDrive):
    key = "xunlei"
    name = "迅雷网盘"
    domains = ("pan.xunlei.com", "xunlei.com")
    required_cookie = ()
    supports_qr = True
    qr_note = "用迅雷 App 扫码，或在浏览器打开链接后确认授权"

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
        extra["client_id"] = CLIENT_ID_WEB  # 网页端发的 token 只能用网页端 client_id 刷新
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
            raise DriveError("迅雷授权已失效，请重新扫码或导入凭证")
        client_id = str(self.extra.get("client_id") or CLIENT_ID_NAS)
        async with self.client(headers=self._headers()) as client:
            data = self._pick_json(
                await client.post(
                    f"{AUTH}/v1/auth/token",
                    data={
                        "client_id": client_id,
                        "client_secret": "",
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
            )
        token = (data or {}).get("access_token")
        if not token:
            raise DriveError("迅雷 token 刷新失败，请重新扫码授权")
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
        errors: list[str] = []
        for path in ("/member/profile", "/about"):
            try:
                data = await self._api("GET", path)
            except DriveError as exc:
                errors.append(exc.message)
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
        # 之前这里 return 一个假名字，导致无效 token 也显示「绑定成功」——必须显式失败
        raise DriveError(errors[-1] if errors else "迅雷凭证无效，请重新导入 access_token")

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
        async with self.client(headers=self._headers()) as client:
            resp = await client.post(
                f"{AUTH}/v1/auth/device/code",
                data={"client_id": CLIENT_ID_NAS},
            )
            data = self._pick_json(resp)
        device_code = (data or {}).get("device_code")
        verify_url = (data or {}).get("verification_uri_complete") or (data or {}).get("verification_url")
        if not device_code or not verify_url:
            raise DriveError("迅雷未返回设备码，请改用凭证导入")
        return QRStart(
            state={"device_code": device_code, "client_id": CLIENT_ID_NAS},
            mode="content",
            content=str(verify_url),
            tip=f"{self.qr_note}；也可直接打开：{verify_url}",
            expires_in=int((data or {}).get("expires_in") or 120),
            interval=float((data or {}).get("interval") or 2),
        )

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        client_id = str(state.get("client_id") or CLIENT_ID_NAS)
        async with self.client(headers=self._headers()) as client:
            resp = await client.post(
                f"{AUTH}/v1/auth/token",
                data={
                    "client_id": client_id,
                    "client_secret": "",
                    "grant_type": GRANT_DEVICE,
                    "device_code": state.get("device_code"),
                },
            )
            body = self._pick_json(resp)

        error = str((body or {}).get("error") or "")
        if error in ("authorization_pending", "slow_down"):
            return QRPoll(status="pending", message="等待授权" if error == "authorization_pending" else "请稍慢一点")
        if error in ("expired_token", "access_denied"):
            return QRPoll(status="expired", message="授权码已过期或被拒绝，请刷新二维码")

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
                "client_id": client_id,
                "device_id": self.device_id,
            },
        )
