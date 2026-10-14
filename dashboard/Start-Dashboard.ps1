$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'server.py'
$logPath = Join-Path $PSScriptRoot 'local-logs'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $pythonPath -and $_.CommandLine -and $_.CommandLine.Contains($scriptPath)
}
if (-not $existing) {
    New-Item -ItemType Directory -Path $logPath -Force | Out-Null
    $process = Start-Process -FilePath $pythonPath -ArgumentList ('"' + $scriptPath + '"') -WindowStyle Hidden -PassThru `
        -RedirectStandardError (Join-Path $logPath 'error.log') -RedirectStandardOutput (Join-Path $logPath 'server.log')
    Start-Sleep -Seconds 1
    if ($process.HasExited) { throw 'Dashboard could not start; check dashboard/local-logs/error.log.' }
}
$response = Invoke-WebRequest 'http://127.0.0.1:8787/' -UseBasicParsing -TimeoutSec 5
if ($response.StatusCode -ne 200) { throw 'Dashboard is not responding.' }
Write-Host 'Dashboard running at http://127.0.0.1:8787/'
