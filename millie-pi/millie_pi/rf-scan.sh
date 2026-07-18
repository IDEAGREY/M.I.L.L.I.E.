#!/bin/bash
# Runs as root via sudoers — single entry for Pi WiFi/BLE scans.
set -uo pipefail
IFACE="${1:-wlan0}"
MODE="${2:-wifi}"

wifi_scan() {
  if command -v wpa_cli >/dev/null 2>&1; then
    wpa_cli -i "$IFACE" scan >/dev/null 2>&1 || true
    sleep 2
    echo "===WPA==="
    wpa_cli -i "$IFACE" scan_results 2>/dev/null || true
  fi
  if command -v iwlist >/dev/null 2>&1; then
    echo "===IWLIST==="
    iwlist "$IFACE" scan 2>/dev/null || true
  fi
  if command -v iw >/dev/null 2>&1; then
    iw dev "$IFACE" scan trigger >/dev/null 2>&1 || true
    sleep 2
    echo "===IW==="
    iw dev "$IFACE" scan dump -u 2>/dev/null || iw dev "$IFACE" scan -u 2>/dev/null || true
  fi
}

ble_scan() {
  echo "===BLE==="
  timeout 6 hcitool lescan --duplicates 2>/dev/null || true
  if command -v bluetoothctl >/dev/null 2>&1; then
    bluetoothctl power on >/dev/null 2>&1 || true
    timeout 5 bluetoothctl scan on >/dev/null 2>&1 || true
    bluetoothctl devices 2>/dev/null || true
  fi
}

case "$MODE" in
  wifi) wifi_scan ;;
  ble)  ble_scan ;;
  both) wifi_scan; ble_scan ;;
  *)    wifi_scan ;;
esac
