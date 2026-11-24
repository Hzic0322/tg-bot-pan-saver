"""全局配置：全部通过环境变量 / .env 注入。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 基础 ----
    app_name: str = "PanSaver"
    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: Path = Path("data")
    database_url: str = ""

    # ---- 管理后台安全 ----
    session_ttl_hours: int = 24 * 7
    cookie_secure: bool = False
    cookie_name: str = "pansave_session"
    # 未配置时，首个访问 /api/auth/setup 的人即可创建管理员（仅当库中没有管理员）
    allow_open_setup: bool = True
    setup_token: str = ""  # 非空时，首次初始化必须带上该口令

    # ---- Telegram Bot ----
    telegram_bot_token: str = ""
    bot_enabled: bool = True
    bot_proxy: str = ""  # 形如 http://127.0.0.1:7890
    bot_max_links_per_message: int = 3
    bot_default_allow_all: bool = False  # 预留：默认允许所有人为 false
    enable_mock: bool = False  # 开启 Mock 网盘（仅调试/测试）

    # ---- HTTP ----
    http_timeout: float = 20.0
    http_retries: int = 2
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.data_dir / 'pansaver.db'}"

    @property
    def bot_ready(self) -> bool:
        return bool(self.telegram_bot_token) and self.bot_enabled


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
