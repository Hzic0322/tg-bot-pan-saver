"""日志与统计 API。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import get_current_admin
from ..models import SaveLog

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(get_current_admin)])


def _serialize(log: SaveLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "drive": log.drive,
        "share_url": log.share_url,
        "passcode": log.passcode,
        "dir_id": log.dir_id,
        "dir_name": log.dir_name,
        "status": log.status,
        "message": log.message,
        "files": log.files,
        "source": log.source,
        "tg_user_id": log.tg_user_id,
        "tg_username": log.tg_username,
        "duration_ms": log.duration_ms,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.get("")
async def list_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    drive: str | None = None,
    status: str | None = None,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(SaveLog)
    count_stmt = select(func.count()).select_from(SaveLog)
    conditions = []
    if drive:
        conditions.append(SaveLog.drive == drive)
    if status in ("success", "failed"):
        conditions.append(SaveLog.status == status)
    if q:
        like = f"%{q}%"
        conditions.append(or_(SaveLog.share_url.ilike(like), SaveLog.message.ilike(like), SaveLog.dir_name.ilike(like)))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(
            stmt.order_by(SaveLog.id.desc()).offset((page - 1) * size).limit(size)
        )
    ).scalars()
    return {"items": [_serialize(r) for r in rows], "total": total, "page": page, "size": size}


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    today = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = int((await session.execute(select(func.count()).select_from(SaveLog))).scalar_one())
    today_total = int(
        (await session.execute(select(func.count()).select_from(SaveLog).where(SaveLog.created_at >= today))).scalar_one()
    )
    today_ok = int(
        (
            await session.execute(
                select(func.count())
                .select_from(SaveLog)
                .where(SaveLog.created_at >= today, SaveLog.status == "success")
            )
        ).scalar_one()
    )
    per_drive_rows = (
        await session.execute(
            select(SaveLog.drive, func.count()).group_by(SaveLog.drive).order_by(func.count().desc())
        )
    ).all()
    return {
        "total": total,
        "today": today_total,
        "today_success": today_ok,
        "today_failed": today_total - today_ok,
        "success_rate": round(today_ok / today_total * 100, 1) if today_total else 0.0,
        "per_drive": [{"drive": d, "count": c} for d, c in per_drive_rows],
    }


@router.delete("/{log_id}")
async def delete_log(log_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    log = await session.get(SaveLog, log_id)
    if log:
        await session.delete(log)
        await session.commit()
    return {"ok": True}
