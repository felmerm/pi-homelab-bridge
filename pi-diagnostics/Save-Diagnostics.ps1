$ErrorActionPreference = 'Stop'
$sshArgs = @('-i', "$env:USERPROFILE\.ssh\id_ed25519", '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'HostKeyAlias=raspberrypi.local')
$result = & ssh @sshArgs 'pi@192.168.1.50' 'sudo python3 -I /opt/pi-diagnostics/report.py'
if ($LASTEXITCODE -ne 0) { throw 'Could not collect diagnostics. Pi must be reachable and diagnostics installed.' }
$json = $result -join "`n"
$null = $json | ConvertFrom-Json
$reports = Join-Path $PSScriptRoot 'reports'
New-Item -ItemType Directory -Path $reports -Force | Out-Null
$path = Join-Path $reports ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.json')
[IO.File]::WriteAllText($path, $json, [Text.UTF8Encoding]::new($false))
Write-Host "Health-only report saved: $path"
