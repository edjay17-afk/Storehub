param([string]$Address = '192.168.110.89')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. "$PSScriptRoot\load-storehub.ps1"
if (-not (Get-NetIPAddress -IPAddress $Address -ErrorAction SilentlyContinue)) {
    throw "This computer does not have IP $Address. Use -Address with this computer's current LAN IP."
}
$taskPython = 'C:\Python314\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = 'python' }
$env:WAREHOUSE_HOST = $Address
Write-Host "Open http://${Address}:5077 on this computer or an Android phone on the same network."
Write-Host 'Keep the app running. The secure scanner starts automatically with this launcher.'
try { & "$PSScriptRoot\start-secure-scanner.ps1" } catch { Write-Warning 'The secure scanner could not start. See data/scanner-monitor-error.log.' }
& $taskPython app.py
