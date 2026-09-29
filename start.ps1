$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. "$PSScriptRoot\load-storehub.ps1"
$taskPython = 'C:\Python314\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = 'python' }
Write-Host 'Open http://127.0.0.1:5077 in your browser. Close this window to stop the app.'
& $taskPython app.py
