#!/bin/bash
set -euo pipefail

# Pull, reinstall, rebuild, restart. Run as the account that owns the checkout
# (bassem) — the units run as that account too, so sudo here would leave
# root-owned files in .venv/, node_modules/ and .next/ that the services
# then cannot rewrite on the next deploy.

APP_DIR="/home/bassem/Coda/Digital_Asset_live_kit"
NEXT_DIR="${APP_DIR}/frontend/coda-frontend/coda-next"
NODE_BIN="/opt/coda-node22/bin"

cd "${APP_DIR}"

echo "⏳ Pulling latest code..."
git restore .
git pull origin "$(git rev-parse --abbrev-ref HEAD)"
git submodule update --init --recursive

echo "⏳ Syncing agent dependencies..."
/usr/local/bin/uv sync

echo "⏳ Building the frontend..."
cd "${NEXT_DIR}"
PATH="${NODE_BIN}:${PATH}" "${NODE_BIN}/npm" ci
PATH="${NODE_BIN}:${PATH}" "${NODE_BIN}/npm" run build

echo "⏳ Restarting services..."
sudo systemctl restart coda-agent.service coda-next-frontend.service

sudo systemctl --no-pager --full status coda-agent.service coda-next-frontend.service
echo "✅ Deployment complete"
