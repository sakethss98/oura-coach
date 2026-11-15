#!/usr/bin/env bash
# Installs the USDA FoodData Central MCP server (FelipeAdachi/mcp-food-data-central, MIT) at a pinned commit
# into vendor/usda-mcp (gitignored), with its own virtualenv and the versions from its uv.lock.
#
# Why this one: Python (no Node needed), MIT licensed; reviewed 2026-11-15 (see docs/M4_plan.md, Part F).
# Run it over stdio only (config/mcp.yaml sets TRANSPORT=stdio; its default is HTTP on 0.0.0.0:8050).
# Its main.py calls load_dotenv(), which searches upward from its own folder: the empty .env written below stops
# that search there, so it never reads this project's .env (it gets only USDA_API_KEY, from config/mcp.yaml).
set -euo pipefail

REPO="https://github.com/FelipeAdachi/mcp-food-data-central.git"
COMMIT="47addeb644518f24c1f5f04e040ea0a49f891a6f"      # 2025-06-18, "update transport to use streamable-http"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/vendor/usda-mcp"
PYTHON="${PYTHON:-python3.13}"

if [ ! -d "$DEST/.git" ]; then
  git clone --quiet "$REPO" "$DEST"
fi
git -C "$DEST" fetch --quiet origin
git -C "$DEST" checkout --quiet "$COMMIT"
test "$(git -C "$DEST" rev-parse HEAD)" = "$COMMIT"

: > "$DEST/.env"                                   # stops load_dotenv() from finding this project's .env

"$PYTHON" -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet "fastmcp==2.8.1" "mcp==1.9.4" "httpx==0.28.1" "python-dotenv==1.1.0" \
  "pydantic==2.11.7"
echo "USDA MCP server installed at $DEST (commit ${COMMIT:0:7})"
