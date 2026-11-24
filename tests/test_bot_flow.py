"""Bot 端到端测试：真实走 aiogram Dispatcher，用假 Session 记录对 Telegram 的调用。

覆盖需求「bot 收到转发消息 → 自动识别网盘域名 → 弹出目录选择 → 转存 → 回执 → 落日志」。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot import handlers
from app.db import SessionFactory
from app.models import DriveAccount, SaveLog, TgAllow

USER_ID = 777001


class FakeTelegram(BaseSession):
    """记录所有对外 API 调用，不真的发请求。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []

    async def make_request(self, bot: Bot, method: Any, timeout: Any = None) -> Any:  # noqa: ANN401
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        chat_id = getattr(method, "chat_id", USER_ID)
        return Message(
            message_id=1,
            date=dt.datetime.now(),
            chat=Chat(id=int(chat_id), type="private"),
            text=getattr(method, "text", ""),
        )

    async def stream_content(self, *args: Any, **kwargs: Any):  # noqa: ANN002,ANN003,ANN201
        yield b""

    async def close(self) -> None:
        return None

    # --- 便捷断言 ---
    def of(self, kind: type) -> list[Any]:
        return [c for c in self.calls if isinstance(c, kind)]


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def bot(tg: FakeTelegram) -> Bot:
    return Bot(token="42:TESTTOKEN", session=tg)


@pytest.fixture(scope="session")
def dp() -> Dispatcher:
    """Router 只能挂在同一个 Dispatcher 上，所以整个测试会话共用一个。"""
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.router)
    return dispatcher


@pytest.fixture(autouse=True)
def _reset_handler_state() -> None:
    handlers.PENDING.clear()
    handlers.DIR_CACHE.clear()


async def _bind_mock(*, allow: bool = True, bind: bool = True) -> None:
    async with SessionFactory() as session:
        if allow:
            session.add(TgAllow(tg_user_id=USER_ID, username="tester"))
        if bind:
            session.add(
                DriveAccount(
                    drive="mock",
                    cookie=json.dumps({"MOCK_SESSION": "mock-token"}),
                    status="ok",
                    account_name="Mock 用户",
                    default_dir_id="100",
                    default_dir_name="电影",
                )
            )
        await session.commit()


def _message(bot: Bot, text: str) -> Message:
    msg = Message(
        message_id=10,
        date=dt.datetime.now(),
        chat=Chat(id=USER_ID, type="private"),
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
        text=text,
    )
    msg.as_(bot)
    return msg


async def _send(bot: Bot, dp: Dispatcher, text: str) -> None:
    update = Update(update_id=1, message=_message(bot, text))
    update.as_(bot)
    await dp.feed_update(bot, update)


def _bot_message(bot: Bot, sent: SendMessage) -> Message:
    """还原「Bot 发出的那条带键盘的消息」，作为 callback.message。"""
    msg = Message(
        message_id=11,
        date=dt.datetime.now(),
        chat=Chat(id=USER_ID, type="private"),
        text=sent.text,
        reply_markup=sent.reply_markup,
    )
    msg.as_(bot)
    return msg


async def _callback(bot: Bot, dp: Dispatcher, message: Message, data: str) -> None:
    query = CallbackQuery(
        id="cb-1",
        from_user=User(id=USER_ID, is_bot=False, first_name="T"),
        chat_instance="ci",
        message=message,
        data=data,
    )
    query.as_(bot)
    update = Update(update_id=2, callback_query=query)
    update.as_(bot)
    await dp.feed_update(bot, update)


async def test_forwarded_link_shows_dir_keyboard_and_saves(client, bot, dp, tg):
    await _bind_mock()
    await _send(bot, dp, "合集 https://mockdrive.test/s/abc123 提取码：mock")

    sent = tg.of(SendMessage)
    assert len(sent) == 1
    assert "分享链接已识别" in sent[0].text
    assert "https://mockdrive.test/s/abc123" in sent[0].text
    assert "mock" in sent[0].text

    keyboard = sent[0].reply_markup
    labels = [row[0].text for row in keyboard.inline_keyboard]
    assert any("电影" in label for label in labels)
    assert any("默认目录：电影" in label for label in labels)

    first = keyboard.inline_keyboard[0][0].callback_data
    assert first.startswith("sv:")
    await _callback(bot, dp, _bot_message(bot, sent[0]), first)

    edits = tg.of(EditMessageText)
    assert edits and "转存成功" in edits[-1].text
    assert "电影" in edits[-1].text

    async with SessionFactory() as session:
        rows = (await session.execute(__import__("sqlalchemy").select(SaveLog))).scalars().all()
    assert len(rows) == 1
    assert (rows[0].drive, rows[0].status, rows[0].source) == ("mock", "success", "bot")
    assert rows[0].tg_user_id == USER_ID
    assert rows[0].dir_id == "100"


async def test_default_dir_button_uses_configured_target(client, bot, dp, tg):
    await _bind_mock()
    await _send(bot, dp, "https://mockdrive.test/s/def456")
    sent = tg.of(SendMessage)[0]
    default_button = [row[0] for row in sent.reply_markup.inline_keyboard if "默认目录" in row[0].text][0]

    await _callback(bot, dp, _bot_message(bot, sent), default_button.callback_data)

    async with SessionFactory() as session:
        row = (await session.execute(__import__("sqlalchemy").select(SaveLog))).scalars().first()
    assert (row.status, row.dir_id) == ("success", "100")


async def test_cancel_button_does_not_transfer(client, bot, dp, tg):
    await _bind_mock()
    await _send(bot, dp, "https://mockdrive.test/s/abc123")
    sent = tg.of(SendMessage)[0]
    cancel = sent.reply_markup.inline_keyboard[-1][0]
    assert cancel.text == "❌ 取消"

    await _callback(bot, dp, _bot_message(bot, sent), cancel.callback_data)

    assert "已取消" in tg.of(EditMessageText)[-1].text
    async with SessionFactory() as session:
        assert (await session.execute(__import__("sqlalchemy").select(SaveLog))).scalars().first() is None


async def test_unauthorized_user_gets_id_and_no_keyboard(client, bot, dp, tg):
    await _bind_mock(allow=False)
    await _send(bot, dp, "https://mockdrive.test/s/abc123")

    sent = tg.of(SendMessage)
    assert len(sent) == 1
    assert "还没有使用权限" in sent[0].text
    assert str(USER_ID) in sent[0].text
    assert sent[0].reply_markup is None


async def test_unbound_drive_asks_to_login_first(client, bot, dp, tg):
    await _bind_mock(bind=False)
    await _send(bot, dp, "https://mockdrive.test/s/abc123")

    sent = tg.of(SendMessage)
    assert len(sent) == 1
    assert "还没有绑定凭证" in sent[0].text
    assert sent[0].reply_markup is None


async def test_text_without_link_is_ignored(client, bot, dp, tg):
    await _bind_mock()
    await _send(bot, dp, "今天天气不错，晚上吃什么")
    assert tg.calls == []


async def test_failed_transfer_reports_reason(client, bot, dp, tg):
    await _bind_mock()
    await _send(bot, dp, "https://mockdrive.test/s/abc123 提取码：wrong")
    sent = tg.of(SendMessage)[0]
    await _callback(bot, dp, _bot_message(bot, sent), sent.reply_markup.inline_keyboard[0][0].callback_data)

    text = tg.of(EditMessageText)[-1].text
    assert "转存失败" in text
    assert "提取码错误" in text

    async with SessionFactory() as session:
        row = (await session.execute(__import__("sqlalchemy").select(SaveLog))).scalars().first()
    assert row.status == "failed"


async def test_expired_pending_selection_is_rejected(client, bot, dp, tg):
    await _bind_mock()
    await _send(bot, dp, "https://mockdrive.test/s/abc123")
    sent = tg.of(SendMessage)[0]
    handlers.PENDING.clear()  # 模拟超时清理
    await _callback(bot, dp, _bot_message(bot, sent), "sv:notexist:0")

    alerts = [c for c in tg.of(AnswerCallbackQuery) if c.show_alert]
    assert alerts and "过期" in alerts[-1].text
