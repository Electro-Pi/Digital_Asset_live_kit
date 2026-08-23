#!/usr/bin/env bash
set -euo pipefail

readonly APP_DIR="/home/Coda-digital-asset/Digital_Asset_live_kit/frontend/coda-admin"
readonly NODE_DIR="/opt/coda-node22/bin"
readonly SERVICE="coda-admin-frontend.service"
readonly ENV_FILE="/etc/coda-admin/admin.env"

if [[ ! -x "${NODE_DIR}/node" || ! -x "${NODE_DIR}/npm" ]]; then
  echo "Node 22 is missing from ${NODE_DIR}; follow ADMIN_DEPLOYMENT.md first." >&2
  exit 1
fi

# npm's launcher resolves `node` through PATH; keep the public frontend's
# system Node untouched while guaranteeing this build uses Node 22.
export PATH="${NODE_DIR}:${PATH}"

if [[ ! -r "${ENV_FILE}" ]]; then
  echo "Missing restricted environment file: ${ENV_FILE}" >&2
  exit 1
fi

cd "${APP_DIR}"
"${NODE_DIR}/npm" ci
"${NODE_DIR}/npm" audit --omit=dev --audit-level=critical
"${NODE_DIR}/npm" run typecheck
"${NODE_DIR}/npm" test
"${NODE_DIR}/npm" run build
install -d -m 0700 -o coda-admin -g coda-admin "${APP_DIR}/.next/cache"
chown -R coda-admin:coda-admin "${APP_DIR}/.next/cache"

sudo systemctl restart "${SERVICE}"
sudo systemctl --no-pager --full status "${SERVICE}"
# The app is mounted on /admin, so it answers there on the loopback port too --
# nginx forwards the prefix rather than stripping it.
curl --fail --silent --show-error http://127.0.0.1:8092/admin/api/health >/dev/null

echo "CODA admin build is healthy on 127.0.0.1:8092"
