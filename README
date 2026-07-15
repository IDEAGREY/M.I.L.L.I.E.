# MILLIE — Mobile Intelligence Live Location Intel Engine

**Passive surveillance detection for Termux + ESP32 CYD**

MILLIE watches WiFi and BLE signals around you, classifies suspicious devices (hidden networks, cameras, police equipment), and displays a live radar dashboard.

![MILLIE dashboard](screenshot.png) <!-- add your own screenshot -->

---

## ✨ Features

- **Passive detection** – no active probing, just listening.
- **Radar view** – colour‑coded blips for Flock, consumer cameras, WiFi, BLE.
- **Sus tab** – devices with ≥2 suspicious indicators (hidden, camera, police, moving, anomaly).
- **Clickable details** – RSSI timeline, movement, anomaly flags.
- **Auto‑updating OUIs** – daily fetch from community sources.
- **Router SSID filtering** – reduces false positives (T‑Mobile, Xfinity, etc.).
- **CSV export** – one‑click download of device data.
- **Duress PIN** – wipe all data instantly (default: `1234`).
- **SQLCipher** – optional encrypted database.
- **ESP32 CYD sniffer** – captures hidden‑SSID probe requests that the phone cannot see.

---

## 📱 Requirements

### Termux side (on your Android phone)
- Android phone with Termux and Termux:API (from F‑Droid).
- Location permission granted to Termux.

### ESP32 side (optional but recommended)
- ESP32-2432S028R (Cheap Yellow Display) board.
- USB‑OTG cable to connect the CYD to the phone.
- A computer (Mac/Linux) with `arduino-cli` to flash the firmware.

---

## 🚀 Installation

### On Termux (phone)

Open Termux and run:

```bash
curl -sSL https://raw.githubusercontent.com/IDEAGREY/M.I.L.L.I.E./main/setup_millie.sh | bash
