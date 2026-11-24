"""ORM 模型。"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Admin(Base):
    """后台管理员（首个管理员通过 /api/auth/setup 创建）。"""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    """登录会话（只保存 token 的 sha256）。"""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(255), default="")


class DriveAccount(Base):
    """一个网盘的绑定状态（每种网盘一条）。"""

    __tablename__ = "drive_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drive: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    cookie: Mapped[str] = mapped_column(Text, default="")  # JSON: {"k": "v"}
    extra: Mapped[str] = mapped_column(Text, default="{}")  # JSON: token / refresh_token 等
    account_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="unbound")  # unbound|ok|expired|error
    status_message: Mapped[str] = mapped_column(Text, default="")
    default_dir_id: Mapped[str] = mapped_column(String(64), default="0")
    default_dir_name: Mapped[str] = mapped_column(String(255), default="根目录")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SaveLog(Base):
    """转存日志。"""

    __tablename__ = "save_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drive: Mapped[str] = mapped_column(String(16), index=True)
    share_url: Mapped[str] = mapped_column(Text)
    share_id: Mapped[str] = mapped_column(String(128), default="")
    passcode: Mapped[str] = mapped_column(String(32), default="")
    dir_id: Mapped[str] = mapped_column(String(64), default="0")
    dir_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), index=True)  # success|failed
    message: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="bot")  # bot|web|cli
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    tg_username: Mapped[str] = mapped_column(String(128), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TgAllow(Base):
    """允许使用 Bot 的 Telegram 用户白名单。"""

    __tablename__ = "tg_allows"
    __table_args__ = (UniqueConstraint("tg_user_id", name="uq_tg_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """键值配置（预留）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
