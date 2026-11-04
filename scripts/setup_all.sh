#!/bin/bash
# ==============================================================================
# Raspberry Pi Zero W - PI UAC2 Recorder 一括セットアップスクリプト
#
# 【対象OS】Raspberry Pi OS (Bookworm / Bullseye, Lite推奨)
# 【前提条件】初期PiOSがインストールされ、ネットワーク（Wi-Fi等）に接続されていること
# 【実行方法】sudo bash scripts/setup_all.sh
# ==============================================================================

set -e

# 色定義
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}   PI UAC2 Recorder 一括全自動セットアップを開始します           ${NC}"
echo -e "${BLUE}================================================================${NC}"

# Root権限チェック
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] 本スクリプトは root 権限で実行してください。(例: sudo bash setup_all.sh)${NC}"
    exit 1
fi

PI_USER="pi"
PI_HOME="/home/${PI_USER}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# 1. config.txt / cmdline.txt のパス特定
CONFIG_TXT="/boot/firmware/config.txt"
CMDLINE_TXT="/boot/firmware/cmdline.txt"
if [ ! -f "$CONFIG_TXT" ]; then
    CONFIG_TXT="/boot/config.txt"
fi
if [ ! -f "$CMDLINE_TXT" ]; then
    CMDLINE_TXT="/boot/cmdline.txt"
fi

echo -e "${GREEN}[1/8] 必要パッケージのインストール...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    alsa-utils \
    pipewire \
    wireplumber \
    pipewire-pulse \
    bluez \
    bluez-tools \
    network-manager \
    python3 \
    curl \
    rfkill

echo -e "${GREEN}[2/8] カーネルおよびブート設定の更新...${NC}"

# config.txt への DWC2 ガジェット追記
if ! grep -q "dtoverlay=dwc2,dr_mode=peripheral" "$CONFIG_TXT"; then
    echo "" >> "$CONFIG_TXT"
    echo "# PI UAC2 Recorder Settings" >> "$CONFIG_TXT"
    echo "dtoverlay=dwc2,dr_mode=peripheral" >> "$CONFIG_TXT"
    echo "otg_mode=1" >> "$CONFIG_TXT"
    echo -e "${YELLOW}  -> $CONFIG_TXT に dwc2 (peripheral) を追記しました${NC}"
else
    echo -e "  -> $CONFIG_TXT: 既に設定済"
fi

# cmdline.txt への modules-load=dwc2 追記
if ! grep -q "modules-load=dwc2" "$CMDLINE_TXT"; then
    # 単一行の末尾に追加
    sed -i '$ s/$/ modules-load=dwc2/' "$CMDLINE_TXT"
    echo -e "${YELLOW}  -> $CMDLINE_TXT に modules-load=dwc2 を追記しました${NC}"
else
    echo -e "  -> $CMDLINE_TXT: 既に設定済"
fi

echo -e "${GREEN}[3/8] アプリケーションファイルおよびディレクトリの配置...${NC}"
mkdir -p "${PI_HOME}/recordings"
mkdir -p "${PI_HOME}/html"

# ファイルの配備（BASE_DIR または SCRIPT_DIR から）
if [ -f "${BASE_DIR}/src/recorder_web.py" ]; then
    cp -f "${BASE_DIR}/src/recorder_web.py" "${PI_HOME}/recorder_web.py"
    cp -f "${BASE_DIR}/html/index.html" "${PI_HOME}/html/index.html"
    cp -f "${BASE_DIR}/html/index.html" "${PI_HOME}/index.html"
    cp -f "${BASE_DIR}/src/setup-usb-gadget.sh" "${PI_HOME}/setup-usb-gadget.sh"
    cp -f "${BASE_DIR}/scripts/verify_setup.sh" "${PI_HOME}/verify_setup.sh"
elif [ -f "${SCRIPT_DIR}/../src/recorder_web.py" ]; then
    cp -f "${SCRIPT_DIR}/../src/recorder_web.py" "${PI_HOME}/recorder_web.py"
    cp -f "${SCRIPT_DIR}/../html/index.html" "${PI_HOME}/html/index.html"
    cp -f "${SCRIPT_DIR}/../html/index.html" "${PI_HOME}/index.html"
    cp -f "${SCRIPT_DIR}/../src/setup-usb-gadget.sh" "${PI_HOME}/setup-usb-gadget.sh"
    cp -f "${SCRIPT_DIR}/verify_setup.sh" "${PI_HOME}/verify_setup.sh"
fi

chmod +x "${PI_HOME}/recorder_web.py"
chmod +x "${PI_HOME}/setup-usb-gadget.sh"
chmod +x "${PI_HOME}/verify_setup.sh" 2>/dev/null || true
chown -R ${PI_USER}:${PI_USER} "${PI_HOME}/recordings" "${PI_HOME}/html" "${PI_HOME}/index.html" "${PI_HOME}/recorder_web.py" "${PI_HOME}/setup-usb-gadget.sh" "${PI_HOME}/verify_setup.sh"

echo -e "${GREEN}[4/8] systemd サービスの作成と有効化...${NC}"

# usb-gadget.service 作成
cat << 'EOF' > /etc/systemd/system/usb-gadget.service
[Unit]
Description=USB UAC2 Standard Audio Gadget Setup
After=sound.target network.target

[Service]
Type=oneshot
ExecStart=/home/pi/setup-usb-gadget.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# recorder-web.service 作成
cat << 'EOF' > /etc/systemd/system/recorder-web.service
[Unit]
Description=PI UAC2 Recorder Web Server
After=sound.target network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi
Environment=HTML_FILE=/home/pi/html/index.html
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
ExecStart=/usr/bin/python3 /home/pi/recorder_web.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable usb-gadget.service
systemctl enable recorder-web.service

echo -e "${GREEN}[5/8] PipeWire ユーザーセッションと永続化 (Linger) の設定...${NC}"
# pi ユーザーの linger 設定（ログアウト後もユーザーデーモンが稼働）
loginctl enable-linger ${PI_USER} || true

# Bluetooth サービスの有効化
systemctl enable bluetooth.service
systemctl start bluetooth.service || true

echo -e "${GREEN}[6/8] NetworkManager 緊急用 AP (Pi-Recorder-Hotspot) の設定...${NC}"
# 既存の Hotspot プロファイルがあれば再作成
nmcli connection delete "Pi-Recorder-Hotspot" 2>/dev/null || true
nmcli connection add type wifi ifname wlan0 con-name "Pi-Recorder-Hotspot" autoconnect yes ssid "Pi-Recorder-Hotspot" 2>/dev/null || true
nmcli connection modify "Pi-Recorder-Hotspot" 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared 2>/dev/null || true
nmcli connection modify "Pi-Recorder-Hotspot" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "recorder1234" 2>/dev/null || true
nmcli connection modify "Pi-Recorder-Hotspot" connection.autoconnect-priority -10 2>/dev/null || true
nmcli connection modify "Pi-Recorder-Hotspot" ipv4.addresses 192.168.4.1/24 2>/dev/null || true

echo -e "${GREEN}[7/8] 省電力 & ブート高速化チューニング...${NC}"
# 不要サービスの停止とマスク（起動時間の劇的短縮）
systemctl mask apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer 2>/dev/null || true
systemctl mask raspi-config.service 2>/dev/null || true
systemctl mask rpi-eeprom-update.service 2>/dev/null || true

# LED消灯設定の反映
if ! grep -q "act_led_trigger=none" "$CONFIG_TXT"; then
    echo "dtparam=act_led_trigger=none" >> "$CONFIG_TXT"
    echo "dtparam=act_led_activelow=off" >> "$CONFIG_TXT"
fi

# apply_power_save.sh の作成
cat << 'EOF' > /usr/local/bin/apply_power_save.sh
#!/bin/bash
# HDMI出力の停止（約20〜30mA削減）
if command -v tvservice &>/dev/null; then
    tvservice -o >/dev/null 2>&1 || true
fi
# ACT LED消灯
if [ -e /sys/class/leds/ACT/brightness ]; then
    echo 0 > /sys/class/leds/ACT/brightness 2>/dev/null || true
fi
exit 0
EOF
chmod +x /usr/local/bin/apply_power_save.sh

echo -e "${GREEN}[8/8] セットアップ完了！${NC}"
echo -e "${BLUE}================================================================${NC}"
echo -e "${GREEN}  セットアップが正常に完了しました！${NC}"
echo -e "${YELLOW}  ※ カーネルモジュール (DWC2) を反映させるため、システムの再起動が必要です。${NC}"
echo -e "${BLUE}  再起動コマンド: sudo reboot${NC}"
echo -e "${BLUE}================================================================${NC}"
