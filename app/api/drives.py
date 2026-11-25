"""网盘管理 API：绑定（扫码 / Cookie）、目录浏览、默认目录、解绑。"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import get_current_admin
from ..drives.base import DriveError, NotSupported
from ..drives.registry import DRIVE_CLASSES, build_drive, drive_meta, get_drive_class
from ..models import DriveAccount
from ..schemas import CookieIn, DefaultDirIn, ManualSaveIn
from ..services import transfer as transfer_service
from ..services.qrsession import qr_store

router = APIRouter(prefix="/api/drives", tags=["drives"], dependencies=[Depends(get_current_admin)])

ALLOWED_IMAGE_HOSTS = ("115.com", "baidu.com", "quark.cn", "xunlei.com", "mockdrive.test")


def _account_payload(account: DriveAccount | None, meta: dict[str, Any]) -> dict[str, Any]:
    bound = bool(account and (account.cookie or account.extra) and account.status != "unbound")
    return {
        **meta,
        "bound": bound,
        "status": account.status if account else "unbound",
        "status_message": account.status_message if account else "",
        "account_name": account.account_name if account else "",
        "default_dir_id": account.default_dir_id if account else "0",
        "default_dir_name": account.default_dir_name if account else "根目录",
        "updated_at": (
            (account.updated_at if account.updated_at.tzinfo else account.updated_at.replace(tzinfo=dt.timezone.utc)).isoformat()
            if account and account.updated_at
            else None
        ),
    }


@router.get("")
async def list_drives(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    accounts = {a.drive: a for a in (await session.execute(select(DriveAccount))).scalars()}
    return {"items": [_account_payload(accounts.get(meta["key"]), meta) for meta in drive_meta()]}


@router.get("/qr-image")
async def qr_image(url: str = Query(...), _: Any = Depends(get_current_admin)) -> Response:
    """代理第三方二维码图片（仅白名单域名，避免 SSRF）。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not any(
        host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_IMAGE_HOSTS
    ):
        raise HTTPException(status_code=400, detail="不允许的图片地址")
    import httpx

    from ..config import settings

    async with httpx.AsyncClient(timeout=settings.http_timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": settings.user_agent, "Referer": f"https://{host}/"})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"二维码加载失败（HTTP {resp.status_code}）")
    content_type = resp.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        # 上游返回网页/错误页时，<img> 只会显示裂图，这里给出可读原因
        raise HTTPException(
            status_code=502,
            detail=f"上游返回的不是图片（{content_type or '未知类型'}），请改用 Cookie 导入",
        )
    return Response(content=resp.content, media_type=content_type)


@router.post("/{drive}/cookie")
async def import_cookie(
    drive: str,
    payload: CookieIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    drive_cls = get_drive_class(drive)  # 校验网盘名
    account = await transfer_service.get_or_create_account(session, drive)
    cookie, extra = drive_cls.parse_import(payload.cookie)
    if not cookie and not extra:
        raise HTTPException(
            status_code=400,
            detail="没能解析出凭证：Cookie 型网盘请粘贴 a=1; b=2；迅雷请粘贴 credentials JSON 或 access_token",
        )
    account.cookie = json.dumps(cookie, ensure_ascii=False)
    if extra:
        # 整体替换：避免上一次导入的 refresh_token 残留，跟新 token 混在一起
        account.extra = json.dumps(extra, ensure_ascii=False)
    account.status = "ok"
    account.status_message = ""
    await session.commit()
    ok, message = await transfer_service.check_account(session, account)
    await session.refresh(account)
    return {"ok": ok, "message": message or "绑定成功", "account_name": account.account_name}


@router.post("/{drive}/qr")
async def start_qr(drive: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    get_drive_class(drive)
    account = await transfer_service.get_or_create_account(session, drive)
    drive_obj = build_drive(account)
    try:
        start = await drive_obj.qr_start()
    except NotSupported as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except DriveError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except Exception as exc:  # 网络异常
        raise HTTPException(status_code=502, detail=f"获取二维码失败：{exc}") from None
    session_obj = qr_store.create(
        drive=drive,
        state=start.state,
        mode=start.mode,
        image_url=start.image_url,
        content=start.content,
        tip=start.tip,
        interval=start.interval,
        expires_in=start.expires_in,
    )
    return session_obj.to_public()


@router.get("/{drive}/qr/{qr_id}")
async def poll_qr(drive: str, qr_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    session_obj = qr_store.get(qr_id)
    if session_obj is None or session_obj.drive != drive:
        raise HTTPException(status_code=404, detail="二维码会话不存在或已过期，请刷新重试")
    if session_obj.expired:
        qr_store.drop(qr_id)
        return {"status": "expired", "message": "二维码已过期，请刷新"}

    account = await transfer_service.get_or_create_account(session, drive)
    drive_obj = build_drive(account)
    try:
        poll = await drive_obj.qr_poll(session_obj.state)
    except NotSupported as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except DriveError as exc:
        return {"status": "failed", "message": exc.message}
    except Exception as exc:  # 网络异常，让前端继续轮询
        return {"status": "pending", "message": f"轮询异常：{exc}"}

    if poll.status != "success":
        return {"status": poll.status, "message": poll.message}

    cookie = poll.cookie or {}
    extra = poll.extra or {}
    account.cookie = json.dumps(cookie, ensure_ascii=False) if cookie else account.cookie
    if extra:
        # 整体替换：避免上一次导入的 refresh_token 残留，跟新 token 混在一起
        account.extra = json.dumps(extra, ensure_ascii=False)
    await session.commit()
    qr_store.drop(qr_id)
    ok, message = await transfer_service.check_account(session, account)
    await session.refresh(account)
    return {
        "status": "success" if ok else "failed",
        "message": "登录成功" if ok else (message or "登录后校验失败"),
        "account_name": account.account_name,
    }


@router.get("/{drive}/qr/{qr_id}/png")
async def qr_png(drive: str, qr_id: str, _: Any = Depends(get_current_admin)) -> Response:
    """把「内容型」二维码（如迅雷授权链接）在服务端渲染成 PNG，方便手机扫。"""
    session_obj = qr_store.get(qr_id)
    if session_obj is None or session_obj.drive != drive:
        raise HTTPException(status_code=404, detail="二维码会话不存在或已过期")
    import io

    import qrcode

    img = qrcode.make(session_obj.content, box_size=8, border=2)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/{drive}/check")
async def check(drive: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    account = await transfer_service.get_account(session, drive)
    if account is None or not (account.cookie or account.extra):
        raise HTTPException(status_code=400, detail="该网盘尚未绑定")
    ok, message = await transfer_service.check_account(session, account)
    await session.refresh(account)
    return {"ok": ok, "message": message or "凭证有效", "account_name": account.account_name}


@router.delete("/{drive}")
async def unbind(drive: str, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    account = await transfer_service.get_account(session, drive)
    if account:
        account.cookie = ""
        account.extra = "{}"
        account.status = "unbound"
        account.status_message = ""
        account.account_name = ""
        await session.commit()
    return {"ok": True}


@router.get("/{drive}/dirs")
async def dirs(
    drive: str,
    parent_id: str = Query("0"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    account = await transfer_service.get_account(session, drive)
    if account is None or not (account.cookie or account.extra):
        raise HTTPException(status_code=400, detail="该网盘尚未绑定，请先扫码登录")
    try:
        items = await transfer_service.list_dirs(session, drive, parent_id)
    except NotSupported as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except DriveError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取目录失败：{exc}") from None
    return {"parent_id": parent_id, "items": items}


@router.put("/{drive}/default-dir")
async def set_default_dir(
    drive: str,
    payload: DefaultDirIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    account = await transfer_service.get_account(session, drive)
    if account is None:
        raise HTTPException(status_code=400, detail="该网盘尚未绑定")
    account.default_dir_id = str(payload.dir_id or "0")
    account.default_dir_name = payload.dir_name or ("根目录" if str(payload.dir_id or "0") == "0" else "自定义目录")
    await session.commit()
    return {"ok": True, "default_dir_id": account.default_dir_id, "default_dir_name": account.default_dir_name}


@router.post("/save")
async def manual_save(payload: ManualSaveIn, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """后台手动转存（便于测试链接与凭证）。"""
    if payload.drive not in DRIVE_CLASSES:
        raise HTTPException(status_code=400, detail="不支持的网盘")
    log = await transfer_service.save_share(
        session,
        drive_key=payload.drive,
        share_url=payload.share_url,
        passcode=payload.passcode,
        dir_id=payload.dir_id,
        source="web",
    )
    return {
        "ok": log.status == "success",
        "message": log.message,
        "files": log.files,
        "log_id": log.id,
        "duration_ms": log.duration_ms,
    }
