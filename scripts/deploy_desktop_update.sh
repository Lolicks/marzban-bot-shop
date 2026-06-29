#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/deploy_desktop_update.sh <user@host> <zip_path> <version>
#
# Example:
#   ./scripts/deploy_desktop_update.sh root@194.104.9.207 \
#     desktop/pepevpn-desktop/dist2/pepevpn-desktop-server.zip 0.1.0

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <user@host> <zip_path> <version>"
  exit 1
fi

HOST="$1"
ZIP_PATH="$2"
VERSION="$3"

REMOTE_DIR="/root/marzban-bot-shop/web_panel/static/downloads"
REMOTE_ZIP="/root/pepevpn-desktop-server.zip"

echo "==> Uploading archive to ${HOST}:${REMOTE_ZIP}"
scp -q "$ZIP_PATH" "${HOST}:${REMOTE_ZIP}"

echo "==> Deploying on server..."
ssh -q "$HOST" bash -s <<EOF
set -euo pipefail
mkdir -p "$REMOTE_DIR"
unzip -o -q "$REMOTE_ZIP" -d /tmp/pepevpn-desktop
cp -f /tmp/pepevpn-desktop/desktop/pepevpn-desktop/dist2/PEPE-VPN-Setup-0.1.0.exe "$REMOTE_DIR/PEPE-VPN-Setup.exe"
cp -f /tmp/pepevpn-desktop/desktop/pepevpn-desktop/dist2/PEPE-VPN-Portable-0.1.0.exe "$REMOTE_DIR/PEPE-VPN-Portable.exe"
rm -rf /tmp/pepevpn-desktop

if [ -f /root/marzban-bot-shop/.env ]; then
  sed -i "s/^DESKTOP_LATEST_VERSION=.*/DESKTOP_LATEST_VERSION=${VERSION}/" /root/marzban-bot-shop/.env || true
  sed -i "s/^DESKTOP_MIN_VERSION=.*/DESKTOP_MIN_VERSION=${VERSION}/" /root/marzban-bot-shop/.env || true
fi

systemctl restart marzban-bot-shop
EOF

echo "==> Done."
