"""Record reachability, without sending SSH credentials or using an SSH session."""
import argparse
import ipaddress
import socket
import time
from pathlib import Path

from diagnostics import append_record, timestamps


def probe(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return {'reachable': True}
    except OSError as error:
        return {'reachable': False, 'error': type(error).__name__}


def local_route(host):
    try:
        # UDP connect selects a route but sends no packet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.connect((host, 22))
            local = ipaddress.ip_address(client.getsockname()[0])
            return local in ipaddress.ip_network('192.168.1.0/24')
    except OSError:
        return False


def run(directory, host, reference):
    directory.mkdir(parents=True, exist_ok=True)
    while not (directory / 'stop.request').exists():
        record = {**timestamps(), 'pi_ssh': probe(host, 22),
                  'nas_web_reference': probe(reference, 80),
                  'pc_has_home_lan_route': local_route(host)}
        append_record(directory, 'reachability-', record, max_bytes=8 * 1024 * 1024)
        # Stop promptly without killing processes or risking PID reuse.
        for _ in range(30):
            if (directory / 'stop.request').exists():
                return
            time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--host', default='192.168.1.50')
    parser.add_argument('--reference', default='192.168.1.100')
    args = parser.parse_args()
    run(args.directory, args.host, args.reference)
