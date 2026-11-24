# PanSaver · 网盘自动转存 Bot

> 把网盘分享链接转发给 Telegram Bot，选一个目录，自动转存 —— 后台可扫码登录、看日志、管目录。

[![CI](https://github.com/Hzic0322/tg-bot-pan-saver/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776ab.svg)](https://www.python.org/)

支持 **115 网盘 · 百度网盘 · 夸克网盘 · 迅雷网盘**，一个容器同时跑「管理后台 + Telegram Bot」。

---

## 功能

### Telegram Bot

- 转发 / 粘贴消息即可，自动识别网盘域名（`115.com`、`pan.baidu.com`、`pan.quark.cn`、`pan.xunlei.com`）
- 自动抓提取码（`?pwd=xxxx`、`提取码: xxxx`、`密码：xxxx` 等写法）
- 回复一条「选择保存目录」的按钮消息：根目录下的文件夹逐个列出，另有「⭐ 默认目录」和「取消」
- 点一下就开始转存，原消息直接改成结果回执（成功/失败、文件数、耗时）
- 多链接消息会逐条处理（默认每条消息最多 3 个链接，可配置）
- 白名单机制：不在名单里的人会被拒绝，并被告知自己的 Telegram ID

### 管理后台（免构建，浏览器直接用）

- **首次登录即初始化**：库里没有管理员时，打开页面就是「设置账号密码」；之后正常登录（密码 scrypt 哈希、会话 Cookie、登录失败限速）
- **扫码绑定**：后台点「扫码登录」→ 弹出二维码 → 手机确认 → 自动保存 Cookie/Token
- **Cookie 导入**：扫码接口不可用时的兜底，粘贴 `k=v; k2=v2` 即可，保存后立即校验
- **目录浏览**：逐级进出网盘目录，任意目录可「设为默认」
- **默认保存位置**：每个网盘独立配置，Bot 与后台手动转存都遵守
- **转存日志**：时间、网盘、状态、目录、文件数、耗时、来源（Bot/后台）、失败原因、原链接；支持按网盘/状态/关键词过滤与分页
- **统计卡片**：今日转存 / 成功 / 失败 / 成功率 / 累计
- **设置**：Bot 白名单增删启停、修改后台密码

---

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| Web / API | FastAPI + Uvicorn | 单进程同时提供 API 与静态后台 |
| Bot | aiogram 3 | 长轮询，随 FastAPI 生命周期启停 |
| 存储 | SQLite (SQLAlchemy async + aiosqlite) | WAL 模式，零外部依赖，挂一个目录就能跑 |
| 前端 | 原生 HTML/CSS/JS | 不需要 node、不需要构建产物 |
| 网盘 | 自研适配器层 | 每家一个 `BaseDrive` 子类，统一「认证 / 列目录 / 转存」 |

```
.
├── app/
│   ├── main.py              # FastAPI 入口（静态托管 + API + Bot 生命周期）
│   ├── config.py            # 环境变量配置
│   ├── db.py  models.py     # 引擎与表结构
│   ├── security.py  deps.py # 口令哈希、会话、登录态依赖
│   ├── api/                 # auth / drives / logs / settings 四组接口
│   ├── drives/              # ★ 网盘适配器：base + 115/baidu/quark/xunlei + mock
│   ├── services/            # 链接识别、转存编排、扫码会话
│   └── bot/                 # aiogram 路由与生命周期
├── static/                  # 后台前端（index.html / app.js / style.css）
├── tests/                   # 21 个用例，全部离线可跑
├── Dockerfile  docker-compose.yml
└── .env.example
```

---

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
git clone https://github.com/Hzic0322/tg-bot-pan-saver.git && cd tg-bot-pan-saver
cp .env.example .env

# 填上 Bot Token（@BotFather 创建）
vi .env        # TELEGRAM_BOT_TOKEN=123456:AA...

docker compose up -d --build
docker compose logs -f
```

打开 `http://<服务器IP>:8080`：

1. 首次进入 → 设置后台账号密码
2. 逐个网盘点「扫码登录」，手机上确认
3. 在「目录 / 默认位置」里挑一个默认保存目录
4. 去 Telegram 找你的 Bot，发 `/start`，把页面告诉你的 ID 加到「设置 → 白名单」

### 方式二：裸机

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && vi .env
uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```

> 必须 `--workers 1`：扫码会话存在进程内存里，多进程会导致轮询拿不到状态。

### 方式三：systemd 常驻（裸机不想装 Docker）

```ini
# /etc/systemd/system/pansaver.service
[Unit]
Description=PanSaver netdisk bot
After=network-online.target

[Service]
WorkingDirectory=/opt/pansaver
EnvironmentFile=/opt/pansaver/.env
ExecStart=/opt/pansaver/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
Restart=always
RestartSec=5
User=pansaver

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now pansaver
journalctl -u pansaver -f
```

### 反向代理（可选，但建议配 HTTPS）

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

用了 HTTPS 记得把 `.env` 里的 `COOKIE_SECURE=true` 打开。

---

## 使用

### Bot 侧

```
[你转发]  【合集】链接：https://pan.quark.cn/s/abc123 提取码：k9m2

[Bot]     🟣 夸克网盘 分享链接已识别
          链接：https://pan.quark.cn/s/abc123
          提取码：k9m2
          请选择保存到哪个目录 👇
          [📁 电影] [📁 剧集] [📁 音乐] [⭐ 默认目录：剧集] [❌ 取消]

[你点]    📁 剧集

[Bot]     ✅ 转存成功
          网盘：quark
          目录：剧集
          文件数：3
          耗时：1240 ms
```

命令：`/start`、`/help`、`/drives`（查看已绑定网盘与默认目录）。

### 后台侧

- 「扫码登录」：二维码 3 分钟内有效，过期点关闭重开即可
- 「Cookie 导入」：扫码拿不到二维码时用，见下表字段
- 「校验」：随时验证凭证是否还活着
- 「目录 / 默认位置」：点文件夹进入，点「设为默认」保存

---

## 网盘支持情况

| 网盘 | 扫码登录 | 需要的凭证 | 说明 |
|---|---|---|---|
| **115** | ✅ 官方二维码接口 | `UID` `CID` `SEID` `KID` | 手机确认后自动写入 Cookie，最稳 |
| **迅雷** | ✅ OAuth Device Code | `access_token` `refresh_token` | 展示授权链接的二维码，token 过期自动刷新 |
| **百度** | ⚠️ 实验性 | `BDUSS` `STOKEN` | 走 passport unicast 流程；失败请用 Cookie 导入 |
| **夸克** | ⚠️ 实验性 | `__pus` `__puus` | 官方扫码接口未公开，取不到二维码时会自动引导到 Cookie 导入 |
| Mock（调试） | ✅ | 任意 | `ENABLE_MOCK=true` 时出现，不联网，用于本地跑通全流程 |

> 这些接口都是各家 Web/App 端正在用的公开调用，**官方随时可能改版**。改版后的典型表现：扫码一直 pending、转存报「参数错误」、目录读不出来。
> 适配器集中在 `app/drives/*.py`，改一个文件即可，不需要动 Bot 和前端。

### 自己加一家网盘

```python
# app/drives/mydrive.py
from .base import BaseDrive, DirEntry, SaveResult

class MyDrive(BaseDrive):
    key = "mydrive"
    name = "我的网盘"
    domains = ("mydrive.com",)
    required_cookie = ("token",)
    supports_qr = False

    async def account_info(self): ...          # 返回 {"name": 昵称}，失败抛 DriveError
    async def list_dirs(self, parent_id="0"):  # 返回 [DirEntry(id, name)]
        ...
    async def save_share(self, share_url, passcode, dir_id):
        return SaveResult(ok=True, message="转存成功", files=1)
```

然后在 `app/drives/registry.py` 的 `DRIVE_CLASSES` 里注册，并在 `app/services/linkparse.py` 的 `DRIVE_PATTERNS` 里加链接特征。

---

## 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 空 | 留空则只跑后台，不启 Bot |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | 监听地址 |
| `DATA_DIR` | `./data` | SQLite 与运行数据目录 |
| `DATABASE_URL` | 由 `DATA_DIR` 推导 | 也可指向其它 SQLite 路径 |
| `SESSION_TTL_HOURS` | `168` | 后台登录有效期 |
| `COOKIE_SECURE` | `false` | HTTPS 部署时设 `true` |
| `ALLOW_OPEN_SETUP` | `true` | 是否允许首个访问者初始化管理员 |
| `SETUP_TOKEN` | 空 | 初始化口令（多人环境建议配） |
| `BOT_ENABLED` | `true` | 临时停 Bot 保留后台 |
| `BOT_PROXY` | 空 | Bot 出口代理，如 `http://127.0.0.1:7890` |
| `BOT_MAX_LINKS_PER_MESSAGE` | `3` | 单条消息最多处理几个链接 |
| `ENABLE_MOCK` | `false` | 开启 Mock 网盘（仅调试/测试） |

---

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/auth/status` | 是否需要初始化 / 是否已登录 |
| POST | `/api/auth/setup` | 首次创建管理员 |
| POST | `/api/auth/login` / `logout` | 登录 / 登出 |
| POST | `/api/auth/password` | 修改密码 |
| GET | `/api/drives` | 四家网盘绑定状态、默认目录 |
| POST | `/api/drives/{drive}/qr` | 开始扫码，返回二维码 |
| GET | `/api/drives/{drive}/qr/{qr_id}` | 轮询扫码状态 |
| GET | `/api/drives/{drive}/qr/{qr_id}/png` | 内容型二维码渲染成 PNG |
| POST | `/api/drives/{drive}/cookie` | 导入 Cookie 并校验 |
| GET | `/api/drives/{drive}/dirs?parent_id=0` | 列目录 |
| PUT | `/api/drives/{drive}/default-dir` | 设置默认保存目录 |
| POST | `/api/drives/{drive}/check` | 校验凭证 |
| DELETE | `/api/drives/{drive}` | 解绑 |
| POST | `/api/drives/save` | 后台手动转存（便于测链接） |
| GET | `/api/logs` / `/api/logs/stats` | 日志分页 / 统计 |
| GET/POST/PATCH/DELETE | `/api/settings/allows` | Bot 白名单管理 |

带交互式文档：`/api/docs`。

---

## 安全与数据

- 后台口令用 **scrypt(N=2^14)** 加盐哈希；会话 token 只存 sha256 摘要；登录失败同 IP 10 分钟内 10 次锁定
- **网盘 Cookie / Token 明文存在 SQLite 里**（`data/pansaver.db`）——它们等价于账号登录态。请：限制服务器访问权限、给后台配 HTTPS、不要把 `data/` 提交或分享
- 二维码图片由本机代理下载（只放行 115/百度/夸克/迅雷域名），避免前端跨域与 SSRF
- Bot 默认拒绝所有人，必须显式加白名单

---

## 开发与测试

```bash
pip install -r requirements.txt pytest pytest-asyncio
python -m pytest -q          # 21 passed，全程离线，走 Mock 网盘
```

测试覆盖：首次初始化/登录/改密/越权拦截、四家链接与提取码识别、Cookie 绑定、目录浏览、默认目录、转存成功与失败日志、统计、白名单、扫码端到端（Mock）。

`ENABLE_MOCK=true` 时后台会多出一张「Mock 网盘」卡片，可完整演示扫码 → 目录 → 转存 → 日志，不碰真实账号。

---

## 常见问题

**Q：扫码一直「等待扫码」？**
先确认手机上点的是「确认登录」而不是只扫了一下；百度/夸克若 30 秒无反应，八成是官方接口改版，改用 Cookie 导入。

**Q：Bot 不回复？**
看容器日志里有没有 `Bot 已启动：@xxx`；没有就是 Token 没配或代理不通。再看是不是没加白名单（Bot 会回复你的 ID）。

**Q：转存提示「Cookie 已失效」？**
后台点「校验」→ 若失效重新扫码。百度的 Cookie 掉得比较快，属正常。

**Q：能多用户共用吗？**
一个实例对应一套网盘账号（转存目标是这份账号的网盘）。多人共用同一个账号可以，只是共用同一个默认目录；需要多账号请起多实例。

**Q：能不能不部署后台，只用 Bot？**
可以，但首次绑定凭证需要后台（扫码/粘贴 Cookie）。也可以直接调 `/api/drives/{drive}/cookie` 接口。

---

## 路线图

- [x] 四家网盘绑定（扫码 + Cookie）、目录浏览、默认目录
- [x] Bot 链接识别、目录选择键盘、转存回执、日志
- [x] 后台登录、日志检索、统计、白名单
- [ ] 转存失败自动重试 / 定时任务（链接订阅追更）
- [ ] 多网盘账号（同一家绑定多个账号并选择目标）
- [ ] Webhook 模式（Serverless / 反代场景）
- [ ] 更多网盘：123 云盘、天翼云盘、阿里云盘

---

## 免责声明

本项目仅用于**个人备份自己有权访问的公开分享资源**，不含任何资源内容，也不提供绕过付费/会员限制的能力。
所有网盘接口均来自对应产品 Web/App 的公开调用，使用第三方客户端存在**风控、限速甚至封号**风险，请自行评估。
请勿用于商业推广（拉新返利）、批量采集或任何侵犯版权与平台条款的用途。

## License

[MIT](LICENSE)
