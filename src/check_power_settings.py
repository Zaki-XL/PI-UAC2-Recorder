#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PI UAC2 Recorder: 省電力設定・インジケーター総合診断スクリプト
Raspberry Pi Zero W に適用された省電力チューニングおよびLED動作設定を自動検証します。
"""

import os
import sys
import glob
import subprocess

# ANSIカラーコード
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

results = []

def check(category, name, condition, details=""):
    """診断項目の判定と結果格納 (必須項目)"""
    status = "OK" if condition else "NG"
    results.append({
        "category": category,
        "name": name,
        "status": status,
        "details": details
    })
    tag = f"{GREEN}[  OK  ]{RESET}" if condition else f"{RED}[  NG  ]{RESET}"
    print(f" {tag} {name:<45} {details}")

def warn(category, name, condition, details=""):
    """推奨・参考項目の判定と結果格納"""
    status = "OK" if condition else "WARN"
    results.append({
        "category": category,
        "name": name,
        "status": status,
        "details": details
    })
    tag = f"{GREEN}[  OK  ]{RESET}" if condition else f"{YELLOW}[ WARN ]{RESET}"
    print(f" {tag} {name:<45} {details}")

def find_config_txt():
    """Raspberry Pi OSのconfig.txtパスを特定"""
    for p in ['/boot/firmware/config.txt', '/boot/config.txt']:
        if os.path.isfile(p):
            return p
    return None

def main():
    print(f"\n{BOLD}{CYAN}======================================================{RESET}")
    print(f"{BOLD}{CYAN}  PI UAC2 Recorder: 省電力＆動作設定 総合診断ツール{RESET}")
    print(f"{BOLD}{CYAN}======================================================{RESET}\n")

    # 1. config.txt 静的設定チェック
    print(f"{BOLD}1. ブート構成ファイル (config.txt) の静的検証{RESET}")
    config_path = find_config_txt()
    if not config_path:
        check("BootConfig", "config.txtの存在", False, "ファイルが見つかりません")
        sys.exit(1)
    
    check("BootConfig", f"config.txt 検出 ({config_path})", True)

    with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
        cfg_text = f.read()

    # 有効な（コメントアウトされていない）行を抽出
    active_lines = []
    for line in cfg_text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            active_lines.append(line)
    active_blob = "\n".join(active_lines)

    check("BootConfig", "HDMI無効化 (dtoverlay=disable-hdmi)",
          "dtoverlay=disable-hdmi" in active_blob, "常時約25mA削減")
    
    check("BootConfig", "LED SDアクセス連動 (dtparam=act_led_trigger)",
          ("act_led_trigger=mmc" in active_blob or "act_led_trigger=mmc0" in active_blob),
          "待機時消灯・SDアクセス時点灯")
    
    check("BootConfig", "LED極性補正 (dtparam=act_led_activelow=off)",
          "act_led_activelow=off" in active_blob, "Zero WアクティブLow極性反転")

    check("BootConfig", "内蔵オーディオPWM停止 (dtparam=audio=off)",
          "dtparam=audio=off" in active_blob, "内蔵PWM音声回路遮断 (UAC2独立)")

    check("BootConfig", "SPIコントローラ停止 (dtparam=spi=off)",
          "dtparam=spi=off" in active_blob, "未使用SPIバス給電遮断")

    check("BootConfig", "I2Cコントローラ停止 (dtparam=i2c_arm=off)",
          "dtparam=i2c_arm=off" in active_blob, "未使用I2Cバス給電遮断")

    check("BootConfig", "UARTピン駆動停止 (enable_uart=0)",
          "enable_uart=0" in active_blob, "シリアルコンソールクロック停止")

    # 2. 実行時（Live OS）ハードウェア状態チェック
    print(f"\n{BOLD}2. 実行時 (Live OS) ハードウェア・カーネル状態の検証{RESET}")

    # LED Trigger 検証 (ACT または mmc0 / led0)
    led_trigger_ok = False
    led_trigger_desc = "未検出"
    led_paths = glob.glob("/sys/class/leds/*/trigger")
    for trigger_path in led_paths:
        try:
            with open(trigger_path, "r") as lf:
                content = lf.read()
                # いずれかのLEDで [mmc0] または [mmc] が選択されているか
                if "[mmc0]" in content or "[mmc]" in content:
                    led_trigger_ok = True
                    led_trigger_desc = f"{os.path.basename(os.path.dirname(trigger_path))} で [mmc0] 動作中"
                    break
        except Exception:
            pass
    check("LiveHardware", "ACT LED microSDアクセス連動", led_trigger_ok, led_trigger_desc)

    # ARM I2C バス (/dev/i2c-1) の停止検証 (/dev/i2c-2 はVC4 KMS内部用)
    arm_i2c_exists = os.path.exists("/dev/i2c-1")
    check("LiveHardware", "ARM I2Cバス (/dev/i2c-1) 遮断", not arm_i2c_exists,
          "未検出 (良好)" if not arm_i2c_exists else "稼働中 (警告)")

    # SPI デバイス (/dev/spidev*) の停止検証
    spidevs = glob.glob("/dev/spidev*")
    check("LiveHardware", "SPIデバイス (/dev/spidev*) 遮断", len(spidevs) == 0,
          "未検出 (良好)" if len(spidevs) == 0 else f"{spidevs} が存在")

    # 内蔵オーディオモジュール (snd_bcm2835) の稼働状況
    try:
        lsmod = subprocess.check_output(["lsmod"], text=True)
        snd_loaded = "snd_bcm2835" in lsmod
        warn("LiveHardware", "内蔵PWMオーディオ (snd_bcm2835)", not snd_loaded,
             "未ロード (良好)" if not snd_loaded else "ロード中 (先頭config.txt設定に依存)")
    except Exception:
        pass

    # 3. ストレージ (microSD) マウントオプション検証
    print(f"\n{BOLD}3. ストレージ (microSD) マウント設定の検証{RESET}")
    noatime_ok = False
    root_mount_info = "ルートマウント未特定"
    if os.path.isfile("/proc/mounts"):
        with open("/proc/mounts", "r") as mf:
            for line in mf:
                parts = line.strip().split()
                if len(parts) >= 4 and parts[1] == "/":
                    options = parts[3].split(",")
                    noatime_ok = "noatime" in options
                    root_mount_info = f"オプション: {parts[3]}"
                    break
    check("Storage", "ルートファイルシステム (/) noatime 有効化", noatime_ok, root_mount_info)

    # 4. 不要バックグラウンドサービス・タイマー検証
    print(f"\n{BOLD}4. 不要デーモン・タイマー停止状態の検証{RESET}")
    services = [
        ("triggerhappy.service", "キーボード監視デーモン"),
        ("apt-daily.timer", "apt自動更新タイマー"),
        ("apt-daily-upgrade.timer", "aptアップグレードタイマー"),
        ("ModemManager.service", "モデム検出デーモン")
    ]
    for s_name, desc in services:
        is_active = False
        try:
            res = subprocess.run(["systemctl", "is-active", s_name],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            is_active = (res.stdout.strip() == "active")
        except Exception:
            pass
        check("Services", f"{s_name} ({desc}) 停止", not is_active,
              "停止中 (良好)" if not is_active else "稼働中 (警告)")

    # 5. UAC2 / WebUI コア機能健全性チェック
    print(f"\n{BOLD}5. コア機能（USB UAC2 / ネットワーク）の健全性検証{RESET}")
    gadget_ok = os.path.isdir("/sys/kernel/config/usb_gadget/composite")
    check("Core", "USB Composite Gadget 構築状態", gadget_ok,
          "UAC2 + CDC-ECM 構成済" if gadget_ok else "未構成")

    # 診断結果サマリー
    print(f"\n{BOLD}{CYAN}------------------------------------------------------{RESET}")
    print(f"{BOLD}{CYAN}  総合診断結果サマリー{RESET}")
    print(f"{BOLD}{CYAN}------------------------------------------------------{RESET}")
    
    total = len(results)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    ng_count = sum(1 for r in results if r["status"] == "NG")

    print(f" 全診断項目: {total} 件")
    print(f" {GREEN}合格 (OK):     {ok_count} 件{RESET}")
    if warn_count > 0:
        print(f" {YELLOW}注意 (WARN):   {warn_count} 件{RESET}")
    if ng_count > 0:
        print(f" {RED}不合格 (NG):   {ng_count} 件{RESET}")

    if ng_count == 0:
        print(f"\n{BOLD}{GREEN}>>> [判定: ALL PASSED] 省電力・動作設定が正常に稼働しています！{RESET}\n")
        sys.exit(0)
    else:
        print(f"\n{BOLD}{RED}>>> [判定: FAILED] 一部の省電力設定に不整合があります。上記項目を確認してください。{RESET}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
