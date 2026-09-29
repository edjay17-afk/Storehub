$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskTunnel = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$taskStatePath = Join-Path $PSScriptRoot 'data\scanner-status.json'
$taskUrlPath = Join-Path $PSScriptRoot 'data\scanner-url.txt'
$taskLogPath = Join-Path $PSScriptRoot 'data\scanner-tunnel-error.log'
$taskProxy = $null
$taskMutex = New-Object System.Threading.Mutex($false, 'Local\CentroWarehouseScannerMonitor')
if (-not $taskMutex.WaitOne(0)) { exit 0 }
function Set-ScannerState([bool]$Ready, [string]$Url, [string]$Message) {
    $taskState = @{ready=$Ready; url=$Url; message=$Message; checked=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText("$taskStatePath.tmp", $taskState)
    Move-Item -LiteralPath "$taskStatePath.tmp" -Destination $taskStatePath -Force
    if ($Ready) {
        [System.IO.File]::WriteAllText("$taskUrlPath.tmp", $Url)
        Move-Item -LiteralPath "$taskUrlPath.tmp" -Destination $taskUrlPath -Force
    }
}
try {
    while ($true) {
        Set-ScannerState $false '' 'The secure scanner is reconnecting. Try again in a few seconds.'
        $taskProxy = Start-Process -FilePath $taskTunnel -ArgumentList @('tunnel','--url','http://127.0.0.1:5078','--no-autoupdate','--protocol','http2') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\scanner-tunnel.log') -RedirectStandardError $taskLogPath
        $taskUrl = ''
        $taskDeadline = (Get-Date).AddSeconds(50)
        while ((Get-Date) -lt $taskDeadline) {
            Start-Sleep -Milliseconds 500
            $taskProxy.Refresh()
            if ($taskProxy.HasExited) { break }
            $taskLog = Get-Content -LiteralPath $taskLogPath -Raw -ErrorAction SilentlyContinue
            if ($taskLog -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $taskUrl=$Matches[0]; break }
        }
        $taskFailures = 0
        while ($taskUrl) {
            $taskProxy.Refresh()
            $taskLog = Get-Content -LiteralPath $taskLogPath -Raw -ErrorAction SilentlyContinue
            if ($taskProxy.HasExited -or $taskLog -match 'Unauthorized: Tunnel not found') { break }
            try {
                $taskHealth = Invoke-WebRequest -Uri $taskUrl -UseBasicParsing -TimeoutSec 12
                if ($taskHealth.StatusCode -ne 200 -or $taskHealth.Content -notmatch 'Live barcode scanner') { throw 'Unexpected scanner response.' }
                Set-ScannerState $true $taskUrl 'Ready'
                $taskFailures = 0
            } catch {
                $taskFailures++
                Set-ScannerState $false '' 'The secure scanner is reconnecting. Try again in a few seconds.'
                if ($taskFailures -ge 3) { break }
            }
            Start-Sleep -Seconds 20
        }
        if ($taskProxy -and -not $taskProxy.HasExited) { Stop-Process -Id $taskProxy.Id }
        $taskProxy = $null
        Start-Sleep -Seconds 5
    }
} finally {
    if ($taskProxy -and -not $taskProxy.HasExited) { Stop-Process -Id $taskProxy.Id }
    Set-ScannerState $false '' 'The secure scanner is offline. Start the secure scanner on the warehouse computer.'
    $taskMutex.ReleaseMutex()
    $taskMutex.Dispose()
}
