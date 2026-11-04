# ==============================================================================
# Windows PC から Raspberry Pi Zero W への一括全自動インストーラー
#
# 【使用方法】
# powershell -ExecutionPolicy Bypass -File setup_to_pi.ps1 [-ip "192.168.1.186"]
# ==============================================================================

param (
    [string]$ip = "192.168.1.186",
    [string]$user = "pi"
)

$ErrorActionPreference = "Stop"

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  PI UAC2 Recorder - PCからの一括全自動インストーラー" -ForegroundColor Cyan
Write-Host "  ターゲット: ${user}@${ip}" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

# 1. 接続確認
Write-Host "[1/4] Raspberry Pi ($ip) への疎通確認..." -ForegroundColor Green
if (-not (Test-Connection -ComputerName $ip -Count 1 -Quiet)) {
    Write-Error "ターゲット ($ip) に ping が通りません。ネットワーク接続を確認してください。"
    exit 1
}
Write-Host "  -> 疎通確認 OK" -ForegroundColor Gray

# 2. 必要ファイルの転送
Write-Host "[2/4] プロジェクトファイルの転送中 (SCP)..." -ForegroundColor Green
$baseDir = $PSScriptRoot
if (-not (Test-Path "$baseDir\src\recorder_web.py")) {
    $baseDir = Split-Path -Parent $PSScriptRoot
}

$filesToTransfer = @(
    "src/recorder_web.py",
    "src/setup-usb-gadget.sh",
    "html/index.html",
    "scripts/setup_all.sh",
    "scripts/verify_setup.sh"
)

ssh -o StrictHostKeyChecking=no "${user}@${ip}" "mkdir -p /tmp/pi_setup/src /tmp/pi_setup/html /tmp/pi_setup/scripts"

scp -o StrictHostKeyChecking=no "$baseDir/src/recorder_web.py" "${user}@${ip}:/tmp/pi_setup/src/"
scp -o StrictHostKeyChecking=no "$baseDir/src/setup-usb-gadget.sh" "${user}@${ip}:/tmp/pi_setup/src/"
scp -o StrictHostKeyChecking=no "$baseDir/html/index.html" "${user}@${ip}:/tmp/pi_setup/html/"
scp -o StrictHostKeyChecking=no "$baseDir/scripts/setup_all.sh" "${user}@${ip}:/tmp/pi_setup/scripts/"
scp -o StrictHostKeyChecking=no "$baseDir/scripts/verify_setup.sh" "${user}@${ip}:/tmp/pi_setup/scripts/"

Write-Host "  -> ファイル転送完了" -ForegroundColor Gray

# 3. セットアップスクリプトの実行
Write-Host "[3/4] Raspberry Pi 上で一括セットアップスクリプトを実行中..." -ForegroundColor Green
Write-Host "  ※ パッケージ更新やビルドのため、数分かかる場合があります..." -ForegroundColor Yellow
ssh -o StrictHostKeyChecking=no -t "${user}@${ip}" "sudo bash /tmp/pi_setup/scripts/setup_all.sh"

# 4. セットアップ検証
Write-Host "[4/4] セットアップ結果の検証を実行中..." -ForegroundColor Green
ssh -o StrictHostKeyChecking=no "${user}@${ip}" "bash /home/pi/verify_setup.sh 2>/dev/null || bash /tmp/pi_setup/scripts/verify_setup.sh"

Write-Host "`n================================================================" -ForegroundColor Cyan
Write-Host "  一括セットアップ処理が完了しました！" -ForegroundColor Green
Write-Host "  カーネル設定を反映するため、必要に応じて再起動してください:" -ForegroundColor Yellow
Write-Host "  ssh pi@$ip 'sudo reboot'" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Cyan
