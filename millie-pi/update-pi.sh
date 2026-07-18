#!/usr/bin/env bash
# Update millie-pi on the Pi after git pull — run ON THE PI
set -euo pipefail
CLONE="${MILLIE_HOME:-$HOME/MILLIE}"
cd "$CLONE"
git pull
cd millie-pi
bash setup.sh
echo ""
echo "Test scan (must show wifi_found > 0):"
source "${MILLIE_PI_HOME:-$HOME/.millie-pi-install}/venv/bin/activate"
python3 -m millie_pi --config "$HOME/.millie-pi/config.yaml" --test-scan || true
echo ""
echo "Restart: ~/.millie-pi-install/run-millie-pi.sh"
