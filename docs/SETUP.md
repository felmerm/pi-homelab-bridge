# Setup reference

This is source for an existing working homelab, not a one-command installer.
Review scripts before applying them to another host. Example IPs, usernames,
MACs, and spreadsheet IDs must be replaced with your own values.

## Prerequisites

- A Debian-based Raspberry Pi with Python 3.13, systemd, working Bluetooth for
  the scale, and SSH. The original setup used a Pi 3B+ alongside Pi-hole.
- An Alexa-enabled Echo and its Amazon account for speech/audio features.
- A compatible Renpho QN-protocol scale for measurement features.
- A Google service account with access only to your intended spreadsheet for
  Sheets logging. Expected columns: Date, Time, Weight (lb), Status / Source, Notes.
- Optional Hue Bridge, linked by pressing its physical button.
- A Windows PC with Python, SSH, and optional FFmpeg/GitHub CLI for the dashboard.

## Python environments

The components belong in separate venvs at `/opt/notification-bridge/venv` and
`/opt/renpho-scale/venv`; the matching source folders are copied to their parent
installation directories. Do not copy private state into the source tree.

```sh
python3 -m venv /opt/notification-bridge/venv
/opt/notification-bridge/venv/bin/pip install -r notification-bridge/requirements.lock
python3 -m venv /opt/renpho-scale/venv
/opt/renpho-scale/venv/bin/pip install -r renpho-scale/requirements.lock
```

The pinned libraries depend on unofficial Amazon behavior and device-specific
Bluetooth protocols. Upgrades should be deliberate and tested. Source in `/opt`
should be root-owned; application users should be able to write only their state.

## Bridge configuration

Create the `notification-bridge` service account with a non-login shell, its
private `/var/lib/notification-bridge` state directory, and the `homelab-controls`
group/state directory described in [local controls](../notification-bridge/docs/CONTROLS.md).
Use the provided systemd unit after configuring those users and directories.

The root-owned `/etc/notification-bridge.env` supplies:

```text
BRIDGE_TOKEN=<generate a unique random value of at least 32 characters>
BRIDGE_HOST=0.0.0.0
BRIDGE_PORT=8765
BRIDGE_TLS_CERT=/etc/notification-bridge-tls/server.crt
BRIDGE_TLS_KEY=/etc/notification-bridge-tls/server.key
ALEXA_STATE_DIR=/var/lib/notification-bridge
```

Generate your own TLS certificate/key; install them with restricted ownership,
and make clients verify the certificate. No certificate from the creator's Pi
is distributed here. The iPhone SSH route verifies loopback TLS on the Pi, so
it does not require trusting a root certificate on the phone.

Edit the public PowerShell launcher's example SSH user/key/host, then use
`notification-bridge/Link-Alexa-Browser.ps1`. Sign in at Amazon in your browser
and paste the authorization callback only into that private terminal. Select
your Echo. Do not paste session data into issues or commit it.

For iPhone transport, `setup_iphone_ssh.sh` prepares the forced-command user
and restricted token/certificate copies. It expects the receiver source staged
at `/tmp/ssh_receive.py`. Install your own phone's public key separately into
`/etc/ssh/authorized_keys/iphone-announce`. In Shortcuts use Run Script over SSH
as `iphone-announce`, with the input text as Script. Notification automations
must extract an available text field; availability varies with iOS version.

## Scale configuration

Copy `sheets.example.json` to `/etc/renpho-scale/sheets.json` and fill in your
scale MAC, spreadsheet ID, and timezone. Place your own restricted Google key
at the credentials path. Never commit either live file. `discover_scale.py`
and `test_qn.py` help check hardware compatibility; close competing phone apps
when testing. They contact the hardware only when invoked explicitly.

Stage the service with `stage_sheets.py`; it expects the example config and unit
in `/tmp`. Configure the local announcement token/certificate copies with
`enable_announcement.py`. Copy the reaction catalog to `/etc/renpho-scale/reactions.json`
and `/opt/renpho-scale/reactions.json`, with mode 640 and appropriate ownership
for the `/etc` copy. The special light scene is disabled in the sample.

Sample audio URLs play only an original test tone. Replace them with hosted
clips you may publish, or choose No audio. Install the service unit only after
the scale, Google access, and local bridge are individually verified.

## Dashboard

The dashboard is standard-library Python plus browser HTML/CSS/JavaScript. It
stays on the PC, binds to `127.0.0.1:8787`, and reaches the Pi over administrator
SSH with strict host-key verification and passwordless sudo for fixed helpers.
That administrator account is privileged; the dashboard is not a sandbox for
untrusted PC users.

Set these local environment values before `dashboard/Start-Dashboard.ps1`:

```powershell
$env:HOMELAB_PI_HOST = 'raspberrypi.local'
$env:HOMELAB_PI_USER = 'your-admin-user'
$env:HOMELAB_SSH_KEY = "$env:USERPROFILE\.ssh\id_ed25519"
$env:HOMELAB_PI_HOST_ALIAS = 'raspberrypi.local'
$env:HOMELAB_NAS_HOST = 'nas.local'
# Optional: a public audio repo YOU own. Omit to disable publishing.
$env:HOMELAB_AUDIO_REPOSITORY = 'your-handle/meme-audio'
```

Verify/pin the Pi's host key through your own trusted setup first. No SSH key
is supplied. The static Pi-hole/NAS links use example hostnames; adjust them
locally. Sheet links open the generic Sheets landing page until you configure
your own destination. Put FFmpeg on PATH for audio conversion and sign into
GitHub CLI only if you choose explicit public audio publication.

The diagnostics PowerShell wrappers similarly contain example SSH values;
edit them before use. The dashboard's data cache, recorder logs, reports, and
audio cache are local private state and ignored by Git.
