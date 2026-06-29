#!/usr/bin/env bash
set -euo pipefail

# Full production install for Debian 12 (no Docker).
# - Installs dependencies
# - Creates/updates .env interactively
# - Creates venv and installs requirements
# - Configures systemd service
# - Configures nginx reverse proxy
# - Issues TLS certificate via certbot

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash install_full_debian12.sh"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

SERVICE_NAME="marzban-bot-shop"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"
ENV_FILE="${PROJECT_DIR}/.env"
EXAMPLE_ENV="${PROJECT_DIR}/.env.example"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"

set_env() {
  local key="$1"
  local value="${2:-}"
  local escaped
  escaped="$(printf '%s' "$value" | sed -e 's/[\/&]/\\&/g')"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

ask_default() {
  local prompt="$1"
  local default="$2"
  local out
  read -r -p "${prompt} [${default}]: " out
  if [[ -z "${out}" ]]; then
    printf '%s' "$default"
  else
    printf '%s' "$out"
  fi
}

echo "==> [1/9] Installing OS dependencies..."
apt update
apt install -y python3 python3-venv python3-pip python3-dev build-essential git nginx certbot python3-certbot-nginx

echo "==> Preparing clean .env template..."
if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%F-%H%M%S)"
fi
if [[ -f "$EXAMPLE_ENV" ]]; then
  cp "$EXAMPLE_ENV" "$ENV_FILE"
else
  : > "$ENV_FILE"
fi

echo "==> [2/9] Collecting configuration..."
BRAND_NAME="$(ask_default "BRAND_NAME (shown on site/mini app/bot)" "My VPN")"
DOMAIN="$(ask_default "Domain for web panel (DNS must already point to this server)" "example.com")"
CERTBOT_EMAIL="$(ask_default "Email for Let's Encrypt notifications" "admin@${DOMAIN}")"
BOT_TOKEN="$(ask_default "Telegram BOT_TOKEN" "CHANGE_ME")"
SUPERADMIN_ID="$(ask_default "SUPERADMIN_TELEGRAM_ID" "0")"
MARZBAN_BASE_URL="$(ask_default "MARZBAN_BASE_URL (example: https://panel.example.com:8000)" "https://panel.example.com:8000")"
MARZBAN_USERNAME="$(ask_default "MARZBAN_USERNAME" "admin")"
read -r -s -p "MARZBAN_PASSWORD: " MARZBAN_PASSWORD
echo
MARZBAN_INSECURE_TLS="$(ask_default "MARZBAN_INSECURE_TLS (true/false)" "false")"
WEB_ADMIN_USERNAME="$(ask_default "WEB admin username" "admin")"
read -r -s -p "WEB admin password (plain, hash generated automatically): " WEB_ADMIN_PASSWORD_PLAIN
echo
SUPPORT_USERNAME="$(ask_default "SUPPORT_USERNAME (without @)" "support_username")"
DEFAULT_TRIAL_DAYS="$(ask_default "DEFAULT_TRIAL_DAYS" "1")"
REFERRAL_BONUS_DAYS="$(ask_default "REFERRAL_BONUS_DAYS" "3")"

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

echo "==> [3/9] Creating virtualenv + installing Python dependencies..."
python3 -m venv "$VENV_DIR"
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -r requirements.txt

echo "==> [4/9] Generating web admin password hash..."
WEB_ADMIN_PASSWORD_HASH="$(
  WEB_ADMIN_PASSWORD_PLAIN="$WEB_ADMIN_PASSWORD_PLAIN" "${PYTHON_BIN}" - <<'PY'
import os
from services.security import hash_password
print(hash_password(os.environ["WEB_ADMIN_PASSWORD_PLAIN"]))
PY
)"

echo "==> [5/9] Writing .env values..."
set_env "BOT_TOKEN" "$BOT_TOKEN"
set_env "BRAND_NAME" "$BRAND_NAME"
set_env "SUPERADMIN_TELEGRAM_ID" "$SUPERADMIN_ID"
set_env "DATABASE_URL" "sqlite+aiosqlite:///./data.db"
set_env "MARZBAN_BASE_URL" "$MARZBAN_BASE_URL"
set_env "MARZBAN_USERNAME" "$MARZBAN_USERNAME"
set_env "MARZBAN_PASSWORD" "$MARZBAN_PASSWORD"
set_env "MARZBAN_INSECURE_TLS" "$MARZBAN_INSECURE_TLS"
set_env "WEB_HOST" "127.0.0.1"
set_env "WEB_PORT" "8080"
set_env "WEB_SECRET_KEY" "$("${PYTHON_BIN}" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
set_env "WEB_ADMIN_USERNAME" "$WEB_ADMIN_USERNAME"
set_env "WEB_ADMIN_PASSWORD_HASH" "$WEB_ADMIN_PASSWORD_HASH"
set_env "PAYMENT_MODE" "manual"
set_env "SUPPORT_USERNAME" "$SUPPORT_USERNAME"
set_env "DEFAULT_TRIAL_DAYS" "$DEFAULT_TRIAL_DAYS"
set_env "DEFAULT_TRIAL_HOURS" "24"
set_env "REFERRAL_BONUS_DAYS" "$REFERRAL_BONUS_DAYS"
set_env "MAX_REQUESTS_PER_MINUTE" "25"
set_env "FREEKASSA_SHOP_ID" "$FREEKASSA_SHOP_ID"
set_env "FREEKASSA_SECRET_WORD_1" "$FREEKASSA_SECRET_WORD_1"
set_env "FREEKASSA_SECRET_WORD_2" "$FREEKASSA_SECRET_WORD_2"
set_env "FREEKASSA_CURRENCY" "$FREEKASSA_CURRENCY"
set_env "FREEKASSA_PAYMENT_URL" "https://pay.fk.money/"

echo "==> [6/9] Creating systemd unit..."
cat > "$SYSTEMD_UNIT" <<EOF
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
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "==> [7/9] Configuring nginx..."
cat > "$NGINX_AVAILABLE" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
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

ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl reload nginx

echo "==> [8/9] Issuing TLS certificate via certbot..."
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect; then
  echo "TLS certificate issued successfully."
else
  echo "WARNING: certbot failed. Check DNS and try manually:"
  echo "certbot --nginx -d ${DOMAIN}"
fi

echo "==> [9/9] Final checks..."
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "Installation completed."
echo "Web admin: https://${DOMAIN}/admin"
echo "FreeKassa notify URL: https://${DOMAIN}/payments/freekassa/notify"
echo "Service logs: journalctl -u ${SERVICE_NAME} -f"
