"""夸克网盘适配器。

凭证：Cookie 中的 __pus / __puus（扫码登录后自动写入，或手动粘贴）。

⚠️ 夸克官方扫码接口没有公开文档，不同版本返回字段不一致：
   若扫码接口未返回二维码，前端会自动引导使用「Cookie 导入」。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from .base import BaseDrive, DirEntry, DriveError, NotSupported, QRStart, QRPoll, SaveResult

API = "https://drive-pc.quark.cn/1/clouddrive"
COMMON = {"pr": "ucpro", "fr": "pc"}
SHARE_RE = re.compile(r"(?:pan\.)?quark\.cn/s/([0-9a-zA-Z]+)")


def _ok(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("status", 200) == 200 and payload.get("code", 0) == 0


def _err(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        msg = payload.get("message")
        data = payload.get("data")
        if not msg and isinstance(data, dict):
            msg = data.get("message")
        if msg:
            return str(msg)
        code = payload.get("code")
        if code:
            return f"{fallback}（code={code}）"
    return fallback


class QuarkDrive(BaseDrive):
    key = "quark"
    name = "夸克网盘"
    domains = ("pan.quark.cn", "quark.cn")
    required_cookie = ("__puus",)
    supports_qr = True
    qr_note = "用夸克 App 扫码登录"

    def _headers(self, referer: str = "https://pan.quark.cn/") -> dict[str, str]:
        return {"Referer": referer, "Origin": "https://pan.quark.cn"}

    # ---------- 账号 ----------
    async def account_info(self) -> dict[str, Any]:
        self.require_cookie()
        async with self.client(headers=self._headers()) as client:
            data = self._pick_json(
                await client.get("https://pan.quark.cn/account/info", params={"fr": "pc", "platform": "pc"})
            )
            if not _ok(data):
                raise DriveError(_err(data, "Cookie 已失效，请重新登录"))
            info = data.get("data") or {}
            return {"name": info.get("nickname") or info.get("email") or "夸克账号"}

    # ---------- 目录 ----------
    async def list_dirs(self, parent_id: str = "0") -> list[DirEntry]:
        self.require_cookie()
        pdir = "0" if parent_id in ("", "0", None) else str(parent_id)
        params = {
            **COMMON,
            "pdir_fid": pdir,
            "_page": 1,
            "_size": 200,
            "_fetch_total": 1,
            "_sort": "file_type:asc,updated_at:desc",
            "_is_hl": 0,
        }
        async with self.client(headers=self._headers()) as client:
            data = self._pick_json(await client.get(f"{API}/file/sort", params=params))
            if not _ok(data):
                raise DriveError(_err(data, "读取目录失败"))
            items = ((data.get("data") or {}).get("list")) or []
            return [
                DirEntry(id=str(item.get("fid")), name=str(item.get("file_name") or "未命名"))
                for item in items
                if item.get("file_type") == 0 and item.get("fid")
            ]

    # ---------- 转存 ----------
    async def save_share(self, share_url: str, passcode: str, dir_id: str) -> SaveResult:
        self.require_cookie()
        match = SHARE_RE.search(share_url)
        if not match:
            raise DriveError("无法从链接中解析出夸克分享 ID")
        pwd_id = match.group(1)
        to_pdir = "0" if dir_id in ("", "0", None) else str(dir_id)

        async with self.client(headers=self._headers(share_url)) as client:
            token = self._pick_json(
                await client.post(
                    f"{API}/share/sharepage/token",
                    params=COMMON,
                    json={"pwd_id": pwd_id, "passcode": passcode or ""},
                )
            )
            if not _ok(token):
                return SaveResult(ok=False, message=_err(token, "解析分享失败（提取码错误或链接已失效）"))
            stoken = (token.get("data") or {}).get("stoken")
            if not stoken:
                return SaveResult(ok=False, message="未获取到分享凭证，链接可能已失效")

            detail = self._pick_json(
                await client.get(
                    f"{API}/share/sharepage/detail",
                    params={
                        **COMMON,
                        "pwd_id": pwd_id,
                        "stoken": stoken,
                        "pdir_fid": "0",
                        "force": 0,
                        "_page": 1,
                        "_size": 200,
                        "_fetch_banner": 0,
                        "_fetch_share": 0,
                        "_fetch_total": 1,
                        "_sort": "file_type:asc,file_name:asc",
                    },
                )
            )
            if not _ok(detail):
                return SaveResult(ok=False, message=_err(detail, "读取分享内容失败"))
            entries = ((detail.get("data") or {}).get("list")) or []
            fid_list = [str(e.get("fid")) for e in entries if e.get("fid")]
            fid_token_list = [str(e.get("share_fid_token")) for e in entries if e.get("share_fid_token")]
            if not fid_list:
                return SaveResult(ok=False, message="分享为空或需要提取码")

            saved = self._pick_json(
                await client.post(
                    f"{API}/share/sharepage/save",
                    params=COMMON,
                    json={
                        "fid_list": fid_list,
                        "fid_token_list": fid_token_list,
                        "to_pdir_fid": to_pdir,
                        "pwd_id": pwd_id,
                        "stoken": stoken,
                        "pdir_fid": "0",
                        "scene": "link",
                    },
                )
            )
            if not _ok(saved):
                return SaveResult(ok=False, message=_err(saved, "转存失败"))
            task_id = (saved.get("data") or {}).get("task_id")
            if task_id:
                await self._wait_task(client, task_id)
            return SaveResult(ok=True, message="转存成功", files=len(fid_list))

    async def _wait_task(self, client: httpx.AsyncClient, task_id: str, timeout: float = 20.0) -> None:
        """等待异步任务完成（失败不阻塞主流程）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self._pick_json(
                    await client.get(f"{API}/task", params={**COMMON, "task_id": task_id, "retry_index": 0})
                )
                status = ((data.get("data") or {}).get("status"))
                if status in (2, 3):  # 2 完成 / 3 失败
                    return
            except DriveError:
                return
            await asyncio.sleep(0.8)

    # ---------- 扫码 ----------
    async def qr_start(self) -> QRStart:
        async with self.client(headers={"Referer": "https://pan.quark.cn/"}) as client:
            data = self._pick_json(
                await client.get(
                    "https://uop.quark.cn/cas/ajax/getTokenForQrcodeLogin",
                    params={"client_id": "532", "v": "1.2"},
                )
            )
            token = ((data or {}).get("data") or {}).get("token") or (data or {}).get("token")
            if not token:
                raise NotSupported("夸克扫码接口未返回 token，请改用 Cookie 导入")
            qr_image = ""
            for key in ("qrcode", "qrcode_url", "qrcodeUrl", "img", "image"):
                value = ((data or {}).get("data") or {}).get(key)
                if isinstance(value, str) and value.startswith("http"):
                    qr_image = value
                    break
            if not qr_image:
                raise NotSupported("夸克扫码接口未返回二维码图片，请改用 Cookie 导入")
            return QRStart(
                state={"token": token},
                mode="image",
                image_url=qr_image,
                tip=self.qr_note,
                expires_in=180,
                interval=2.0,
            )

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        token = state.get("token")
        async with self.client(headers={"Referer": "https://pan.quark.cn/"}) as client:
            data = self._pick_json(
                await client.get(
                    "https://uop.quark.cn/cas/ajax/getServiceTicketByQrcodeToken",
                    params={"token": token, "client_id": "532", "v": "1.2"},
                )
            )
            payload = (data or {}).get("data") or {}
            ticket = (payload.get("members") or {}).get("service_ticket") or payload.get("service_ticket")
            if not ticket:
                # 未扫描时官方返回 code=10001 等，统一按 pending 处理
                message = str((data or {}).get("message") or "等待扫码")
                return QRPoll(status="pending", message=message)

            resp = await client.get(
                "https://pan.quark.cn/account/info", params={"st": ticket, "lw": "scan", "fr": "pc"}
            )
            cookie: dict[str, str] = {}
            for raw in resp.headers.get_list("set-cookie"):
                piece = raw.split(";", 1)[0]
                if "=" in piece:
                    k, v = piece.split("=", 1)
                    cookie[k.strip()] = v.strip()
            cookie.update({k: v for k, v in client.cookies.items()})
            if not cookie.get("__puus"):
                return QRPoll(status="failed", message="扫码成功但未取到登录凭证，请重试")
            return QRPoll(status="success", message="登录成功", cookie=cookie, account_name="夸克账号")
