"""扫码会话（内存存储）。

说明：状态保存在进程内存里，因此部署时必须单进程运行（uvicorn 不要开多 worker），
或改用 uvicorn --workers 1 + 反向代理。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

TTL_SECONDS = 300


@dataclass
class QRSession:
    id: str
    drive: str
    state: dict[str, Any]
    mode: str
    image_url: str = ""
    content: str = ""
    tip: str = ""
    interval: float = 1.5
    expires_in: int = 180
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > min(self.expires_in, TTL_SECONDS)

    def to_public(self) -> dict[str, Any]:
        return {
            "qr_id": self.id,
            "drive": self.drive,
            "mode": self.mode,
            "image_url": self.image_url,
            "content": self.content,
            "tip": self.tip,
            "interval": self.interval,
            "expires_in": max(0, int(self.expires_in - (time.time() - self.created_at))),
        }


class QRStore:
    def __init__(self) -> None:
        self._items: dict[str, QRSession] = {}

    def _sweep(self) -> None:
        now = time.time()
        for key in [k for k, v in self._items.items() if now - v.created_at > TTL_SECONDS]:
            self._items.pop(key, None)

    def create(self, drive: str, state: dict[str, Any], **kwargs: Any) -> QRSession:
        self._sweep()
        qr_id = secrets.token_urlsafe(12)
        session = QRSession(id=qr_id, drive=drive, state=state, **kwargs)
        self._items[qr_id] = session
        return session

    def get(self, qr_id: str) -> QRSession | None:
        self._sweep()
        return self._items.get(qr_id)

    def drop(self, qr_id: str) -> None:
        self._items.pop(qr_id, None)


qr_store = QRStore()
