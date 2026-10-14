"""Configure the scale logger to use the local, authenticated Alexa bridge."""
import json
from pathlib import Path
import subprocess

subprocess.run(['install', '-o', 'root', '-g', 'renpho-scale', '-m', '640',
                '/etc/notification-bridge-iphone.token', '/etc/renpho-scale/announcement.token'], check=True)
path = Path('/etc/renpho-scale/sheets.json')
config = json.loads(path.read_text())
config.pop('announce_after_upload', None)
config.update(announce_on_reading=True,
              announcement_token='/etc/renpho-scale/announcement.token',
              announcement_certificate='/etc/notification-bridge-iphone.crt')
path.write_text(json.dumps(config, indent=2) + '\n')
print('Local announcement connection configured; no secrets displayed.')
