"""Low-frequency private weight cache; credentials remain on the Pi."""
import json
import datetime as dt
from pathlib import Path
import shutil
import subprocess
import runtime_config as config
import threading
import time
from remote_weights import merge_rows

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'local-logs' / 'weight-history.json'
LOCK = threading.Lock()
FETCH_LOCK = threading.Lock()
CHANGES = threading.Condition(LOCK)
LAST_CHECK = 0.0
SNAPSHOT = None
LIVE_ROWS = None
STREAM_LIVE = False
REVISION = 0


def remote():
    ssh = shutil.which('ssh')
    key = config.SSH_KEY
    if not ssh or not key.is_file():
        raise ValueError('SSH unavailable')
    args = [ssh, '-i', str(key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=4', '-o', 'ConnectionAttempts=1', '-o', 'HostKeyAlias=' + config.PI_HOST_ALIAS,
            config.SSH_TARGET, 'sudo -n /opt/renpho-scale/venv/bin/python -I -']
    result = subprocess.run(args, input=(ROOT / 'remote_weights.py').read_text(encoding='utf-8'),
                            capture_output=True, text=True, encoding='utf-8', timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Pi unavailable')
    data = json.loads(result.stdout)
    if not isinstance(data.get('rows'), list) or len(data['rows']) > 20000:
        raise ValueError('History unavailable')
    return data


def load_saved():
    global SNAPSHOT
    # Caller owns LOCK.
    if SNAPSHOT is None:
        try:
            SNAPSHOT = json.loads(CACHE.read_text(encoding='utf-8'))
            SNAPSHOT.update(cached=True, error='Showing saved sheet history until it refreshes.')
        except (OSError, ValueError, AttributeError):
            SNAPSHOT = {'rows': [], 'synced_at': None, 'timezone': 'America/Vancouver', 'cached': True}
    SNAPSHOT['stream_live'] = STREAM_LIVE


def save():
    # Caller owns LOCK. Called only on a changed reading or sheet refresh.
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE.with_suffix('.tmp')
    temporary.write_text(json.dumps(SNAPSHOT), encoding='utf-8')
    temporary.replace(CACHE)


def overlay_live(data):
    if LIVE_ROWS is not None:
        live_by_id = {row['id']: row for row in LIVE_ROWS}
        confirmed = [{**live_by_id.get(row['id'], {}), **row} for row in data['rows'] if row['status'] == 'synced']
        combined = {row['id']: row for row in data['rows']}
        combined.update({row['id']: row for row in LIVE_ROWS})
        data['rows'] = merge_rows(confirmed, list(combined.values()))
        data['outbox_ok'] = STREAM_LIVE or data.get('outbox_ok', False)
    data['stream_live'] = STREAM_LIVE
    return data


def stream_state(online, rows=None, timezone_name=None):
    global STREAM_LIVE, LIVE_ROWS, SNAPSHOT, REVISION
    with CHANGES:
        load_saved()
        changed = STREAM_LIVE != online or rows is not None and rows != LIVE_ROWS
        STREAM_LIVE = online
        if rows is not None:
            LIVE_ROWS = rows
            SNAPSHOT['live_at'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            SNAPSHOT['timezone'] = timezone_name
            if SNAPSHOT.get('cached'):
                SNAPSHOT['error'] = 'Showing saved sheet history; live Pi readings are current.'
        overlay_live(SNAPSHOT)
        if changed:
            if rows is not None:
                try:
                    save()
                except OSError:
                    pass
            REVISION += 1
            CHANGES.notify_all()


def next_event(after, timeout=10):
    with CHANGES:
        if after == REVISION:
            CHANGES.wait_for(lambda: after != REVISION, timeout)
        load_saved()
        return REVISION, json.dumps(SNAPSHOT)


def watch():
    delay = 5
    while True:
        process = None
        try:
            ssh = shutil.which('ssh')
            key = config.SSH_KEY
            if not ssh or not key.is_file():
                raise ValueError('SSH unavailable')
            args = [ssh, '-i', str(key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                    '-o', 'ConnectTimeout=4', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                    '-o', 'HostKeyAlias=' + config.PI_HOST_ALIAS, config.SSH_TARGET, 'sudo -n python3 -u -I -']
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            process.stdin.write((ROOT / 'remote_weight_watch.py').read_text(encoding='utf-8'))
            process.stdin.close()
            while True:
                line = process.stdout.readline(4_000_000)
                if not line:
                    break
                event = json.loads(line)
                if event.get('kind') == 'readings' and isinstance(event.get('rows'), list) and len(event['rows']) <= 10000:
                    stream_state(True, event['rows'], event['timezone'])
                    delay = 5
                elif event.get('kind') == 'unavailable':
                    break
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
        finally:
            stream_state(False)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()
                if not process.stdin.closed:
                    process.stdin.close()
        time.sleep(delay)
        delay = min(delay * 2, 60)


def history():
    global LAST_CHECK, SNAPSHOT
    with FETCH_LOCK:
        with LOCK:
            load_saved()
            cached = dict(SNAPSHOT)
        if LAST_CHECK and time.monotonic() - LAST_CHECK < 60:
            return cached
        LAST_CHECK = time.monotonic()
        try:
            data = remote()
            data['cached'] = not data['sheet_ok']
            data['error'] = (None if data['sheet_ok'] and data['outbox_ok'] else
                             'Sheets unavailable. Showing saved history and available Pi readings.' if not data['sheet_ok'] else
                             'Sheet history loaded; pending Pi uploads could not be checked.')
            with LOCK:
                if not data['sheet_ok']:
                    data['rows'] = merge_rows(SNAPSHOT['rows'], data['rows'])
                    data['synced_at'] = SNAPSHOT.get('synced_at')
                if SNAPSHOT.get('live_at'):
                    data['live_at'] = SNAPSHOT['live_at']
                SNAPSHOT = overlay_live(data)
                save()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            with LOCK:
                SNAPSHOT.update(cached=True, error='Sheet history could not refresh. Showing saved history and live Pi readings.' if STREAM_LIVE else
                                'Pi unavailable. Showing saved weight history.' if SNAPSHOT['rows'] else
                                'Pi unavailable. Weight history will appear when it reconnects.')
        with LOCK:
            return dict(SNAPSHOT)
