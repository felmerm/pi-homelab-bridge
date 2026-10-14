"""Change the linked group without re-pairing or changing any light state."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hue_local import HueLocal, hue_request


def set_group(identifier):
    if os.geteuid() != 0 or not re.fullmatch(r'[0-9]{1,8}', identifier):
        raise ValueError('Root and a numeric Hue group ID required')
    client = HueLocal()
    config = client.load()
    group = hue_request(config['host'], config['certificate_sha256'], 'GET',
                        f"/api/{config['username']}/groups/{identifier}")
    config['targets']['all lights'] = {'type': 'group', 'id': identifier}
    original = client.path.stat()
    fd, temporary = tempfile.mkstemp(prefix='.hue-', dir=client.path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o640)
            os.fchown(stream.fileno(), 0, original.st_gid)
            json.dump(config, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, client.path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(json.dumps({'ok': True, 'target': 'all lights', 'group_id': identifier,
                      'light_count': len(group.get('lights', [])), 'lights_changed': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group', help='0 means all Hue lights')
    args = parser.parse_args()
    try:
        set_group(args.group)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__}))
        raise SystemExit(1)
