#!/bin/bash
set -e

CONFIGFS_DIR="/sys/kernel/config/usb_gadget"
GADGET_DIR="$CONFIGFS_DIR/composite"

echo "--- Stopping current gadget ---"
if [ -d "$GADGET_DIR" ]; then
    echo "" > "$GADGET_DIR/UDC" 2>/dev/null || true
    rm -f "$GADGET_DIR"/configs/c.1/*.usb0 2>/dev/null || true
    rmdir "$GADGET_DIR"/configs/c.1/strings/0x409 2>/dev/null || true
    rmdir "$GADGET_DIR"/configs/c.1 2>/dev/null || true
    rmdir "$GADGET_DIR"/functions/uac1.* 2>/dev/null || true
    rmdir "$GADGET_DIR"/functions/uac2.* 2>/dev/null || true
    rmdir "$GADGET_DIR"/strings/0x409 2>/dev/null || true
    rmdir "$GADGET_DIR" 2>/dev/null || true
fi

echo "--- Loading UAC2 modules ---"
modprobe libcomposite
modprobe usb_f_uac2

mkdir -p "$GADGET_DIR"
cd "$GADGET_DIR"

# Full-Speed on Raspberry Pi Zero W DWC2
echo "full-speed" > max_speed

# 標準 UAC2 USB-C オーディオデバイス仕様
echo "0x18d1" > idVendor
echo "0x5033" > idProduct
echo "0x0100" > bcdDevice
echo "0x0200" > bcdUSB

echo "0x00" > bDeviceClass
echo "0x00" > bDeviceSubClass
echo "0x00" > bDeviceProtocol

mkdir -p strings/0x409
echo "0123456789" > strings/0x409/serialnumber
echo "Generic USB Audio" > strings/0x409/manufacturer
echo "USB-C Audio Adapter" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Standard Audio Device" > configs/c.1/strings/0x409/configuration
echo 0xc0 > configs/c.1/bmAttributes
echo 1 > configs/c.1/MaxPower

# Function: UAC2 with Headphones Terminal (770 = 0x0302)
mkdir -p functions/uac2.usb0

# ホスト（スマホ）からPiへの音声入力（キャプチャ用）
echo 48000 > functions/uac2.usb0/c_srate
echo 2 > functions/uac2.usb0/c_ssize
echo 3 > functions/uac2.usb0/c_chmask
# 770 = 0x0302 (Headphones - Androidで有線イヤホンとして確実に認識される)
echo 770 > functions/uac2.usb0/c_terminal_type

# Piからホストへの音声出力
echo 48000 > functions/uac2.usb0/p_srate
echo 2 > functions/uac2.usb0/p_ssize
echo 3 > functions/uac2.usb0/p_chmask
echo 770 > functions/uac2.usb0/p_terminal_type

echo 8 > functions/uac2.usb0/req_number 2>/dev/null || true

ln -s functions/uac2.usb0 configs/c.1/

UDC_NAME=$(ls /sys/class/udc | head -n 1)
echo "$UDC_NAME" > UDC
echo "UAC2 Gadget successfully bound to $UDC_NAME"
