"""115 网盘适配器。

凭证：Cookie 中的 UID / CID / SEID / KID（扫码登录后自动写入）。
说明：接口来自 115 Web 端公开调用，若官方改版需同步调整。
"""

from __future__ import annotations

import re
from typing import Any

from .base import BaseDrive, DirEntry, DriveError, QRStart, QRPoll, SaveResult

SHARE_RE = re.compile(r"(?:115|115cdn|anxia)\.com/s/([0-9a-zA-Z]+)")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Pan115Drive(BaseDrive):
    key = "115"
    name = "115 网盘"
    domains = ("115.com", "115cdn.com", "anxia.com", "115pan.com")
    required_cookie = ("UID", "CID", "SEID")
    supports_qr = True
    qr_note = "用 115 App 扫码并在手机上确认登录"

    # ---------- 账号 ----------
    async def account_info(self) -> dict[str, Any]:
        self.require_cookie()
        async with self.client(headers={"Referer": "https://115.com/"}) as client:
            resp = await client.get(
                "https://webapi.115.com/files",
                params={"aid": 1, "cid": 0, "offset": 0, "limit": 1, "show_dir": 1},
            )
            data = self._pick_json(resp)
            if isinstance(data, dict) and data.get("state") is False:
                raise DriveError(str(data.get("error") or "Cookie 已失效，请重新登录"))
            name = "115 账号"
            try:
                nav = self._pick_json(await client.get("https://my.115.com/", params={"ct": "ajax", "ac": "nav"}))
                user = (nav or {}).get("data", {}) or {}
                name = user.get("uname") or user.get("user_name") or name
            except Exception:
                pass
            return {"name": name}

    # ---------- 目录 ----------
    async def list_dirs(self, parent_id: str = "0") -> list[DirEntry]:
        self.require_cookie()
        cid = "0" if parent_id in ("", "0", None) else str(parent_id)
        async with self.client(headers={"Referer": "https://115.com/"}) as client:
            resp = await client.get(
                "https://webapi.115.com/files",
                params={
                    "aid": 1,
                    "cid": cid,
                    "offset": 0,
                    "limit": 1000,
                    "show_dir": 1,
                    "o": "user_ptime",
                    "asc": 0,
                    "natsort": 1,
                },
            )
            data = self._pick_json(resp)
            if isinstance(data, dict) and data.get("state") is False:
                raise DriveError(str(data.get("error") or "读取目录失败，请检查登录状态"))
            items = (data or {}).get("data") or []
            dirs: list[DirEntry] = []
            for item in items:
                # 115 的目录项没有 fid（或 fid 为 0），文件项有 fid
                if item.get("fid") in (None, 0, "0"):
                    dirs.append(DirEntry(id=str(item.get("cid")), name=str(item.get("n") or "未命名")))
            return dirs

    # ---------- 转存 ----------
    async def save_share(self, share_url: str, passcode: str, dir_id: str) -> SaveResult:
        self.require_cookie()
        match = SHARE_RE.search(share_url)
        if not match:
            raise DriveError("无法从链接中解析出 115 分享码")
        share_code = match.group(1)
        cid = "0" if dir_id in ("", "0", None) else str(dir_id)

        async with self.client(headers={"Referer": share_url}) as client:
            snap = self._pick_json(
                await client.post(
                    "https://webapi.115.com/share/snap",
                    data={
                        "share_code": share_code,
                        "receive_code": passcode or "",
                        "offset": 0,
                        "limit": 100,
                        "cid": cid,
                    },
                )
            )
            if not isinstance(snap, dict) or snap.get("state") is False:
                msg = str((snap or {}).get("error") or "解析分享失败（提取码错误或链接已失效）")
                return SaveResult(ok=False, message=msg)
            data = snap.get("data") or {}
            share_id = data.get("share_id") or data.get("shareid")
            if not share_id:
                return SaveResult(ok=False, message="未获取到分享 ID，链接可能已失效")

            receive = self._pick_json(
                await client.post(
                    "https://webapi.115.com/share/receive",
                    data={"share_id": share_id, "receive_code": passcode or "", "cid": cid},
                )
            )
            if not isinstance(receive, dict) or receive.get("state") is False:
                msg = str((receive or {}).get("error") or "转存失败")
                return SaveResult(ok=False, message=msg)
            count = _as_int(data.get("count") or data.get("file_count") or 0)
            return SaveResult(ok=True, message="转存成功", files=count or 1)

    # ---------- 扫码 ----------
    async def qr_start(self) -> QRStart:
        async with self.client(headers={"Referer": "https://115.com/"}) as client:
            resp = await client.get("https://qrcodeapi.115.com/api/1.0/web/1.0/token/")
            data = self._pick_json(resp)
            if not isinstance(data, dict) or not data.get("data"):
                raise DriveError("获取 115 二维码失败")
            info = data["data"]
            return QRStart(
                state={"uid": info.get("uid"), "time": info.get("time"), "sign": info.get("sign")},
                mode="image",
                image_url=str(info.get("qrcode") or ""),
                tip=self.qr_note,
                expires_in=180,
                interval=2.0,
            )

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        params = {"uid": state.get("uid"), "time": state.get("time"), "sign": state.get("sign")}
        async with self.client(headers={"Referer": "https://115.com/"}) as client:
            resp = await client.get("https://qrcodeapi.115.com/get/status/", params=params, timeout=10)
            data = self._pick_json(resp)
            payload = (data or {}).get("data") or {}
            status = _as_int(payload.get("status"), 0)
            # 115：0 未扫描 / 1 已扫描待确认 / 2 已确认
            if status == 0:
                return QRPoll(status="pending", message="等待扫码")
            if status == 1:
                return QRPoll(status="scanned", message="已扫描，请在手机上确认登录")
            if status != 2:
                return QRPoll(status="pending", message="等待扫码")

            login = self._pick_json(
                await client.post(
                    "https://passportapi.115.com/app/1.0/web/1.0/login/qrcode/",
                    data={"account": state.get("uid"), "app": "web"},
                )
            )
            if not isinstance(login, dict) or login.get("state") is False:
                return QRPoll(status="failed", message=str((login or {}).get("message") or "确认登录失败"))
            cookie = ((login.get("data") or {}).get("cookie")) or {}
            cookie = {str(k): str(v) for k, v in cookie.items() if v}
            if not cookie.get("UID"):
                return QRPoll(status="failed", message="未获取到 115 凭证，请重试")
            return QRPoll(status="success", message="登录成功", cookie=cookie, account_name="115 账号")
