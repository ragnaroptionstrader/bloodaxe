#!/usr/bin/env bash
# bloodaxe_auto_with_env.sh — cron wrapper for Bloodaxe orchestrator.
#
# Sets venv + dotenv + paths before invoking bloodaxe_auto.py.
# Mirrors vidar_auto_with_env.sh structure for cron parity.
#
# Usage:
#   bash bloodaxe_auto_with_env.sh pre_build        # 9:00 ET — writes spec
#   bash bloodaxe_auto_with_env.sh open             # 9:35 ET — places trade
#   bash bloodaxe_auto_with_env.sh open --dry-run   # VERIFY without placing
#   bash bloodaxe_auto_with_env.sh exit_review      # every 20m RTH
#
# --dry-run flag is passed through to the python orchestrator, which then
# skips the actual broker.place_combo_iron_condor() call. Use this to verify
# the open phase logic without risking a trade on the live broker.

set -euo pipefail

# Resolve repo + venv
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="/home/freya/RAGNAR/verticals-bot/.venv/bin/python"

# Load .env (operator-provided; do not commit secrets)
if [ -f "/home/freya/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "/home/freya/.env"
  set +a
fi

# Honor PIPELINE selector — only demo runs paper-trade on first deploy
PIPELINE="${PIPELINE:-demo}"
export TIGER_ACCOUNT_TYPE="paper"
export TIGER_ACCOUNT_ID="21224823943487560"
export TIGER_PRIVATE_KEY_PATH="/home/freya/RAGNAR/tiger_openapi_demo.properties"

# Cron marker (mirrors dez/VIDAR pattern)
echo "/tmp/expected_account_type = $TIGER_ACCOUNT_TYPE" > /tmp/expected_account_type_bloodaxe 2>/dev/null || true

exec "$VENV_PY" "$REPO_ROOT/ragnar_scripts/bloodaxe_auto.py" "$@"