param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$SiteId
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'data\warehouse.db'))) {
    throw 'Local warehouse database is missing. Do not start a public tunnel.'
}

$secret = Read-Host 'Enter the Netlify proxy secret' -AsSecureString
$secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:NETLIFY_PROXY_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
}
if ([string]::IsNullOrWhiteSpace($env:NETLIFY_PROXY_SECRET)) {
    throw 'A nonempty Netlify proxy secret is required.'
}

$env:WAREHOUSE_DEPLOY_MODE = '1'
$env:REQUIRE_NETLIFY_PROXY = '1'
$env:NETLIFY_SITE_ID = $SiteId

Write-Host 'Starting the private warehouse service on 127.0.0.1:5077.'
Write-Host 'Keep this window open while the site is in use.'
try {
    & "$PSScriptRoot\start.ps1"
} finally {
    Remove-Item Env:\NETLIFY_PROXY_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:\NETLIFY_SITE_ID -ErrorAction SilentlyContinue
    Remove-Item Env:\REQUIRE_NETLIFY_PROXY -ErrorAction SilentlyContinue
    Remove-Item Env:\WAREHOUSE_DEPLOY_MODE -ErrorAction SilentlyContinue
}
