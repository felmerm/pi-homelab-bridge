"""Direct local Hue HTTPS control. No Amazon/cloud fallback or redirects."""
import asyncio
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl

COLORS = {'red': (0, 254), 'green': (25500, 254), 'blue': (46920, 254),
          'purple': (50000, 254), 'yellow': (12750, 254), 'white': (0, 0)}


class HueAPIError(RuntimeError):
    """Expose only numeric error codes, never key-bearing resource paths/bodies."""
    def __init__(self, result):
        self.error_types = tuple(item['error']['type'] for item in result
                                 if isinstance(item, dict) and isinstance(item.get('error'), dict)
                                 and isinstance(item['error'].get('type'), int))
        super().__init__('Hue resource request rejected')


def local_host(host):
    address = ipaddress.ip_address(host)
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in networks):
        raise ValueError('Hue requires a private LAN IPv4 address')
    return str(address)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, fingerprint, timeout=5):
        host = local_host(host)
        if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise ValueError('Invalid Hue certificate pin')
        # The physical linking ceremony pins the exact certificate. Verification
        # happens before HTTP headers (which carry/use the private key) are sent.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(host, timeout=timeout, context=context)
        self.fingerprint = fingerprint

    def connect(self):
        super().connect()
        actual = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, self.fingerprint):
            self.close()
            raise ssl.SSLError('Hue certificate changed; relink locally')


def hue_request(host, fingerprint, method, path, body=None):
    connection = PinnedHTTPS(host, fingerprint)
    try:
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, data, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('Hue HTTP request rejected')
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Hue response too large')
        result = json.loads(raw)
        if isinstance(result, list) and any('error' in item for item in result):
            raise HueAPIError(result)
        return result
    finally:
        connection.close()


class HueLocal:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('HUE_CONFIG', '/etc/notification-bridge-hue/hue.json'))

    def load(self):
        config = json.loads(self.path.read_text())
        local_host(config['host'])
        if not re.fullmatch('[0-9a-f]{64}', config['certificate_sha256']):
            raise ValueError('Invalid Hue certificate pin')
        if not re.fullmatch('[a-zA-Z0-9_-]{8,128}', config['username']):
            raise ValueError('Invalid Hue application key')
        targets = config['targets']
        if not isinstance(targets, dict) or not targets:
            raise ValueError('No Hue targets configured')
        for target in targets.values():
            if (target.get('type') not in ('group', 'light')
                    or not re.fullmatch('[0-9]{1,8}', str(target.get('id', '')))):
                raise ValueError('Invalid Hue resource')
        return config

    def configured(self):
        try:
            self.load()
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return False

    async def set_lights(self, target, color):
        config = self.load()
        item = config['targets'].get(target)
        if item is None or color not in COLORS:
            raise ValueError('Hue target/color not configured')
        hue, saturation = COLORS[color]
        kind = 'groups' if item['type'] == 'group' else 'lights'
        suffix = 'action' if kind == 'groups' else 'state'
        path = f"/api/{config['username']}/{kind}/{item['id']}/{suffix}"
        await asyncio.to_thread(hue_request, config['host'], config['certificate_sha256'],
                               'PUT', path, {'on': True, 'hue': hue, 'sat': saturation})
