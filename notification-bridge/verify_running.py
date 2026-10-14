"""Run as root on Pi: verify receiver without printing the bridge secret."""
import json
import ssl
from pathlib import Path
import urllib.error
import urllib.request

token = next(line.partition("=")[2].strip().strip('"')
             for line in Path("/etc/notification-bridge.env").read_text().splitlines()
             if line.startswith("BRIDGE_TOKEN="))

def request(path, body=None, authorized=True):
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://raspberrypi.local:8765" + path, data, headers)
    tls = ssl.create_default_context(cafile="/etc/notification-bridge-tls/server.crt")
    try:
        response = urllib.request.urlopen(req, timeout=3, context=tls)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)

assert request("/health")[0] == 200
assert request("/status", authorized=False)[0] == 401
assert request("/notify", {"message": "receiver test", "dry_run": True})[0] == 200
status = request("/status")
assert status[0] == 200 and status[1]["backend"] == "amazon_direct"
print("PASS: health, authentication, dry run, direct backend")
print(json.dumps(status[1]))
