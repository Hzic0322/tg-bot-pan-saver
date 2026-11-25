"""转存编排：统一处理「调用适配器 → 写日志 → 更新账号状态」。"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..drives.base import DriveError, NotSupported
from ..drives.registry import build_drive
from ..models import DriveAccount, SaveLog, utcnow


async def get_account(session: AsyncSession, drive: str) -> DriveAccount | None:
    return (await session.execute(select(DriveAccount).where(DriveAccount.drive == drive))).scalar_one_or_none()


async def get_or_create_account(session: AsyncSession, drive: str) -> DriveAccount:
    account = await get_account(session, drive)
    if account is None:
        account = DriveAccount(drive=drive)
        session.add(account)
        await session.commit()
        await session.refresh(account)
    return account


def _mask_error(exc: Exception) -> str:
    if isinstance(exc, (DriveError, NotSupported)):
        return exc.message
    return f"{type(exc).__name__}: {exc}"


async def persist_extra(account: DriveAccount, drive_obj: Any) -> None:
    """迅雷等适配器会刷新 token，需要写回数据库。"""
    extra = getattr(drive_obj, "extra", None)
    if not extra:
        return
    serialized = json.dumps(extra, ensure_ascii=False)
    if serialized != account.extra:
        account.extra = serialized


async def check_account(session: AsyncSession, account: DriveAccount) -> tuple[bool, str]:
    """校验凭证有效性，并更新账号状态。"""
    try:
        drive_obj = build_drive(account)
        info = await drive_obj.account_info()
        account.status = "ok"
        account.status_message = ""
        account.account_name = str(info.get("name") or account.account_name or "")
        await persist_extra(account, drive_obj)
    except NotSupported as exc:
        account.status = "error"
        account.status_message = exc.message
    except DriveError as exc:
        account.status = "expired"
        account.status_message = exc.message
    except Exception as exc:  # 网络等异常
        account.status = "error"
        account.status_message = _mask_error(exc)
    account.checked_at = utcnow()
    await session.commit()
    return account.status == "ok", account.status_message


async def list_dirs(session: AsyncSession, drive_key: str, parent_id: str = "0") -> list[dict[str, str]]:
    account = await get_account(session, drive_key)
    drive_obj = build_drive(account)
    dirs = await drive_obj.list_dirs(parent_id)
    if account is not None:
        await persist_extra(account, drive_obj)
        await session.commit()
    return [{"id": d.id, "name": d.name} for d in dirs]


async def save_share(
    session: AsyncSession,
    *,
    drive_key: str,
    share_url: str,
    passcode: str = "",
    dir_id: str | None = None,
    dir_name: str = "",
    source: str = "web",
    tg_user_id: int | None = None,
    tg_username: str = "",
) -> SaveLog:
    """执行一次转存并落库日志（无论成功失败都会留下记录）。"""
    started = time.perf_counter()
    account = await get_account(session, drive_key)
    use_default = dir_id in (None, "")
    target_dir = (account.default_dir_id if account else "0") if use_default else dir_id
    if dir_name:
        target_name = dir_name
    elif use_default:
        target_name = account.default_dir_name if account else "根目录"
    else:
        # 显式指定了目录但没给名字（如后台手动转存），留空由前端显示目录 ID
        target_name = ""

    log = SaveLog(
        drive=drive_key,
        share_url=share_url,
        share_id="",
        passcode=passcode or "",
        dir_id=str(target_dir or "0"),
        dir_name=target_name or "",
        status="failed",
        source=source,
        tg_user_id=tg_user_id,
        tg_username=tg_username or "",
    )

    try:
        drive_obj = build_drive(account)
        result = await drive_obj.save_share(share_url, passcode or "", str(target_dir or "0"))
        log.files = result.files
        log.message = result.message or ("转存成功" if result.ok else "转存失败")
        log.status = "success" if result.ok else "failed"
        account.status = "ok"
        account.status_message = ""
        await persist_extra(account, drive_obj)
    except NotSupported as exc:
        log.message = exc.message
    except DriveError as exc:
        log.message = exc.message
        if account is not None:
            expired_hint = ("Cookie", "授权", "凭证", "token", "Token")
            account.status = "expired" if any(h in exc.message for h in expired_hint) else account.status
            account.status_message = exc.message
    except Exception as exc:  # noqa: BLE001
        log.message = _mask_error(exc)

    log.duration_ms = int((time.perf_counter() - started) * 1000)
    session.add(log)
    if account is not None:
        account.checked_at = utcnow()
    await session.commit()
    await session.refresh(log)
    return log
