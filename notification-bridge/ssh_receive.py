"""Forced SSH command: treat the supplied command as speech, never execute it."""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
# The forced command runs Python with -I. Explicitly load sibling code from
# this root-owned installation, rather than user-site packages or cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from actions import parse_action


def payload(command):
    if command.startswith('bridge-action:'):
        if len(command.encode('utf-8')) > 8192:
            raise ValueError('Bridge action is too long.')
        try:
            body = json.loads(command.removeprefix('bridge-action:'))
            parse_action(body)
        except (ValueError, TypeError):
            raise ValueError('Invalid bridge action. Check the documented action JSON fields.') from None
        return body
    if len(command.encode('utf-8')) > 6100:
        raise ValueError('Notification text is too long; limit it to 1500 characters.')
    dry_run = command.startswith('bridge-dry-run:')
    speech = command.removeprefix('bridge-dry-run:') if dry_run else command
    if not speech.strip():
        raise ValueError('No notification text received. Test with a real incoming notification, and select its Message or Body property.')
    if len(speech) > 1500:
        raise ValueError('Notification text is too long; limit it to 1500 characters.')
    return {'message': speech, 'dry_run': dry_run}


def main():
    try:
        body = payload(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
        token = Path('/etc/notification-bridge-iphone.token').read_text().strip()
        context = ssl.create_default_context(cafile='/etc/notification-bridge-iphone.crt')
        request = urllib.request.Request(
            'https://127.0.0.1:8765' + ('/actions' if 'action' in body else '/notify'), data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
            method='POST')
        with urllib.request.urlopen(request, context=context, timeout=8) as response:
            if response.status not in (200, 202):
                raise ValueError('Unexpected bridge response.')
        print('Connection verified.' if body.get('dry_run', False) else
              ('Announcement queued.' if body.get('action', 'announce') == 'announce'
               else 'Bridge action queued.'))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('The bridge could not accept the request. No credentials were displayed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
