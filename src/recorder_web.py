#!/usr/bin/env python3
import os
import re
import json
import time
import shutil
import socket
import subprocess
import threading
import select
import array
import math
from urllib.parse import urlparse, unquote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# パス設定（環境変数またはデフォルト実機パス）
RECORDINGS_DIR = os.environ.get("RECORDINGS_DIR", "/home/pi/recordings")
HTML_FILE = os.environ.get("HTML_FILE", "/home/pi/index.html")
if not os.path.isfile(HTML_FILE):
    # プロジェクト内相対パスのフォールバック
    local_html = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "html", "index.html")
    if os.path.isfile(local_html):
        HTML_FILE = local_html

STATIC_DIR = os.path.dirname(HTML_FILE)
PORT = int(os.environ.get("PORT", "8080"))

STATIC_FILES = {
    "/manifest.json": "application/manifest+json",
    "/icon-192.png": "image/png",
    "/icon-512.png": "image/png",
    "/apple-touch-icon.png": "image/png",
    "/favicon.png": "image/png",
    "/favicon.ico": "image/x-icon"
}

os.makedirs(RECORDINGS_DIR, exist_ok=True)

# State
state_lock = threading.Lock()
current_recorder = None
current_filename = None
start_timestamp = 0

def load_bluetooth_name():
    """
    Bluetooth接続名を起動時に1回だけ取得・キャッシュする（常時負荷ゼロ）。
    1. /etc/machine-info (PRETTY_HOSTNAME)
    2. bluetoothctl show (Name)
    3. システムホスト名
    """
    if os.path.isfile("/etc/machine-info"):
        try:
            with open("/etc/machine-info", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_HOSTNAME="):
                        val = line.split("=", 1)[1].strip().strip('"\'')
                        if val:
                            return val
        except Exception:
            pass

    try:
        res = subprocess.run(["bluetoothctl", "show"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1)
        for line in res.stdout.splitlines():
            if "Name:" in line:
                val = line.split("Name:", 1)[1].strip()
                if val:
                    return val
    except Exception:
        pass

    try:
        return socket.gethostname()
    except Exception:
        return "raspberrypi"

def ensure_bt_loopback():
    """
    PipeWire環境下で、スマホのUSB音声（UAC2）をBluetoothイヤホンへ流す
    pw-loopback プロセスが常駐していることを保証する。
    """
    try:
        check = subprocess.run(["pgrep", "-f", "pw-loopback"], stdout=subprocess.DEVNULL)
        if check.returncode != 0:
            subprocess.Popen(
                ["pw-loopback", "-C", "alsa_input.platform-20980000.usb.stereo-fallback"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception:
        pass
CACHED_BT_NAME = load_bluetooth_name()
CACHED_HOSTNAME = socket.gethostname()
CACHED_FQDN = f"{CACHED_HOSTNAME}.local"

def load_device_model():
    """
    Raspberry Piの公式ハードウェア機種名をデバイスツリーから起動時に1度だけ取得・キャッシュ。
    例: "Raspberry Pi Zero W Rev 1.1" -> "Raspberry Pi Zero W"
    """
    for p in ["/proc/device-tree/model", "/sys/firmware/devicetree/base/model"]:
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    raw = f.read().decode("utf-8", errors="ignore").strip("\x00\r\n ")
                    if raw:
                        cleaned = re.sub(r"\s+Rev\s+[\d\.]+", "", raw)
                        return cleaned
            except Exception:
                pass
    return "Raspberry Pi"

CACHED_MODEL_NAME = load_device_model()

def get_wifi_active():
    """
    Linux sysfs を直接読み取り、超軽量（1ms未満・CPU負荷ゼロ）でWi-Fi有効状態を判定する。
    """
    try:
        with open("/sys/class/net/wlan0/operstate", "r") as f:
            state = f.read().strip()
            return state != "down"
    except Exception:
        return False

_cached_sample_rate = 48000
_cached_sample_rate_time = 0

def detect_sample_rate(force=False):
    """
    スマホ（USBホスト）が要求しているサンプリング周波数をALSA情報から高速検知する。
    毎回のファイルアクセス負荷を避けるため、待機中は3秒キャッシュを活用する。
    """
    global _cached_sample_rate, _cached_sample_rate_time
    now = time.time()
    if not force and (now - _cached_sample_rate_time < 3.0):
        return _cached_sample_rate

    paths = [
        "/proc/asound/UAC1Gadget/pcm0c/sub0/hw_params",
        "/proc/asound/UAC2Gadget/pcm0c/sub0/hw_params",
        "/proc/asound/card1/pcm0c/sub0/hw_params",
        "/proc/asound/card2/pcm0c/sub0/hw_params"
    ]
    detected = 48000
    for p in paths:
        if os.path.isfile(p):
            try:
                with open(p, "r") as f:
                    content = f.read()
                    match = re.search(r"rate:\s*(\d+)", content)
                    if match:
                        val = int(match.group(1))
                        if val in (44100, 48000):
                            detected = val
                            break
            except Exception:
                pass

    _cached_sample_rate = detected
    _cached_sample_rate_time = now
    return detected

def get_audio_device():
    """
    UAC1GadgetまたはUAC2Gadgetのキャプチャデバイス名を自動検出する。
    """
    for name in ["UAC1Gadget", "UAC2Gadget"]:
        if os.path.exists(f"/proc/asound/{name}"):
            return f"hw:{name},0"
    return "hw:1,0"


def get_wav_info_and_repair(filepath, size):
    """
    1回のファイルオープンでヘッダー検証・自動修復・メタデータ取得を一括実行。
    microSDへの多重アクセスを排除し、ファイルオープン回数を半減させる。
    """
    if size < 44:
        return 44100, 0, False

    was_repaired = False
    rate = 44100
    byte_rate = 176400
    try:
        with open(filepath, "r+b") as f:
            header = f.read(44)
            if len(header) >= 44 and header[0:4] == b"RIFF" and header[8:12] == b"WAVE":
                rate = int.from_bytes(header[24:28], "little")
                byte_rate = int.from_bytes(header[28:32], "little")
                riff_size = int.from_bytes(header[4:8], "little")

                data_pos = header.find(b"data")
                if data_pos != -1 and data_pos + 8 <= 44:
                    data_size = int.from_bytes(header[data_pos+4:data_pos+8], "little")
                    expected_data_size = size - (data_pos + 8)
                    expected_riff_size = size - 8

                    # 抜去・電源断によるサイズ不整合の自動修復
                    if riff_size != expected_riff_size or data_size != expected_data_size:
                        f.seek(4)
                        f.write(expected_riff_size.to_bytes(4, "little"))
                        f.seek(data_pos + 4)
                        f.write(expected_data_size.to_bytes(4, "little"))
                        f.flush()
                        was_repaired = True
    except Exception as e:
        print(f"Error inspecting/repairing {filepath}: {e}")

    duration_sec = max(0, int((size - 44) / byte_rate)) if byte_rate > 0 else 0
    return rate, duration_sec, was_repaired

def trigger_usb_reconnect():
    """
    給電（5V）を維持したまま、DWC2 USB通信線（UDC）をソフト切断・再バインドし、
    スマホに「物理的なケーブル抜き差し」と全く同じ再エニュメレーション信号を強制送信する。
    """
    try:
        # 1. 物理切断: DWC2 コントローラの D+ プルアップ抵抗を切断 (DCTL.SftDiscon=1)
        # ホスト（スマートフォン・PC）は SE0 を検知し、「物理的にケーブルが抜かれた」と確実に判定
        subprocess.run(["sudo", "sh", "-c", 'echo disconnect > /sys/class/udc/20980000.usb/soft_connect'], check=False, timeout=3)
        time.sleep(3.0)
        # 2. 物理再接続: D+ プルアップ抵抗を再投入 (DCTL.SftDiscon=0)
        # ホスト（スマートフォン・PC）が「新規USBデバイス挿入」を検知し、強制再エニュメレーションを開始
        res = subprocess.run(["sudo", "sh", "-c", 'echo connect > /sys/class/udc/20980000.usb/soft_connect'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if res.returncode == 0:
            return True, "USBサウンドデバイスの再接続信号を送信しました（ホスト側で再認識中）"
        return False, f"再接続失敗: {res.stderr.strip()}"
    except Exception as e:
        return False, str(e)

# --- Wi-Fi & Bluetooth 管理ヘルパー ---

def get_wifi_details():
    """
    Wi-Fiの接続状況、IPアドレス、現在接続中のSSID、AP動作中かどうかを取得
    """
    info = {
        "active": get_wifi_active(),
        "connected": False,
        "ssid": None,
        "mode": "Client",
        "ip": None,
        "signal": 0
    }
    
    # IPアドレスの取得
    try:
        res = subprocess.run(["ip", "-4", "addr", "show", "wlan0"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip_part = line.split()[1].split("/")[0]
                info["ip"] = ip_part
                break
    except Exception:
        pass

    # nmcli によるアクティブ接続の取得
    try:
        res = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2
        )
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[2] == "wlan0":
                info["connected"] = True
                info["ssid"] = parts[0]
                if "hotspot" in parts[0].lower() or "ap" in parts[0].lower():
                    info["mode"] = "AP (Hotspot)"
                break
    except Exception:
        pass

    # シグナル強度の取得
    if info["connected"] and info["mode"] == "Client":
        try:
            res = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2
            )
            for line in res.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == "yes":
                    try:
                        info["signal"] = int(parts[2])
                    except ValueError:
                        info["signal"] = 0
                    break
        except Exception:
            pass

    return info

def scan_wifi_networks():
    """
    周囲のWi-Fiネットワークをスキャン（重複除外、信号強度順）
    """
    networks = []
    seen_ssids = set()
    try:
        res = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,BARS", "dev", "wifi", "list", "--rescan", "yes"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=6
        )
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                ssid = parts[0].strip()
                if not ssid or ssid in seen_ssids:
                    continue
                seen_ssids.add(ssid)
                signal = int(parts[1]) if parts[1].isdigit() else 0
                security = parts[2].strip() if parts[2] else "Open"
                bars = parts[3].strip() if len(parts) > 3 else ""
                networks.append({
                    "ssid": ssid,
                    "signal": signal,
                    "security": security,
                    "bars": bars
                })
        networks.sort(key=lambda x: x["signal"], reverse=True)
    except Exception as e:
        print(f"Wi-Fi scan error: {e}")
    return networks

def get_saved_wifi_connections():
    """
    NetworkManagerに保存されているWi-Fiプロファイル一覧を取得
    """
    saved = []
    try:
        res = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT-PRIORITY", "connection", "show"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2
        )
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[2] == "802-11-wireless":
                prio = int(parts[3]) if len(parts) > 3 and parts[3].lstrip("-").isdigit() else 0
                saved.append({
                    "name": parts[0],
                    "uuid": parts[1],
                    "priority": prio
                })
        saved.sort(key=lambda x: x["priority"], reverse=True)
    except Exception as e:
        print(f"Error getting saved Wi-Fi connections: {e}")
    return saved

def add_wifi_connection(ssid, password=None, priority=50):
    """
    新しいWi-Fi接続（テザリング等）をNetworkManagerに追加・保存
    """
    if not ssid or not ssid.strip():
        return False, "SSIDが空です"
    ssid = ssid.strip()
    try:
        # 既存に同名プロファイルがあれば削除
        subprocess.run(["sudo", "nmcli", "connection", "delete", "id", ssid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # コマンド構築
        cmd = ["sudo", "nmcli", "connection", "add", "type", "wifi", "con-name", ssid, "ifname", "wlan0", "ssid", ssid]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if res.returncode != 0:
            return False, f"プロファイル作成失敗: {res.stderr.strip()}"
        
        # パスワード設定（WPA-PSK）
        if password and password.strip():
            pw = password.strip()
            subprocess.run(["sudo", "nmcli", "connection", "modify", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", pw], check=True, timeout=3)
        
        # 優先度設定
        subprocess.run(["sudo", "nmcli", "connection", "modify", ssid, "connection.autoconnect-priority", str(priority), "connection.autoconnect", "yes"], check=True, timeout=3)
        
        # バックグラウンドで接続試行
        def try_connect():
            try:
                subprocess.run(["sudo", "nmcli", "connection", "up", ssid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except Exception:
                pass
        threading.Thread(target=try_connect, daemon=True).start()

        return True, f"Wi-Fi「{ssid}」を登録しました（接続試行中）"
    except Exception as e:
        return False, str(e)

def delete_wifi_connection(con_name):
    """
    保存済みWi-Fiプロファイルを削除（初期APプロファイルは保護）
    """
    if not con_name:
        return False, "プロファイル名が指定されていません"
    if con_name == "Pi-Recorder-Hotspot":
        return False, "「Pi-Recorder-Hotspot」は緊急フォールバック用の保護プロファイルのため削除できません"
    try:
        res = subprocess.run(["sudo", "nmcli", "connection", "delete", "id", con_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        if res.returncode == 0:
            return True, f"プロファイル「{con_name}」を削除しました"
        return False, res.stderr.strip()
    except Exception as e:
        return False, str(e)

def restore_hotspot_connection():
    """
    緊急アクセス用APプロファイル（Pi-Recorder-Hotspot）を再作成・復元
    """
    try:
        subprocess.run(["sudo", "nmcli", "connection", "delete", "id", "Pi-Recorder-Hotspot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cmds = [
            ["sudo", "nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0", "con-name", "Pi-Recorder-Hotspot", "autoconnect", "yes", "ssid", "Pi-Recorder-Hotspot"],
            ["sudo", "nmcli", "connection", "modify", "Pi-Recorder-Hotspot", "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg", "ipv4.method", "shared"],
            ["sudo", "nmcli", "connection", "modify", "Pi-Recorder-Hotspot", "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", "recorder1234"],
            ["sudo", "nmcli", "connection", "modify", "Pi-Recorder-Hotspot", "connection.autoconnect-priority", "-10"],
            ["sudo", "nmcli", "connection", "modify", "Pi-Recorder-Hotspot", "ipv4.addresses", "192.168.4.1/24"]
        ]
        for c in cmds:
            subprocess.run(c, check=True, timeout=5)
        return True, "初期AP「Pi-Recorder-Hotspot (192.168.4.1)」を正常に復元・再作成しました（フォールバック優先度: -10）"
    except Exception as e:
        return False, f"復元失敗: {e}"

# --- Bluetooth ヘルパー ---

def get_bt_status():
    """
    Bluetoothの状態および接続中の機器情報を取得
    """
    status = {
        "powered": False,
        "name": CACHED_BT_NAME,
        "connected_device": None
    }
    # hci0がDOWNの場合の自己修復
    try:
        res = subprocess.run(["bluetoothctl", "show"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2)
        has_powered = False
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("Powered:"):
                status["powered"] = ("yes" in line.lower())
                has_powered = True
                break
        if not status["powered"]:
            # hci0をUPにしてpower onを試行
            subprocess.run(["hciconfig", "hci0", "up"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            subprocess.run(["bluetoothctl", "power", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            status["powered"] = True
    except Exception:
        pass

    try:
        # 接続中デバイスの取得
        res = subprocess.run(["bluetoothctl", "info"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2)
        dev_name = None
        dev_mac = None
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("Device "):
                parts = line.split()
                if len(parts) >= 2:
                    dev_mac = parts[1]
            elif line.startswith("Name:"):
                dev_name = line.split(":", 1)[1].strip()
            elif line.startswith("Connected:") and "yes" in line.lower():
                if dev_mac:
                    status["connected_device"] = {
                        "mac": dev_mac,
                        "name": dev_name or dev_mac
                    }
                    ensure_bt_loopback()
                    break
    except Exception:
        pass

    return status

def scan_bt_devices(timeout_sec=5):
    """
    周囲のBluetoothデバイスをスキャンし、機器名・エイリアス・オーディオ種別を解決して返す
    """
    devices = []
    seen_macs = set()
    try:
        # 1. スキャンを実行（タイムアウト付き）
        subprocess.run(
            ["bluetoothctl", "--timeout", str(timeout_sec), "scan", "on"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout_sec + 2
        )
        
        # 2. 検出されたデバイス一覧を取得
        res = subprocess.run(
            ["bluetoothctl", "devices"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2
        )
        
        raw_list = []
        for line in res.stdout.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 2 and parts[0] == "Device":
                mac = parts[1].strip()
                name = parts[2].strip() if len(parts) >= 3 else ""
                if mac and mac not in seen_macs:
                    seen_macs.add(mac)
                    raw_list.append((mac, name))

        # 3. 各デバイスの詳細情報（Name / Alias / Icon / Class）を解決
        for mac, initial_name in raw_list:
            resolved_name = initial_name
            is_audio = False
            icon = ""
            
            # MACアドレス形式チェック（名前がMACアドレスと同じ文字列の場合は未解決とみなす）
            mac_norm = mac.replace(":", "-").upper()
            name_norm = resolved_name.replace(":", "-").upper()
            needs_lookup = (not resolved_name or name_norm == mac_norm)
            
            # 詳細情報の問い合わせ（キャッシュされた情報から即座に返る）
            try:
                info_res = subprocess.run(
                    ["bluetoothctl", "info", mac],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.5
                )
                info_name = ""
                info_alias = ""
                for iline in info_res.stdout.splitlines():
                    iline_str = iline.strip()
                    if iline_str.startswith("Name:"):
                        info_name = iline_str.split(":", 1)[1].strip()
                    elif iline_str.startswith("Alias:"):
                        info_alias = iline_str.split(":", 1)[1].strip()
                    elif iline_str.startswith("Icon:"):
                        icon = iline_str.split(":", 1)[1].strip()
                    elif iline_str.startswith("Class:"):
                        class_val = iline_str.split(":", 1)[1].strip()
                        # オーディオ関連クラス（0x200000番台メジャーサービス・クラス）
                        if any(c in class_val.lower() for c in ["0x24", "0x20", "0x26"]):
                            is_audio = True

                # 名前の確定（Alias > Name > initial_name）
                best_name = info_alias or info_name
                if best_name and best_name.replace(":", "-").upper() != mac_norm:
                    resolved_name = best_name

                # アイコンからのオーディオ判定
                if "audio" in icon.lower() or "headphone" in icon.lower() or "headset" in icon.lower():
                    is_audio = True

            except Exception:
                pass

            if not resolved_name or resolved_name.replace(":", "-").upper() == mac_norm:
                resolved_name = "名称未設定 (Bluetooth機器)"

            # オーディオ名キーワード判定（アイコン情報がない場合のフォールバック）
            audio_keywords = ["buds", "earphone", "headset", "headphone", "audio", "sound", "wf-", "wh-", "airpods", "freebuds", "wireless"]
            if any(kw in resolved_name.lower() for kw in audio_keywords):
                is_audio = True

            devices.append({
                "mac": mac,
                "name": resolved_name,
                "is_audio": is_audio,
                "has_name": (resolved_name != "名称未設定 (Bluetooth機器)")
            })

        # 4. 優先度ソート: オーディオ機器かつ名称あり(0) > 名称あり(1) > オーディオ機器(2) > 名称未設定(3)
        def sort_key(d):
            if d["is_audio"] and d["has_name"]:
                return 0
            if d["has_name"]:
                return 1
            if d["is_audio"]:
                return 2
            return 3

        devices.sort(key=sort_key)

    except Exception as e:
        print(f"Bluetooth scan error: {e}")
    return devices

def connect_bt_device(mac):
    """
    Bluetoothオーディオ機器をペアリング・接続
    """
    if not mac:
        return False, "MACアドレスが空です"
    try:
        # 1. 信頼済み設定
        subprocess.run(["bluetoothctl", "trust", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        
        # 2. ペアリング試行（非オーディオ機器や未応答で止まらないようソフトハンドリング）
        try:
            pair_res = subprocess.run(["bluetoothctl", "pair", mac], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=6)
        except subprocess.TimeoutExpired:
            # タイムアウトしても connect を試みる
            pass

        # 3. オーディオ接続試行
        res = subprocess.run(["bluetoothctl", "connect", mac], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
        combined_out = (res.stdout + " " + res.stderr).strip()
        
        if "Successful" in res.stdout or "Connection successful" in res.stdout or res.returncode == 0:
            ensure_bt_loopback()
            return True, f"接続に成功しました ({mac})"
        
        if "Failed to connect" in combined_out:
            if "org.bluez.Error.Failed" in combined_out or "not available" in combined_out.lower():
                return False, "接続できませんでした（オーディオ非対応機器であるか、プロファイルが見つかりません）"
            return False, f"接続失敗: {combined_out}"
        
        return False, combined_out or "接続に失敗しました（イヤホンがペアリング待機状態かご確認ください）"
    except subprocess.TimeoutExpired:
        return False, "応答がタイムアウトしました。イヤホンがペアリングモード（LED点滅中）になっているかご確認ください"
    except Exception as e:
        return False, f"エラー: {e}"

def disconnect_bt_device(mac=None):
    """
    Bluetooth機器を切断
    """
    try:
        cmd = ["bluetoothctl", "disconnect"]
        if mac:
            cmd.append(mac)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        return True, "切断しました"
    except Exception as e:
        return False, str(e)

def get_paired_bt_devices():
    """
    ペアリング済み（保存済み）のBluetooth機器一覧を取得
    """
    devices = []
    try:
        res = subprocess.run(["bluetoothctl", "paired-devices"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2)
        for line in res.stdout.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 2 and parts[0] == "Device":
                mac = parts[1].strip()
                name = parts[2].strip() if len(parts) >= 3 else ""
                
                resolved_name = name
                is_audio = False
                try:
                    info_res = subprocess.run(["bluetoothctl", "info", mac], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.5)
                    info_alias = ""
                    info_name = ""
                    icon = ""
                    for iline in info_res.stdout.splitlines():
                        iline_str = iline.strip()
                        if iline_str.startswith("Name:"):
                            info_name = iline_str.split(":", 1)[1].strip()
                        elif iline_str.startswith("Alias:"):
                            info_alias = iline_str.split(":", 1)[1].strip()
                        elif iline_str.startswith("Icon:"):
                            icon = iline_str.split(":", 1)[1].strip()
                        elif iline_str.startswith("Class:"):
                            c_val = iline_str.split(":", 1)[1].strip()
                            if any(c in c_val.lower() for c in ["0x24", "0x20", "0x26"]):
                                is_audio = True
                    best_name = info_alias or info_name
                    if best_name:
                        resolved_name = best_name
                    if "audio" in icon.lower() or "headphone" in icon.lower() or "headset" in icon.lower():
                        is_audio = True
                except Exception:
                    pass
                
                if not resolved_name or resolved_name.replace(":", "-").upper() == mac.replace(":", "-").upper():
                    resolved_name = "名称未設定 (Bluetooth機器)"
                
                devices.append({
                    "mac": mac,
                    "name": resolved_name,
                    "is_audio": is_audio
                })
    except Exception as e:
        print(f"Error fetching paired devices: {e}")
    return devices

def remove_bt_device(mac):
    """
    Bluetooth機器のペアリング情報を完全削除 (アンペア)
    """
    if not mac:
        return False, "MACアドレスが空です"
    try:
        # 切断を事前試行
        try:
            subprocess.run(["bluetoothctl", "disconnect", mac], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except Exception:
            pass
        # ペアリング削除
        res = subprocess.run(["bluetoothctl", "remove", mac], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        combined = (res.stdout + " " + res.stderr).lower()
        if res.returncode == 0 or "successful" in combined or "not available" in combined:
            return True, f"ペアリング情報を削除しました ({mac})"
        return True, "ペアリング情報を削除しました"
    except Exception as e:
        return False, f"削除エラー: {e}"

def format_duration(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}時間{m:02d}分{s:02d}秒"
    else:
        return f"{m:02d}分{s:02d}秒"

def get_disk_info():
    try:
        total, used, free = shutil.disk_usage(RECORDINGS_DIR)
        free_gb = free / (1024 * 1024 * 1024)
        # 44100 * 2 * 2 = 176,400 bytes/sec => ~635 MB/hour
        hours_left = free / (176400 * 3600)
        return round(free_gb, 2), round(hours_left, 1)
    except Exception:
        return 0.0, 0.0

class ContinuousRecorder:
    """
    スマホ側の無音停止・アプリ切り替え・待機状態でも落ちずに無音パディング（ゼロフィル）を
    行い、再生が再開したらシームレスにデジタル音声を結合して1つの完全なWAVファイルを記録する
    高信頼性録音エンジン。
    """
    def __init__(self, filepath, device, sample_rate=48000, channels=2, bytes_per_sample=2):
        self.filepath = filepath
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.bytes_per_sample = bytes_per_sample
        self.bytes_per_sec = sample_rate * channels * bytes_per_sample
        self.running = False
        self.thread = None
        self.proc = None
        self.total_bytes_written = 0
        self.start_time = 0
        self.max_peak = 0  # リアルタイムピーク保持（メモリ4バイト）

    def start(self):
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def is_active(self):
        return self.running and (self.thread is not None and self.thread.is_alive())

    def stop(self):
        self.running = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1)
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self._finalize_wav()

    def _write_wav_header(self, f):
        header = bytearray(44)
        header[0:4] = b"RIFF"
        header[8:12] = b"WAVE"
        header[12:16] = b"fmt "
        header[16:20] = (16).to_bytes(4, "little")
        header[20:22] = (1).to_bytes(2, "little")
        header[22:24] = (self.channels).to_bytes(2, "little")
        header[24:28] = (self.sample_rate).to_bytes(4, "little")
        header[28:32] = (self.bytes_per_sec).to_bytes(4, "little")
        header[32:34] = (self.channels * self.bytes_per_sample).to_bytes(2, "little")
        header[34:36] = (self.bytes_per_sample * 8).to_bytes(2, "little")
        header[36:40] = b"data"
        f.write(header)
        f.flush()

    def _finalize_wav(self):
        try:
            if os.path.isfile(self.filepath):
                size = os.path.getsize(self.filepath)
                if size >= 44:
                    data_size = size - 44
                    riff_size = size - 8
                    with open(self.filepath, "r+b") as f:
                        f.seek(4)
                        f.write(riff_size.to_bytes(4, "little"))
                        f.seek(40)
                        f.write(data_size.to_bytes(4, "little"))
                        f.flush()

                # メタデータ算出（ノイズフロア判定＆ゼロ除算・異常増幅ガード）
                peak = self.max_peak
                if peak < 100:  # ノイズフロア以下（ほぼ完全無音）
                    gain = 1.0
                    peak_db = -99.0
                else:
                    target_amp = 30900.0  # -0.5 dBFS
                    gain = round(min(8.0, target_amp / peak), 2)
                    peak_db = round(20 * math.log10(peak / 32767.0), 1)

                meta = {
                    "peak": peak,
                    "peak_db": peak_db,
                    "recommend_gain": gain
                }
                meta_path = self.filepath.rsplit(".", 1)[0] + ".meta.json"
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f)
        except Exception as e:
            print(f"Error finalizing WAV: {e}")

    def _run(self):
        bytes_per_frame = self.channels * self.bytes_per_sample  # 4 bytes (16bit stereo)
        with open(self.filepath, "wb") as f:
            self._write_wav_header(f)
            self.total_bytes_written = 0
            
            chunk_time = 0.05  # 50ms
            chunk_bytes = (int(self.bytes_per_sec * chunk_time) // bytes_per_frame) * bytes_per_frame
            leftover = bytearray()
            
            use_pw = (shutil.which("pw-record") is not None)
            while self.running:
                try:
                    if use_pw:
                        cmd = [
                            "pw-record",
                            "--target", "alsa_input.platform-20980000.usb.stereo-fallback",
                            "--format", "s16",
                            "--rate", str(self.sample_rate),
                            "--channels", str(self.channels),
                            "-"
                        ]
                    else:
                        cmd = [
                            "arecord",
                            "-D", self.device,
                            "-f", "S16_LE",
                            "-r", str(self.sample_rate),
                            "-c", str(self.channels),
                            "-t", "raw",
                            "-q",
                            "--buffer-size=19200",
                            "--period-size=4800"
                        ]
                    try:
                        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                        if self.proc and self.proc.stdout:
                            os.set_blocking(self.proc.stdout.fileno(), False)
                    except Exception:
                        self.proc = None

                    if self.proc and self.proc.stdout:
                        fd = self.proc.stdout.fileno()
                        while self.running and self.proc.poll() is None:
                            rlist, _, _ = select.select([self.proc.stdout], [], [], chunk_time)
                            if rlist:
                                try:
                                    raw_in = os.read(fd, chunk_bytes)
                                except (BlockingIOError, InterruptedError):
                                    raw_in = b""
                                except Exception:
                                    break

                                if raw_in:
                                    leftover.extend(raw_in)
                                    # 4バイト（フレーム）境界単位でのみ書き出し
                                    aligned_len = (len(leftover) // bytes_per_frame) * bytes_per_frame
                                    if aligned_len > 0:
                                        chunk_to_write = bytes(leftover[:aligned_len])
                                        del leftover[:aligned_len]
                                        f.write(chunk_to_write)
                                        self.total_bytes_written += aligned_len
                                        try:
                                            arr = array.array('h')
                                            arr.frombytes(chunk_to_write)
                                            if arr:
                                                p = max(abs(min(arr)), abs(max(arr)))
                                                if p > 32767:
                                                    p = 32767
                                                if p > self.max_peak:
                                                    self.max_peak = p
                                        except Exception:
                                            pass
                                else:
                                    break
                            else:
                                # タイムアウト時：実経過時間に合わせて無音ゼロフィル（必ず4バイト境界で書き込み）
                                expected_bytes = int((time.time() - self.start_time) * self.bytes_per_sec)
                                if expected_bytes > self.total_bytes_written:
                                    diff = expected_bytes - self.total_bytes_written
                                    aligned_pad = (diff // bytes_per_frame) * bytes_per_frame
                                    if aligned_pad > 0:
                                        pad_len = min(aligned_pad, chunk_bytes * 2)
                                        f.write(b"\x00" * pad_len)
                                        self.total_bytes_written += pad_len
                        
                        try:
                            self.proc.terminate()
                            self.proc.wait(timeout=0.2)
                        except Exception:
                            pass
                        self.proc = None

                    # arecord/pw-recordが待機中/未接続の間の無音補完（必ず4バイト境界で書き込み）
                    if self.running and (self.proc is None or self.proc.poll() is not None):
                        expected_bytes = int((time.time() - self.start_time) * self.bytes_per_sec)
                        if expected_bytes > self.total_bytes_written:
                            diff = expected_bytes - self.total_bytes_written
                            aligned_pad = (diff // bytes_per_frame) * bytes_per_frame
                            if aligned_pad > 0:
                                to_write = min(aligned_pad, chunk_bytes)
                                f.write(b"\x00" * to_write)
                                self.total_bytes_written += to_write
                        time.sleep(chunk_time)

                except Exception as loop_e:
                    print(f"Error in recording loop: {loop_e}", flush=True)
                    time.sleep(chunk_time)

            # 録音終了時に端数バッファがあればパディングして書き出し
            if leftover:
                rem = len(leftover) % bytes_per_frame
                if rem != 0:
                    leftover.extend(b"\x00" * (bytes_per_frame - rem))
                f.write(bytes(leftover))
                self.total_bytes_written += len(leftover)

            f.flush()
        self._finalize_wav()

def is_recording():
    global current_recorder, current_filename, start_timestamp
    with state_lock:
        if current_recorder is not None:
            if current_recorder.is_active():
                return True
            else:
                current_recorder.stop()
                current_recorder = None
                current_filename = None
                start_timestamp = 0
        return False

def start_recording(client_time=None, client_epoch=None):
    global current_recorder, current_filename, start_timestamp
    with state_lock:
        if current_recorder is not None and current_recorder.is_active():
            return False, "Already recording"
        
        # スマホの正確なUNIX時刻でRaspberry Piのシステム時計を補正（ベストエフォート）
        if client_epoch is not None and isinstance(client_epoch, (int, float)):
            try:
                subprocess.run(["date", "-s", f"@{int(client_epoch)}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            except Exception:
                pass
        
        # ファイル名タイムスタンプの決定（スマホ時刻を最優先、安全な書式のみ許可）
        if client_time and isinstance(client_time, str) and re.match(r"^\d{8}_\d{6}$", client_time):
            timestamp_str = client_time
        else:
            timestamp_str = time.strftime("%Y%m%d_%H%M%S")

        filename = f"audio_{timestamp_str}.wav"
        filepath = os.path.join(RECORDINGS_DIR, filename)
        
        sample_rate = detect_sample_rate()
        audio_device = get_audio_device()

        try:
            recorder = ContinuousRecorder(filepath, audio_device, sample_rate=sample_rate)
            recorder.start()
            current_recorder = recorder
            current_filename = filename
            start_timestamp = time.time()
            return True, filename
        except Exception as e:
            return False, str(e)

def stop_recording():
    global current_recorder, current_filename, start_timestamp
    with state_lock:
        if current_recorder is None:
            return False, "Not recording"
        
        saved_file = current_filename
        try:
            current_recorder.stop()
        except Exception:
            pass
        
        current_recorder = None
        current_filename = None
        start_timestamp = 0
        return True, saved_file

class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suppress noisy console logs

    def serve_static(self, path, head_only=False):
        """
        HTMLおよびPWA静的ファイルを配信（do_HEAD / do_GET で共通利用）。
        """
        if path == "/" or path == "/index.html":
            if os.path.isfile(HTML_FILE):
                size = os.path.getsize(HTML_FILE)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if not head_only:
                    with open(HTML_FILE, "rb") as f:
                        shutil.copyfileobj(f, self.wfile)
                return True
            else:
                self.send_error(404, "index.html not found")
                return True

        if path in STATIC_FILES:
            target_path = os.path.join(STATIC_DIR, os.path.basename(path))
            if os.path.isfile(target_path):
                size = os.path.getsize(target_path)
                self.send_response(200)
                self.send_header("Content-Type", STATIC_FILES[path])
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                if not head_only:
                    with open(target_path, "rb") as f:
                        shutil.copyfileobj(f, self.wfile)
                return True
            else:
                self.send_error(404, f"{path} not found")
                return True

        return False

    def do_HEAD(self):
        url = urlparse(self.path)
        path = unquote(url.path)

        if self.serve_static(path, head_only=True):
            return

        if path.startswith("/audio/"):
            filename = os.path.basename(path[7:])
            filepath = os.path.join(RECORDINGS_DIR, filename)
            if not os.path.isfile(filepath):
                self.send_error(404, "File not found")
                return
            self.serve_file(filepath, head_only=True)
            return

        self.send_error(404, "Not found")

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        
        if self.serve_static(path, head_only=False):
            return
            
        elif path == "/api/status":
            rec = is_recording()
            free_gb, hours_left = get_disk_info()
            duration = int(time.time() - start_timestamp) if rec else 0
            wifi_active = get_wifi_active()
            sample_rate_val = current_recorder.sample_rate if (rec and current_recorder) else detect_sample_rate()
            
            res_data = {
                "recording": rec,
                "current_file": current_filename if rec else None,
                "duration_sec": duration,
                "free_gb": free_gb,
                "hours_left": hours_left,
                "wifi_active": wifi_active,
                "bt_name": CACHED_BT_NAME,
                "model_name": CACHED_MODEL_NAME,
                "sample_rate": f"{round(sample_rate_val / 1000, 1)} kHz",
                "fqdn": CACHED_FQDN,
                "port": PORT
            }
            self.send_json(res_data)
            return
            
        elif path == "/api/files":
            files = []
            rec_file = current_filename if is_recording() else None
            try:
                for f in sorted(os.listdir(RECORDINGS_DIR), reverse=True):
                    if f.endswith(".wav"):
                        p = os.path.join(RECORDINGS_DIR, f)
                        st = os.stat(p)
                        is_writing = (f == rec_file)

                        if is_writing:
                            rate = current_recorder.sample_rate if current_recorder else 48000
                            duration_sec = int(time.time() - start_timestamp)
                            was_repaired = False
                        else:
                            rate, duration_sec, was_repaired = get_wav_info_and_repair(p, st.st_size)
                            if was_repaired:
                                st = os.stat(p)

                        rate_str = f"{round(rate / 1000, 1)} kHz"
                        
                        files.append({
                            "name": f,
                            "size_mb": round(st.st_size / (1024 * 1024), 2),
                            "duration": format_duration(duration_sec),
                            "sample_rate": rate_str,
                            "repaired": was_repaired,
                            "writing": is_writing,
                            "modified": time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(st.st_mtime)),
                            "url": f"/audio/{f}" if not is_writing else None
                        })
            except Exception as e:
                print(f"Error listing files: {e}")
            self.send_json(files)
            return
            
        elif path == "/api/wifi/status":
            self.send_json(get_wifi_details())
            return
            
        elif path == "/api/wifi/scan":
            self.send_json(scan_wifi_networks())
            return

        elif path == "/api/wifi/saved":
            self.send_json(get_saved_wifi_connections())
            return

        elif path == "/api/bt/status":
            self.send_json(get_bt_status())
            return

        elif path == "/api/bt/paired":
            self.send_json(get_paired_bt_devices())
            return

        elif path == "/api/bt/scan":
            self.send_json(scan_bt_devices())
            return
            
        elif path.startswith("/audio/"):
            filename = os.path.basename(path[7:])
            filepath = os.path.join(RECORDINGS_DIR, filename)
            if not os.path.isfile(filepath):
                self.send_error(404, "File not found")
                return
            
            self.serve_file(filepath)
            return
            
        self.send_error(404, "Not found")

    def read_json_body(self):
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            try:
                body = self.rfile.read(content_len).decode("utf-8")
                return json.loads(body)
            except Exception:
                pass
        return {}

    def do_POST(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        
        if path == "/api/start":
            data = self.read_json_body()
            client_time = data.get("client_time")
            client_epoch = data.get("client_epoch")
            ok, msg = start_recording(client_time=client_time, client_epoch=client_epoch)
            self.send_json({"success": ok, "message": msg})
            return
            
        elif path == "/api/stop":
            ok, msg = stop_recording()
            self.send_json({"success": ok, "message": msg})
            return

        elif path == "/api/usb/reconnect":
            ok, msg = trigger_usb_reconnect()
            self.send_json({"success": ok, "message": msg})
            return
            
        elif path == "/api/wifi/add":
            data = self.read_json_body()
            ssid = data.get("ssid")
            password = data.get("password")
            priority = int(data.get("priority", 50))
            ok, msg = add_wifi_connection(ssid, password, priority)
            self.send_json({"success": ok, "message": msg})
            return

        elif path == "/api/wifi/delete":
            data = self.read_json_body()
            name = data.get("name")
            ok, msg = delete_wifi_connection(name)
            self.send_json({"success": ok, "message": msg})
            return

        elif path == "/api/wifi/restore_hotspot":
            ok, msg = restore_hotspot_connection()
            self.send_json({"success": ok, "message": msg})
            return

        elif path == "/api/bt/connect":
            data = self.read_json_body()
            mac = data.get("mac")
            ok, msg = connect_bt_device(mac)
            self.send_json({"success": ok, "message": msg})
            return

        elif path == "/api/bt/disconnect":
            data = self.read_json_body()
            mac = data.get("mac")
            ok, msg = disconnect_bt_device(mac)
            self.send_json({"success": ok, "message": msg})
            return

        elif path == "/api/bt/remove":
            data = self.read_json_body()
            mac = data.get("mac")
            ok, msg = remove_bt_device(mac)
            self.send_json({"success": ok, "message": msg})
            return
            
        elif path.startswith("/api/delete/"):
            filename = os.path.basename(path[12:])
            if is_recording() and filename == current_filename:
                self.send_json({"success": False, "error": "Cannot delete currently recording file"})
                return
            filepath = os.path.join(RECORDINGS_DIR, filename)
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                    meta_path = filepath.rsplit(".", 1)[0] + ".meta.json"
                    if os.path.isfile(meta_path):
                        try:
                            os.remove(meta_path)
                        except Exception:
                            pass
                    self.send_json({"success": True})
                    return
                except Exception as e:
                    self.send_json({"success": False, "error": str(e)})
                    return
            self.send_error(404, "File not found")
            return
            
        self.send_error(404, "Not found")

    def send_json(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filepath, head_only=False):
        file_size = os.path.getsize(filepath)
        range_header = self.headers.get("Range")

        if range_header:
            bytes_range = range_header.strip().split("=")[-1]
            start_str, end_str = bytes_range.split("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            length = end - start + 1
            
            self.send_response(206)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            
            if not head_only:
                with open(filepath, "rb") as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
        else:
            meta_path = filepath.rsplit(".", 1)[0] + ".meta.json"
            gain_val = 1.0
            peak_db_val = -99.0
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                        gain_val = mdata.get("recommend_gain", 1.0)
                        peak_db_val = mdata.get("peak_db", -99.0)
                except Exception:
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Audio-Recommend-Gain", str(gain_val))
            self.send_header("X-Audio-Peak-Db", str(peak_db_val))
            self.end_headers()
            
            if not head_only:
                with open(filepath, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)

if __name__ == "__main__":
    print(f"Bluetooth Device Name (cached): {CACHED_BT_NAME}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    print(f"Recorder Web server running on port {PORT}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
