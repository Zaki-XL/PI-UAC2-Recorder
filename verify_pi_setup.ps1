# ==============================================================================
# Windows PC から Raspberry Pi の稼働・設定状態を遠隔検証するチェッカー
#
# 【使用方法】
# powershell -ExecutionPolicy Bypass -File verify_pi_setup.ps1 [-ip "192.168.1.186"]
# ==============================================================================

param (
    [string]$ip = "192.168.1.186",
    [string]$user = "pi"
)

$baseDir = $PSScriptRoot
$scriptPath = "$baseDir\scripts\verify_setup.sh"

Write-Host "Raspberry Pi ($ip) の設定・稼働状態を検証しています..." -ForegroundColor Cyan

# スクリプトを転送して実行
scp -o StrictHostKeyChecking=no "$scriptPath" "${user}@${ip}:/tmp/verify_setup.sh" > $null 2>&1
ssh -o StrictHostKeyChecking=no -t "${user}@${ip}" "bash /tmp/verify_setup.sh"
