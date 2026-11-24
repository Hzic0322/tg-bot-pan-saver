"""Bot 启动重试（代理/隧道晚就绪时不至于一直死着）。"""

from __future__ import annotations

import pytest

from app.bot.runner import retry_async


async def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("proxy not ready")
        return "ok"

    assert await retry_async(flaky, attempts=5, delay=0) == "ok"
    assert calls["n"] == 3


async def test_retry_raises_after_exhausting_attempts():
    calls = {"n": 0}
    errors: list[tuple[str, int]] = []

    async def always_fail() -> str:
        calls["n"] += 1
        raise TimeoutError("boom")

    with pytest.raises(TimeoutError):
        await retry_async(
            always_fail, attempts=3, delay=0, on_error=lambda exc, i: errors.append((str(exc), i))
        )
    assert calls["n"] == 3
    assert errors == [("boom", 1), ("boom", 2), ("boom", 3)]


async def test_retry_passes_cancelled_through():
    async def cancelled() -> str:
        raise __import__("asyncio").CancelledError()

    with pytest.raises(__import__("asyncio").CancelledError):
        await retry_async(cancelled, attempts=3, delay=0)


async def test_start_does_not_crash_app_when_bot_init_fails(monkeypatch):
    """Bot 起不来（如缺 aiohttp-socks、token 格式错）时，网页面板必须继续服务。"""
    from app.bot import runner as runner_mod

    monkeypatch.setattr(runner_mod.settings, "telegram_bot_token", "123456:fake-token", raising=False)
    monkeypatch.setattr(runner_mod.settings, "bot_enabled", True, raising=False)

    runner = runner_mod.BotRunner()

    def boom() -> None:
        raise RuntimeError("In order to use aiohttp client for proxy requests, install aiohttp-socks")

    monkeypatch.setattr(runner, "build_bot", boom)
    await runner.start()  # 不应抛异常

    assert "aiohttp-socks" in runner.error
    assert runner.running is False
