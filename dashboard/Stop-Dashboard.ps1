$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'server.py'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$owned = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $pythonPath -and $_.CommandLine -and $_.CommandLine.Contains($scriptPath)
}
foreach ($process in $owned) { Stop-Process -Id $process.ProcessId -ErrorAction Stop }
Write-Host 'Dashboard stopped. The separate Pi outage recorder is unaffected.'
