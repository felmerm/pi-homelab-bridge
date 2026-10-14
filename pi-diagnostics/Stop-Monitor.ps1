$ErrorActionPreference = 'Stop'
$logPath = Join-Path $PSScriptRoot 'local-logs'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
Set-Content -LiteralPath (Join-Path $logPath 'stop.request') -Value 'Stop recorder' -Encoding ascii
Write-Host 'Recorder will stop within a few seconds. Its logs are kept.'
