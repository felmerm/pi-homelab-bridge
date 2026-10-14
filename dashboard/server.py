"""Loopback homelab status and explicit response tests; standard library, pinned SSH."""
import datetime as dt
import json
from pathlib import Path
import shutil
import socket
import subprocess
import runtime_config as config
import threading
import time
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from remote_responses import ResponseError, validate_play, validate_edit
import custom_audio
import weight_logs

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
PORT = 8787
ALLOWED_HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
ALLOWED_ORIGINS = {'http://' + host for host in ALLOWED_HOSTS}
LOCK = threading.Lock()
REFRESH = threading.Event()
PLAY_LOCK = threading.Lock()
LAST_PLAY = 0.0
EVENT_CLIENTS = threading.BoundedSemaphore(8)
STATE = {'checking': True, 'updated_at': None, 'pi': {'reachable': None}, 'nas': {'reachable': None},
         'remote': None, 'history': [], 'events': [], 'backup': None}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def probe(host, port):
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=2):
            return {'reachable': True, 'latency_ms': round((time.monotonic() - start) * 1000)}
    except OSError:
        return {'reachable': False}


def read_tail(path, limit=120):
    try:
        with path.open('rb') as stream:
            size = path.stat().st_size
            offset = max(0, size - 512_000)
            stream.seek(offset)
            if offset:
                stream.readline()
            lines = stream.read().decode('utf-8').splitlines()
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except ValueError:
                pass
        return result[-limit:]
    except (OSError, UnicodeError):
        return []


def history():
    files = sorted((WORKSPACE / 'pi-diagnostics' / 'local-logs').glob('reachability-????-??-??.jsonl'))[-2:]
    records = []
    for path in files:
        records.extend(read_tail(path))
    # No fabricated points, even if the recorder is not running.
    return [{'utc': record.get('utc'), 'pi': record.get('pi_ssh', {}).get('reachable'),
             'nas': record.get('nas_web_reference', {}).get('reachable')}
            for record in records[-120:]]


def events(points):
    result = []
    previous = None
    for point in points:
        for key, label in (('pi', 'Pi SSH'), ('nas', 'NAS web interface')):
            if point[key] is None:
                continue
            if previous is None or point[key] != previous[key]:
                result.append({'utc': point['utc'], 'kind': 'ok' if point[key] else 'warn',
                               'text': label + (' reachable' if point[key] else ' unreachable')})
        previous = point
    return list(reversed(result[-6:]))


def backup():
    files = sorted((WORKSPACE / 'pi-backups').glob('pi-*.dpapi.json'))
    if not files:
        return None
    try:
        metadata = json.loads(files[-1].read_text(encoding='utf-8-sig'))
        archive = files[-1].with_suffix('')
        return {'created': metadata.get('created'), 'encrypted': True,
                'present': archive.is_file(), 'includes_scale': False if files[-1].name ==
                'pi-20261006-220948.tar.gz.dpapi.json' else None}
    except (OSError, ValueError):
        return None


def remote_status():
    ssh = shutil.which('ssh')
    key = config.SSH_KEY
    if not ssh or not key.is_file():
        return None, 'SSH key unavailable on this PC'
    args = [ssh, '-i', str(key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=4', '-o', 'ConnectionAttempts=1', '-o', 'HostKeyAlias=' + config.PI_HOST_ALIAS,
            config.SSH_TARGET, 'sudo -n python3 -I -']
    try:
        result = subprocess.run(args, input=(ROOT / 'remote_probe.py').read_text(encoding='utf-8'),
                                capture_output=True, text=True, encoding='utf-8', timeout=24,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            return None, 'SSH status check failed'
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or 'health' not in data:
            return None, 'Pi status collection unavailable'
        return data, None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, 'Pi status collection unavailable'


def remote_responses(operation, body=None):
    ssh = shutil.which('ssh')
    key = config.SSH_KEY
    if not ssh or not key.is_file():
        return 503, {'error': 'SSH access to the Pi is unavailable on this PC.'}
    args = [ssh, '-i', str(key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=4', '-o', 'ConnectionAttempts=1', '-o', 'HostKeyAlias=' + config.PI_HOST_ALIAS,
            config.SSH_TARGET, 'sudo -n python3 -I -']
    # JSON is a Python string literal in stdin, never a shell command argument.
    payload = json.dumps({'operation': operation, 'body': body})
    code = (ROOT / 'remote_responses.py').read_text(encoding='utf-8') + '\nmain(json.loads(' + repr(payload) + '))\n'
    try:
        result = subprocess.run(args, input=code, capture_output=True, text=True, encoding='utf-8', timeout=50 if operation == 'apply' else 20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout) if result.returncode == 0 else None
        if (not isinstance(data, dict) or data.get('code') not in (200, 202, 400, 409, 503)
                or not isinstance(data.get('result'), dict)):
            raise ValueError('Invalid response')
        return data['code'], data['result']
    except (OSError, ValueError, subprocess.SubprocessError):
        return 503, {'error': 'Pi unavailable. Check your Echo before retrying playback.'}


def collect_once():
    pi = probe(config.PI_HOST, 22)
    nas = probe(config.NAS_HOST, 80)
    remote, error = remote_status() if pi['reachable'] else (None, None)
    points = history()
    with LOCK:
        old = STATE.get('remote')
        STATE.update(checking=False, updated_at=now(), pi=pi, nas=nas,
                     remote=remote or old, detail_live=remote is not None,
                     ssh_error=error, collection_error=None, history=points, events=events(points), backup=backup())
        if remote:
            STATE['last_seen'] = remote['health'].get('utc')


def collector():
    while True:
        try:
            collect_once()
        except Exception:
            with LOCK:
                STATE.update(checking=False, collection_error='Status check unavailable', updated_at=now())
        REFRESH.wait(30)
        REFRESH.clear()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, code, data, content_type='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def local_access(self, write=False):
        origin = self.headers.get('Origin')
        return not (self.headers.get('Host') not in ALLOWED_HOSTS or
                origin is not None and origin not in ALLOWED_ORIGINS or
                self.headers.get('Sec-Fetch-Site') == 'cross-site' or
                write and (origin not in ALLOWED_ORIGINS or
                           self.headers.get('X-Lab-Action') != 'response-test'))

    def do_GET(self):
        if not self.local_access():
            return self.reply(403, b'{"error":"local access only"}')
        path = urlsplit(self.path)
        if path.path == '/api/weight-events':
            return self.weight_events()
        if path.path == '/api/weights':
            return self.reply(200, json.dumps(weight_logs.history()).encode())
        if path.path == '/api/audio':
            return self.reply(200, json.dumps({'clips': custom_audio.list_clips()}).encode())
        if path.path.startswith('/audio/'):
            try:
                identifier = custom_audio.clip_id(path.path.removeprefix('/audio/').removesuffix('.mp3'))
                data = (custom_audio.CACHE / (identifier + '.mp3')).read_bytes()
                return self.reply(200, data, 'audio/mpeg')
            except (ValueError, OSError):
                return self.reply(404, b'{"error":"Processed clip not found."}')
        if path.path == '/api/status':
            if path.query == 'refresh=1':
                REFRESH.set()
            with LOCK:
                data = json.dumps(STATE).encode()
            return self.reply(200, data)
        if path.path == '/api/responses':
            code, result = remote_responses('catalog')
            return self.reply(code, json.dumps(result).encode())
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path.path in files:
            name, content_type = files[path.path]
            return self.reply(200, (ROOT / name).read_bytes(), content_type)
        self.reply(404, b'{"error":"not found"}')

    def weight_events(self):
        if not EVENT_CLIENTS.acquire(blocking=False):
            return self.reply(503, b'{"error":"Too many live dashboard connections."}')
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            previous = None
            while True:
                revision, snapshot = weight_logs.next_event(previous)
                if revision != previous:
                    payload = f'event: weights\nid: {revision}\ndata: {snapshot}\n\n'
                    previous = revision
                else:
                    payload = ': keep-alive\n\n'
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (OSError, TimeoutError):
            pass
        finally:
            self.close_connection = True
            EVENT_CLIENTS.release()

    def do_POST(self):
        global LAST_PLAY
        if not self.local_access(write=True):
            return self.reply(403, b'{"error":"local dashboard actions only"}')
        if self.path in ('/api/audio/file', '/api/audio/link', '/api/audio/publish'):
            return self.audio_action()
        operations = {'/api/responses/play':'play', '/api/responses/validate':'validate',
                      '/api/responses/play-draft':'play-draft', '/api/responses/apply':'apply'}
        operation = operations.get(self.path)
        if operation is None:
            return self.reply(405, b'{"error":"unsupported dashboard action"}')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (1024 if operation == 'play' else 100_000) or self.headers.get('Content-Type') != 'application/json':
                raise ResponseError(400, 'Expected valid JSON under 100 KB.')
            body = json.loads(self.rfile.read(size))
            if operation in ('play','play-draft'):
                validate_play(body, draft=operation == 'play-draft')
            else:
                validate_edit(body)
        except (ValueError, UnicodeError, TimeoutError) as exc:
            message = exc.message if isinstance(exc, ResponseError) else 'Invalid response selection.'
            return self.reply(400, json.dumps({'error': message}).encode())
        if not PLAY_LOCK.acquire(blocking=False):
            return self.reply(429, b'{"error":"A response test is already running."}')
        try:
            if operation in ('play','play-draft') and time.monotonic() - LAST_PLAY < 3:
                return self.reply(429, b'{"error":"Wait a moment before another test."}')
            if operation in ('play','play-draft'):
                LAST_PLAY = time.monotonic()
            code, result = remote_responses(operation, body)
            if operation == 'apply' and code == 200 and isinstance(result.get('catalog'), dict):
                source = WORKSPACE / 'renpho-scale' / 'reactions.json'
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=source.parent,
                                                     prefix='.catalog-', delete=False) as file:
                        temporary = Path(file.name)
                        json.dump(result['catalog'], file, indent=2, ensure_ascii=False); file.write('\n')
                    temporary.replace(source)
                except OSError:
                    result['message'] += ' Local source copy could not be updated.'
                finally:
                    if temporary and temporary.exists(): temporary.unlink()
            self.reply(code, json.dumps(result).encode())
        finally:
            PLAY_LOCK.release()

    def audio_action(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if self.path == '/api/audio/file':
                name = custom_audio.clip_name(self.headers.get('X-Clip-Name'))
                if self.headers.get('Content-Type') != 'audio/mpeg' or not 0 < size <= custom_audio.MAX_BYTES:
                    raise ResponseError(400, 'Upload an MP3 no larger than 20 MB.')
                self.connection.settimeout(20)
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ResponseError(400, 'MP3 upload was incomplete. Try again.')
                clip = custom_audio.process_clip(name, data=data)
            else:
                if self.headers.get('Content-Type') != 'application/json' or not 0 < size <= 8192:
                    raise ResponseError(400, 'Provide a clip name and direct HTTPS MP3 link.')
                body = json.loads(self.rfile.read(size))
                expected = {'name', 'url'} if self.path == '/api/audio/link' else {'id', 'publish_publicly'}
                if not isinstance(body, dict) or set(body) != expected:
                    raise ResponseError(400, 'Invalid audio request.')
                if self.path == '/api/audio/link':
                    clip = custom_audio.process_clip(body['name'], url=body['url'])
                else:
                    if body['publish_publicly'] is not True:
                        raise ResponseError(400, 'Publish explicitly to make this clip available to your Echo.')
                    clip = custom_audio.publish_clip(body['id'])
            self.reply(200, json.dumps({'clip': clip}).encode())
        except ResponseError as error:
            self.reply(error.code, json.dumps({'error': error.message}).encode())
        except (ValueError, UnicodeError, OSError, TimeoutError):
            self.reply(400, b'{"error":"Audio operation was not confirmed. Your existing clips and cards are unchanged."}')


if __name__ == '__main__':
    threading.Thread(target=collector, daemon=True).start()
    threading.Thread(target=weight_logs.watch, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    print(f'Homelab dashboard: http://127.0.0.1:{PORT}', flush=True)
    server.serve_forever()
