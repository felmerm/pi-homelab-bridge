$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$archiveId = [Guid]::NewGuid().ToString('N')
$archivePath = Join-Path $env:TEMP "homelab-controls-$archiveId.tar"
$files = @(
    'notification-bridge/actions.py','notification-bridge/alexa_direct.py','notification-bridge/bridge.py',
    'notification-bridge/control_state.py','notification-bridge/dispatch.py','notification-bridge/hue_local.py',
    'notification-bridge/link_hue.py','notification-bridge/verify_hue.py','notification-bridge/set_hue_target.py','notification-bridge/ssh_receive.py','notification-bridge/browser_link.py','notification-bridge/test_bridge.py',
    'notification-bridge/test_local_controls.py','notification-bridge/install_controls.py',
    'notification-bridge/docs/CONTROLS.md','notification-bridge/docs/ACTIONS.md',
    'renpho-scale/reactions.py','renpho-scale/reactions.json','renpho-scale/sheets_logger.py',
    'renpho-scale/test_reactions.py','renpho-scale/test_sheets_logger.py',
    'renpho-scale/ROAST-SOURCES.md','renpho-scale/REACTIONS.md'
)
& tar -cf $archivePath -C $workspace @files
if ($LASTEXITCODE -ne 0) { throw 'Could not package the reviewed source files.' }
$sshArgs = @('-i',"$env:USERPROFILE\.ssh\id_ed25519",'-o','BatchMode=yes','-o','ConnectTimeout=8','-o','HostKeyAlias=raspberrypi.local')
$remoteHost = 'pi@192.168.1.50'
$remoteArchive = "/tmp/homelab-controls-$archiveId.tar"
& scp @sshArgs $archivePath "${remoteHost}:$remoteArchive"
if ($LASTEXITCODE -ne 0) { throw 'Pi unreachable or source transfer failed; nothing installed.' }
$stage = (& ssh @sshArgs $remoteHost 'sudo mktemp -d /tmp/homelab-controls.XXXXXXXX').Trim()
if ($LASTEXITCODE -ne 0 -or $stage -notmatch '^/tmp/homelab-controls\.[a-zA-Z0-9]+$') { throw 'Could not create a safe staging directory.' }
& ssh @sshArgs $remoteHost "sudo tar -xf $remoteArchive -C $stage && sudo python3 $stage/notification-bridge/install_controls.py $stage"
if ($LASTEXITCODE -ne 0) { throw 'Deployment did not finish; check the tests and rollback message above.' }
Write-Host 'Local controls installed. Hue can now be linked with Link-Hue.ps1.'
