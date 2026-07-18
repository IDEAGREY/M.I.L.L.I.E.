# MILLIE Pi

**CIVOPS RF hub for Raspberry Pi** — a separate project that syncs with your Android MILLIE field unit over the local network.

Your phone stays the mobile probe (GPS, radar, USB CYD). The Pi is the always-on archive: long Wall Sense history, alert log, and a dashboard you can open on any screen on your LAN.

```
┌─────────────────┐     WiFi / USB tether      ┌──────────────────┐
│  Android MILLIE │ ◄──── HTTP :8770 ────────► │  Raspberry Pi    │
│  + ESP32 CYD    │      /api/state            │  millie-pi :8780 │
│  (field unit)   │      /api/civops           │  SQLite archive  │
└─────────────────┘      /api/civops/wallsense └──────────────────┘
```

## What it unlocks

| Feature | Phone alone | Phone + Pi |
|---------|-------------|------------|
| Wall Sense live graphs | ~180 samples | Hours/days in SQLite |
| Alert history | Notifications only | Searchable log + CSV export |
| Dashboard | On phone | Pi monitor at `:8780` |
| Always-on when phone away | Stops | Pi keeps last sync + can run direct ESP |
| Command proxy | — | Start/stop/calibrate Wall Sense on phone from Pi UI |

## Quick install (on the Pi)

```bash
git clone https://github.com/IDEAGREY/MILLIE.git
cd MILLIE/millie-pi
bash setup.sh
~/.millie-pi-install/run-millie-pi.sh
```

Open **http://\<pi-ip\>:8780/** in a browser.

## Connect to your phone

1. On the phone: open MILLIE, start scanning, connect USB CYD as usual.
2. Put the phone and Pi on the **same WiFi**, or **USB-tether** the phone to the Pi.
3. Either let Pi auto-discover, or set the phone URL in `~/.millie-pi/config.yaml`:

```yaml
phone:
  url: "http://192.168.1.42:8770"   # phone IP on your LAN
  poll_seconds: 2
```

Find the phone automatically:

```bash
~/.millie-pi-install/discover-phone.sh
# prints e.g. http://192.168.42.129:8770
```

### USB tether note

When the phone shares internet to the Pi via USB, the phone is often `192.168.42.129`. Pi scans that subnet by default.

## Optional: CYD on the Pi

If the ESP32 is plugged into the **Pi** instead of the phone (fixed sensor post):

```yaml
esp:
  enabled: true
  port: ""        # auto-detect CP2102
  baud: 115200
```

Pi ingests `wallsense` JSON directly and archives it. Commands go to `/api/hub/esp/command`.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/hub/status` | Link state, last sync, live Wall Sense snapshot |
| `GET /api/hub/history/wallsense` | Archived motion/variance/presence |
| `GET /api/hub/alerts` | Pi alert log (presence, watchlist, ESP hits) |
| `GET /api/hub/snapshots/{state,civops,devices,radar,wallsense}` | Latest phone snapshot |
| `POST /api/hub/phone/wallsense` | Proxy Wall Sense cmd to phone |
| `POST /api/hub/phone/command` | Proxy CIVOPS ESP cmd to phone |
| `GET /api/hub/export/alerts.csv` | Export alert log |

Phone side (existing MILLIE API — unchanged except handshake):

| Endpoint | Description |
|----------|-------------|
| `GET /api/hub/handshake` | Pi discovery — `{ "millie": true, ... }` |

## Run as a service

```bash
sudo systemctl enable --now millie-pi
journalctl -u millie-pi -f
```

## Privacy

All local. No cloud. Data lives in `~/.millie-pi/millie_pi.db`.

## Requirements

- Raspberry Pi 3/4/5 (or any Linux box)
- Raspberry Pi OS / Debian with Python 3.9+
- MILLIE CIVOPS APK on Android (same WiFi or USB tether)
