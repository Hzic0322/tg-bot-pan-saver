"""网盘适配器注册表。"""

from __future__ import annotations

import json
from typing import Any

from ..config import settings
from ..models import DriveAccount
from .baidu import BaiduDrive
from .base import BaseDrive, DriveError, NotSupported
from .mock import MockDrive
from .pan115 import Pan115Drive
from .quark import QuarkDrive
from .xunlei import XunleiDrive

DRIVE_CLASSES: dict[str, type[BaseDrive]] = {
    Pan115Drive.key: Pan115Drive,
    BaiduDrive.key: BaiduDrive,
    QuarkDrive.key: QuarkDrive,
    XunleiDrive.key: XunleiDrive,
}

if settings.enable_mock:
    DRIVE_CLASSES[MockDrive.key] = MockDrive

OFFICIAL_DRIVES = ("115", "baidu", "quark", "xunlei")


def get_drive_class(key: str) -> type[BaseDrive]:
    cls = DRIVE_CLASSES.get(key)
    if cls is None:
        raise DriveError(f"不支持的网盘：{key}")
    return cls


def build_drive(account: DriveAccount | None) -> BaseDrive:
    """根据数据库中的绑定记录构造适配器实例。"""
    if account is None:
        raise DriveError("该网盘未绑定，请先在后台扫码登录")
    cls = get_drive_class(account.drive)
    cookie: dict[str, str] = {}
    extra: dict[str, Any] = {}
    if account.cookie:
        try:
            parsed = json.loads(account.cookie)
            cookie = {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            cookie = {}
    if account.extra:
        try:
            parsed = json.loads(account.extra)
            extra = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            extra = {}
    return cls(cookie=cookie, extra=extra)


def drive_meta() -> list[dict[str, Any]]:
    metas: list[dict[str, Any]] = []
    for key, cls in DRIVE_CLASSES.items():
        metas.append(
            {
                "key": key,
                "name": cls.name,
                "domains": list(cls.domains),
                "supports_qr": cls.supports_qr,
                "qr_note": cls.qr_note,
                "required_cookie": list(cls.required_cookie),
                "official": key in OFFICIAL_DRIVES,
            }
        )
    return metas


__all__ = [
    "BaseDrive",
    "DriveError",
    "NotSupported",
    "build_drive",
    "drive_meta",
    "get_drive_class",
    "DRIVE_CLASSES",
    "OFFICIAL_DRIVES",
]
