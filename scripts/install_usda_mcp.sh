#!/usr/bin/env bash
# Installs the USDA FoodData Central MCP server (FelipeAdachi/mcp-food-data-central, MIT) at a pinned commit
# into vendor/usda-mcp (gitignored), with its own virtualenv holding exactly the versions in that commit's uv.lock.
#
# Why this one: Python (no Node needed), MIT licensed; reviewed 2026-11-15 (see docs/M4_plan.md, Part F).
# Run it over stdio only (config/mcp.yaml sets TRANSPORT=stdio; its default is HTTP on 0.0.0.0:8050).
# Its main.py calls load_dotenv(), which searches upward from its own folder: the empty .env written below stops
# that search there, so it never reads this project's .env (it gets only USDA_API_KEY, from config/mcp.yaml).
#
# Dependencies come from its uv.lock, never from pyproject.toml's minimum versions: `uv sync --locked` when uv is
# installed, else pip with a requirements file generated from uv.lock (scripts/uv_lock_requirements.py: every
# locked package at its version, with the lock's sha256 hashes, installed with --require-hashes --no-deps).
set -euo pipefail

REPO="https://github.com/FelipeAdachi/mcp-food-data-central.git"
COMMIT="47addeb644518f24c1f5f04e040ea0a49f891a6f"      # 2025-06-18, "update transport to use streamable-http"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/vendor/usda-mcp"
PYTHON="${PYTHON:-python3.13}"

fail() { echo "install_usda_mcp: $*" >&2; exit 1; }

if [ ! -d "$DEST/.git" ]; then
  git clone --quiet "$REPO" "$DEST"
fi
if ! git -C "$DEST" cat-file -e "$COMMIT^{commit}" 2> /dev/null; then
  git -C "$DEST" fetch --quiet origin || fail "could not fetch $REPO"
fi
git -C "$DEST" checkout --quiet --detach "$COMMIT" || fail "could not check out $COMMIT"
HEAD="$(git -C "$DEST" rev-parse HEAD)"
[ "$HEAD" = "$COMMIT" ] || fail "checked out $HEAD, expected $COMMIT"
# Edited tracked files would survive a checkout, so HEAD alone doesn't prove the code is the pinned code.
[ -z "$(git -C "$DEST" status --porcelain --untracked-files=no)" ] || fail "tracked files in $DEST differ from $COMMIT"

: > "$DEST/.env"                                   # stops load_dotenv() from finding this project's .env

rm -rf "$DEST/.venv"                               # a fresh env: nothing left from an earlier install
if command -v uv > /dev/null; then
  (cd "$DEST" && uv sync --locked --no-dev --python "$PYTHON" --quiet)
  HOW="uv sync --locked"
else
  "$PYTHON" -I "$ROOT/scripts/uv_lock_requirements.py" "$DEST/uv.lock" > "$DEST/requirements.lock.txt"
  "$PYTHON" -m venv "$DEST/.venv"
  "$DEST/.venv/bin/python" -m pip install --quiet --upgrade pip
  "$DEST/.venv/bin/python" -m pip install --quiet --require-hashes --no-deps -r "$DEST/requirements.lock.txt"
  "$DEST/.venv/bin/python" -m pip check > /dev/null || fail "installed packages don't satisfy each other"
  HOW="pip, from uv.lock (requirements.lock.txt)"
fi
echo "USDA MCP server installed at $DEST (commit ${COMMIT:0:7}, $HOW)"
