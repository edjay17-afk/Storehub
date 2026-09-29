# DPAPI credential file can be opened only by its Windows user on this computer.
$taskCredentialFile = Join-Path $PSScriptRoot 'data\storehub-connection.xml'
if (Test-Path -LiteralPath $taskCredentialFile) {
    $taskCredential = Import-Clixml -LiteralPath $taskCredentialFile
    $env:STOREHUB_USERNAME = $taskCredential.UserName
    $env:STOREHUB_API_TOKEN = $taskCredential.GetNetworkCredential().Password
    Remove-Variable taskCredential
}
