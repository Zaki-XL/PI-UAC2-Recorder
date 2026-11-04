#!/bin/bash
set -e

# 1. /usr/local/bin/apply_power_save.sh を作成
sudo bash -c 'cat > /usr/local/bin/apply_power_save.sh' << 'EOF'
#!/bin/bash
# 全ての LED (ACT, default-on, mmc0 等) を完全消灯
for led in /sys/class/leds/*; do
    if [ -d "$led" ]; then
        echo none > "$led/trigger" 2>/dev/null || true
        echo 0 > "$led/brightness" 2>/dev/null || true
    fi
done

# Wi-Fi 送信電力を控えめにして電力半減
iwconfig wlan0 txpower 10 2>/dev/null || true

# CPU ガバナーを ondemand
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    echo ondemand > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || true
fi
EOF

sudo chmod +x /usr/local/bin/apply_power_save.sh
sudo /usr/local/bin/apply_power_save.sh

# 状態確認
echo "=== LED Status After Full Turn-Off ==="
for d in /sys/class/leds/*; do
    [ -d "$d" ] && echo "$d -> trigger: $(cat $d/trigger | grep -o '\[.*\]'), brightness: $(cat $d/brightness)"
done
