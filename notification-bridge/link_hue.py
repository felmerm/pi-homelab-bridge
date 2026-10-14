"""Run as root in a private terminal when home. Press the Hue link button."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hue_local import HueAPIError, local_host, hue_request


def main(host_arg=None, wait_for_button=False):
    if os.geteuid() != 0:
        raise SystemExit('Run this linker with sudo on the Pi.')
    host = local_host(host_arg or input('Hue bridge LAN IPv4 (from the Hue app): ').strip())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((host, 443), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            fingerprint = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
    print('Hue certificate SHA-256: ' + fingerprint)
    print('First-link trust: verify this IP is your Hue bridge on your trusted home LAN.')
    if wait_for_button:
        print('Waiting up to 120 seconds for the physical link button on ' + host + '.', flush=True)
        deadline = time.monotonic() + 120
        while True:
            try:
                result = hue_request(host, fingerprint, 'POST', '/api', {'devicetype': 'local-control#pi'})
                break
            except HueAPIError as exc:
                if exc.error_types != (101,) or time.monotonic() >= deadline:
                    raise
                time.sleep(2)
    else:
        if input('Press its physical link button, then type LINK: ').strip() != 'LINK':
            raise SystemExit('Linking cancelled.')
        result = hue_request(host, fingerprint, 'POST', '/api', {'devicetype': 'local-control#pi'})
    username = result[0]['success']['username']
    groups = hue_request(host, fingerprint, 'GET', f'/api/{username}/groups')
    print('0: all lights')
    for identifier, group in groups.items():
        print(identifier + ': ' + group.get('name', 'Unnamed group'))
    identifier = input('Group to control (Enter for all lights): ').strip() or '0'
    if identifier != '0' and identifier not in groups:
        raise SystemExit('Unknown group. No local configuration saved; rerun to link.')
    config = {'host': host, 'certificate_sha256': fingerprint, 'username': username,
              'targets': {'all lights': {'type': 'group', 'id': identifier}}}
    directory = Path('/etc/notification-bridge-hue')
    directory.mkdir(exist_ok=True)
    gid = grp.getgrnam('notification-bridge').gr_gid
    os.chown(directory, 0, gid)
    directory.chmod(0o750)
    temp = directory / 'hue.json.tmp'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    os.fchown(fd, 0, gid)
    with os.fdopen(fd, 'w') as output:
        json.dump(config, output)
    os.replace(temp, directory / 'hue.json')
    print('Hue linked locally. Application key was not displayed. No lights changed.')
    print('The over-230 scene stays paused; restart the bridge if this directory was newly created.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host')
    parser.add_argument('--wait-for-button', action='store_true')
    args = parser.parse_args()
    try:
        main(args.host, args.wait_for_button)
    except Exception as exc:
        # Never display upstream bodies, private keys or key-bearing request paths.
        codes = ' API codes ' + ','.join(map(str, exc.error_types)) if isinstance(exc, HueAPIError) else ''
        print('Hue linking failed (' + type(exc).__name__ + codes + '). Verify bridge IP/link button.', file=sys.stderr)
        sys.exit(1)
