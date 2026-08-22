#!/usr/bin/env bash
# Install hysmi-top environment with uv (creates .venv and installs the package).
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "[-] uv not found, installing via https://astral.sh/uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "[*] uv sync (create .venv and install hysmi-top)"
uv sync

echo
echo "[+] done. usage:"
echo "      uv run hysmi-top             # interactive TUI"
echo "      uv run hysmi-top --once      # text snapshot"
echo "      uv run hysmi-top --once --json"