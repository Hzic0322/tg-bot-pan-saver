"""Bot 生命周期：在 FastAPI 启动时拉起 polling，关闭时优雅停止。

设计要点（国内服务器 + 代理场景）：
- 代理/隧道可能比 Bot 晚就绪，或中途断开，所以 getMe 和 polling 都带重试，不死在一个连接错误上。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from ..config import settings
from .handlers import router

log = logging.getLogger(__name__)
T = TypeVar("T")

RETRY_ATTEMPTS = 5
RETRY_DELAY = 3.0
POLLING_RESTART_DELAY = 10.0


async def retry_async(
    factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = RETRY_ATTEMPTS,
    delay: float = RETRY_DELAY,
    on_error: Callable[[Exception, int], None] | None = None,
) -> T:
    """把一次「可能因为网络/代理没起来而失败」的调用重试几次。"""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if on_error:
                on_error(exc, attempt)
            if attempt < attempts:
                await asyncio.sleep(delay)
    assert last is not None
    raise last


class BotRunner:
    def __init__(self) -> None:
        self.bot: Bot | None = None
        self.dispatcher: Dispatcher | None = None
        self.task: asyncio.Task | None = None
        self.error: str = ""
        self.username: str = ""
        self._stopping = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def build_bot(self) -> Bot:
        session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else AiohttpSession()
        return Bot(
            token=settings.telegram_bot_token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    async def start(self) -> None:
        if not settings.bot_ready:
            log.info("未配置 TELEGRAM_BOT_TOKEN，Bot 未启动（网页面板可正常使用）")
            return
        try:
            self.bot = self.build_bot()
            self.dispatcher = Dispatcher()
            self.dispatcher.include_router(router)
        except Exception as exc:  # noqa: BLE001 - Bot 起不来也不能影响网页面板
            self.error = f"{type(exc).__name__}: {exc}"
            log.error("Bot 初始化失败：%s（网页面板继续可用）", exc)
            return
        self.task = asyncio.create_task(self._serve(), name="pansaver-bot-polling")
        log.info("Bot 轮询已启动（代理：%s）", settings.bot_proxy or "直连")

    async def _serve(self) -> None:
        """先握手确认凭证，再进入 polling；任何一步断了都退避重来。"""
        assert self.bot is not None and self.dispatcher is not None
        while not self._stopping:
            try:
                me = await retry_async(
                    lambda: self.bot.get_me() if self.bot else None,  # type: ignore[misc]
                    on_error=lambda exc, i: log.warning("Bot getMe 第 %s 次失败：%s", i, exc),
                )
                if me is not None:
                    self.username = me.username or ""
                    self.error = ""
                    log.info("Bot 已就绪：@%s", self.username)
                await self.dispatcher.start_polling(self.bot, handle_signals=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                log.warning("Bot 轮询中断：%s；%.0fs 后重试", exc, POLLING_RESTART_DELAY)
                try:
                    await asyncio.sleep(POLLING_RESTART_DELAY)
                except asyncio.CancelledError:
                    raise

    async def stop(self) -> None:
        self._stopping = True
        if self.dispatcher is not None:
            try:
                await self.dispatcher.stop_polling()
            except Exception:  # noqa: BLE001
                pass
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.bot is not None:
            try:
                await self.bot.session.close()
            except Exception:  # noqa: BLE001
                pass


bot_runner = BotRunner()
