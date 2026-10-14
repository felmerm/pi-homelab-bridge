$ErrorActionPreference = 'Stop'
$archiveId = [Guid]::NewGuid().ToString('N')
$archivePath = Join-Path $env:TEMP "pi-diagnostics-$archiveId.tar"
$files = @('diagnostics.py', 'snapshot.py', 'report.py', 'install.py', 'test_diagnostics.py', 'pc_monitor.py',
    'pi-diagnostics.service', 'pi-diagnostics.timer', '90-pi-diagnostics.conf')
& tar -cf $archivePath -C $PSScriptRoot @files
if ($LASTEXITCODE -ne 0) { throw 'Could not package diagnostics.' }
$sshArgs = @('-i', "$env:USERPROFILE\.ssh\id_ed25519", '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'HostKeyAlias=raspberrypi.local')
$remoteHost = 'pi@192.168.1.50'
$remoteArchive = "/tmp/pi-diagnostics-$archiveId.tar"
& scp @sshArgs $archivePath "${remoteHost}:$remoteArchive"
if ($LASTEXITCODE -ne 0) { throw 'Pi unreachable or transfer failed; diagnostics not installed.' }
$stage = (& ssh @sshArgs $remoteHost 'sudo mktemp -d /tmp/pi-diagnostics.XXXXXXXX').Trim()
if ($LASTEXITCODE -ne 0 -or $stage -notmatch '^/tmp/pi-diagnostics\.[a-zA-Z0-9]+$') { throw 'Could not stage diagnostics safely.' }
& ssh @sshArgs $remoteHost "sudo tar -xf $remoteArchive -C $stage && sudo python3 -I $stage/install.py"
if ($LASTEXITCODE -ne 0) { throw 'Diagnostics installation failed; read the installer output.' }
Write-Host 'Pi crash diagnostics installed. Run Save-Diagnostics.ps1 to collect evidence.'
