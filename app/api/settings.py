"""系统设置 API：Bot 白名单、运行信息。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..deps import get_current_admin
from ..models import TgAllow
from ..schemas import AllowIn, AllowPatchIn
from ..drives.registry import drive_meta

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(get_current_admin)])


@router.get("")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    allows = (await session.execute(select(TgAllow).order_by(TgAllow.id))).scalars()
    return {
        "app_name": settings.app_name,
        "bot_ready": settings.bot_ready,
        "bot_token_masked": (settings.telegram_bot_token[:8] + "***") if settings.telegram_bot_token else "",
        "allow_open_setup": settings.allow_open_setup,
        "drives": drive_meta(),
        "allows": [
            {
                "id": a.id,
                "tg_user_id": a.tg_user_id,
                "username": a.username,
                "note": a.note,
                "enabled": a.enabled,
                "created_at": (
                    (a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=dt.timezone.utc)).isoformat()
                    if a.created_at
                    else None
                ),
            }
            for a in allows
        ],
    }


@router.post("/allows")
async def add_allow(payload: AllowIn, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    exists = (
        await session.execute(select(TgAllow).where(TgAllow.tg_user_id == payload.tg_user_id))
    ).scalar_one_or_none()
    if exists:
        exists.enabled = True
        exists.username = payload.username or exists.username
        exists.note = payload.note or exists.note
        await session.commit()
        return {"ok": True, "id": exists.id}
    row = TgAllow(tg_user_id=payload.tg_user_id, username=payload.username, note=payload.note)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"ok": True, "id": row.id}


@router.patch("/allows/{allow_id}")
async def patch_allow(
    allow_id: int, payload: AllowPatchIn, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    row = await session.get(TgAllow, allow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    row.enabled = payload.enabled
    await session.commit()
    return {"ok": True}


@router.delete("/allows/{allow_id}")
async def delete_allow(allow_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    row = await session.get(TgAllow, allow_id)
    if row is not None:
        await session.delete(row)
        await session.commit()
    return {"ok": True}
