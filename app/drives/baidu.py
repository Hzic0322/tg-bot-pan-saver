"""百度网盘适配器。

凭证：Cookie 中的 BDUSS + STOKEN（扫码登录后自动写入，或手动粘贴）。
百度的目录用「路径」标识，根目录在这里统一映射成 `0`。
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

import httpx

from .base import BaseDrive, DirEntry, DriveError, NotSupported, QRStart, QRPoll, SaveResult

log = logging.getLogger(__name__)

SHARE_RE = re.compile(r"pan\.baidu\.com/s/([0-9a-zA-Z_\-]+)")
YUNDATA_RE = re.compile(r"yunData\.setData\((\{.*?\})\);", re.S)
ERRNO_HINT = {
    -9: "提取码错误",
    -12: "分享已失效",
    -62: "访问过于频繁，稍后再试",
    -20: "需要验证码，请在浏览器中打开该链接",
    2: "参数错误",
    105: "链接地址错误",
    112: "转存数量超出限制",
}


class BaiduDrive(BaseDrive):
    key = "baidu"
    name = "百度网盘"
    domains = ("pan.baidu.com", "eyun.baidu.com")
    required_cookie = ("BDUSS",)
    supports_qr = True
    qr_note = "用百度 App 扫码登录"

    def _headers(self, referer: str = "https://pan.baidu.com/disk/home") -> dict[str, str]:
        return {"Referer": referer, "Origin": "https://pan.baidu.com"}

    @staticmethod
    def _to_path(dir_id: str) -> str:
        if not dir_id or dir_id in ("0", "root", "/"):
            return "/"
        return dir_id if dir_id.startswith("/") else f"/{dir_id}"

    async def _bdstoken(self, client) -> tuple[str, str]:
        data = self._pick_json(
            await client.get(
                "https://pan.baidu.com/api/gettemplatevariable",
                params={
                    "clienttype": 0,
                    "app_id": 250528,
                    "web": 1,
                    "fields": '["bdstoken","uk"]',
                },
            )
        )
        payload = (data or {}).get("result") or {}
        if not payload:
            raise DriveError("无法读取百度网盘账号信息，Cookie 可能已失效")
        return str(payload.get("bdstoken") or ""), str(payload.get("uk") or "")

    # ---------- 账号 ----------
    async def account_info(self) -> dict[str, Any]:
        self.require_cookie()
        async with self.client(headers=self._headers()) as client:
            _, uk = await self._bdstoken(client)
            return {"name": f"百度网盘（uk={uk}）" if uk else "百度网盘"}

    # ---------- 目录 ----------
    async def list_dirs(self, parent_id: str = "0") -> list[DirEntry]:
        self.require_cookie()
        path = self._to_path(parent_id)
        async with self.client(headers=self._headers()) as client:
            bdstoken, _ = await self._bdstoken(client)
            data = self._pick_json(
                await client.get(
                    "https://pan.baidu.com/api/list",
                    params={
                        "order": "name",
                        "desc": 0,
                        "showempty": 0,
                        "web": 1,
                        "page": 1,
                        "num": 1000,
                        "dir": path,
                        "t": str(int(time.time() * 1000)),
                        "channel": "chunlei",
                        "app_id": 250528,
                        "clienttype": 0,
                        "bdstoken": bdstoken,
                    },
                )
            )
            errno = (data or {}).get("errno")
            if errno not in (0, None):
                raise DriveError(ERRNO_HINT.get(errno, f"读取目录失败（errno={errno}）"))
            items = (data or {}).get("list") or []
            dirs: list[DirEntry] = []
            for item in items:
                if item.get("isdir") == 1:
                    child = item.get("path") or f"{path.rstrip('/')}/{item.get('server_filename')}"
                    dirs.append(DirEntry(id=child, name=str(item.get("server_filename") or "未命名")))
            return dirs

    # ---------- 转存 ----------
    async def save_share(self, share_url: str, passcode: str, dir_id: str) -> SaveResult:
        self.require_cookie()
        match = SHARE_RE.search(share_url)
        if not match:
            raise DriveError("无法从链接中解析出百度分享地址")
        share_key = match.group(1)
        surl = share_key[1:] if share_key.startswith("1") else share_key
        target_path = self._to_path(dir_id)

        async with self.client(headers=self._headers(share_url)) as client:
            bdstoken, _ = await self._bdstoken(client)

            # 1) 校验提取码，拿到 sekey
            verify = self._pick_json(
                await client.post(
                    "https://pan.baidu.com/share/verify",
                    params={
                        "surl": surl,
                        "t": str(int(time.time() * 1000)),
                        "channel": "chunlei",
                        "web": 1,
                        "app_id": 250528,
                        "clienttype": 0,
                    },
                    data={"pwd": passcode or "", "vcode": "", "vcode_str": ""},
                )
            )
            if (verify or {}).get("errno") not in (0, None):
                errno = (verify or {}).get("errno")
                return SaveResult(ok=False, message=ERRNO_HINT.get(errno, f"提取码校验失败（errno={errno}）"))
            sekey = (verify or {}).get("randsk", "")
            if not sekey and (verify or {}).get("errno") == 0:
                sekey = ""

            # 2) 解析分享页，拿到 shareid / uk / fs_id 列表
            page = await client.get(
                f"https://pan.baidu.com/s/{share_key}",
                params={"pwd": passcode} if passcode else None,
            )
            html = page.text or ""
            shareid, share_uk, fs_ids = "", "", []
            found = YUNDATA_RE.search(html)
            if found:
                try:
                    meta = json.loads(found.group(1))
                except json.JSONDecodeError:
                    meta = {}
                shareid = str(meta.get("shareid") or "")
                share_uk = str(meta.get("share_uk") or meta.get("uk") or "")
                for item in meta.get("file_list") or []:
                    if item.get("fs_id"):
                        fs_ids.append(str(item["fs_id"]))
            if not shareid:
                hit = re.search(r'"shareid":\s*"?(\d+)"?', html)
                shareid = hit.group(1) if hit else ""
            if not share_uk:
                hit = re.search(r'"share_uk":"?(\d+)"?', html)
                share_uk = hit.group(1) if hit else ""
            if not fs_ids:
                fs_ids = re.findall(r'"fs_id":\s*(\d+)', html)[:100]
            if not shareid or not share_uk:
                return SaveResult(ok=False, message="解析分享页失败（可能需要验证码，或链接已失效）")
            if not fs_ids:
                return SaveResult(ok=False, message="分享中没有可转存的文件")

            # 3) 转存
            transfer = self._pick_json(
                await client.post(
                    "https://pan.baidu.com/share/transfer",
                    params={
                        "shareid": shareid,
                        "from": share_uk,
                        "sekey": sekey,
                        "ondup": "newcopy",
                        "async": 1,
                        "channel": "chunlei",
                        "web": 1,
                        "app_id": 250528,
                        "clienttype": 0,
                        "bdstoken": bdstoken,
                    },
                    data={"fsidlist": json.dumps(fs_ids), "path": target_path},
                )
            )
            errno = (transfer or {}).get("errno")
            if errno not in (0, None):
                return SaveResult(ok=False, message=ERRNO_HINT.get(errno, f"转存失败（errno={errno}）"))
            return SaveResult(ok=True, message="转存成功", files=len(fs_ids))

    # ---------- 扫码 ----------
    async def qr_start(self) -> QRStart:
        gid = uuid.uuid4().hex.upper()
        async with self.client(headers=self._headers("https://passport.baidu.com/")) as client:
            data = self._pick_json(
                await client.get(
                    "https://passport.baidu.com/v2/api/getqrcode",
                    params={"lp": "pc", "qrloginfrom": "pc", "gid": gid, "tpl": "netdisk"},
                )
            )
            imgurl = (data or {}).get("imgurl") or ""
            sign = (data or {}).get("sign") or ""
            if not sign:
                raise NotSupported("百度扫码接口未返回签名，请改用 Cookie 导入")
            # 百度返回的 imgurl 是「passport.baidu.com/v2/api/qrcode?...」，没有 scheme，
            # 直接用会被当成相对路径，图片必然裂开。
            if imgurl.startswith("//"):
                imgurl = "https:" + imgurl
            elif imgurl and not imgurl.lower().startswith(("http://", "https://")):
                imgurl = "https://" + imgurl
            return QRStart(
                state={"sign": sign, "gid": gid},
                mode="image",
                image_url=imgurl,
                tip=self.qr_note,
                expires_in=180,
                interval=2.0,
            )

    QR_LOGIN_URL = "https://passport.baidu.com/v3/login/main/qrbdusslogin"

    async def qr_poll(self, state: dict[str, Any]) -> QRPoll:
        """按百度真实语义判定状态（参考 komorebiCarry/BaiduPCS-Rust 的实现）：

        - `status=0` 且没有 `v`  → 等待扫码
        - `status=1`             → 已扫码，等待确认
        - 出现 `v` 字段           → 用户已确认，用 v 去换 BDUSS/STOKEN
          （注意：确认后 status 仍然可能是 0，所以不能只看 status）

        `channel/unicast` 是长轮询，未扫码时会挂住，因此用短超时并视超时为「还没扫」。
        """
        sign = state.get("sign")
        async with self.client(headers=self._headers("https://passport.baidu.com/")) as client:
            try:
                resp = await client.get(
                    "https://passport.baidu.com/channel/unicast",
                    params={
                        "channel_id": sign,
                        "tpl": "netdisk",
                        "apiver": "v3",
                        "tt": str(int(time.time() * 1000)),
                    },
                    timeout=8.0,
                )
            except httpx.TimeoutException:
                return QRPoll(status="pending", message="等待扫码")
            except httpx.HTTPError as exc:
                return QRPoll(status="pending", message=f"网络抖动（{type(exc).__name__}），继续等待")

            raw = (resp.text or "").strip()
            if not raw:
                return QRPoll(status="pending", message="等待扫码")
            try:
                data = resp.json()
            except ValueError:
                log.info("baidu unicast 非 JSON：%s", raw[:200])
                return QRPoll(status="pending", message="等待扫码")
            log.info("baidu unicast raw=%s", raw[:300])

            channel = (data or {}).get("channel_v")
            if not channel:
                return QRPoll(status="pending", message="等待扫码")
            info = channel
            if isinstance(channel, str):
                try:
                    info = json.loads(channel)
                except json.JSONDecodeError:
                    return QRPoll(status="pending", message="等待扫码")
            if not isinstance(info, dict):
                return QRPoll(status="pending", message="等待扫码")

            v_code = str(info.get("v") or info.get("bduss") or "")
            try:
                status = int(info.get("status"))
            except (TypeError, ValueError):
                status = 0

            if v_code:
                return await self._confirm_login(client, v_code)
            if status == 1:
                return QRPoll(status="scanned", message="已扫码，请在手机上确认")
            return QRPoll(status="pending", message="等待扫码")

    async def _confirm_login(self, client: httpx.AsyncClient, v_code: str) -> QRPoll:
        ts = int(time.time() * 1000)
        resp = await client.get(
            self.QR_LOGIN_URL,
            params={
                "v": ts,
                "bduss": v_code,
                "u": "https://pan.baidu.com/disk/main",
                "tpl": "netdisk",
                "qrcode": 1,
                "apiver": "v3",
                "tt": ts,
            },
        )
        cookie: dict[str, str] = {}
        for raw in resp.headers.get_list("set-cookie"):
            piece = raw.split(";", 1)[0]
            if "=" in piece:
                k, v = piece.split("=", 1)
                cookie[k.strip()] = v.strip()
        if not cookie.get("BDUSS"):
            log.info("baidu qrbdusslogin 未拿到 BDUSS：HTTP %s body=%s", resp.status_code, (resp.text or "")[:200])
            return QRPoll(status="failed", message="确认登录失败（未取到 BDUSS），请改用 Cookie 导入")

        note = "登录成功" if cookie.get("STOKEN") else "登录成功（未取到 STOKEN，上传类功能可能受限）"
        return QRPoll(status="success", message=note, cookie=cookie, account_name="百度网盘")
