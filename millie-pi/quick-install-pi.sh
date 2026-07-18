#!/usr/bin/env bash
# Paste on Pi — one shot install. Branch: main
set -e
CLONE="$HOME/MILLIE"
if [ ! -d "$CLONE/millie-pi" ]; then
  sudo apt update
  sudo apt install -y git
  git clone --depth 1 -b main https://github.com/IDEAGREY/MILLIE.git "$CLONE"
fi
cd "$CLONE/millie-pi"
bash setup.sh
echo ""
echo "Next: nano ~/.millie-pi/config.yaml"
echo "Set phone url to http://192.168.1.213:8770"
echo "Then: ~/.millie-pi-install/run-millie-pi.sh"
