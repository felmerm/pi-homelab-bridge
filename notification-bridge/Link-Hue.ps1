$ErrorActionPreference = 'Stop'
Write-Host 'Link Hue locally on the Pi. Have the Hue bridge link button within reach.'
& ssh -tt -i "$env:USERPROFILE\.ssh\id_ed25519" -o HostKeyAlias=raspberrypi.local pi@192.168.1.50 'sudo python3 -I /opt/notification-bridge/link_hue.py'
if ($LASTEXITCODE -ne 0) { throw 'Hue linking did not finish. Read the safe message above.' }
