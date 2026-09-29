param([string]$Username)
$ErrorActionPreference = 'Stop'
if (-not $Username) { $Username = Read-Host 'StoreHub back-office subdomain (for example dailycentro)' }
if ($Username -notmatch '^[a-zA-Z0-9][a-zA-Z0-9-]*$') { throw 'Enter the subdomain only, without a URL.' }
$taskToken = Read-Host 'StoreHub API token (hidden)' -AsSecureString
if ($taskToken.Length -eq 0) { throw 'The API token is required.' }
$taskCredential = New-Object System.Management.Automation.PSCredential($Username, $taskToken)
$taskCredential | Export-Clixml -LiteralPath (Join-Path $PSScriptRoot 'data\storehub-connection.xml')
Remove-Variable taskToken,taskCredential
Write-Host 'StoreHub credentials saved encrypted for this Windows account. Restart the warehouse app to use them.'
