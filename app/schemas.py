"""请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)  # 强度校验在 security.password_strength_error
    setup_token: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class AuthStatusOut(BaseModel):
    setup_required: bool
    authenticated: bool
    username: str | None = None
    bot_ready: bool = False
    bot_running: bool = False
    bot_username: str = ""
    app_name: str = "PanSaver"


class CookieIn(BaseModel):
    cookie: str = Field(min_length=1)


class DefaultDirIn(BaseModel):
    dir_id: str
    dir_name: str = ""


class AllowIn(BaseModel):
    tg_user_id: int
    username: str = ""
    note: str = ""


class AllowPatchIn(BaseModel):
    enabled: bool


class ManualSaveIn(BaseModel):
    drive: str
    share_url: str
    passcode: str = ""
    dir_id: str | None = None
