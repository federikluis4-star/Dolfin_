#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
APP_PATH="${DIST_DIR}/Support Copilot.app"
SCRIPT_PATH="${ROOT_DIR}/macos_launcher.applescript"

mkdir -p "$DIST_DIR"
rm -rf "$APP_PATH"
osacompile -o "$APP_PATH" "$SCRIPT_PATH"

echo "Built app: ${APP_PATH}"
