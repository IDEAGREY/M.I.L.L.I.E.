#!/data/data/com.termux/files/usr/bin/bash
# MILLIE v3 — full fresh install with Axon OUIs + police flag
set -e
ROOT="$HOME/millie"
echo "== Installing MILLIE v3 into $ROOT =="
rm -rf "$ROOT"
mkdir -p "$ROOT/millie/config" "$ROOT/millie/scanners" "$ROOT/millie/web/templates" "$ROOT/esp32/sniffer"

# ---- README ----
cat > "$ROOT/README.md" << 'PYEOF'
# MILLIE v3 — Mobile Intelligence Live Location Intel Engine
Passive surveillance detection (WiFi, BLE) with a web dashboard.
Features:
- Auto‑updating OUI list (daily cache)
- Router SSID filtering (T‑Mobile, etc.)
- RSSI anomaly detection (sudden drops)
- RSSI timeline in device cards
- CSV export
- Duress PIN (wipe data)
- SQLCipher support (optional)
- Sus tab: devices with ≥2 suspicious indicators
- Clickable details
- Radar with colour‑coded blips
- Axon Body cameras flagged as police
## Install
    bash setup_millie.sh
## Run
    bash ~/millie/run_millie.sh
PYEOF

# ---- run_millie.sh ----
cat > "$ROOT/run_millie.sh" << 'RUNEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
MODE="${1:-serve}"
DEV=$(termux-usb -l 2>/dev/null | python3 -c "import sys,json; l=json.load(sys.stdin); print(l[0] if l else '')")
if [ -z "$DEV" ]; then
  echo "No USB device found. Is the CYD plugged in via OTG?"
  echo "Try:  termux-usb -l"
  exit 1
fi
echo "Found USB device: $DEV"
LAUNCHER="$PREFIX/tmp/millie_usb_launch.sh"
mkdir -p "$PREFIX/tmp"
if [ "$MODE" = "test" ]; then
  cat > "$LAUNCHER" << INNER
#!/data/data/com.termux/files/usr/bin/bash
export TERMUX_USB_FD="\$1"
cd "$HOME/millie"
exec python3 -u -m millie usbtest
INNER
  echo "Running STANDALONE USB DIAGNOSTIC..."
else
  cat > "$LAUNCHER" << INNER
#!/data/data/com.termux/files/usr/bin/bash
export TERMUX_USB_FD="\$1"
cd "$HOME/millie"
exec python3 -u -m millie serve --usb
INNER
  echo "Requesting permission + launching MILLIE..."
fi
chmod +x "$LAUNCHER"
termux-usb -r -e "$LAUNCHER" "$DEV"
RUNEOF
chmod +x "$ROOT/run_millie.sh"

# ---- millie/__init__.py ----
cat > "$ROOT/millie/__init__.py" << 'PYEOF'
__version__ = "3.0.0"
PYEOF

# ---- millie/__main__.py ----
cat > "$ROOT/millie/__main__.py" << 'PYEOF'
from .cli import main
if __name__ == "__main__":
    main()
PYEOF

# ---- millie/cli.py ----
cat > "$ROOT/millie/cli.py" << 'CLIEOF'
import argparse, shutil, time
from . import storage
from .scanners import wifi_scanner
COLOR = {"HIGH": "\033[1;31m", "MEDIUM": "\033[0;33m", "LOW": "\033[0;90m"}
RESET = "\033[0m"
def do_scan(conn, quiet=False):
    hits = wifi_scanner.scan(force=True)
    flock_count = 0
    for label, confidence, bssid, ssid, rssi, is_flock in hits:
        if is_flock:
            flock_count += 1
            storage.record_detection(conn, "WIFI", label, "flock", bssid, rssi, None, confidence, None)
        if not quiet:
            c = COLOR.get(confidence, "")
            tag = "  ⚠ FLOCK" if is_flock else ""
            print(f"{c}[{confidence:6}]{tag} {label}{RESET}")
            print(f"         bssid={bssid} ssid={ssid or '<hidden>'} rssi={rssi}")
    if not quiet:
        if flock_count:
            print(f"\n{COLOR['HIGH']}== {flock_count} likely Flock camera(s) detected =={RESET}")
        else:
            print("No Flock cameras detected this pass.")
    return flock_count
def do_watch(conn, interval):
    print(f"[millie] Watching. Poll {interval}s. Ctrl+C to stop.")
    try:
        while True:
            print(f"\n--- scan @ {time.strftime('%H:%M:%S')} ---")
            do_scan(conn)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[millie] Stopped.")
def do_history(conn, limit=25):
    cur = conn.execute("SELECT ts, label, severity, identifier, rssi FROM detections WHERE category='flock' ORDER BY ts DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    if not rows:
        print("No Flock detections recorded yet.")
        return
    for ts, label, conf, bssid, rssi in rows:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        c = COLOR.get(conf, "")
        print(f"{t}  {c}[{conf}]{RESET} {label}  bssid={bssid} rssi={rssi}")
def do_doctor():
    print("MILLIE environment check")
    print("-" * 40)
    ok = shutil.which("termux-wifi-scaninfo")
    print("WiFi backend (termux-wifi-scaninfo):", "OK" if ok else "MISSING (pkg install termux-api + Termux:API app)")
    print("Note: Android requires Termux to have LOCATION permission to read WiFi scan results.")
def main():
    parser = argparse.ArgumentParser(prog="millie")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("scan", help="One-shot scan")
    p_watch = sub.add_parser("watch", help="Continuous scanning")
    p_watch.add_argument("--interval", type=int, default=45)
    p_hist = sub.add_parser("history", help="Recent Flock detections")
    p_hist.add_argument("--limit", type=int, default=25)
    sub.add_parser("doctor", help="Check scan backend")
    sub.add_parser("usbtest", help="Standalone USB link diagnostic")
    p_serve = sub.add_parser("serve", help="Launch web dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8770)
    p_serve.add_argument("--usb", action="store_true")
    p_serve.add_argument("--ap", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        do_doctor(); return
    if args.command == "usbtest":
        from . import usb_reader; usb_reader._standalone_test(); return
    if args.command == "serve":
        from .web.server import serve; serve(host=args.host, port=args.port, usb=args.usb, ap=args.ap); return
    conn, encrypted = storage.get_connection()
    if not encrypted:
        print("[millie] Storage: plain sqlite3 (set MILLIE_DB_KEY + sqlcipher3 to encrypt).")
    if args.command == "watch":
        do_watch(conn, args.interval)
    elif args.command == "history":
        do_history(conn, args.limit)
    else:
        do_scan(conn)
if __name__ == "__main__":
    main()
CLIEOF

# ---- millie/storage.py ----
cat > "$ROOT/millie/storage.py" << 'STOREOF'
import os, sqlite3, time
from pathlib import Path
DB_DIR = Path(os.environ.get("MILLIE_HOME", str(Path.home() / ".millie")))
DB_PATH = DB_DIR / "millie.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    protocol TEXT NOT NULL,
    label TEXT NOT NULL,
    category TEXT NOT NULL,
    identifier TEXT,
    rssi REAL,
    threat_score REAL,
    severity TEXT,
    reasons TEXT,
    reviewed INTEGER DEFAULT 0,
    false_positive INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_detections_ts ON detections(ts);
CREATE INDEX IF NOT EXISTS idx_detections_identifier ON detections(identifier);
"""
def get_connection():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("MILLIE_DB_KEY")
    if key:
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
            conn = sqlcipher.connect(str(DB_PATH))
            conn.execute(f"PRAGMA key = '{key}'")
            conn.executescript(SCHEMA)
            return conn, True
        except ImportError:
            print("[millie] SQLCipher not installed – using plain sqlite3")
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA)
    return conn, False
def record_detection(conn, protocol, label, category, identifier=None, rssi=None, threat_score=None, severity=None, reasons=None):
    conn.execute("INSERT INTO detections (ts, protocol, label, category, identifier, rssi, threat_score, severity, reasons) VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), protocol, label, category, identifier, rssi, threat_score, severity, ",".join(reasons) if reasons else None))
    conn.commit()
STOREOF

# ---- millie/location.py ----
cat > "$ROOT/millie/location.py" << 'LOCEOF'
import json, subprocess
def get_fix(provider="gps", timeout=25):
    try:
        out = subprocess.run(["termux-location", "-p", provider], capture_output=True, text=True, timeout=timeout)
        data = json.loads(out.stdout or "{}")
    except Exception:
        return None
    if "latitude" not in data:
        return None
    return {"lat": data.get("latitude"), "lon": data.get("longitude"), "accuracy": data.get("accuracy"), "provider": provider}
def get_fix_any(timeout=25):
    fix = get_fix("gps", timeout=timeout)
    if fix:
        return fix
    return get_fix("network", timeout=timeout)
LOCEOF

# ---- millie/estimator.py ----
cat > "$ROOT/millie/estimator.py" << 'ESTEOF'
import math
TX_POWER_1M = -45.0
PATH_LOSS_N = 3.0
EARTH_R = 6371000.0
def rssi_to_distance(rssi):
    if rssi is None:
        return None
    try:
        d = 10 ** ((TX_POWER_1M - float(rssi)) / (10 * PATH_LOSS_N))
    except:
        return None
    return round(max(0.5, min(d, 2000)), 1)
def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))
def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360
def estimate_position(samples):
    gps_samples = [s for s in samples if s.get("lat") is not None and s.get("rssi") is not None]
    if not gps_samples:
        return {"method": "none", "confidence": "unknown"}
    if len(gps_samples) == 1:
        s = gps_samples[0]
        return {"method": "distance_ring", "confidence": "low", "ring_center": {"lat": s["lat"], "lon": s["lon"]}, "distance_m": rssi_to_distance(s["rssi"])}
    total_w = 0.0
    wlat = wlon = 0.0
    for s in gps_samples:
        w = max(0.01, (float(s["rssi"]) + 100.0)) ** 2
        wlat += s["lat"] * w
        wlon += s["lon"] * w
        total_w += w
    est_lat = wlat / total_w
    est_lon = wlon / total_w
    lats = [s["lat"] for s in gps_samples]
    lons = [s["lon"] for s in gps_samples]
    spread_m = haversine(min(lats), min(lons), max(lats), max(lons))
    n = len(gps_samples)
    if n >= 5 and spread_m > 30:
        conf = "good"
    elif n >= 3:
        conf = "medium"
    else:
        conf = "low"
    return {"method": "weighted_centroid", "confidence": conf, "estimate": {"lat": round(est_lat, 6), "lon": round(est_lon, 6)}, "samples": n, "spread_m": round(spread_m, 1)}
ESTEOF

# ---- millie/usb_reader.py ----
cat > "$ROOT/millie/usb_reader.py" << 'USBEOF'
import ctypes, ctypes.util, json, os, queue, sys, time
_outgoing = queue.Queue()
def send_det(mac, conf, bearing=None, dist=None, rssi=None):
    b = "" if bearing is None else str(int(bearing))
    d = "" if dist is None else f"{float(dist):.1f}"
    r = "" if rssi is None else str(int(rssi))
    _outgoing.put(f"DET|{mac}|{conf}|{b}|{d}|{r}\n".encode("ascii", "ignore"))
LIBUSB_OPTION_NO_DEVICE_DISCOVERY = 2
CP2102_EP_IN = 0x82
CP2102_EP_OUT = 0x01
CP210X_IFC_ENABLE = 0x00
CP210X_SET_BAUDRATE = 0x1E
CP210X_SET_LINE_CTL = 0x03
UART_ENABLE = 0x0001
BAUD = 115200
LINE_CTL_8N1 = 0x0800
REQTYPE_HOST_TO_DEVICE_VENDOR = 0x41
class LibUSBError(Exception): pass
def _load_libusb():
    name = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so"
    try:
        lib = ctypes.CDLL(name)
    except OSError as e:
        raise LibUSBError(f"could not load libusb-1.0 ({e}). Run: pkg install libusb")
    lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.libusb_init.restype = ctypes.c_int
    lib.libusb_wrap_sys_device.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    lib.libusb_wrap_sys_device.restype = ctypes.c_int
    lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.libusb_claim_interface.restype = ctypes.c_int
    lib.libusb_control_transfer.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint]
    lib.libusb_control_transfer.restype = ctypes.c_int
    lib.libusb_bulk_transfer.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_uint]
    lib.libusb_bulk_transfer.restype = ctypes.c_int
    lib.libusb_error_name.argtypes = [ctypes.c_int]
    lib.libusb_error_name.restype = ctypes.c_char_p
    lib.libusb_close.argtypes = [ctypes.c_void_p]
    lib.libusb_exit.argtypes = [ctypes.c_void_p]
    return lib
def _errname(lib, code):
    try:
        return lib.libusb_error_name(code).decode()
    except:
        return str(code)
def _get_fd():
    for cand in sys.argv[1:]:
        if cand.isdigit():
            return int(cand)
    if os.environ.get("TERMUX_USB_FD"):
        return int(os.environ["TERMUX_USB_FD"])
    return None
class UsbLink:
    def __init__(self):
        self.lib = _load_libusb()
        self.ctx = ctypes.c_void_p()
        self.handle = None
    def open(self):
        lib = self.lib
        fd = _get_fd()
        if fd is None:
            raise LibUSBError("No USB fd. Launch via run_millie.sh.")
        rc = lib.libusb_init(ctypes.byref(self.ctx))
        if rc != 0:
            raise LibUSBError(f"libusb_init failed: {_errname(lib, rc)}")
        try:
            lib.libusb_set_option(self.ctx, LIBUSB_OPTION_NO_DEVICE_DISCOVERY)
        except:
            pass
        handle = ctypes.c_void_p()
        rc = lib.libusb_wrap_sys_device(self.ctx, fd, ctypes.byref(handle))
        if rc != 0:
            raise LibUSBError(f"libusb_wrap_sys_device failed: {_errname(lib, rc)}")
        self.handle = handle
        rc = lib.libusb_claim_interface(self.handle, 0)
        if rc != 0:
            print(f"[usb] claim_interface(0) warning: {_errname(lib, rc)}")
        self._cp210x_setup()
    def _ctrl_out(self, request, value, data=b""):
        rc = self.lib.libusb_control_transfer(self.handle, REQTYPE_HOST_TO_DEVICE_VENDOR, request, value, 0, data, len(data), 1000)
        if rc < 0:
            print(f"[usb] control_transfer req=0x{request:02x} failed: {_errname(self.lib, rc)}")
        return rc
    def _cp210x_setup(self):
        self._ctrl_out(CP210X_IFC_ENABLE, UART_ENABLE)
        self._ctrl_out(CP210X_SET_BAUDRATE, 0, BAUD.to_bytes(4, "little"))
        self._ctrl_out(CP210X_SET_LINE_CTL, LINE_CTL_8N1)
    def read(self, length=256, timeout_ms=2000):
        buf = ctypes.create_string_buffer(length)
        transferred = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(self.handle, CP2102_EP_IN, buf, length, ctypes.byref(transferred), timeout_ms)
        if rc == -7:
            return b""
        if rc != 0:
            raise LibUSBError(f"bulk read failed: {_errname(self.lib, rc)}")
        return buf.raw[:transferred.value]
    def write(self, data, timeout_ms=1000):
        transferred = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(self.handle, CP2102_EP_OUT, data, len(data), ctypes.byref(transferred), timeout_ms)
        return rc
    def close(self):
        try:
            if self.handle:
                self.lib.libusb_close(self.handle)
        except:
            pass
        try:
            if self.ctx:
                self.lib.libusb_exit(self.ctx)
        except:
            pass
def read_lines(on_line):
    link = UsbLink()
    link.open()
    buf = b""
    try:
        while True:
            try:
                while True:
                    out_line = _outgoing.get_nowait()
                    link.write(out_line)
            except queue.Empty:
                pass
            data = link.read(512, timeout_ms=2000)
            if data:
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", "ignore"))
                    except:
                        continue
                    on_line(obj)
    except LibUSBError as e:
        print(f"[usb] link error: {e}")
        raise
    finally:
        link.close()
def _standalone_test():
    print("=== MILLIE USB standalone test ===")
    def _p(obj): print(obj)
    try:
        read_lines(_p)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"FATAL: {e}")
if __name__ == "__main__":
    _standalone_test()
USBEOF

# ---- millie/ap_client.py ----
cat > "$ROOT/millie/ap_client.py" << 'APEOF'
import json, time, urllib.request, urllib.parse
ESP_BASE = "http://192.168.4.1"
POLL_INTERVAL = 2.0
def _get_dets(timeout=4):
    try:
        with urllib.request.urlopen(f"{ESP_BASE}/api/dets", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except:
        return None
def post_verdict(mac, conf, bearing=None, dist=None, rssi=None, timeout=4):
    params = {"mac": mac, "conf": conf}
    if bearing is not None:
        params["brg"] = str(int(bearing))
    if dist is not None:
        params["dist"] = f"{float(dist):.1f}"
    if rssi is not None:
        params["rssi"] = str(int(rssi))
    url = f"{ESP_BASE}/api/verdict?" + urllib.parse.urlencode(params)
    try:
        urllib.request.urlopen(url, timeout=timeout).read()
        return True
    except:
        return False
def poll_loop(on_hit, running=lambda: True):
    while running():
        dets = _get_dets()
        if dets:
            for d in dets:
                on_hit({"mac": d.get("mac"), "conf_esp": d.get("conf"), "ssid": d.get("ssid",""), "rssi": d.get("rssi")})
        time.sleep(POLL_INTERVAL)
APEOF

# ---- millie/config/ouis.py ----
cat > "$ROOT/millie/config/ouis.py" << 'OUISEOF'
"""
MAC OUI – Auto‑updated from community sources + IEEE.
Axon Body cameras flagged as police.
"""
import os, json, re, time, requests
from pathlib import Path

CACHE_FILE = Path(__file__).parent / "oui_cache.json"
CACHE_TTL = 86400  # 24 hours

# ---- Known Flock OUIs (fallback) ----
FLOCK_FALLBACK = [
    "70:c9:4e", "3c:91:80", "d8:f3:bc", "80:30:49", "b8:35:32",
    "14:5a:fc", "74:4c:a1", "08:3a:88", "9c:2f:9d", "c0:35:32",
    "94:08:53", "e4:aa:ea", "f4:6a:dd", "f8:a2:d6", "24:b2:b9",
    "00:f4:8d", "d0:39:57", "e8:d0:fc", "e0:4f:43", "b8:1e:a4",
    "70:08:94", "58:8e:81", "ec:1b:bd", "3c:71:bf", "58:00:e3",
    "90:35:ea", "5c:93:a2", "64:6e:69", "48:27:ea", "a4:cf:12",
    "82:6b:f2",
]
RING = [
    "00:b4:63", "18:7f:88", "24:2b:d6", "34:3e:a4", "50:e4:67",
    "54:e0:19", "5c:47:5e", "64:9a:63", "90:48:6c", "9c:76:13",
    "ac:9f:c3", "c4:db:ad", "cc:3b:fb",
]
BLINK = ["f0:74:c1", "3c:a0:70", "70:ad:43", "74:ab:93"]

# ---- Axon Body cameras (police) ----
AXON = ["00:58:28", "00:25:df", "84:70:03"]

# ---- Police OUIs (including Axon) ----
POLICE = [
    "00:1a:2b", "00:0d:6f", "00:18:1a", "00:1e:4f", "00:1c:42",
    "00:1d:72", "00:22:64", "00:20:91", "00:25:9e", "00:30:65",
    "00:50:56", "00:0b:86", "00:1a:e1", "00:17:a4", "00:23:df",
]
POLICE.extend(AXON)

EERO = [
    "20:be:cd", "48:b4:24", "fc:3f:a6", "44:ac:85", "88:67:46",
    "68:4a:76", "00:ab:48", "08:9b:f1", "08:f0:1e", "0c:1c:1a",
    "14:22:db", "18:90:88", "20:e6:df", "24:2d:6c", "30:34:22",
    "28:ec:22", "3c:5c:f1", "d4:05:de", "30:57:8e", "5c:5a:35",
    "40:47:5e", "48:dd:0c", "4c:01:43", "50:27:a9", "5c:a5:bc",
    "60:57:7d",
]
ESPRESSIF = [
    "ac:67:b2", "84:f3:eb", "b4:e6:2d", "cc:db:a7", "24:0a:c4",
    "30:ae:a4", "94:b9:7e", "c0:49:ef", "08:3a:f2", "10:52:1c",
    "18:fe:34", "1c:bf:ce", "24:6f:28", "2c:3a:e8", "34:94:54",
    "3c:61:05", "40:f5:20", "44:17:93", "48:27:e2", "4c:11:ae",
    "50:02:91", "54:5a:a6", "58:bf:25", "5c:cf:7f", "60:55:f9",
    "64:b7:08", "68:c6:3a", "6c:19:8f", "70:03:9d", "74:da:38",
    "78:e3:6d", "7c:df:a1", "80:7d:3a", "84:0d:8e", "88:b1:e1",
    "8c:aa:ce", "90:97:d5", "94:65:2d", "98:f4:ab", "9c:9d:7e",
    "a0:76:4e", "a8:61:0a", "b0:a7:32", "b8:27:eb", "bc:dd:c2",
    "c4:4f:33", "c8:f0:9e", "d0:76:5f", "d8:a0:1d", "dc:4f:22",
    "e0:5a:1b", "e4:95:6e", "e8:68:e7", "ec:fa:bc", "f0:08:d1",
    "f4:12:fa", "f8:f0:05", "fc:f5:28",
]

ROUTER_SSID_PATTERNS = [
    "t-mobile", "xfinity", "spectrum", "att", "verizon",
    "starbucks", "googlewifi", "linksys", "netgear", "asus",
    "tp-link", "arcadyan", "sercomm",
]

def is_router_ssid(ssid):
    if not ssid:
        return False
    s = ssid.lower()
    return any(p in s for p in ROUTER_SSID_PATTERNS)

def fetch_ouis():
    try:
        url = "https://raw.githubusercontent.com/colonelpanichacks/flock-you/main/datasets/NitekryDPaul_wifi_ouis.md"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            new_ouis = []
            for line in lines:
                match = re.search(r'([0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2})', line, re.I)
                if match:
                    new_ouis.append(match.group(1).lower())
            if new_ouis:
                return new_ouis
    except:
        pass
    return FLOCK_FALLBACK

def get_flock_ouis():
    now = time.time()
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            if (now - data.get("time", 0)) < CACHE_TTL:
                return data.get("flock", FLOCK_FALLBACK)
        except:
            pass
    fresh = fetch_ouis()
    with open(CACHE_FILE, "w") as f:
        json.dump({"flock": fresh, "time": now}, f)
    return fresh

FLOCK_OUIS = get_flock_ouis()
CAMERA_OUIS = set(RING + BLINK + FLOCK_OUIS + AXON)
POLICE_SET = set(POLICE)
EERO_SET = set(EERO)
ALL_OUIS = CAMERA_OUIS | POLICE_SET | EERO_SET | set(ESPRESSIF)

def get_camera_type(prefix):
    if prefix in RING:
        return "ring"
    if prefix in BLINK:
        return "blink"
    if prefix in FLOCK_OUIS:
        return "flock"
    if prefix in AXON:
        return "axon"
    return None

def match_oui(bssid):
    if not bssid or len(bssid) < 8:
        return None
    prefix = bssid.lower()[:8]
    if prefix in POLICE_SET:
        return "Police Equipment", True, False   # Mark as police
    if prefix in EERO_SET:
        return "Eero (whitelisted)", False, True
    cam_type = get_camera_type(prefix)
    if cam_type:
        return f"{cam_type.upper()} Camera", True, False
    if prefix in ALL_OUIS:
        return "Other device", False, False
    return None
OUISEOF

# ---- millie/config/ssid_patterns.py ----
cat > "$ROOT/millie/config/ssid_patterns.py" << 'SSIDEOF'
import re
FLOCK_SSID_RE = re.compile(r"(?i)^(flock|falcon|sparrow|raven|penguin)[_-]?")
SSIDEOF

# ---- millie/scanners/wifi_scanner.py ----
cat > "$ROOT/millie/scanners/wifi_scanner.py" << 'SCANEOF'
import json, subprocess, time
from ..config.ouis import match_oui
from ..config.ssid_patterns import FLOCK_SSID_RE
MIN_POLL_INTERVAL_SECONDS = 30
_last_poll = 0.0

def _run_scaninfo():
    try:
        out = subprocess.run(["termux-wifi-scaninfo"], capture_output=True, text=True, timeout=15)
        return json.loads(out.stdout or "[]")
    except:
        return []

def scan(force=False):
    global _last_poll
    now = time.time()
    if not force and now - _last_poll < MIN_POLL_INTERVAL_SECONDS:
        return []
    _last_poll = now
    detections = []
    for net in _run_scaninfo():
        ssid = (net.get("ssid") or "").strip()
        bssid = net.get("bssid") or ""
        rssi = net.get("rssi") or net.get("level")
        oui_match = match_oui(bssid)
        if not oui_match:
            continue
        label_oui, _, _ = oui_match
        ssid_is_flock = bool(ssid and FLOCK_SSID_RE.search(ssid))
        hidden = (ssid == "")
        if ssid_is_flock:
            detections.append(("FLOCK ALPR camera (OUI + Flock SSID)", "HIGH", bssid, ssid, rssi, True))
        elif hidden:
            detections.append(("Likely Flock/ESP32 camera (Flock OUI, hidden SSID)", "MEDIUM", bssid, ssid, rssi, True))
        else:
            detections.append((f"Possible ESP32 device ({label_oui}) — weak", "LOW", bssid, ssid, rssi, False))
    return detections

def scan_all(force=False):
    global _last_poll
    now = time.time()
    if not force and now - _last_poll < MIN_POLL_INTERVAL_SECONDS:
        return []
    networks = _run_scaninfo()
    out = []
    for net in networks:
        ssid = (net.get("ssid") or "").strip()
        bssid = net.get("bssid") or ""
        rssi = net.get("rssi") or net.get("level")
        if not bssid:
            continue
        oui_match = match_oui(bssid)
        out.append({"mac": bssid, "ssid": ssid, "rssi": rssi, "oui_flock": bool(oui_match)})
    return out
SCANEOF

# ---- millie/device_table.py ----
cat > "$ROOT/millie/device_table.py" << 'DEVEOF'
import math, threading, time
from collections import deque, defaultdict
from .config.ouis import match_oui, get_camera_type, is_router_ssid

RANGE_BAND_FT = 3.0
MAX_RANGE_FT = 50.0
CONCURRENT_WINDOW_S = 15
MOVEMENT_HISTORY_LEN = 20
MOVEMENT_MIN_SAMPLES = 5
MOVEMENT_MIN_DELTA_FT = 2.0
MOVEMENT_SLOPE_THRESHOLD = 0.8
MOVEMENT_MAX_AGE_S = 30
STALE_SECONDS = 300
ANOMALY_RSSI_DROP = 20

TX_POWER_1M = -45.0
PATH_LOSS_N = 3.0
M_TO_FT = 3.28084

def rssi_to_feet(rssi):
    if rssi is None:
        return None
    try:
        meters = 10 ** ((TX_POWER_1M - float(rssi)) / (10 * PATH_LOSS_N))
    except:
        return None
    meters = max(0.15, min(meters, 600))
    return round(meters * M_TO_FT, 1)

class DeviceTable:
    def __init__(self, stale_seconds=STALE_SECONDS):
        self.lock = threading.Lock()
        self.devices = {}
        self.stale_seconds = stale_seconds

    def _enrich_device(self, d):
        if d is None: return d
        hidden = d.get("hidden", False)
        is_camera = d.get("is_camera", False)
        is_police = d.get("is_police", False)
        is_whitelisted = d.get("is_whitelisted", False)
        camera_type = d.get("camera_type", None)
        is_router = d.get("is_router", False)

        moving = False
        history = d.get("movement_history", [])
        now = time.time()
        recent = [(ts, dist) for ts, dist in history if (now - ts) <= MOVEMENT_MAX_AGE_S]
        if len(recent) >= MOVEMENT_MIN_SAMPLES:
            t0 = recent[0][0]
            xs = [ts - t0 for ts, _ in recent]
            ys = [dist for _, dist in recent]
            n = len(xs)
            sum_x = sum(xs); sum_y = sum(ys); sum_xx = sum(x*x for x in xs); sum_xy = sum(x*y for x,y in zip(xs,ys))
            denom = n * sum_xx - sum_x * sum_x
            if denom != 0:
                slope = (n * sum_xy - sum_x * sum_y) / denom
                total_delta = ys[-1] - ys[0]
                if abs(total_delta) >= MOVEMENT_MIN_DELTA_FT and abs(slope) >= MOVEMENT_SLOPE_THRESHOLD:
                    moving = True

        anomaly = False
        rssi_history = d.get("rssi_history", deque(maxlen=10))
        if len(rssi_history) >= 3:
            last_rssi = rssi_history[-1]
            prev_rssi = rssi_history[-2]
            if (prev_rssi - last_rssi) >= ANOMALY_RSSI_DROP:
                anomaly = True

        if is_whitelisted or is_router:
            suspicious = False
            score = 0.0
        else:
            indicators = (1 if hidden else 0) + (1 if is_camera else 0) + (1 if is_police else 0) + (1 if moving else 0) + (1 if anomaly else 0)
            suspicious = indicators >= 2
            score = indicators / 5.0

        if isinstance(history, deque):
            history = list(history)
        if isinstance(rssi_history, deque):
            rssi_history = list(rssi_history)

        d["is_camera"] = is_camera
        d["is_police"] = is_police
        d["is_whitelisted"] = is_whitelisted
        d["is_router"] = is_router
        d["is_moving"] = moving
        d["is_anomaly"] = anomaly
        d["suspicion_score"] = round(score, 2)
        d["suspicious"] = suspicious
        d["movement_history"] = history
        d["rssi_history"] = rssi_history
        d["indicators"] = {"hidden": hidden, "camera": is_camera, "police": is_police,
                           "moving": moving, "anomaly": anomaly}
        d["camera_type"] = camera_type
        return d

    def ingest(self, mac, kind, rssi=None, name="", ssid="", ftype="",
               oui_flock=False, gps=None, flock_conf=None, source="esp32"):
        mac = (mac or "").upper()
        if not mac: return None
        now = time.time()
        dist_ft = rssi_to_feet(rssi)
        oui_result = match_oui(mac) if mac else None
        is_camera = False; is_police = False; is_whitelisted = False; camera_type = None
        if oui_result:
            label, is_camera, is_whitelisted = oui_result
            if label == "Police Equipment":
                is_police = True
            prefix = mac.lower()[:8]
            camera_type = get_camera_type(prefix)
        is_router = is_router_ssid(ssid)

        with self.lock:
            d = self.devices.get(mac)
            if d is None:
                d = {
                    "mac": mac, "kind": kind, "first_seen": now,
                    "last_seen": now, "rssi": rssi, "dist_ft": dist_ft,
                    "name": name, "ssid": ssid, "ftype": ftype,
                    "oui_flock": oui_flock, "flock_conf": flock_conf,
                    "gps_samples": [], "suspicious": False, "sightings": 0,
                    "source": source, "hidden": (kind == "wifi" and not ssid),
                    "is_camera": is_camera, "is_police": is_police,
                    "is_whitelisted": is_whitelisted, "is_router": is_router,
                    "is_moving": False, "is_anomaly": False,
                    "suspicion_score": 0.0,
                    "movement_history": deque(maxlen=MOVEMENT_HISTORY_LEN),
                    "rssi_history": deque(maxlen=10),
                    "camera_type": camera_type,
                }
                self.devices[mac] = d
            d["last_seen"] = now
            d["sightings"] += 1
            d["source"] = source
            if rssi is not None:
                d["rssi"] = rssi
                d["dist_ft"] = dist_ft
                d["rssi_history"].append(rssi)
            if name:
                d["name"] = name
            if ssid:
                d["ssid"] = ssid
                d["hidden"] = False
                d["is_router"] = is_router_ssid(ssid)
            elif kind == "wifi" and not d.get("ssid"):
                d["hidden"] = True
            if ftype:
                d["ftype"] = ftype
            if oui_flock:
                d["oui_flock"] = True
            if flock_conf:
                d["flock_conf"] = flock_conf
            if gps and gps.get("lat") is not None:
                d["gps_samples"].append({"lat": gps["lat"], "lon": gps["lon"], "rssi": rssi, "ts": now})
                d["gps_samples"] = d["gps_samples"][-200:]
            oui_result2 = match_oui(mac) if mac else None
            if oui_result2:
                label2, is_cam2, is_white2 = oui_result2
                d["is_camera"] = is_cam2
                d["is_police"] = (label2 == "Police Equipment")
                d["is_whitelisted"] = is_white2
                prefix2 = mac.lower()[:8]
                d["camera_type"] = get_camera_type(prefix2)
            if dist_ft is not None:
                d["movement_history"].append((now, dist_ft))
            return self._enrich_device(dict(d))

    def set_verdict(self, mac, flock_conf, bearing=None, dist=None):
        mac = (mac or "").upper()
        with self.lock:
            d = self.devices.get(mac)
            if d:
                d["flock_conf"] = flock_conf
                if bearing is not None:
                    d["bearing"] = bearing
                if dist is not None:
                    d["dist"] = dist

    def _prune(self, now):
        cutoff = now - self.stale_seconds
        dead = [m for m, d in self.devices.items() if d["last_seen"] < cutoff]
        for m in dead:
            del self.devices[m]

    def view(self, which, source=None):
        now = time.time()
        with self.lock:
            self._prune(now)
            items = [dict(d) for d in self.devices.values()]
        items = [self._enrich_device(d) for d in items]
        if source:
            items = [d for d in items if d.get("source") == source]
        if which == "flock":
            return [d for d in items if d.get("flock_conf") in ("H", "M", "HIGH", "MEDIUM")]
        if which == "ble":
            return [d for d in items if d["kind"] == "ble"]
        if which == "all":
            return items
        return items

    def snapshot(self, source=None):
        all_devs = self.view("all", source)
        ble_devs = self.view("ble", source)
        flock_devs = self.view("flock", source)
        return {"all": all_devs, "ble": ble_devs, "flock": flock_devs, "clusters": {}}
DEVEOF

# ---- millie/web/server.py ----
cat > "$ROOT/millie/web/server.py" << 'SRVEOF'
import threading, time, os
from collections import deque, defaultdict
from flask import Flask, jsonify, render_template, request
from .. import storage, location as loc, estimator
from ..scanners import wifi_scanner
from ..device_table import DeviceTable
import csv, io
from flask import send_file

app = Flask(__name__, template_folder="templates")
_devices = DeviceTable()
_state = {
    "scanning": False, "interval": 20, "last_scan": None,
    "flock_active": [], "flock_count": 0,
    "log": deque(maxlen=200), "gps": None,
    "usb_enabled": False, "ap_enabled": False,
    "esp_alive": None, "esp_channel": None, "esp_radio": None,
}
_seen_flock_bssids = set()
_samples = defaultdict(lambda: deque(maxlen=200))
_labels = {}
_lock = threading.Lock()

def _push_log(msg, source="PHONE"):
    ts = time.strftime("%H:%M:%S")
    _state["log"].appendleft({"t": ts, "msg": msg, "source": source})
    print(f"[{ts}] [{source}] {msg}", flush=True)

def _push_verdict_to_cyd(bssid, confidence, rssi):
    if not _state.get("usb_enabled"):
        return
    try:
        from .. import usb_reader
        bearing = dist = None
        est = estimator.estimate_position(list(_samples.get(bssid, [])))
        gps = _state.get("gps")
        if gps and est.get("method") == "weighted_centroid":
            e = est["estimate"]
            bearing = estimator.bearing(gps["lat"], gps["lon"], e["lat"], e["lon"])
            dist = estimator.haversine(gps["lat"], gps["lon"], e["lat"], e["lon"])
        usb_reader.send_det(bssid, confidence, bearing, dist, rssi)
    except:
        pass

def _ingest_hit(conn, label, confidence, bssid, ssid, rssi, cur_gps, source):
    bssid = (bssid or "").upper()
    storage.record_detection(conn, source, label, "flock", bssid, rssi,
                             None, confidence, None)
    _labels[bssid] = (label, confidence, ssid or "<hidden>")
    _samples[bssid].append({"lat": cur_gps["lat"] if cur_gps else None,
                            "lon": cur_gps["lon"] if cur_gps else None,
                            "rssi": rssi, "ts": time.time()})
    if bssid not in _seen_flock_bssids:
        _seen_flock_bssids.add(bssid)
        _push_log(f"NEW {confidence} Flock hit [{source}]: {bssid} ({ssid or 'hidden'}) rssi={rssi}")
    with _lock:
        _state["flock_count"] = len(_seen_flock_bssids)
    _push_verdict_to_cyd(bssid, confidence, rssi)
    return {"label": label, "confidence": confidence, "bssid": bssid,
            "ssid": ssid or "<hidden>", "rssi": rssi, "is_flock": True}

def _classify_esp_hit(obj):
    from ..config.ssid_patterns import FLOCK_SSID_RE
    ssid = obj.get("ssid") or ""
    ftype = obj.get("ftype", "")
    rapid = obj.get("rapid", False)
    oui_flock = obj.get("oui_flock", False)
    if not oui_flock:
        return "ESP32 device seen via sniffer (weak)", "LOW", ssid
    if ssid and FLOCK_SSID_RE.search(ssid):
        return "FLOCK ALPR camera (OUI + Flock SSID)", "HIGH", ssid
    if rapid:
        return "FLOCK ALPR camera (OUI + rapid probes)", "HIGH", ssid
    if ftype == "probe_req" and not ssid:
        return "Likely Flock camera (OUI + hidden probe)", "MEDIUM", ssid
    return "Possible ESP32 device (OUI match) – weak", "LOW", ssid

def _usb_loop():
    from .. import usb_reader
    conn, _ = storage.get_connection()
    def on_line(obj):
        if obj.get("_status"):
            with _lock:
                _state["esp_alive"] = time.strftime("%H:%M:%S")
                if "ch" in obj:
                    _state["esp_channel"] = obj["ch"]
                if "radio" in obj:
                    _state["esp_radio"] = obj["radio"]
            _push_log(f"heartbeat radio={obj.get('radio')} ch={obj.get('ch')} "
                      f"wifi={obj.get('wifi')} ble={obj.get('ble')}", source="ESP")
            return
        kind = obj.get("t")
        mac = obj.get("mac")
        if not mac or not kind:
            return
        cur_gps = _state["gps"]
        _push_log(f"{kind} {mac} rssi={obj.get('rssi')} "
                  f"{obj.get('ssid') or obj.get('name') or ''}".strip(), source="ESP")
        _devices.ingest(mac, kind, rssi=obj.get("rssi"),
                        name=obj.get("name", ""), ssid=obj.get("ssid", ""),
                        ftype=obj.get("ftype", ""), oui_flock=obj.get("oui_flock", False),
                        gps=cur_gps, source="esp32")
        if kind == "wifi" and obj.get("oui_flock"):
            label, confidence, ssid = _classify_esp_hit({
                "mac": mac, "ssid": obj.get("ssid", ""),
                "ftype": obj.get("ftype", "probe_req"),
                "rapid": obj.get("rapid", False), "oui_flock": obj.get("oui_flock", False)
            })
            if confidence in ("HIGH", "MEDIUM"):
                _ingest_hit(conn, label, confidence, mac, ssid,
                            obj.get("rssi"), cur_gps, "ESP32")
                bearing = dist = None
                est = estimator.estimate_position(list(_samples.get(mac.upper(), [])))
                if cur_gps and est.get("method") == "weighted_centroid":
                    e = est["estimate"]
                    bearing = estimator.bearing(cur_gps["lat"], cur_gps["lon"],
                                                e["lat"], e["lon"])
                    dist = estimator.haversine(cur_gps["lat"], cur_gps["lon"],
                                               e["lat"], e["lon"])
                _devices.set_verdict(mac, confidence[0], bearing, dist)
                try:
                    usb_reader.send_det(mac, confidence, bearing, dist,
                                        obj.get("rssi"))
                except:
                    pass
    while _state.get("usb_enabled"):
        try:
            _push_log("USB firehose connected — reading ESP32")
            usb_reader.read_lines(on_line)
        except Exception as e:
            _push_log(f"USB reader error: {e} (retrying in 3s)")
            time.sleep(3)

def _ap_loop():
    from .. import ap_client
    conn, _ = storage.get_connection()
    def on_hit(h):
        mac = h.get("mac")
        if not mac:
            return
        label, confidence, ssid = _classify_esp_hit({
            "mac": mac, "ssid": h.get("ssid", ""),
            "ftype": "probe_req" if not h.get("ssid") else "beacon",
            "rapid": False, "oui_flock": True,
        })
        if confidence == "LOW":
            return
        cur_gps = _state["gps"]
        entry = _ingest_hit(conn, label, confidence, mac, ssid,
                            h.get("rssi"), cur_gps, "ESP-AP")
        with _lock:
            _state["esp_alive"] = time.strftime("%H:%M:%S")
            existing = [e for e in _state["flock_active"] if e["bssid"] != entry["bssid"]]
            _state["flock_active"] = existing + [entry]
        bearing = dist = None
        est = estimator.estimate_position(list(_samples.get(mac.upper(), [])))
        if cur_gps and est.get("method") == "weighted_centroid":
            e = est["estimate"]
            bearing = estimator.bearing(cur_gps["lat"], cur_gps["lon"], e["lat"], e["lon"])
            dist = estimator.haversine(cur_gps["lat"], cur_gps["lon"], e["lat"], e["lon"])
        ap_client.post_verdict(mac, confidence, bearing, dist, h.get("rssi"))
    _push_log("AP mode — polling ESP at 192.168.4.1")
    ap_client.poll_loop(on_hit, running=lambda: _state.get("ap_enabled"))

def _scan_loop():
    conn, _ = storage.get_connection()
    while _state["scanning"]:
        try:
            fix = loc.get_fix_any(timeout=8)
            if fix:
                with _lock:
                    _state["gps"] = fix
            cur_gps = _state["gps"]
            hits = wifi_scanner.scan(force=True)
            active = []
            for label, confidence, bssid, ssid, rssi, is_flock in hits:
                if is_flock:
                    entry = _ingest_hit(conn, label, confidence, bssid, ssid,
                                       rssi, cur_gps, "WIFI")
                    active.append(entry)
            with _lock:
                esp_only = [e for e in _state["flock_active"]
                            if e["bssid"] not in {a["bssid"] for a in active}]
                _state["flock_active"] = active + esp_only
                _state["last_scan"] = time.strftime("%H:%M:%S")
            if not active:
                _push_log("wifi scan clear — no Flock cameras in range")
            all_nets = wifi_scanner.scan_all(force=True)
            for net in all_nets:
                _devices.ingest(net["mac"], "wifi", rssi=net.get("rssi"),
                                ssid=net.get("ssid", ""), oui_flock=net.get("oui_flock", False),
                                gps=cur_gps, source="phone")
            if all_nets:
                _push_log(f"phone wifi scan: {len(all_nets)} networks seen")
        except Exception as e:
            _push_log(f"scan error: {e}")
        time.sleep(_state["interval"])

@app.route("/api/devices")
def api_devices():
    source = request.args.get("source")
    snap = _devices.snapshot(source=source)
    return jsonify({
        "all": snap["all"],
        "ble": snap["ble"],
        "flock": snap["flock"],
        "clusters": {},
    })

@app.route("/api/radar")
def api_radar():
    with _lock:
        gps = _state["gps"]
        active_bssids = {e["bssid"] for e in _state["flock_active"]}
    all_devs = _devices.view("all")
    devices_out = []
    for d in all_devs:
        bssid = d["mac"]
        kind = d["kind"]
        flock_conf = d.get("flock_conf")
        label = _labels.get(bssid, ("Unknown device", "?", ""))[0]
        confidence = flock_conf if flock_conf in ("H", "M") else "LOW"
        if confidence == "H":
            confidence = "HIGH"
        elif confidence == "M":
            confidence = "MEDIUM"
        rssi = d.get("rssi")
        samples = list(_samples.get(bssid, []))
        est = estimator.estimate_position(samples)
        distance_m = estimator.rssi_to_distance(rssi) if rssi is not None else None
        bearing = None
        est_distance_m = None
        if gps and est.get("method") == "weighted_centroid":
            e = est["estimate"]
            bearing = round(estimator.bearing(gps["lat"], gps["lon"],
                                              e["lat"], e["lon"]), 1)
            est_distance_m = round(estimator.haversine(
                gps["lat"], gps["lon"], e["lat"], e["lon"]), 1)
        elif distance_m is not None:
            est_distance_m = distance_m
        is_camera = d.get("is_camera", False)
        is_police = d.get("is_police", False)
        camera_type = d.get("camera_type", None)

        if kind == "ble":
            color = "blue"
        elif camera_type == "flock":
            color = "red"
        elif is_camera:
            color = "yellow"
        elif is_police:
            color = "red"   # Police devices also red
        else:
            color = "green"

        dev_out = {
            "bssid": bssid, "label": label, "confidence": confidence,
            "ssid": d.get("ssid", ""), "rssi": rssi,
            "kind": kind, "flock_conf": flock_conf, "color": color,
            "bearing": bearing, "distance_m": distance_m,
            "est_distance_m": est_distance_m,
            "in_range": bssid in active_bssids,
            "last_seen": d.get("last_seen"), "source": d.get("source"),
            "is_camera": is_camera,
            "is_police": is_police,
            "is_whitelisted": d.get("is_whitelisted", False),
            "is_moving": d.get("is_moving", False),
            "is_anomaly": d.get("is_anomaly", False),
            "is_router": d.get("is_router", False),
            "suspicious": d.get("suspicious", False),
            "indicators": d.get("indicators", {}),
        }
        devices_out.append(dev_out)
    return jsonify({"gps": gps, "scanning": _state["scanning"], "devices": devices_out})

@app.route("/api/state")
def api_state():
    with _lock:
        return jsonify({
            "scanning": _state["scanning"],
            "interval": _state["interval"],
            "last_scan": _state["last_scan"],
            "flock_active": _state["flock_active"],
            "flock_count": _state["flock_count"],
            "log": list(_state["log"])[:60],
            "usb_enabled": _state["usb_enabled"],
            "esp_alive": _state["esp_alive"],
            "esp_channel": _state["esp_channel"],
        })

@app.route("/")
def index():
    return render_template("dashboard.html")

# ---- v3 features ----
@app.route("/api/export")
def api_export():
    source = request.args.get("source")
    snap = _devices.snapshot(source=source)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["MAC", "Kind", "RSSI", "Distance(ft)", "SSID", "Source", "Last Seen", "Suspicious", "Camera", "Police", "Router", "Moving", "Anomaly"])
    for d in snap["all"]:
        writer.writerow([
            d.get("mac", ""), d.get("kind", ""), d.get("rssi", ""),
            d.get("dist_ft", ""), d.get("ssid", ""), d.get("source", ""),
            d.get("last_seen", ""), d.get("suspicious", False),
            d.get("is_camera", False), d.get("is_police", False),
            d.get("is_router", False), d.get("is_moving", False),
            d.get("is_anomaly", False)
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8')),
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name='millie_export.csv')

DURESS_PIN = os.environ.get("MILLIE_DURESS_PIN", "1234")

@app.route("/api/duress", methods=["POST"])
def api_duress():
    data = request.get_json() or {}
    pin = data.get("pin", "")
    if pin == DURESS_PIN:
        import sqlite3
        db_path = storage.DB_PATH
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.execute("DELETE FROM detections")
            conn.commit()
            conn.close()
        _devices.devices.clear()
        _seen_flock_bssids.clear()
        _samples.clear()
        _labels.clear()
        with _lock:
            _state["flock_count"] = 0
            _state["flock_active"] = []
        _push_log("⚠️ Duress PIN activated – data wiped")
        return jsonify({"status": "wiped"})
    else:
        return jsonify({"error": "invalid pin"}), 403

def serve(host="127.0.0.1", port=8770, autostart=True, usb=False, ap=False):
    if autostart:
        _state["scanning"] = True
        threading.Thread(target=_scan_loop, daemon=True).start()
        _push_log("scanning started (autostart)")
    if usb:
        _state["usb_enabled"] = True
        threading.Thread(target=_usb_loop, daemon=True).start()
        _push_log("USB sniffer feed enabled")
        print("  ESP32 USB sniffer feed: ENABLED")
    if ap:
        _state["ap_enabled"] = True
        threading.Thread(target=_ap_loop, daemon=True).start()
        _push_log("ESP AP feed enabled")
        print("  ESP32 AP feed: ENABLED")
    print(f"\n  MILLIE dashboard -> http://{host}:{port}\n")
    app.run(host=host, port=port, threaded=True)
SRVEOF

# ---- millie/web/templates/dashboard.html ----
cat > "$ROOT/millie/web/templates/dashboard.html" << 'DASHV3'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>MILLIE v3</title>
<style>
  *{box-sizing:border-box; margin:0}
  :root{--bg:#0a0f0a; --panel:#0d140d; --line:#1c2a1c; --green:#39ff14; --green-dim:#1f8a12; --amber:#ffb000; --red:#ff3131; --blue:#508cff; --yellow:#ffd700; --grey:#666; --mono:'Courier New',ui-monospace,monospace}
  html,body{background:var(--bg);color:var(--green);font-family:var(--mono);font-size:14px;padding:10px 10px 20px;max-width:800px;margin:0 auto;text-shadow:0 0 4px rgba(57,255,20,.35)}
  body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:99;background:repeating-linear-gradient(0deg,transparent 0,transparent 2px,rgba(0,0,0,.18) 3px);}
  .header{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin-bottom:6px}
  .header h1{font-size:18px;letter-spacing:3px;margin:0;display:flex;align-items:center;gap:6px}
  .live{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);animation:blink 1.4s infinite;vertical-align:middle}
  @keyframes blink{50%{opacity:.3}}
  .gps-status{font-size:11px;color:var(--green-dim)}
  .esp-status{font-size:11px;color:var(--green-dim);margin-left:auto}
  .esp-status.on{color:var(--green)}
  .stats{display:flex;gap:8px;margin:8px 0}
  .stat{flex:1;background:var(--panel);border:1px solid var(--line);padding:8px;border-radius:2px;text-align:center}
  .stat .n{font-size:26px;font-weight:bold;line-height:1}
  .stat .l{font-size:10px;color:var(--green-dim);margin-top:2px}
  .stat.alert .n{color:var(--red);text-shadow:0 0 8px rgba(255,49,49,.6);animation:pulse 1s infinite}
  @keyframes pulse{50%{opacity:.5}}
  .status-bar{display:flex;gap:8px;margin:8px 0;justify-content:center;font-size:12px;color:var(--green-dim)}
  .status-bar .on{color:var(--green)}
  #radar-wrap{position:relative;width:100%;max-width:400px;margin:8px auto}
  canvas{width:100%;height:auto;display:block}
  .legend{display:flex;justify-content:center;gap:16px;font-size:10px;color:var(--green-dim);margin:2px 0 8px;flex-wrap:wrap}
  .legend .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}
  .dot.red{background:var(--red)} .dot.yellow{background:var(--yellow)} .dot.green{background:var(--green)} .dot.blue{background:var(--blue)}
  .tab-bar{display:flex;gap:0;border-bottom:1px solid var(--line);margin:12px 0 0;flex-wrap:wrap}
  .tab-bar button{flex:1;background:transparent;border:none;border-bottom:2px solid transparent;color:var(--green-dim);padding:8px 4px;font-family:var(--mono);font-size:12px;letter-spacing:1px;cursor:pointer;transition:all .2s;min-width:55px}
  .tab-bar button.active{color:var(--green);border-bottom-color:var(--green)}
  .tab-bar button .badge{background:var(--green-dim);color:#000;border-radius:10px;padding:0 6px;font-size:10px;margin-left:4px}
  .filter-bar{display:flex;gap:10px;margin:8px 0;font-size:11px;color:var(--green-dim);flex-wrap:wrap}
  .filter-bar label{display:flex;align-items:center;gap:4px;cursor:pointer}
  .filter-bar input[type="radio"]{accent-color:var(--green);width:14px;height:14px}
  .tab-content{display:none;padding-top:8px}
  .tab-content.active{display:block}
  .hit,.dev{border-left:3px solid var(--green);padding:6px 10px;margin-bottom:5px;background:rgba(57,255,20,.04);font-size:12px;word-break:break-all}
  .hit.HIGH{border-left-color:var(--red)}
  .hit.MEDIUM{border-left-color:var(--amber)}
  .hit .tag{font-weight:bold;letter-spacing:1px}
  .hit.HIGH .tag{color:var(--red)} .hit.MEDIUM .tag{color:var(--amber)}
  .hit .meta{color:var(--green-dim);font-size:11px;margin-top:2px}
  .hit .loc{color:var(--grey);font-size:11px;margin-top:2px}
  .dev.ble{border-left-color:var(--blue)}
  .dev.flock{border-left-color:var(--red)}
  .dev .tag{font-weight:bold;letter-spacing:1px}
  .dev .tag.ble{color:var(--blue)}
  .dev .tag.flock{color:var(--red)}
  .dev .m{color:var(--green-dim);font-size:10px;margin-top:1px}
  .dev .src{font-size:10px;color:var(--grey);margin-left:6px}
  .flag{display:inline-block;font-size:10px;padding:0 4px;border:1px solid var(--green-dim);border-radius:2px;margin:0 2px}
  .flag.hidden{color:var(--amber)}
  .flag.camera{color:var(--red)}
  .flag.police{color:var(--blue)}
  .flag.moving{color:var(--green)}
  .flag.anomaly{color:var(--red);animation:blink 0.5s infinite}
  .flag.router{color:var(--grey)}
  .sus-item{border:1px solid var(--line);border-radius:3px;padding:6px 10px;margin-bottom:5px;cursor:pointer;background:rgba(255,176,0,.05);transition:background .2s}
  .sus-item:hover{background:rgba(255,176,0,.1)}
  .sus-item .header{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  .sus-item .header .mac{font-weight:bold;color:var(--green)}
  .sus-item .details{display:none;margin-top:6px;padding-top:6px;border-top:1px solid var(--line);font-size:12px}
  .sus-item .details .row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:4px}
  .sus-item .details .label{color:var(--green-dim)}
  .sus-item .details .value{color:var(--green)}
  .empty{color:var(--green-dim);font-size:12px;padding:10px 0}
  .log-entry{font-size:11px;line-height:1.6;border-bottom:1px solid var(--line);padding:2px 0}
  .log-entry .ts{color:var(--green)}
  .log-container{max-height:400px;overflow-y:auto}
  .nav-links{display:flex;gap:10px;margin-top:10px;font-size:12px;flex-wrap:wrap}
  .nav-links a{color:var(--green-dim);text-decoration:none;border-bottom:1px dotted var(--green-dim)}
  .nav-links a:hover{color:var(--green)}
  .export-btn{background:var(--panel);border:1px solid var(--line);color:var(--green);padding:4px 8px;border-radius:2px;cursor:pointer;font-family:var(--mono);font-size:11px}
  .export-btn:hover{background:var(--green-dim);color:#000}
</style>
</head>
<body>
<div class="header">
  <h1>MILLIE v3 <span class="live" id="live"></span></h1>
  <span class="gps-status" id="gps-status">no fix</span>
  <span class="esp-status" id="esp-status"></span>
  <button class="export-btn" onclick="exportCSV()">📥 Export CSV</button>
</div>

<div class="stats">
  <div class="stat" id="stat-flock"><div class="n" id="n-flock">0</div><div class="l">FLOCK SEEN</div></div>
  <div class="stat"><div class="n" id="n-active">0</div><div class="l">IN RANGE</div></div>
  <div class="stat"><div class="n" id="n-last" style="font-size:16px;padding-top:6px">--:--</div><div class="l">LAST SCAN</div></div>
</div>

<div class="status-bar"><span>SCANNING <span class="on">●</span></span></div>

<div id="radar-wrap">
  <canvas id="radar" width="400" height="400"></canvas>
  <div class="legend">
    <span><span class="dot red"></span> Flock</span>
    <span><span class="dot yellow"></span> Consumer Cam</span>
    <span><span class="dot green"></span> WiFi</span>
    <span><span class="dot blue"></span> BLE</span>
  </div>
</div>

<div class="filter-bar">
  <label><input type="radio" name="source" value="both" checked onchange="applyFilters()"> Both</label>
  <label><input type="radio" name="source" value="esp32" onchange="applyFilters()"> CYD</label>
  <label><input type="radio" name="source" value="phone" onchange="applyFilters()"> Phone</label>
</div>

<div class="tab-bar">
  <button id="tab-all" class="active" onclick="switchTab('all')">All <span class="badge" id="badge-all">0</span></button>
  <button id="tab-sus" onclick="switchTab('sus')">Sus <span class="badge" id="badge-sus">0</span></button>
  <button id="tab-cam" onclick="switchTab('cam')">CAM <span class="badge" id="badge-cam">0</span></button>
  <button id="tab-wifi" onclick="switchTab('wifi')">WiFi <span class="badge" id="badge-wifi">0</span></button>
  <button id="tab-ble" onclick="switchTab('ble')">BLE <span class="badge" id="badge-ble">0</span></button>
  <button id="tab-logs" onclick="switchTab('logs')">Logs <span class="badge" id="badge-logs">0</span></button>
</div>

<div id="tab-all-content" class="tab-content active"><div id="all-list"></div></div>
<div id="tab-sus-content" class="tab-content"><div id="sus-list"></div></div>
<div id="tab-cam-content" class="tab-content"><div id="cam-list"></div></div>
<div id="tab-wifi-content" class="tab-content"><div id="wifi-list"></div></div>
<div id="tab-ble-content" class="tab-content"><div id="ble-list"></div></div>
<div id="tab-logs-content" class="tab-content"><div class="log-container" id="log-list"></div></div>

<div class="nav-links">
  <span style="color:var(--green-dim)">|</span>
  <a href="#" onclick="event.preventDefault();window.location.reload()">⟳ Refresh</a>
  <span style="color:var(--green-dim)">|</span>
  <a href="#" onclick="duress()" style="color:var(--red)">⚠️ Duress</a>
</div>

<script>
// ===== RADAR =====
const cv=document.getElementById('radar'), ctx=cv.getContext('2d');
const W=400,H=400,CX=W/2,CY=H/2,Rmax=W/2-20;
let sweep=0, scaleM=150, radarDevices=[];
function polar(bearingDeg, dist) {
  const r=Math.min(dist/scaleM*Rmax, Rmax);
  const a=(bearingDeg-90)*Math.PI/180;
  return [CX+r*Math.cos(a), CY+r*Math.sin(a)];
}
function drawGrid() {
  ctx.clearRect(0,0,W,H);
  ctx.strokeStyle='rgba(31,138,18,.5)'; ctx.fillStyle='rgba(57,255,20,.5)';
  ctx.lineWidth=1;
  for(let i=1;i<=4;i++) {
    ctx.beginPath(); ctx.arc(CX,CY,Rmax*i/4,0,7); ctx.stroke();
    ctx.font='9px monospace'; ctx.fillStyle='rgba(31,138,18,.9)';
    ctx.fillText(Math.round(scaleM*i/4)+'m', CX+4, CY-Rmax*i/4+11);
  }
  ctx.beginPath(); ctx.moveTo(CX,CY-Rmax); ctx.lineTo(CX,CY+Rmax);
  ctx.moveTo(CX-Rmax,CY); ctx.lineTo(CX+Rmax,CY); ctx.stroke();
  ctx.fillStyle='var(--green)'; ctx.font='11px monospace';
  ctx.fillText('N', CX-3, CY-Rmax-4);
  ctx.fillText('S', CX-3, CY+Rmax+13);
  ctx.fillText('E', CX+Rmax+4, CY+4);
  ctx.fillText('W', CX-Rmax-13, CY+4);
  ctx.fillStyle='var(--green)'; ctx.beginPath(); ctx.arc(CX,CY,4,0,7); ctx.fill();
}
function drawSweep() {
  const a=(sweep-90)*Math.PI/180;
  const g=ctx.createRadialGradient(CX,CY,0,CX,CY,Rmax);
  g.addColorStop(0,'rgba(57,255,20,.25)'); g.addColorStop(1,'rgba(57,255,20,0)');
  ctx.fillStyle=g;
  ctx.beginPath(); ctx.moveTo(CX,CY);
  ctx.arc(CX,CY,Rmax,a-0.35,a); ctx.closePath(); ctx.fill();
  ctx.strokeStyle='rgba(57,255,20,.6)'; ctx.beginPath();
  ctx.moveTo(CX,CY); ctx.lineTo(CX+Rmax*Math.cos(a), CY+Rmax*Math.sin(a)); ctx.stroke();
  sweep=(sweep+3)%360;
}
function drawRadarDevices() {
  radarDevices.forEach(d => {
    let col = d.color === 'red' ? '#ff3131' : d.color === 'blue' ? '#508cff' : d.color === 'yellow' ? '#ffd700' : '#39ff14';
    if (d.bearing != null && d.est_distance_m != null && d.est_distance_m > 0) {
      const [x,y]=polar(d.bearing, d.est_distance_m);
      ctx.fillStyle=col; ctx.beginPath(); ctx.arc(x,y,5,0,7); ctx.fill();
      ctx.strokeStyle=col; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(x,y,8,0,7); ctx.stroke();
    } else if (d.distance_m != null) {
      const r=Math.min(d.distance_m/scaleM*Rmax, Rmax);
      ctx.strokeStyle=col; ctx.setLineDash([3,5]); ctx.lineWidth=1.2;
      ctx.beginPath(); ctx.arc(CX,CY,r,0,7); ctx.stroke(); ctx.setLineDash([]);
    }
  });
}
function renderRadar() { drawGrid(); drawSweep(); drawRadarDevices(); requestAnimationFrame(renderRadar); }
renderRadar();

// ===== UI STATE =====
let currentTab='all', sourceFilter='both', deviceData={all:[], ble:[], flock:[], clusters:{}}, logEntries=[], lastRendered={};

function switchTab(tab) {
  currentTab=tab;
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'));
  document.getElementById(`tab-${tab}-content`).classList.add('active');
  document.querySelectorAll('.tab-bar button').forEach(b=>b.classList.remove('active'));
  document.getElementById(`tab-${tab}`).classList.add('active');
  renderCurrentView();
}

function applyFilters() {
  const radios=document.querySelectorAll('input[name="source"]');
  for(let r of radios) if(r.checked) sourceFilter=r.value;
  renderCurrentView();
}
function filterBySource(items) {
  if(sourceFilter==='both') return items;
  return items.filter(d=>d.source===sourceFilter);
}
function ago(ts) {
  const s=Math.round(Date.now()/1000-ts);
  return s<60?s+'s':Math.round(s/60)+'m';
}
function devHtml(d) {
  const isBle=d.kind==='ble';
  const conf=d.flock_conf;
  const cls=isBle?'ble':(conf==='H'||conf==='HIGH')?'flock':(conf==='M'||conf==='MEDIUM')?'flock':'';
  const tag=isBle?'BLE':(conf==='H'||conf==='HIGH')?'FLK':(conf==='M'||conf==='MEDIUM')?'flk':'wifi';
  const label=d.name||d.ssid||(d.hidden?'<hidden>':'');
  const loc=(d.bearing!=null&&d.dist!=null)?`brg ${Math.round(d.bearing)}° ~${Math.round(d.dist)}m`:'';
  const distft=d.dist_ft!=null?`~${d.dist_ft}ft`:'';
  const src=d.source==='phone'?'phone':'CYD';
  let flags='';
  if(d.hidden) flags+='<span class="flag hidden">🔒</span>';
  if(d.is_camera) flags+='<span class="flag camera">📷</span>';
  if(d.is_police) flags+='<span class="flag police">👮</span>';
  if(d.is_moving) flags+='<span class="flag moving">🚗</span>';
  if(d.is_anomaly) flags+='<span class="flag anomaly">⚠️</span>';
  if(d.is_router) flags+='<span class="flag router">📶</span>';
  let rssi_hist = d.rssi_history || [];
  let timeline = rssi_hist.slice(-5).map(r => `${r}`).join(' → ');
  return `<div class="dev ${cls}"><span class="tag ${cls}">${tag}</span> ${d.mac} ${label} ${flags}<div class="m">${src} · rssi ${d.rssi??'?'} ${distft} · seen ${d.sightings||1}× · ${d.last_seen?ago(d.last_seen)+' ago':''} ${loc}</div><div class="m" style="font-size:10px;color:var(--grey);">RSSI: ${timeline}</div></div>`;
}
function toggleSus(mac) {
  const details=document.getElementById('sus-details-'+mac.replace(/:/g,''));
  if(details) details.style.display=details.style.display==='block'?'none':'block';
}
function susItemHtml(d) {
  const mac=d.mac;
  const flags=[];
  if(d.hidden) flags.push('🔒 Hidden');
  if(d.is_camera) flags.push('📷 Camera');
  if(d.is_police) flags.push('👮 Police');
  if(d.is_moving) flags.push('🚗 Moving');
  if(d.is_anomaly) flags.push('⚠️ RSSI Anomaly');
  if(d.is_router) flags.push('📶 Router');
  const flagStr=flags.join(' · ');
  const loc=(d.bearing!=null&&d.dist!=null)?`brg ${Math.round(d.bearing)}° ~${Math.round(d.dist)}m`:'';
  const distft=d.dist_ft!=null?`~${d.dist_ft}ft`:'';
  const src=d.source==='phone'?'phone':'CYD';
  const detailsId='sus-details-'+mac.replace(/:/g,'');
  let rssi_hist = d.rssi_history || [];
  let timeline = rssi_hist.slice(-5).map(r => `${r}`).join(' → ');
  return `<div class="sus-item" onclick="toggleSus('${mac}')">
    <div class="header"><span class="mac">${mac}</span><span style="color:var(--green-dim);font-size:11px;">${flags.length} indicators</span><span style="font-size:11px;color:var(--amber);">${flagStr}</span></div>
    <div class="details" id="${detailsId}">
      <div class="row"><span class="label">Source:</span><span class="value">${src}</span></div>
      <div class="row"><span class="label">RSSI:</span><span class="value">${d.rssi??'?'}</span></div>
      <div class="row"><span class="label">Distance:</span><span class="value">${distft}</span></div>
      <div class="row"><span class="label">Sightings:</span><span class="value">${d.sightings||1}×</span></div>
      <div class="row"><span class="label">Last seen:</span><span class="value">${d.last_seen?ago(d.last_seen)+' ago':''}</span></div>
      ${loc?`<div class="row"><span class="label">Bearing/Dist:</span><span class="value">${loc}</span></div>`:''}
      <div class="row"><span class="label">RSSI History:</span><span class="value">${timeline}</span></div>
      <div class="row"><span class="label">Flags:</span><span class="value">${flagStr}</span></div>
    </div>
  </div>`;
}
function renderCurrentView() {
  if(currentTab==='logs') {
    const container=document.getElementById('log-list');
    if(!container) return;
    const html=logEntries.length?logEntries.map(e=>`<div class="log-entry"><span class="ts">${e.t}</span>  ${e.msg}</div>`).join(''):'<div class="empty">No logs yet.</div>';
    if(lastRendered['logs']!==html){container.innerHTML=html;lastRendered['logs']=html;}
    return;
  }
  const data=deviceData;
  const allDevs=data.all||[];
  const filteredAll=filterBySource(allDevs);
  const filteredBle=filterBySource(data.ble||[]);
  const filteredWifi=filterBySource(allDevs.filter(d=>d.kind==='wifi'));
  const filteredCam=filterBySource(allDevs.filter(d=>d.is_camera===true));
  const filteredSus=filterBySource(allDevs.filter(d=>d.suspicious===true));
  const container=document.getElementById(getContainerId(currentTab));
  if(!container) return;
  let newHtml='';
  if(currentTab==='sus') {
    if(!filteredSus.length){newHtml='<div class="empty">No suspicious devices (need 2+ indicators).</div>';}
    else {
      filteredSus.sort((a,b)=>{const ca=(a.hidden?1:0)+(a.is_camera?1:0)+(a.is_police?1:0)+(a.is_moving?1:0)+(a.is_anomaly?1:0);const cb=(b.hidden?1:0)+(b.is_camera?1:0)+(b.is_police?1:0)+(b.is_moving?1:0)+(b.is_anomaly?1:0);if(ca!==cb)return cb-ca;return (b.sightings||0)-(a.sightings||0);});
      newHtml=filteredSus.map(d=>susItemHtml(d)).join('');
    }
  } else {
    let list=[];
    if(currentTab==='all') list=filteredAll;
    else if(currentTab==='cam') list=filteredCam;
    else if(currentTab==='wifi') list=filteredWifi;
    else if(currentTab==='ble') list=filteredBle;
    list.sort((a,b)=>{const sa=a.sightings||0;const sb=b.sightings||0;if(sa!==sb)return sb-sa;return (b.last_seen||0)-(a.last_seen||0);});
    newHtml=list.length?list.map(devHtml).join(''):'<div class="empty">No devices in this view.</div>';
  }
  const key=currentTab+'_'+sourceFilter;
  if(lastRendered[key]!==newHtml){container.innerHTML=newHtml;lastRendered[key]=newHtml;}
}
function getContainerId(tab){const map={all:'all-list',sus:'sus-list',cam:'cam-list',wifi:'wifi-list',ble:'ble-list',logs:'log-list'};return map[tab]||'';}
function updateBadges(){const data=deviceData;const allDevs=data.all||[];document.getElementById('badge-all').textContent=allDevs.length;document.getElementById('badge-sus').textContent=allDevs.filter(d=>d.suspicious===true).length;document.getElementById('badge-cam').textContent=allDevs.filter(d=>d.is_camera===true).length;document.getElementById('badge-wifi').textContent=allDevs.filter(d=>d.kind==='wifi').length;document.getElementById('badge-ble').textContent=(data.ble||[]).length;document.getElementById('badge-logs').textContent=logEntries.length;}
async function pollState(){try{const s=await(await fetch('/api/state')).json();document.getElementById('live').classList.add('on');const gpsEl=document.getElementById('gps-status');if(s.gps){gpsEl.textContent=`lat ${s.gps.lat.toFixed(4)}, lon ${s.gps.lon.toFixed(4)}`;}else{gpsEl.textContent='no fix';}const espEl=document.getElementById('esp-status');if(s.usb_enabled){espEl.className='esp-status on';espEl.textContent=s.esp_alive?`ESP ${s.esp_alive} ch${s.esp_channel||'?'}`:'waiting…';}else{espEl.className='esp-status';espEl.textContent='';}logEntries=s.log||[];if(currentTab==='logs')renderCurrentView();updateBadges();}catch(e){}}
async function pollRadar(){try{const d=await(await fetch('/api/radar')).json();radarDevices=d.devices||[];let maxD=60;radarDevices.forEach(dev=>{const dd=dev.est_distance_m??dev.distance_m;if(dd&&dd>maxD)maxD=dd;});scaleM=Math.ceil(maxD*1.2/50)*50;if(scaleM<50)scaleM=50;}catch(e){}}
async function loadDevices(){try{const data=await(await fetch('/api/devices')).json();deviceData=data;updateBadges();renderCurrentView();}catch(e){}}
function exportCSV(){window.location.href='/api/export';}
function duress(){if(confirm('⚠️ This will wipe ALL data. Continue?')){fetch('/api/duress',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:prompt('Enter duress PIN:')})}).then(r=>r.json()).then(d=>alert(d.status||'Error'));}}
setInterval(pollState,3000);setInterval(pollRadar,5000);setInterval(loadDevices,15000);
pollState();pollRadar();loadDevices();
document.addEventListener('DOMContentLoaded',()=>{renderCurrentView();});
</script>
</body>
</html>
DASHV3

# ---- Install dependencies ----
echo "== Installing dependencies =="
pkg update -y
pkg install -y python termux-api libusb || { echo "core deps failed"; exit 1; }
pip install flask pyusb requests requests-cache --break-system-packages 2>/dev/null && echo "Dependencies installed"
echo "== Done =="
cd "$ROOT"
python -m millie doctor
echo
echo "Now run:  bash ~/millie/run_millie.sh"
echo "Dashboard: http://127.0.0.1:8770"
