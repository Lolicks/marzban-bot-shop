#!/usr/bin/env bash
set -euo pipefail

# Bot-only installer for Debian 12:
# - Installs dependencies for this bot
# - Prepares .env
# - Creates venv and installs requirements
# - Configures systemd service
# - Configures nginx reverse proxy
# - Issues TLS certificate for bot panel domain
#
# Marzban itself must be installed/configured separately.

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root."
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

SERVICE_NAME="marzban-bot-shop"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${PROJECT_DIR}/.env"
EXAMPLE_ENV="${PROJECT_DIR}/.env.example"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"

ask_default() {
  local prompt="$1"
  local default="$2"
  local out
  read -r -p "${prompt} [${default}]: " out
  if [[ -z "${out}" ]]; then
    printf '%s' "${default}"
  else
    printf '%s' "${out}"
  fi
}

set_env() {
  local key="$1"
  local value="${2:-}"
  local escaped
  escaped="$(printf '%s' "${value}" | sed -e 's/[\/&]/\\&/g')"
  if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
}

echo "==> [1/7] Installing OS packages..."
apt update
apt install -y ca-certificates curl git python3 python3-venv python3-pip python3-dev build-essential nginx certbot python3-certbot-nginx

echo "==> [2/7] Collecting configuration..."
BRAND_NAME="$(ask_default "BRAND_NAME (shown on site/mini app/bot)" "My VPN")"
BOT_DOMAIN="$(ask_default "Bot web panel domain/subdomain (for /admin)" "bot.example.com")"
CERTBOT_EMAIL="$(ask_default "Email for SSL notifications" "admin@bot.example.com")"
BOT_TOKEN="$(ask_default "BOT_TOKEN" "CHANGE_ME")"
SUPERADMIN_ID="$(ask_default "SUPERADMIN_TELEGRAM_ID" "0")"
MARZBAN_BASE_URL="$(ask_default "MARZBAN_BASE_URL (example: https://panel.example.com:8000)" "https://panel.example.com:8000")"
MARZBAN_USERNAME="$(ask_default "MARZBAN_USERNAME" "admin")"
read -r -s -p "MARZBAN_PASSWORD: " MARZBAN_PASSWORD
echo
MARZBAN_INSECURE_TLS="$(ask_default "MARZBAN_INSECURE_TLS (true/false)" "false")"
WEB_ADMIN_USERNAME="$(ask_default "WEB_ADMIN_USERNAME" "admin")"
read -r -s -p "WEB admin password (plain): " WEB_ADMIN_PASSWORD_PLAIN
echo
SUPPORT_USERNAME="$(ask_default "SUPPORT_USERNAME (without @)" "support_username")"
DEFAULT_TRIAL_DAYS="$(ask_default "DEFAULT_TRIAL_DAYS" "1")"
REFERRAL_BONUS_DAYS="$(ask_default "REFERRAL_BONUS_DAYS" "3")"
MINI_APP_URL_DEFAULT="https://${BOT_DOMAIN}/mini"
MINI_APP_URL="$(ask_default "MINI_APP_URL" "${MINI_APP_URL_DEFAULT}")"
if [[ ! "${MINI_APP_URL}" =~ ^https?:// ]]; then
  MINI_APP_URL="https://${MINI_APP_URL}"
fi

echo
read -r -p "Configure FreeKassa now? (y/N): " USE_FREEKASSA
USE_FREEKASSA="${USE_FREEKASSA:-N}"
FREEKASSA_SHOP_ID=""
FREEKASSA_SECRET_WORD_1=""
FREEKASSA_SECRET_WORD_2=""
FREEKASSA_CURRENCY="RUB"
if [[ "${USE_FREEKASSA}" =~ ^[Yy]$ ]]; then
  FREEKASSA_SHOP_ID="$(ask_default "FREEKASSA_SHOP_ID" "")"
  read -r -s -p "FREEKASSA_SECRET_WORD_1: " FREEKASSA_SECRET_WORD_1
  echo
  read -r -s -p "FREEKASSA_SECRET_WORD_2: " FREEKASSA_SECRET_WORD_2
  echo
  FREEKASSA_CURRENCY="$(ask_default "FREEKASSA_CURRENCY" "RUB")"
fi

echo "==> [3/7] Preparing clean .env..."
if [[ -f "${ENV_FILE}" ]]; then
  cp "${ENV_FILE}" "${ENV_FILE}.backup.$(date +%F-%H%M%S)"
fi
if [[ -f "${EXAMPLE_ENV}" ]]; then
  cp "${EXAMPLE_ENV}" "${ENV_FILE}"
else
  : > "${ENV_FILE}"
fi

echo "==> [4/7] Creating virtualenv + dependencies..."
python3 -m venv "${VENV_DIR}"
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -r requirements.txt

echo "==> [5/7] Writing .env..."
WEB_ADMIN_PASSWORD_HASH="$(
  WEB_ADMIN_PASSWORD_PLAIN="${WEB_ADMIN_PASSWORD_PLAIN}" "${PYTHON_BIN}" - <<'PY'
import os
from services.security import hash_password
print(hash_password(os.environ["WEB_ADMIN_PASSWORD_PLAIN"]))
PY
)"
WEB_SECRET_KEY="$("${PYTHON_BIN}" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"

set_env "BOT_TOKEN" "${BOT_TOKEN}"
set_env "BRAND_NAME" "${BRAND_NAME}"
set_env "SUPERADMIN_TELEGRAM_ID" "${SUPERADMIN_ID}"
set_env "DATABASE_URL" "sqlite+aiosqlite:///./data.db"
set_env "MARZBAN_BASE_URL" "${MARZBAN_BASE_URL}"
set_env "MARZBAN_USERNAME" "${MARZBAN_USERNAME}"
set_env "MARZBAN_PASSWORD" "${MARZBAN_PASSWORD}"
set_env "MARZBAN_INSECURE_TLS" "${MARZBAN_INSECURE_TLS}"
set_env "WEB_HOST" "127.0.0.1"
set_env "WEB_PORT" "8080"
set_env "WEB_SECRET_KEY" "${WEB_SECRET_KEY}"
set_env "WEB_ADMIN_USERNAME" "${WEB_ADMIN_USERNAME}"
set_env "WEB_ADMIN_PASSWORD_HASH" "${WEB_ADMIN_PASSWORD_HASH}"
set_env "PAYMENT_MODE" "manual"
set_env "FREEKASSA_SHOP_ID" "${FREEKASSA_SHOP_ID}"
set_env "FREEKASSA_SECRET_WORD_1" "${FREEKASSA_SECRET_WORD_1}"
set_env "FREEKASSA_SECRET_WORD_2" "${FREEKASSA_SECRET_WORD_2}"
set_env "FREEKASSA_CURRENCY" "${FREEKASSA_CURRENCY}"
set_env "FREEKASSA_PAYMENT_URL" "https://pay.fk.money/"
set_env "PAYMENT_ENABLE_BALANCE" "true"
set_env "PAYMENT_ENABLE_FREEKASSA" "true"
set_env "PAYMENT_ENABLE_USDT" "true"
set_env "PAYMENT_ENABLE_BTC" "true"
set_env "PAYMENT_ENABLE_ETH" "true"
set_env "SUPPORT_USERNAME" "${SUPPORT_USERNAME}"
set_env "DEFAULT_TRIAL_DAYS" "${DEFAULT_TRIAL_DAYS}"
set_env "DEFAULT_TRIAL_HOURS" "24"
set_env "REFERRAL_BONUS_DAYS" "${REFERRAL_BONUS_DAYS}"
set_env "MAX_REQUESTS_PER_MINUTE" "25"
set_env "MINI_APP_URL" "${MINI_APP_URL}"

echo "==> [6/7] Creating systemd + nginx..."
cat > "${SYSTEMD_UNIT}" <<EOF
[Unit]
Description=Marzban Bot Shop (Aiogram + FastAPI)
After=network.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${PROJECT_DIR}/main.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

cat > "${NGINX_AVAILABLE}" <<EOF
server {
    listen 80;
    server_name ${BOT_DOMAIN};
    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sfn "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "==> [7/7] Issuing TLS for bot domain..."
if certbot --nginx -d "${BOT_DOMAIN}" --non-interactive --agree-tos -m "${CERTBOT_EMAIL}" --redirect; then
  echo "Bot domain TLS is enabled."
else
  echo "WARNING: certbot failed for ${BOT_DOMAIN}. You can run it later:"
  echo "certbot --nginx -d ${BOT_DOMAIN}"
fi

MARZBAN_BASE_URL_CLEAN="${MARZBAN_BASE_URL%/}"
echo
echo "Done."
echo "Panels and URLs:"
echo "  Marzban dashboard: ${MARZBAN_BASE_URL_CLEAN}/dashboard/"
echo "  Marzban API docs: ${MARZBAN_BASE_URL_CLEAN}/docs"
echo "  Bot admin login: https://${BOT_DOMAIN}/admin/login"
echo "  Bot admin panel: https://${BOT_DOMAIN}/admin"
echo "  Telegram Mini App: ${MINI_APP_URL}"
echo "  FreeKassa notify: https://${BOT_DOMAIN}/payments/freekassa/notify"
echo
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
