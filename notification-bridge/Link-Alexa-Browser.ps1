$ErrorActionPreference = 'Stop'
$piBrowserKey = Join-Path $env:USERPROFILE '.ssh\id_ed25519'
$amazonBrowserUrl = (& ssh -i $piBrowserKey -o BatchMode=yes pi@raspberrypi.local 'sudo -n -u notification-bridge /opt/notification-bridge/venv/bin/python /opt/notification-bridge/browser_link.py prepare') -join ''
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Amazon browser session.' }
$amazonBrowserUri = [Uri]$amazonBrowserUrl
if ($amazonBrowserUri.Scheme -ne 'https' -or $amazonBrowserUri.Host -ne 'www.amazon.com' -or $amazonBrowserUri.AbsolutePath -ne '/ap/signin' -or $amazonBrowserUri.Port -ne 443 -or $amazonBrowserUri.UserInfo) {
    throw 'Refusing to open an unexpected login address.'
}
Write-Host 'Opening the real https://www.amazon.com sign-in page in your default browser.'
Start-Process -FilePath $amazonBrowserUrl
ssh -t -i $piBrowserKey pi@raspberrypi.local 'sudo -n -u notification-bridge /opt/notification-bridge/venv/bin/python /opt/notification-bridge/browser_link.py finish && sudo -n systemctl restart notification-bridge'
if ($LASTEXITCODE -ne 0) { Write-Host 'Browser linking did not finish. Read the stage/error above.' -ForegroundColor Yellow }
