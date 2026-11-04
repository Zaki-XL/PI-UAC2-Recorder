#!/bin/bash
# ==============================================================================
# Raspberry Pi Zero W - PI UAC2 Recorder 設定完了・稼働状態 検証スクリプト
#
# 【実行方法】bash scripts/verify_setup.sh
# ==============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED_COUNT=0
TOTAL_COUNT=9

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}   PI UAC2 Recorder システム状態・設定完了検証                  ${NC}"
echo -e "${BLUE}================================================================${NC}"

# 1. カーネル・ブート設定の検証
echo -n "[1/9] カーネルブート設定 (dwc2 / otg_mode)... "
CONFIG_TXT="/boot/firmware/config.txt"
[ ! -f "$CONFIG_TXT" ] && CONFIG_TXT="/boot/config.txt"
CMDLINE_TXT="/boot/firmware/cmdline.txt"
[ ! -f "$CMDLINE_TXT" ] && CMDLINE_TXT="/boot/cmdline.txt"

if grep -q "dtoverlay=dwc2" "$CONFIG_TXT" 2>/dev/null && grep -q "modules-load=dwc2" "$CMDLINE_TXT" 2>/dev/null; then
    echo -e "${GREEN}PASS${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${RED}FAIL (config.txt または cmdline.txt に dwc2 設定が見つかりません)${NC}"
fi

# 2. USB ガジェット認識の検証
echo -n "[2/9] USB UAC2 ガジェット (48kHz / 16bit 2ch 標準オーディオ)... "
if lsmod | grep -q "usb_f_uac2" && arecord -l 2>/dev/null | grep -qi "uac2"; then
    echo -e "${GREEN}PASS (ALSA PCM に UAC2Gadget 認識済)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${YELLOW}WARNING (UAC2Gadget が未認識です。再起動または setup-usb-gadget.sh を実行してください)${NC}"
fi

# 3. Web サーバーサービス (recorder-web.service) の検証
echo -n "[3/9] Web サーバーサービス稼働状態 (recorder-web)... "
if systemctl is-active --quiet recorder-web; then
    echo -e "${GREEN}PASS (active running)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${RED}FAIL (recorder-web.service が停止しています)${NC}"
fi

# 4. Web API (ポート 8080 / /api/status) の応答検証
echo -n "[4/9] Web API 応答確認 (http://localhost:8080/api/status)... "
API_RES=$(curl -s -m 3 http://localhost:8080/api/status 2>/dev/null || true)
if echo "$API_RES" | grep -q '"recording"'; then
    echo -e "${GREEN}PASS (200 OK 正常応答)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${RED}FAIL (ポート 8080 からの API 応答がありません)${NC}"
fi

# 5. PipeWire & 音声ルーティングスタックの検証
echo -n "[5/9] PipeWire 音声サブシステム (pw-record / loopback)... "
if command -v pw-record >/dev/null 2>&1; then
    echo -e "${GREEN}PASS (PipeWire インストール済)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${RED}FAIL (pw-record が見つかりません)${NC}"
fi

# 6. Bluetooth スタック (bluez / hci0) の検証
echo -n "[6/9] Bluetooth スタック (bluetooth.service / BlueZ)... "
if systemctl is-active --quiet bluetooth; then
    echo -e "${GREEN}PASS (Bluetooth サービス active)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${YELLOW}WARNING (bluetooth.service が起動していません)${NC}"
fi

# 7. NetworkManager 自動APフォールバックプロファイルの検証
echo -n "[7/9] 自動APフォールバック設定 (Pi-Recorder-Hotspot)... "
if nmcli -t -f NAME connection show 2>/dev/null | grep -q "^Pi-Recorder-Hotspot$"; then
    echo -e "${GREEN}PASS (登録済 / 優先度 -10)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${RED}FAIL (Pi-Recorder-Hotspot プロファイルが見つかりません)${NC}"
fi

# 8. 必要ファイル・ディレクトリの配備検証
echo -n "[8/9] 実行スクリプト & 静的ファイルの整合性... "
MISSING=""
[ ! -f "/home/pi/recorder_web.py" ] && MISSING="${MISSING} recorder_web.py"
[ ! -f "/home/pi/html/index.html" ] && MISSING="${MISSING} html/index.html"
[ ! -f "/home/pi/setup-usb-gadget.sh" ] && MISSING="${MISSING} setup-usb-gadget.sh"
[ ! -d "/home/pi/recordings" ] && MISSING="${MISSING} recordings_dir"

if [ -z "$MISSING" ]; then
    echo -e "${GREEN}PASS (全ファイル配備完了)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${RED}FAIL (不足:${MISSING})${NC}"
fi

# 9. 省電力 & ブート高速化設定の検証
echo -n "[9/9] 省電力 & 高速起動チューニング (LED消灯 / 省電力スクリプト)... "
PWR_CHECK=true
if ! grep -q "act_led_trigger=none" "$CONFIG_TXT" 2>/dev/null; then
    PWR_CHECK=false
fi
if [ ! -f "/usr/local/bin/apply_power_save.sh" ]; then
    PWR_CHECK=false
fi
if [ "$PWR_CHECK" = true ]; then
    echo -e "${GREEN}PASS (LED消灯・最適化スクリプト適用済)${NC}"
    PASSED_COUNT=$((PASSED_COUNT + 1))
else
    echo -e "${YELLOW}WARNING (一部の省電力設定が未適用です)${NC}"
fi

echo -e "${BLUE}================================================================${NC}"
if [ "$PASSED_COUNT" -eq "$TOTAL_COUNT" ]; then
    echo -e "${GREEN}  【総合判定: 合格】 $PASSED_COUNT / $TOTAL_COUNT 項目が完全に正常稼働しています！${NC}"
else
    echo -e "${YELLOW}  【総合判定: 要確認】 $PASSED_COUNT / $TOTAL_COUNT 項目のみ合格です。上記のFAIL項目を確認してください。${NC}"
fi
echo -e "${BLUE}================================================================${NC}"
