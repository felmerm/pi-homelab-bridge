"""Fixed catalog/play helper sent over pinned SSH; bridge secrets stay on the Pi."""
import hashlib
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import ssl
import sys
import subprocess
import os
import shutil
import tempfile
import urllib.request

CATALOG = Path('/etc/renpho-scale/reactions.json')
INSTALLED_CATALOG = Path('/opt/renpho-scale/reactions.json')
CATALOG_BACKUPS = Path('/root/meme-scale-catalog-backups')
CATALOG_LOCK = Path('/run/meme-scale-catalog.lock')


class ResponseError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def validate_play(body, draft=False):
    fields = {'pool', 'index', 'weight', 'revision'} | ({'catalog'} if draft else set())
    if not isinstance(body, dict) or set(body) != fields:
        raise ResponseError(400, 'Choose a response and sample weight.')
    if (body['pool'] not in ('good', 'bad') or type(body['index']) is not int
            or not 0 <= body['index'] < 100):
        raise ResponseError(400, 'Choose a valid response.')
    weight = body['weight']
    if type(weight) not in (int, float) or not math.isfinite(weight) or not 7 <= weight <= 396:
        raise ResponseError(400, 'Use a sample weight between 7 and 396 lb.')
    if not isinstance(body['revision'], str) or not re.fullmatch('[a-f0-9]{64}', body['revision']):
        raise ResponseError(400, 'Reload responses before playing.')


def validate_edit(body):
    if (not isinstance(body, dict) or set(body) != {'catalog', 'revision'}
            or not isinstance(body['catalog'], dict)
            or not isinstance(body['revision'], str) or not re.fullmatch('[a-f0-9]{64}', body['revision'])):
        raise ResponseError(400, 'Provide draft JSON and its live revision.')


def validate_catalog(catalog):
    sys.path.insert(0, '/opt/notification-bridge')
    from actions import audio_url, parse_action
    allowed = {'version', 'roast_threshold_lb', 'audio_enabled', 'sounds', 'good', 'bad', 'special'}
    if not isinstance(catalog, dict) or set(catalog) - allowed:
        raise ResponseError(400, 'Catalog has an unknown top-level field.')
    if type(catalog.get('version')) is not int or catalog['version'] != 1:
        raise ResponseError(400, 'version must be 1.')
    if type(catalog.get('audio_enabled')) is not bool:
        raise ResponseError(400, 'audio_enabled must be true or false.')
    cutoff = catalog.get('roast_threshold_lb', 100)
    if type(cutoff) not in (int, float) or not math.isfinite(cutoff) or not 7 <= cutoff <= 396:
        raise ResponseError(400, 'roast_threshold_lb must be between 7 and 396.')
    sounds = catalog.get('sounds')
    if not isinstance(sounds, dict) or not 1 <= len(sounds) <= 40:
        raise ResponseError(400, 'sounds must contain 1–40 named HTTPS clips.')
    for name, url in sounds.items():
        if not isinstance(name, str) or not re.fullmatch('[a-z0-9_]{1,40}', name):
            raise ResponseError(400, 'Use letters, numbers, and underscores for sound names.')
        try: audio_url(url)
        except ValueError: raise ResponseError(400, 'Every sound must use a public HTTPS audio URL.') from None
    for pool in ('good', 'bad'):
        entries = catalog.get(pool)
        if not isinstance(entries, list) or not 2 <= len(entries) <= 100:
            raise ResponseError(400, pool + ' must contain 2–100 responses.')
        for index, entry in enumerate(entries):
            label = f'{pool}[{index}]'
            if not isinstance(entry, dict) or set(entry) - {'sound', 'before', 'after', 'source', 'audio_enabled'}:
                raise ResponseError(400, label + ' has an unknown response field.')
            if 'audio_enabled' in entry and type(entry['audio_enabled']) is not bool:
                raise ResponseError(400, label + '.audio_enabled must be true or false.')
            if not isinstance(entry.get('sound'), str) or entry['sound'] not in sounds:
                raise ResponseError(400, label + ' must name an existing sound.')
            for key in ('before', 'after'):
                text = entry.get(key, '')
                if not isinstance(text, str) or len(text) > 700:
                    raise ResponseError(400, label + '.' + key + ' must be text up to 700 characters.')
                remainder = text.replace('{weight}', '')
                if '{' in remainder or '}' in remainder:
                    raise ResponseError(400, label + '.' + key + ' supports only the {weight} placeholder.')
            if 'source' in entry and (not isinstance(entry['source'], str) or len(entry['source']) > 2048):
                raise ResponseError(400, label + '.source must be a short attribution string.')
            try:
                before, after = (entry.get(key, '').format(weight='396') for key in ('before', 'after'))
                if catalog['audio_enabled'] and entry.get('audio_enabled', True):
                    parse_action({'action':'audio', 'url':sounds[entry['sound']], 'before':before, 'after':after})
                else:
                    parse_action({'action':'speak', 'message':' '.join(part for part in (before, after) if part)})
            except ValueError: raise ResponseError(400, label + ' contains invalid speech or audio.') from None
    special = catalog.get('special')
    if (not isinstance(special, dict) or set(special) != {'enabled', 'sound', 'lights_target'}
            or type(special['enabled']) is not bool or not isinstance(special['sound'], str)
            or special['sound'] not in sounds or special['lights_target'] != 'all lights'):
        raise ResponseError(400, 'special must contain enabled, a valid sound, and lights_target: all lights.')
    if len(json.dumps(catalog).encode()) > 65536:
        raise ResponseError(400, 'Keep the catalog under 64 KB.')
    return catalog


def load_catalog(path=None):
    path = path or CATALOG
    data = path.read_bytes()
    catalog = json.loads(data)
    revision = hashlib.sha256(data).hexdigest()
    return catalog, revision


def public_catalog(catalog, revision):
    # Only editable response text/sound labels, never config secrets or raw data.
    entries = []
    for pool in ('good', 'bad'):
        for index, entry in enumerate(catalog[pool]):
            entries.append({'pool': pool, 'index': index, 'sound': entry['sound'],
                            'before': entry.get('before', ''), 'after': entry.get('after', '')})
    return {'revision': revision, 'entries': entries, 'audio_enabled': bool(catalog.get('audio_enabled')),
            'catalog': catalog}


def build_action(body, catalog, revision):
    validate_play(body)
    if body['revision'] != revision:
        raise ResponseError(409, 'Responses changed on the Pi. Reload before playing.')
    entries = catalog[body['pool']]
    if body['index'] >= len(entries):
        raise ResponseError(400, 'That response no longer exists. Reload responses.')
    # Same rounding/template rules as the scale, without selection or outbox writes.
    weight = f"{round(body['weight'], 1):g}"
    entry = entries[body['index']]
    before = entry.get('before', '').format(weight=weight)
    after = entry.get('after', '').format(weight=weight)
    if catalog.get('audio_enabled', False) and entry.get('audio_enabled', True):
        return {'action': 'audio', 'url': catalog['sounds'][entry['sound']],
                'before': before, 'after': after}
    return {'action': 'speak', 'message': ' '.join(p for p in (before, after) if p)}


def queue_action(action):
    sys.path.insert(0, '/opt/notification-bridge')
    from actions import parse_action
    parse_action(action)
    token = next(line.partition('=')[2].strip().strip('"') for line in
                 Path('/etc/notification-bridge.env').read_text().splitlines()
                 if line.startswith('BRIDGE_TOKEN='))
    tls = ssl.create_default_context(cafile='/etc/notification-bridge-tls/server.crt')
    req = urllib.request.Request('https://127.0.0.1:8765/actions', json.dumps(action).encode(),
                                 {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, context=tls, timeout=8) as response:
        result = json.loads(response.read(4096))
        if response.status not in (200, 202):
            raise RuntimeError('Queue unavailable')
    return {'status': 'duplicate' if result.get('duplicate') else 'queued',
            'message': 'Already queued recently. Listen on your Echo.' if result.get('duplicate')
                       else 'Queued for your Echo. Listen for the speech and sound.'}


def atomic_catalog(path, data, uid, gid, mode):
    fd, name = tempfile.mkstemp(prefix='.catalog-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchown(stream.fileno(), uid, gid); os.fchmod(stream.fileno(), mode)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def apply_catalog(body):
    import fcntl
    validate_edit(body); validate_catalog(body['catalog'])
    with CATALOG_LOCK.open('a') as lock:
        os.chmod(lock.name, 0o600); fcntl.flock(lock, fcntl.LOCK_EX)
        old, revision = load_catalog()
        if revision != body['revision']:
            raise ResponseError(409, 'Live JSON changed. Reload it before applying your draft.')
        data = (json.dumps(body['catalog'], indent=2, ensure_ascii=False) + '\n').encode()
        backup_dir = CATALOG_BACKUPS; backup_dir.mkdir(mode=0o700, exist_ok=True)
        backup = backup_dir/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        shutil.copyfile(CATALOG, backup); backup.chmod(0o600)
        targets = (CATALOG, INSTALLED_CATALOG)
        snapshots = [(path, path.read_bytes(), path.stat()) for path in targets]
        try:
            for path, previous, stat in snapshots:
                atomic_catalog(path, data, stat.st_uid, stat.st_gid, stat.st_mode & 0o777)
            subprocess.run(['systemctl', 'restart', 'renpho-sheets'], check=True, timeout=20, capture_output=True)
            subprocess.run(['systemctl', 'is-active', '--quiet', 'renpho-sheets'], check=True, timeout=5)
        except Exception:
            for path, previous, stat in snapshots:
                atomic_catalog(path, previous, stat.st_uid, stat.st_gid, stat.st_mode & 0o777)
            subprocess.run(['systemctl', 'restart', 'renpho-sheets'], check=True, timeout=20, capture_output=True)
            raise ResponseError(503, 'Apply failed. Previous JSON was restored.') from None
        result = public_catalog(body['catalog'], hashlib.sha256(data).hexdigest())
        result['message'] = 'Applied to the Pi. Scale service reloaded; previous JSON backed up.'
        return result


def perform(operation, body=None):
    if operation not in ('catalog', 'play', 'validate', 'play-draft', 'apply'):
        raise ResponseError(400, 'Unknown response operation.')
    if operation in ('play', 'play-draft'):
        validate_play(body, draft=operation == 'play-draft')
    elif operation in ('validate', 'apply'):
        validate_edit(body)
    if operation == 'apply':
        return 200, apply_catalog(body)
    catalog, revision = load_catalog()
    if operation == 'catalog':
        return 200, public_catalog(catalog, revision)
    if operation in ('validate', 'play-draft'):
        validate_catalog(body['catalog'])
        if body['revision'] != revision:
            raise ResponseError(409, 'Live JSON changed. Reload it before testing or applying your draft.')
        if operation == 'validate':
            result = public_catalog(body['catalog'], revision)
            result['message'] = 'Draft is valid. Preview and playback now use your edits.'
            return 200, result
        selection = {key:value for key,value in body.items() if key != 'catalog'}
        return 202, queue_action(build_action(selection, body['catalog'], revision))
    return 202, queue_action(build_action(body, catalog, revision))


def main(payload):
    try:
        code, result = perform(payload['operation'], payload.get('body'))
    except ResponseError as exc:
        code, result = exc.code, {'error': exc.message}
    except Exception:
        code, result = 503, {'error': 'Pi response test unavailable. Check your Echo before retrying playback.'}
    print(json.dumps({'code': code, 'result': result}))
