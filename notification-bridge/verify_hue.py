"""Read-only local Hue verification; keys and key-bearing paths stay private."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hue_local import HueLocal, hue_request


def verify():
    config = HueLocal().load()
    target = config['targets']['all lights']
    group = hue_request(config['host'], config['certificate_sha256'], 'GET',
                        f"/api/{config['username']}/groups/{target['id']}")
    print(json.dumps({'ok': True, 'local_https': True, 'target': 'all lights',
                      'group': group.get('name'), 'light_count': len(group.get('lights', [])),
                      'lights_changed': False}))


if __name__ == '__main__':
    try:
        verify()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__}))
        raise SystemExit(1)
