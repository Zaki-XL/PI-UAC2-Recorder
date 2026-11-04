#!/bin/bash
set -e

sudo bash -c 'cat > /usr/local/bin/apply_power_save.sh' << 'EOF'
#!/bin/bash
# 1. ハードウェアピン直接制御で STATUS_LED_N (GPIO 47) を Drive High (完全消灯)
if command -v pinctrl >/dev/null 2>&1; then
    pinctrl set 47 op dh 2>/dev/null || true
fi

# 念のため sysfs 側もトリガー解除
for led in /sys/class/leds/*; do
    if [ -d "$led" ]; then
        echo none > "$led/trigger" 2>/dev/null || true
    fi
done

# 2. Wi-Fi 送信電力を控えめにして電力半減
iwconfig wlan0 txpower 10 2>/dev/null || true

# 3. CPU ガバナーを ondemand
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    echo ondemand > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || true
fi
EOF

sudo chmod +x /usr/local/bin/apply_power_save.sh
sudo /usr/local/bin/apply_power_save.sh
echo "Power save script updated and executed."
pinctrl get 47
