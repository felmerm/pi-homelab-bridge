"""Arm one private measurement capture for 55 seconds, display, then remove."""
import json
import os
from pathlib import Path
import time

os.umask(0o077)
state = Path('/var/lib/renpho-scale')
request = state / 'inspect-packet.request'
output = state / 'packet-inspection.json'
deadline = time.time() + 55
if output.exists():
    output.unlink()
request.write_text(str(deadline))
print('Capture ready for 55 seconds. Step onto the scale with Renpho closed.', flush=True)
try:
    while time.time() < deadline:
        if output.exists() and not request.exists():
            print(json.dumps(json.loads(output.read_text()), indent=2), flush=True)
            break
        time.sleep(1)
    else:
        print('No stable measurement packet arrived during this window.', flush=True)
finally:
    request.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
