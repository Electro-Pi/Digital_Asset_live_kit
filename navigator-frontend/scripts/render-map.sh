#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PDF_PATH="${APP_DIR}/../../leap plan.pdf"

mkdir -p "${APP_DIR}/public"
pdftocairo -png -singlefile -r 180 "${PDF_PATH}" "${APP_DIR}/public/leap-plan"
echo "Rendered ${APP_DIR}/public/leap-plan.png"
