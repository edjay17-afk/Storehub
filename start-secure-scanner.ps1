$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskTunnel = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$taskPython = 'C:\Python314\python.exe'
if (-not (Test-Path -LiteralPath $taskTunnel)) { throw 'cloudflared is not installed.' }
$taskListener = Get-NetTCPConnection -LocalPort 5078 -State Listen -ErrorAction SilentlyContinue
if ($taskListener) {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskListener.OwningProcess)"
    if ($taskProcess.CommandLine -notlike '*secure_scanner_server.py*') { throw 'Port 5078 is used by another application.' }
} else {
    Start-Process -FilePath $taskPython -ArgumentList 'secure_scanner_server.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\data\scanner-server.log" -RedirectStandardError "$PSScriptRoot\data\scanner-server-error.log" | Out-Null
}
$taskExistingMonitor = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" | Where-Object { $_.CommandLine -like "*$PSScriptRoot\scanner-monitor.ps1*" }
if ($taskExistingMonitor) { Write-Host 'Secure scanner monitor is already running.'; return }
# The monitor's named mutex prevents duplicate tunnel managers.
Start-Process -FilePath powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$PSScriptRoot\scanner-monitor.ps1" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\data\scanner-monitor.log" -RedirectStandardError "$PSScriptRoot\data\scanner-monitor-error.log" | Out-Null
Write-Host 'Secure scanner monitor started. It refreshes expired links automatically.'
Write-Host 'Open the warehouse app on your phone and tap the barcode icon.'
