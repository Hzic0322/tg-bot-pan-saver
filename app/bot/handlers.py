"""Telegram Bot 行为：识别分享链接 → 弹出目录选择 → 转存 → 记日志。"""

from __future__ import annotations

import html
import secrets
import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from ..config import settings
from ..db import SessionFactory
from ..models import DriveAccount, TgAllow
from ..services import transfer as transfer_service
from ..services.linkparse import FoundLink, parse_links

router = Router()

PENDING_TTL = 30 * 60
PENDING: dict[str, dict[str, Any]] = {}
DIR_CACHE_TTL = 120
DIR_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}

DRIVE_EMOJI = {"115": "🔵", "baidu": "🟦", "quark": "🟣", "xunlei": "🔷", "mock": "🧪"}


# ---------------- 权限 ----------------
async def is_allowed(user_id: int) -> bool:
    if settings.bot_default_allow_all:
        return True
    async with SessionFactory() as session:
        row = (
            await session.execute(select(TgAllow).where(TgAllow.tg_user_id == user_id, TgAllow.enabled.is_(True)))
        ).scalar_one_or_none()
    return row is not None


async def describe_allowlist() -> str:
    async with SessionFactory() as session:
        rows = (await session.execute(select(TgAllow).where(TgAllow.enabled.is_(True)))).scalars().all()
    if not rows:
        return "（白名单为空）"
    return "、".join(f"@{r.username or r.tg_user_id}" for r in rows[:10])


# ---------------- 目录缓存 ----------------
async def get_dirs(drive: str) -> list[dict[str, str]]:
    cached = DIR_CACHE.get(drive)
    now = time.time()
    if cached and now - cached[0] < DIR_CACHE_TTL:
        return cached[1]
    async with SessionFactory() as session:
        items = await transfer_service.list_dirs(session, drive, "0")
    DIR_CACHE[drive] = (now, items)
    return items


# ---------------- 键盘 ----------------
def build_keyboard(pid: str, dirs: list[dict[str, str]], default_name: str, drive: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, item in enumerate(dirs[:18]):
        rows.append([InlineKeyboardButton(text=f"📁 {item['name'][:28]}", callback_data=f"sv:{pid}:{idx}")])
    if default_name:
        rows.append([InlineKeyboardButton(text=f"⭐ 默认目录：{default_name}"[:60], callback_data=f"sv:{pid}:d")])
    rows.append([InlineKeyboardButton(text="❌ 取消", callback_data=f"sv:{pid}:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def describe_link(link: FoundLink, drive_name: str) -> str:
    lines = [f"{DRIVE_EMOJI.get(link.drive, '☁️')} <b>{html.escape(drive_name)}</b> 分享链接已识别"]
    lines.append(f"链接：<code>{html.escape(link.url)}</code>")
    lines.append(f"提取码：<code>{html.escape(link.passcode)}</code>" if link.passcode else "提取码：未识别（如需要请在链接后附上）")
    lines.append("")
    lines.append("请选择保存到哪个目录 👇")
    return "\n".join(lines)


# ---------------- 命令 ----------------
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not await is_allowed(message.from_user.id):
        await message.answer(
            "⛔️ 你还没有使用权限。\n"
            f"你的 Telegram ID：<code>{message.from_user.id}</code>\n"
            "把上面这串数字发给管理员，加入白名单后即可使用。"
        )
        return
    await message.answer(
        "👋 我是网盘转存助手。\n\n"
        "把网盘分享链接转发给我（或直接粘贴文字），我会自动识别 115 / 百度 / 夸克 / 迅雷 链接，"
        "并让你选择保存目录。\n\n"
        "指令：\n"
        "/drives —— 查看已绑定的网盘与默认目录\n"
        "/help —— 使用说明"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "使用方式：\n"
        "1. 把带分享链接的消息转发给我；\n"
        "2. 我识别到链接后，会回复一个目录列表，点一下就转存；\n"
        "3. 转存结果会回复在同一个消息里，后台可查完整日志。\n\n"
        "支持：115 网盘、百度网盘、夸克网盘、迅雷网盘。\n"
        "提取码可写在链接后（如「提取码: abcd」），也可以直接带 pwd= 参数。",
        disable_web_page_preview=True,
    )


@router.message(Command("drives"))
async def cmd_drives(message: Message) -> None:
    if not await is_allowed(message.from_user.id):
        await message.answer("⛔️ 你还没有使用权限。")
        return
    async with SessionFactory() as session:
        accounts = (await session.execute(select(DriveAccount))).scalars().all()
    if not accounts:
        await message.answer("还没有绑定任何网盘，请先在后台扫码登录。")
        return
    lines = ["<b>已绑定网盘</b>"]
    for acc in accounts:
        status = {"ok": "✅", "expired": "⚠️", "unbound": "⚪️"}.get(acc.status, "❔")
        lines.append(f"{status} {acc.drive} · {acc.account_name or '未命名'} · 默认目录：{acc.default_dir_name}")
    await message.answer("\n".join(lines))


# ---------------- 链接处理 ----------------
@router.message(F.text | F.caption)
async def on_message(message: Message, bot: Bot) -> None:
    text = message.text or message.caption or ""
    links = parse_links(text, limit=settings.bot_max_links_per_message)
    if not links:
        return

    user = message.from_user
    if not await is_allowed(user.id):
        await message.reply(
            "⛔️ 你还没有使用权限。\n"
            f"你的 Telegram ID：<code>{user.id}</code>\n"
            "请管理员在后台「设置 → 白名单」里加入后再试。"
        )
        return

    for link in links:
        await _prompt_for_dir(message, link)


async def _prompt_for_dir(message: Message, link: FoundLink) -> None:
    async with SessionFactory() as session:
        account = await transfer_service.get_account(session, link.drive)
    if account is None or not (account.cookie or account.extra) or account.status == "unbound":
        await message.reply(
            f"{DRIVE_EMOJI.get(link.drive, '☁️')} 识别到 {link.drive} 链接，但该网盘还没有绑定凭证。\n"
            "请先在网页面板扫码登录后再试。",
            disable_web_page_preview=True,
        )
        return

    try:
        dirs = await get_dirs(link.drive)
    except Exception as exc:  # noqa: BLE001
        await message.reply(f"读取 {link.drive} 目录失败：{exc}")
        return

    if not dirs:
        await message.reply(
            f"{DRIVE_EMOJI.get(link.drive, '☁️')} 该网盘根目录下没有子目录，将直接保存到根目录。\n"
            "回复 /drives 可查看默认目录。"
        )

    pid = secrets.token_urlsafe(6)
    PENDING[pid] = {
        "drive": link.drive,
        "url": link.url,
        "passcode": link.passcode,
        "dirs": dirs,
        "default_dir_id": account.default_dir_id or "0",
        "default_dir_name": account.default_dir_name or "根目录",
        "created_at": time.time(),
        "chat_id": message.chat.id,
        "tg_user_id": message.from_user.id,
        "tg_username": message.from_user.username or "",
    }
    _sweep_pending()

    drive_name = {"115": "115 网盘", "baidu": "百度网盘", "quark": "夸克网盘", "xunlei": "迅雷网盘"}.get(
        link.drive, link.drive
    )
    await message.reply(
        describe_link(link, drive_name),
        reply_markup=build_keyboard(pid, dirs, account.default_dir_name or "", link.drive),
        disable_web_page_preview=True,
    )


def _sweep_pending() -> None:
    now = time.time()
    for key in [k for k, v in PENDING.items() if now - v["created_at"] > PENDING_TTL]:
        PENDING.pop(key, None)


# ---------------- 选择目录 → 转存 ----------------
@router.callback_query(F.data.startswith("sv:"))
async def on_choose_dir(callback: CallbackQuery, bot: Bot) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("数据异常", show_alert=True)
        return
    _, pid, choice = parts
    pending = PENDING.get(pid)
    if pending is None:
        await callback.answer("这个选择已过期，请重新转发链接", show_alert=True)
        return
    if callback.from_user.id != pending["tg_user_id"]:
        await callback.answer("这不是发给你的消息", show_alert=True)
        return

    if choice == "x":
        PENDING.pop(pid, None)
        await callback.message.edit_text("已取消。", reply_markup=None)
        await callback.answer()
        return

    if choice == "d":
        dir_id, dir_name = pending["default_dir_id"], pending["default_dir_name"]
    else:
        try:
            item = pending["dirs"][int(choice)]
        except (ValueError, IndexError):
            await callback.answer("目录不存在", show_alert=True)
            return
        dir_id, dir_name = item["id"], item["name"]

    PENDING.pop(pid, None)
    await callback.answer("开始转存…")
    await callback.message.edit_text(f"⏳ 正在转存到「{dir_name}」…", reply_markup=None)

    async with SessionFactory() as session:
        log = await transfer_service.save_share(
            session,
            drive_key=pending["drive"],
            share_url=pending["url"],
            passcode=pending["passcode"],
            dir_id=dir_id,
            dir_name=dir_name,
            source="bot",
            tg_user_id=pending["tg_user_id"],
            tg_username=pending["tg_username"],
        )

    icon = "✅" if log.status == "success" else "❌"
    text = [
        f"{icon} <b>{'转存成功' if log.status == 'success' else '转存失败'}</b>",
        f"网盘：{pending['drive']}",
        f"目录：{html.escape(dir_name)}",
        f"文件数：{log.files}",
        f"耗时：{log.duration_ms} ms",
    ]
    if log.status != "success":
        text.append(f"原因：{html.escape(log.message or '未知错误')}")
    await callback.message.edit_text("\n".join(text), reply_markup=None, disable_web_page_preview=True)
