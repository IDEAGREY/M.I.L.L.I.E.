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

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq || true
sudo apt-get install -y python3 python3-venv python3-pip python3-full python3-dev git rsync \
  wireless-tools iw bluez bluez-tools
# Optional promiscuous monitor mode (USB Alfa dongle):
# sudo apt-get install -y aircrack-ng

mkdir -p "$PREFIX" "$CONFIG_DIR"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -U pip wheel setuptools
pip install -r "$ROOT/requirements.txt"
pip install -e "$ROOT"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  cp "$ROOT/config.example.yaml" "$CONFIG_DIR/config.yaml"
  echo "Wrote $CONFIG_DIR/config.yaml"
fi

LAUNCHER="$PREFIX/run-millie-pi.sh"
cat > "$LAUNCHER" << EOF
#!/usr/bin/env bash
set -e
source "$VENV/bin/activate"
cd "$ROOT"
exec python3 -m millie_pi --config "$CONFIG_DIR/config.yaml" "\$@"
EOF
chmod +x "$LAUNCHER"

DISCOVER="$PREFIX/discover-phone.sh"
cat > "$DISCOVER" << EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
cd "$ROOT"
python3 -m millie_pi --config "$CONFIG_DIR/config.yaml" --discover
EOF
chmod +x "$DISCOVER"

echo "Verifying install..."
source "$VENV/bin/activate"
python3 -m millie_pi --version

PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "Done."
echo "  Run now:     $LAUNCHER"
echo "  Find phone:  $DISCOVER"
echo "  Hub UI:      http://${PI_IP:-192.168.1.222}:8780/"
echo ""
echo "Edit phone IP if needed:"
echo "  nano $CONFIG_DIR/config.yaml"
echo ""
echo "Test phone from Pi:"
echo "  curl -s http://192.168.1.213:8770/api/state | head -c 120"

if [ -d "$ROOT/systemd" ] && command -v systemctl >/dev/null; then
  sed "s|@USER@|$SERVICE_USER|g;s|@LAUNCHER@|$LAUNCHER|g" \
    "$ROOT/systemd/millie-pi.service" | sudo tee /etc/systemd/system/millie-pi.service >/dev/null
  sudo systemctl daemon-reload
  echo "  Boot start:  sudo systemctl enable --now millie-pi"
fi
