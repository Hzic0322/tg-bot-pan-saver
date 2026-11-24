"""Bot 层纯逻辑测试：目录键盘、链接提示文案、白名单。"""

from __future__ import annotations

from app.bot.handlers import build_keyboard, describe_allowlist, describe_link, is_allowed
from app.db import SessionFactory
from app.models import TgAllow
from app.services.linkparse import FoundLink


def _dirs(n: int) -> list[dict[str, str]]:
    return [{"id": f"id{i}", "name": f"目录{i}"} for i in range(n)]


def test_keyboard_lists_dirs_with_default_and_cancel():
    kb = build_keyboard("pid12345", _dirs(3), "剧集", "quark")
    rows = kb.inline_keyboard
    labels = [r[0].text for r in rows]
    assert any("目录0" in t for t in labels)
    assert any("默认目录：剧集" in t for t in labels)
    assert labels[-1] == "❌ 取消"
    assert rows[-1][0].callback_data == "sv:pid12345:x"
    assert rows[-2][0].callback_data == "sv:pid12345:d"


def test_keyboard_caps_at_18_dirs():
    kb = build_keyboard("p", _dirs(30), "根目录", "115")
    # 18 个目录 + 默认 + 取消
    assert len(kb.inline_keyboard) == 20


def test_keyboard_callback_data_fits_telegram_limit():
    kb = build_keyboard("a" * 12, _dirs(5), "x" * 40, "baidu")
    for row in kb.inline_keyboard:
        for button in row:
            assert len((button.callback_data or "").encode()) <= 64


def test_keyboard_without_default_has_no_default_row():
    kb = build_keyboard("p", _dirs(2), "", "xunlei")
    assert not any("默认目录" in r[0].text for r in kb.inline_keyboard)


def test_keyboard_escapes_nothing_but_truncates_long_names():
    kb = build_keyboard("p", [{"id": "1", "name": "很长的目录名" * 10}], "", "quark")
    assert len(kb.inline_keyboard[0][0].text) <= 32


def test_describe_link_contains_url_and_passcode():
    link = FoundLink(drive="quark", url="https://pan.quark.cn/s/abc", share_id="abc", passcode="k9m2")
    text = describe_link(link, "夸克网盘")
    assert "夸克网盘" in text
    assert "https://pan.quark.cn/s/abc" in text
    assert "k9m2" in text


def test_describe_link_without_passcode_hints():
    link = FoundLink(drive="baidu", url="https://pan.baidu.com/s/1x")
    text = describe_link(link, "百度网盘")
    assert "未识别" in text


async def test_is_allowed_defaults_to_deny(client):
    assert await is_allowed(999001) is False


async def test_is_allowed_after_insert_and_disable(client):
    async with SessionFactory() as session:
        row = TgAllow(tg_user_id=999002, username="tester")
        session.add(row)
        await session.commit()

    assert await is_allowed(999002) is True

    async with SessionFactory() as session:
        row = (await session.execute(__import__("sqlalchemy").select(TgAllow))).scalars().first()
        row.enabled = False
        await session.commit()

    assert await is_allowed(999002) is False


async def test_describe_allowlist(client):
    assert "白名单为空" in await describe_allowlist()
    async with SessionFactory() as session:
        session.add(TgAllow(tg_user_id=999003, username="alice"))
        await session.commit()
    assert "@alice" in await describe_allowlist()
