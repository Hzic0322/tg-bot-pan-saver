"""Bot 生命周期：在 FastAPI 启动时拉起 polling，关闭时优雅停止。"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from ..config import settings
from .handlers import router

log = logging.getLogger(__name__)


class BotRunner:
    def __init__(self) -> None:
        self.bot: Bot | None = None
        self.dispatcher: Dispatcher | None = None
        self.task: asyncio.Task | None = None
        self.error: str = ""

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self) -> None:
        if not settings.bot_ready:
            log.info("未配置 TELEGRAM_BOT_TOKEN，Bot 未启动（网页面板可正常使用）")
            return
        try:
            session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else AiohttpSession()
            self.bot = Bot(
                token=settings.telegram_bot_token,
                session=session,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            me = await self.bot.get_me()
            self.dispatcher = Dispatcher()
            self.dispatcher.include_router(router)
            self.task = asyncio.create_task(self.dispatcher.start_polling(self.bot, handle_signals=False))
            log.info("Bot 已启动：@%s", me.username)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.error("Bot 启动失败：%s", exc)

    async def stop(self) -> None:
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
