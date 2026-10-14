$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'pc_monitor.py'
$logPath = Join-Path $PSScriptRoot 'local-logs'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $pythonPath -and $_.CommandLine -and $_.CommandLine.Contains($scriptPath)
}
if ($existing) { Write-Host 'PC Pi reachability recorder is already running.'; return }
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$stopPath = Join-Path $logPath 'stop.request'
if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath }
$arguments = @(('"' + $scriptPath + '"'), '--directory', ('"' + $logPath + '"'))
$process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WindowStyle Hidden -PassThru `
    -RedirectStandardError (Join-Path $logPath 'monitor-error.log')
Write-Host "PC Pi reachability recorder started (PID $($process.Id)); saves one record every 30 seconds."
Write-Host "Logs: $logPath"
