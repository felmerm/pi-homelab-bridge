#!/bin/sh
set -eu
if ! id iphone-announce >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/empty/iphone-announce --shell /bin/sh iphone-announce
fi
install -d -o root -g root -m 755 /var/empty/iphone-announce /etc/ssh/authorized_keys
install -o root -g root -m 644 /tmp/ssh_receive.py /opt/notification-bridge/ssh_receive.py
python3 - <<'PY'
from pathlib import Path
import grp, os
source = Path('/etc/notification-bridge.env').read_text()
values = dict(line.split('=', 1) for line in source.splitlines() if '=' in line and not line.startswith('#'))
token = values['BRIDGE_TOKEN'].strip().strip('"').strip("'")
if len(token) < 32:
    raise SystemExit('Invalid bridge token')
path = '/etc/notification-bridge-iphone.token'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as handle:
    handle.write(token + '\n')
os.chown(path, 0, grp.getgrnam('iphone-announce').gr_gid)
os.chmod(path, 0o640)
PY
install -o root -g root -m 644 /etc/notification-bridge-tls/server.crt /etc/notification-bridge-iphone.crt
cat > /etc/ssh/sshd_config.d/10-iphone-announcements.conf <<'EOF'
Match User iphone-announce
    AuthorizedKeysFile /etc/ssh/authorized_keys/%u
    ForceCommand /usr/bin/python3 -I /opt/notification-bridge/ssh_receive.py
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    MaxSessions 2
Match all
EOF
sshd -t
systemctl reload ssh
echo 'Restricted iPhone announcement account prepared; public key not yet installed.'
