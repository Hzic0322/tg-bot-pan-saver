"""FastAPI 依赖：登录态校验。"""

from __future__ import annotations

import datetime as dt

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import Admin, AuthSession
from .security import token_digest


def _session_token(request: Request) -> str | None:
    return request.cookies.get(settings.cookie_name)


async def get_current_admin(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Admin:
    token = _session_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    row = (
        await session.execute(
            select(AuthSession, Admin)
            .join(Admin, Admin.id == AuthSession.admin_id)
            .where(AuthSession.token_hash == token_digest(token))
        )
    ).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")
    auth_row, admin = row
    expires = auth_row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if expires < dt.datetime.now(dt.timezone.utc):
        await session.delete(auth_row)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    return admin
