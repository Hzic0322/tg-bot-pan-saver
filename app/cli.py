"""管理员账号维护命令行工具。

部署在服务器上、没有浏览器可用，或者忘了后台密码时用：

    python -m app.cli list-admins
    python -m app.cli create-admin --username admin
    python -m app.cli reset-password --username admin
    python -m app.cli delete-admin --username admin

Docker 部署：

    docker compose exec -it pansaver python -m app.cli reset-password --username admin
    # 非交互（CI / 脚本）：
    echo 'Your-Strong-Pass' | docker compose exec -T pansaver python -m app.cli reset-password --username admin

密码也可以直接用 --password 传（注意会留在 shell history 里）。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import delete, select

from .db import SessionFactory, dispose_db, init_db
from .models import Admin, AuthSession
from .security import hash_password, password_strength_error


class CliError(Exception):
    pass


def _warn_if_root() -> None:
    """以 root 跑会让 SQLite 文件属主变成 root，之后服务号写不进去。"""
    if os.geteuid() == 0:
        print(
            "提示：当前是 root 身份运行，data/ 下的文件属主可能被改成 root。\n"
            "      建议改用服务用户执行：su -s /bin/sh pansaver -c '...'",
            file=sys.stderr,
        )


def _read_password(explicit: str | None) -> str:
    """优先级：--password > 非交互 stdin > 交互式输入两遍。"""
    if explicit:
        password = explicit
    elif not sys.stdin.isatty():
        password = sys.stdin.readline().strip()
        if not password:
            raise CliError("没有从标准输入读到密码")
    else:
        password = getpass.getpass("新密码（至少 8 位，不能纯数字）：")
        if password != getpass.getpass("再输一次："):
            raise CliError("两次输入不一致")
    problem = password_strength_error(password)
    if problem:
        raise CliError(problem)
    return password


async def create_admin(username: str, password: str, *, reset: bool = False) -> str:
    username = username.strip()
    if len(username) < 3:
        raise CliError("用户名至少 3 个字符")
    problem = password_strength_error(password)
    if problem:
        raise CliError(problem)
    async with SessionFactory() as session:
        existing = (
            await session.execute(select(Admin).where(Admin.username == username))
        ).scalar_one_or_none()
        if existing and not reset:
            raise CliError(f"管理员 {username} 已存在；要改密码请用 reset-password")
        if existing:
            existing.password_hash = hash_password(password)
            await session.execute(delete(AuthSession).where(AuthSession.admin_id == existing.id))
            await session.commit()
            return f"已重置管理员 {username} 的密码（旧登录会话已失效）"
        admin = Admin(username=username, password_hash=hash_password(password))
        session.add(admin)
        await session.commit()
        return f"已创建管理员 {username}"


async def reset_password(username: str, password: str, *, keep_sessions: bool = False) -> str:
    problem = password_strength_error(password)
    if problem:
        raise CliError(problem)
    async with SessionFactory() as session:
        admin = (
            await session.execute(select(Admin).where(Admin.username == username.strip()))
        ).scalar_one_or_none()
        if admin is None:
            raise CliError(f"找不到管理员 {username}（可用 list-admins 查看现有账号）")
        admin.password_hash = hash_password(password)
        if not keep_sessions:
            await session.execute(delete(AuthSession).where(AuthSession.admin_id == admin.id))
        await session.commit()
        suffix = "（已有登录会话保留）" if keep_sessions else "（所有登录会话已失效，请重新登录）"
        return f"已重置 {admin.username} 的密码{suffix}"


async def delete_admin(username: str) -> str:
    async with SessionFactory() as session:
        admin = (
            await session.execute(select(Admin).where(Admin.username == username.strip()))
        ).scalar_one_or_none()
        if admin is None:
            raise CliError(f"找不到管理员 {username}")
        await session.execute(delete(AuthSession).where(AuthSession.admin_id == admin.id))
        await session.delete(admin)
        await session.commit()
        return f"已删除管理员 {username}；下次打开后台会重新进入初始化流程"


async def list_admins() -> list[tuple[str, str, str]]:
    async with SessionFactory() as session:
        rows = (await session.execute(select(Admin).order_by(Admin.id))).scalars().all()
    if not rows:
        return []
    return [
        (
            a.username,
            a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "-",
            a.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if a.last_login_at else "从未登录",
        )
        for a in rows
    ]


async def _run(args: argparse.Namespace) -> int:
    _warn_if_root()
    await init_db()
    try:
        if args.command == "list-admins":
            rows = await list_admins()
            if not rows:
                print("（还没有管理员，打开后台即可初始化）")
            for username, created, last in rows:
                print(f"{username}\t创建于 {created}\t最后登录 {last}")
        elif args.command == "create-admin":
            print(await create_admin(args.username, _read_password(args.password), reset=args.reset))
        elif args.command == "reset-password":
            print(
                await reset_password(
                    args.username, _read_password(args.password), keep_sessions=args.keep_sessions
                )
            )
        elif args.command == "delete-admin":
            print(await delete_admin(args.username))
        return 0
    finally:
        await dispose_db()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="PanSaver 管理员账号维护（忘记密码 / 无浏览器环境）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-admins", help="列出所有管理员")

    p_create = sub.add_parser("create-admin", help="创建管理员（已存在则报错）")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--password", help="不传则交互式输入 / 从 stdin 读取")
    p_create.add_argument("--reset", action="store_true", help="已存在时改为重置密码")

    p_reset = sub.add_parser("reset-password", help="重置管理员密码")
    p_reset.add_argument("--username", required=True)
    p_reset.add_argument("--password", help="不传则交互式输入 / 从 stdin 读取")
    p_reset.add_argument("--keep-sessions", action="store_true", help="保留现有登录会话")

    p_del = sub.add_parser("delete-admin", help="删除管理员（下次打开后台重新初始化）")
    p_del.add_argument("--username", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except CliError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
