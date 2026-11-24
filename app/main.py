"""FastAPI 入口：托管管理后台静态页 + API + 启动 Telegram Bot。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, drives, logs
from .api import settings as settings_api
from .bot.runner import bot_runner
from .config import settings
from .db import dispose_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pansaver")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("数据库就绪：%s", settings.db_url)
    await bot_runner.start()
    try:
        yield
    finally:
        await bot_runner.stop()
        await dispose_db()


app = FastAPI(
    title="PanSaver · 网盘自动转存 Bot",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.include_router(auth.router)
app.include_router(drives.router)
app.include_router(logs.router)
app.include_router(settings_api.router)

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "version": VERSION,
        "bot_running": bot_runner.running,
        "bot_error": bot_runner.error,
        "bot_ready": settings.bot_ready,
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> JSONResponse:
    return JSONResponse(status_code=204, content=None)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "接口不存在"})
    target = STATIC_DIR / full_path
    if target.is_file():
        return FileResponse(target)
    return FileResponse(STATIC_DIR / "index.html")
