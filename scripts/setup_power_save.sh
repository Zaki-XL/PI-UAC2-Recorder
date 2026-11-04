#!/bin/bash
# ==============================================================================
# Raspberry Pi Zero W 超省電力化 & 低電圧防止セットアップスクリプト
# ==============================================================================

set -e

echo "=== 1. HDMI 出力回路の完全シャットダウン設定 ==="
# tvservice で即座に HDMI を OFF
if command -v tvservice >/dev/null 2>&1; then
    tvservice -o || true
fi

# config.txt に HDMI 無効化とクロック上限設定を追加
CONFIG_FILE="/boot/firmware/config.txt"
[ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"

echo "Updating $CONFIG_FILE..."
# 既存の省電力設定があれば削除してクリーンに追加
sudo sed -i '/# === PI UAC2 POWER SAVE ===/,/# === END POWER SAVE ===/d' "$CONFIG_FILE"

sudo bash -c "cat >> $CONFIG_FILE" << 'EOF'
# === PI UAC2 POWER SAVE ===
# HDMI を物理的に無効化して常時 25〜30mA 削減
hdmi_blanking=2

# CPU クロックを 700MHz に固定して電流スパイクを抑制
arm_freq=700
core_freq=250

# 基板上の緑色 LED を完全消灯
dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
# === END POWER SAVE ===
EOF

echo "=== 2. Linux カーネルのディスク書き込みバッファ最適化 (電流スパイク解消) ==="
# microSD への頻繁なフラッシュを抑え、RAM に溜めて滑らかに書き込む
sudo bash -c "cat > /etc/sysctl.d/99-power-save.conf" << 'EOF'
# RAM ページキャッシュを有効活用して microSD への電流スパイクを平滑化
vm.dirty_background_ratio = 20
vm.dirty_ratio = 40
vm.dirty_writeback_centisecs = 1500
EOF
sudo sysctl --system >/dev/null 2>&1 || true

echo "=== 3. 不要なバックグラウンドサービスの停止 & 無効化 ==="
SERVICES_TO_DISABLE=(
    "triggerhappy.service"
    "cups.service"
    "cups-browsed.service"
    "ModemManager.service"
    "apt-daily.timer"
    "apt-daily-upgrade.timer"
    "man-db.timer"
)

for svc in "${SERVICES_TO_DISABLE[@]}"; do
    if systemctl list-unit-files | grep -q "$svc"; then
        sudo systemctl disable --now "$svc" 2>/dev/null || true
        echo "Disabled $svc"
    fi
done

echo "=== 4. 起動時省電力常駐スクリプトの作成 ==="
sudo bash -c "cat > /usr/local/bin/apply_power_save.sh" << 'EOF'
#!/bin/bash
# HDMI OFF
command -v tvservice >/dev/null 2>&1 && tvservice -o || true

# LED OFF
if [ -d /sys/class/leds/led0 ]; then
    echo none > /sys/class/leds/led0/trigger || true
    echo 1 > /sys/class/leds/led0/brightness || true
fi

# Wi-Fi 送信電力を控えめにして電力半減
iwconfig wlan0 txpower 12 2>/dev/null || true

# CPU ガバナーを powersave または ondemand(700MHz上限)
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    echo ondemand > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor || true
fi
EOF
sudo chmod +x /usr/local/bin/apply_power_save.sh

# systemd サービス化
sudo bash -c "cat > /etc/systemd/system/power-save.service" << 'EOF'
[Unit]
Description=Apply Raspberry Pi Zero W Power Saving
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/apply_power_save.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now power-save.service

echo "=== 5. 現在の適用状態を確認 ==="
/usr/local/bin/apply_power_save.sh || true
vcgencmd get_throttled
vcgencmd measure_volts
vcgencmd measure_temp

echo "=================================================="
echo " 超省電力化セットアップ完了！"
echo "=================================================="
