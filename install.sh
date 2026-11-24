#!/usr/bin/env bash
#
# PanSaver 一键安装脚本（Ubuntu / Debian 系）
#
#   sudo bash install.sh                          # 交互式，会询问 Bot Token
#   sudo bash install.sh --token 123456:AA...     # 非交互
#   sudo bash install.sh --port 8080 --dir /opt/pansaver
#   sudo bash install.sh --uninstall              # 卸载（保留 data 目录）
#
# 支持：Ubuntu 22.04 / 24.04、Debian 12（Python ≥ 3.10）
#      Ubuntu 20.04、Debian 11（Python 3.8/3.9）请改用 docker compose 部署
#
set -euo pipefail

APP_NAME="pansaver"
DEFAULT_DIR="/opt/pansaver"
REPO_URL="https://github.com/Hzic0322/tg-bot-pan-saver.git"

INSTALL_DIR="$DEFAULT_DIR"
PORT="8080"
TOKEN=""
RUN_USER="pansaver"
DO_START=1
DO_UNINSTALL=0
PURGE=0

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
msg()  { printf '%s[pansaver]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s[警告]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '%s[错误]%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# ---------------- 参数 ----------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="${2:?--dir 需要一个路径}"; shift 2 ;;
    --port) PORT="${2:?--port 需要一个端口}"; shift 2 ;;
    --token) TOKEN="${2:?--token 需要 Bot Token}"; shift 2 ;;
    --user) RUN_USER="${2:?--user 需要一个用户名}"; shift 2 ;;
    --no-start) DO_START=0; shift ;;
    --uninstall) DO_UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    -h|--help) usage ;;
    *) die "未知参数：$1（用 --help 查看用法）" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "请用 root 运行：sudo bash install.sh"

SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
ENV_FILE="${INSTALL_DIR}/.env"

# ---------------- 卸载 ----------------
if [[ $DO_UNINSTALL -eq 1 ]]; then
  msg "停止并移除服务"
  systemctl disable --now "${APP_NAME}" 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  systemctl daemon-reload
  if [[ $PURGE -eq 1 ]]; then
    warn "删除 ${INSTALL_DIR}（含全部日志与网盘凭证）"
    rm -rf "$INSTALL_DIR"
  else
    msg "已卸载，数据保留在 ${INSTALL_DIR}/data（彻底删除加 --purge）"
  fi
  exit 0
fi

command -v systemctl >/dev/null || die "未检测到 systemd，本脚本只适用于 systemd 系统；请改用 docker compose"

# ---------------- Python 版本 ----------------
apt_get() { DEBIAN_FRONTEND=noninteractive apt-get "$@"; }

msg "更新软件源并安装依赖（python3 / venv / pip / curl / git）"
apt_get update -qq
apt_get install -y -qq python3 python3-venv python3-pip curl ca-certificates git >/dev/null

PY_BIN="$(command -v python3)"
PY_VER="$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
msg "检测到 Python ${PY_VER}（${PY_BIN}）"

if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  die "Python ${PY_VER} 版本过低（需要 ≥ 3.10）。
   Ubuntu 20.04 / Debian 11 自带 3.8/3.9，请改用 Docker 部署：
     git clone ${REPO_URL%.git} && cd ${REPO_URL##*/} && cp .env.example .env && docker compose up -d --build"
fi

# ---------------- 用户 ----------------
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  msg "创建系统用户 ${RUN_USER}"
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$RUN_USER"
fi

mkdir -p "$INSTALL_DIR"

# ---------------- 取源码 ----------------
if [[ -f "./app/main.py" && -f "./requirements.txt" ]]; then
  msg "从当前目录复制源码到 ${INSTALL_DIR}"
  install -d "$INSTALL_DIR"
  cp -a ./app ./static ./requirements.txt ./docker-compose.yml "$INSTALL_DIR"/
  if [[ -f ./.env.example ]]; then
    cp -a ./.env.example "$INSTALL_DIR"/
  fi
elif [[ -f "${INSTALL_DIR}/app/main.py" ]]; then
  msg "检测到已有安装，跳过源码复制（如需升级请重新拉取源码后重跑本脚本）"
else
  msg "克隆仓库到 ${INSTALL_DIR}"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# ---------------- 虚拟环境 ----------------
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  msg "创建虚拟环境并安装依赖（首次约 1-2 分钟）"
  "$PY_BIN" -m venv "${INSTALL_DIR}/.venv"
fi
"${INSTALL_DIR}/.venv/bin/python" -m pip install --quiet --upgrade pip
"${INSTALL_DIR}/.venv/bin/python" -m pip install --quiet -r "${INSTALL_DIR}/requirements.txt"
msg "依赖安装完成"

# ---------------- 配置 ----------------
if [[ -z "$TOKEN" && -t 0 ]]; then
  printf 'Telegram Bot Token（直接回车跳过，可稍后再填）：'
  read -r TOKEN || true
fi

if [[ -f "$ENV_FILE" ]]; then
  msg ".env 已存在，保留原配置（如需修改：${ENV_FILE}）"
  if [[ -n "$TOKEN" ]]; then
    if grep -q '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE"; then
      sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${TOKEN}|" "$ENV_FILE"
    else
      printf '\nTELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" >> "$ENV_FILE"
    fi
    msg "已更新 .env 中的 Bot Token"
  fi
else
  if [[ -f "${INSTALL_DIR}/.env.example" ]]; then
    cp "${INSTALL_DIR}/.env.example" "$ENV_FILE"
  else
    : > "$ENV_FILE"
  fi
  sed -i "s|^PORT=.*|PORT=${PORT}|" "$ENV_FILE" 2>/dev/null || printf 'PORT=%s\n' "$PORT" >> "$ENV_FILE"
  sed -i "s|^DATA_DIR=.*|DATA_DIR=${INSTALL_DIR}/data|" "$ENV_FILE" 2>/dev/null || printf 'DATA_DIR=%s/data\n' "$INSTALL_DIR" >> "$ENV_FILE"
  if [[ -n "$TOKEN" ]]; then
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${TOKEN}|" "$ENV_FILE"
  fi
  msg "已生成配置：${ENV_FILE}"
fi

mkdir -p "${INSTALL_DIR}/data"
chown -R "${RUN_USER}:${RUN_USER}" "$INSTALL_DIR"
chmod 600 "$ENV_FILE" || true

# ---------------- systemd ----------------
msg "写入 systemd 服务：${SERVICE_FILE}"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PanSaver 网盘自动转存 Bot
Documentation=${REPO_URL%.git}#readme
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
# 必须单 worker：扫码会话保存在进程内存中
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers
Restart=always
RestartSec=5
# 基础加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${INSTALL_DIR}/data
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

if [[ $DO_START -eq 1 ]]; then
  systemctl enable --now "${APP_NAME}"
  sleep 2
  if systemctl is-active --quiet "${APP_NAME}"; then
    msg "服务已启动"
  else
    warn "服务启动失败，用下面命令看原因："
    echo "    journalctl -u ${APP_NAME} -n 50 --no-pager"
  fi
else
  msg "已跳过启动（--no-start），稍后执行：systemctl enable --now ${APP_NAME}"
fi

# ---------------- 收尾 ----------------
detect_public_ip() {
  # 依次试：公共回显服务（国内可达）→ 云厂商元数据；都失败则返回空
  local ip url
  for url in https://ifconfig.me/ip https://ipinfo.io/ip https://ip.sb https://api.ipify.org; do
    ip="$(curl -fsS -m 3 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      printf '%s' "$ip"
      return 0
    fi
  done
  ip="$(curl -fsS -m 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s' "$ip"
  fi
  return 0
}

LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -z "${LOCAL_IP:-}" ]]; then LOCAL_IP="127.0.0.1"; fi
PUBLIC_IP="$(detect_public_ip)"
if [[ -z "${PUBLIC_IP:-}" ]]; then PUBLIC_IP="$LOCAL_IP"; fi

ACCESS_LINE="  后台地址   http://${PUBLIC_IP}:${PORT}"
if [[ "$PUBLIC_IP" != "$LOCAL_IP" ]]; then
  ACCESS_LINE="${ACCESS_LINE}
  内网地址   http://${LOCAL_IP}:${PORT}"
fi

cat <<EOF

$(printf '%s' "$C_OK")安装完成$(printf '%s' "$C_OFF")

${ACCESS_LINE}
  首次进入   设置后台管理员账号密码
  端口放行   云服务器请在安全组放行 ${PORT} 端口
  配置文件   ${ENV_FILE}
  数据目录   ${INSTALL_DIR}/data   （SQLite、网盘凭证，备份只需搬这个目录）
  常用命令   systemctl status ${APP_NAME}
             journalctl -u ${APP_NAME} -f
             systemctl restart ${APP_NAME}

  下一步：
    1) 打开后台，逐个网盘「扫码登录」并设置默认保存目录
    2) Telegram 里给 Bot 发 /start，把它给你的 ID 加到「设置 → 白名单」
    3) 如果 Bot 没动静：确认 .env 里 TELEGRAM_BOT_TOKEN 已填，然后 systemctl restart ${APP_NAME}

EOF
