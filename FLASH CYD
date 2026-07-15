#!/usr/bin/env bash
# =====================================================================
# MILLIE — ESP32 CYD firmware flasher
# Flashes sentinel_sniffer.ino to an ESP32-2432S028R (Cheap Yellow
# Display) using arduino-cli. Run this on the computer the CYD is
# plugged into over USB (Mac/Linux), NOT on the phone.
#
# Usage:
#   bash flash_esp.sh                 # auto-detect port
#   bash flash_esp.sh /dev/cu.usbserial-1420   # specify port
# =====================================================================
set -e

SKETCH_DIR="$(cd "$(dirname "$0")" && pwd)/sentinel_sniffer"
SKETCH="$SKETCH_DIR/sentinel_sniffer.ino"
FQBN="esp32:esp32:esp32"
UPLOAD_SPEED="115200"   # 921600 (the default) fails on this board — do not raise

echo "== MILLIE ESP32 flasher =="

# ---- locate the sketch ----
if [ ! -f "$SKETCH" ]; then
  # allow running from a folder that just has the .ino next to this script
  if [ -f "$(dirname "$0")/sentinel_sniffer.ino" ]; then
    SKETCH_DIR="$(dirname "$0")"
    SKETCH="$SKETCH_DIR/sentinel_sniffer.ino"
  else
    echo "ERROR: sentinel_sniffer.ino not found."
    echo "Put this script next to the sketch, or in a folder containing"
    echo "a sentinel_sniffer/ subfolder with the .ino inside."
    exit 1
  fi
fi
echo "Sketch: $SKETCH"

# ---- check arduino-cli ----
if ! command -v arduino-cli >/dev/null 2>&1; then
  echo "ERROR: arduino-cli not installed."
  echo "Install it:  https://arduino.github.io/arduino-cli/latest/installation/"
  echo "  macOS:  brew install arduino-cli"
  exit 1
fi

# ---- one-time: ESP32 core + libraries ----
echo "-- ensuring ESP32 board support + libraries --"
if ! arduino-cli config dump 2>/dev/null | grep -q "espressif/package_esp32_index.json"; then
  arduino-cli config init 2>/dev/null || true
  arduino-cli config add board_manager.additional_urls \
    https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json 2>/dev/null || true
fi
arduino-cli core update-index
arduino-cli core install esp32:esp32 2>/dev/null || echo "(esp32 core already present)"
# Libraries the firmware needs:
arduino-cli lib install "TFT_eSPI" 2>/dev/null || echo "(TFT_eSPI already present)"
arduino-cli lib install "NimBLE-Arduino" 2>/dev/null || echo "(NimBLE-Arduino already present)"

# ---- detect the port if not supplied ----
PORT="$1"
if [ -z "$PORT" ]; then
  echo "-- auto-detecting CYD serial port --"
  PORT=$(arduino-cli board list 2>/dev/null | grep -iE "usbserial|ttyUSB|ttyACM|wchusb|SLAB" | awk '{print $1}' | head -n1)
  if [ -z "$PORT" ]; then
    echo "Could not auto-detect the port. Plug in the CYD and pass it manually:"
    echo "  bash flash_esp.sh /dev/cu.usbserial-XXXX"
    echo "List candidates with:  arduino-cli board list"
    exit 1
  fi
  echo "Detected port: $PORT"
fi

# ---- compile ----
echo "-- compiling (this can take a minute the first time) --"
arduino-cli compile --fqbn "$FQBN" "$SKETCH_DIR"

# ---- upload (MUST use 115200; higher speeds fail on this board) ----
echo "-- uploading at $UPLOAD_SPEED baud --"
arduino-cli upload -p "$PORT" --fqbn "$FQBN" \
  --upload-property upload.speed=$UPLOAD_SPEED "$SKETCH_DIR"

echo ""
echo "== Done. The CYD should now show the MILLIE radar. =="
echo "Header shows TX:<count> (climbs as it sniffs) and PHONE:-- / PHONE:LINKED."
echo "To watch its raw output:"
echo "  arduino-cli monitor -p $PORT -c baudrate=115200"
