"""Read-only local Hue discovery. No app keys, pairing, or lighting commands."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import ipaddress
import json
import socket
import subprocess
import time
import xml.etree.ElementTree as ET

NETWORK = ipaddress.ip_network('192.168.1.0/24')


def local(address):
    try:
        return ipaddress.ip_address(address) in NETWORK
    except ValueError:
        return False


def ssdp():
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(('192.168.1.100', 80))
            local_address = route.getsockname()[0]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as client:
            client.bind((local_address, 0))
            client.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_address))
            client.settimeout(1)
            request = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
                       'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n')
            client.sendto(request.encode(), ('239.255.255.250', 1900))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    payload, address = client.recvfrom(8192)
                    if local(address[0]) and ('hue' in payload.decode(errors='replace').lower() or b'IpBridge' in payload):
                        found.add(address[0])
                except socket.timeout:
                    pass
    except OSError:
        pass
    return found


def neighbors():
    script = "Get-NetNeighbor -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '10.0.0.*' -and $_.LinkLayerAddress -ne '00-00-00-00-00-00' -and $_.State -ne 'Permanent' } | Select-Object -ExpandProperty IPAddress | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                                capture_output=True, text=True, timeout=6,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        values = json.loads(result.stdout)
        return {value for value in ([values] if isinstance(values, str) else values) if local(value)}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return set()


def identify(address):
    connection = http.client.HTTPConnection(address, timeout=1.5)
    try:
        connection.request('GET', '/description.xml')
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(32_769)
        if len(raw) > 32_768:
            return None
        root = ET.fromstring(raw)
        values = {element.tag.rsplit('}', 1)[-1]: element.text or '' for element in root.iter()}
        name = values.get('modelName', '')
        if 'hue' not in name.lower():
            return None
        return {'ip': address, 'name': values.get('friendlyName', ''),
                'model': name, 'manufacturer': values.get('manufacturer', '')}
    except (OSError, http.client.HTTPException, ET.ParseError):
        return None
    finally:
        connection.close()


if __name__ == '__main__':
    candidates = ssdp() | neighbors()
    with ThreadPoolExecutor(max_workers=12) as pool:
        bridges = [result for result in pool.map(identify, candidates) if result]
    print(json.dumps({'bridges': bridges}))
