#!/usr/bin/env bash
# MILLIE Pi — install on Raspberry Pi OS / Debian
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${MILLIE_PI_HOME:-$HOME/.millie-pi-install}"
VENV="$PREFIX/venv"
CONFIG_DIR="$HOME/.millie-pi"
SERVICE_USER="${SUDO_USER:-$USER}"

echo "== MILLIE Pi install =="
echo "   source: $ROOT"
echo "   venv:   $VENV"

sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip

mkdir -p "$PREFIX" "$CONFIG_DIR"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -U pip wheel
pip install -r "$ROOT/requirements.txt"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  cp "$ROOT/config.example.yaml" "$CONFIG_DIR/config.yaml"
  echo "Wrote $CONFIG_DIR/config.yaml — edit phone.url if needed"
fi

LAUNCHER="$PREFIX/run-millie-pi.sh"
cat > "$LAUNCHER" << EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
exec python3 -m millie_pi --config "$CONFIG_DIR/config.yaml" "\$@"
EOF
chmod +x "$LAUNCHER"

# Install package in editable mode
pip install -e "$ROOT"

DISCOVER="$PREFIX/discover-phone.sh"
cat > "$DISCOVER" << EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
python3 -m millie_pi --config "$CONFIG_DIR/config.yaml" --discover
EOF
chmod +x "$DISCOVER"

if [ -d "$ROOT/systemd" ] && command -v systemctl >/dev/null; then
  sed "s|@USER@|$SERVICE_USER|g;s|@LAUNCHER@|$LAUNCHER|g" \
    "$ROOT/systemd/millie-pi.service" | sudo tee /etc/systemd/system/millie-pi.service >/dev/null
  sudo systemctl daemon-reload
  echo ""
  echo "Enable on boot:  sudo systemctl enable --now millie-pi"
fi

echo ""
echo "Done."
echo "  Run now:     $LAUNCHER"
echo "  Find phone:  $DISCOVER"
echo "  Hub UI:      http://$(hostname -I 2>/dev/null | awk '{print $1}'):8780/"
echo ""
echo "Phone setup: keep MILLIE scanning + USB CYD connected."
echo "Connect Pi to same WiFi, or USB-tether the phone to the Pi."
