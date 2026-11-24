"""认证：首次初始化管理员、登录、登出、改密。"""

from __future__ import annotations

import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..bot.runner import bot_runner
from ..config import settings
from ..db import get_session
from ..deps import get_current_admin
from ..models import Admin, AuthSession
from ..schemas import AuthStatusOut, ChangePasswordIn, LoginIn, SetupIn
from ..security import (
    hash_password,
    new_session_token,
    password_strength_error,
    token_digest,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 简易登录限速：同一 IP 10 分钟内最多 10 次失败
_FAIL_WINDOW = 600
_FAIL_LIMIT = 10
_failures: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _too_many_failures(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW]
    _failures[ip] = hits
    return len(hits) >= _FAIL_LIMIT


def _record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


async def _issue_session(session: AsyncSession, admin: Admin, request: Request, response: Response) -> str:
    token = new_session_token()
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=settings.session_ttl_hours)
    session.add(
        AuthSession(
            token_hash=token_digest(token),
            admin_id=admin.id,
            expires_at=expires,
            user_agent=(request.headers.get("user-agent") or "")[:250],
        )
    )
    await session.commit()
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return token


async def _admin_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(Admin))).scalar_one())


@router.get("/status", response_model=AuthStatusOut)
async def auth_status(request: Request, session: AsyncSession = Depends(get_session)) -> AuthStatusOut:
    count = await _admin_count(session)
    authenticated, username = False, None
    token = request.cookies.get(settings.cookie_name)
    if token:
        row = (
            await session.execute(
                select(AuthSession, Admin)
                .join(Admin, Admin.id == AuthSession.admin_id)
                .where(AuthSession.token_hash == token_digest(token))
            )
        ).first()
        if row:
            auth_row, admin = row
            expires = auth_row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt.timezone.utc)
            if expires > dt.datetime.now(dt.timezone.utc):
                authenticated, username = True, admin.username
    return AuthStatusOut(
        setup_required=count == 0,
        authenticated=authenticated,
        username=username,
        bot_ready=settings.bot_ready,
        bot_running=bot_runner.running,
        bot_username=bot_runner.username,
        app_name=settings.app_name,
    )


@router.post("/setup", response_model=AuthStatusOut)
async def setup(
    payload: SetupIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthStatusOut:
    if await _admin_count(session) > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="管理员已存在，请直接登录")
    if not settings.allow_open_setup:
        if not settings.setup_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "已关闭开放初始化（ALLOW_OPEN_SETUP=false）但没有设置 SETUP_TOKEN。"
                    "请设置 SETUP_TOKEN 后重试，或在服务器上执行 "
                    "`python -m app.cli create-admin --username admin` 创建管理员。"
                ),
            )
        if payload.setup_token != settings.setup_token:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="初始化口令错误")
    problem = password_strength_error(payload.password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if not payload.username.strip() or len(payload.username.strip()) < 3:
        raise HTTPException(status_code=400, detail="用户名至少 3 个字符")

    admin = Admin(
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        last_login_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    await _issue_session(session, admin, request, response)
    return AuthStatusOut(
        setup_required=False,
        authenticated=True,
        username=admin.username,
        bot_ready=settings.bot_ready,
        app_name=settings.app_name,
    )


@router.post("/login", response_model=AuthStatusOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthStatusOut:
    ip = _client_ip(request)
    if _too_many_failures(ip):
        raise HTTPException(status_code=429, detail="登录失败次数过多，请 10 分钟后再试")
    admin = (
        await session.execute(select(Admin).where(Admin.username == payload.username.strip()))
    ).scalar_one_or_none()
    if admin is None or not verify_password(payload.password, admin.password_hash):
        _record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    _failures.pop(ip, None)
    admin.last_login_at = dt.datetime.now(dt.timezone.utc)
    await _issue_session(session, admin, request, response)
    return AuthStatusOut(
        setup_required=False,
        authenticated=True,
        username=admin.username,
        bot_ready=settings.bot_ready,
        app_name=settings.app_name,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    token = request.cookies.get(settings.cookie_name)
    if token:
        await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_digest(token)))
        await session.commit()
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
async def me(admin: Admin = Depends(get_current_admin)) -> dict[str, str | None]:
    return {"username": admin.username}


@router.post("/password")
async def change_password(
    payload: ChangePasswordIn,
    request: Request,
    response: Response,
    admin: Admin = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    if not verify_password(payload.old_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    problem = password_strength_error(payload.new_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    admin.password_hash = hash_password(payload.new_password)
    # 让其它会话失效
    await session.execute(delete(AuthSession).where(AuthSession.admin_id == admin.id))
    await session.commit()
    await _issue_session(session, admin, request, response)
    return {"ok": True}
