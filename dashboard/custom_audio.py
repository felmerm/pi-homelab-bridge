"""Bounded local MP3 conversion and explicit publication to the meme-audio repo."""
import base64
import runtime_config as config
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit

from remote_responses import ResponseError

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'audio-cache'
MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 90
REPOSITORY = config.AUDIO_REPOSITORY
BRANCH = 'main'
LOCK = threading.Lock()


def clip_name(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z0-9_]{1,40}', value):
        raise ResponseError(400, 'Clip name needs 1–40 lowercase letters, numbers or underscores.')
    return value


def clip_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ResponseError(400, 'Choose a processed clip.')
    return value


def library():
    try:
        return json.loads((CACHE / 'library.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []


def save_library(clips):
    CACHE.mkdir(exist_ok=True)
    temporary = CACHE / 'library.tmp'
    temporary.write_text(json.dumps(clips, indent=2) + '\n', encoding='utf-8')
    temporary.replace(CACHE / 'library.json')


def public_clip(clip):
    return {key: clip[key] for key in ('id', 'name', 'duration', 'bytes', 'created', 'url') if key in clip}


def list_clips():
    with LOCK:
        return [public_clip(clip) for clip in library()]


def checked_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or re.search(r'[\s\\\x00-\x1f]', value):
        raise ResponseError(400, 'Use a direct public HTTPS MP3 link.')
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        if parts.scheme != 'https' or not host or parts.port not in (None, 443) or parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError()
        # Resolve every redirect, reject all non-public answers, and pin the
        # connection to these addresses rather than resolving again at connect.
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global or ipaddress.ip_address(item[4][0]).is_multicast for item in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise ResponseError(400, 'Use a public HTTPS link; local/private network addresses are not allowed.') from None
    return parts, addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, addresses):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.addresses = addresses

    def connect(self):
        for family, socktype, proto, _, address in self.addresses:
            connection = socket.socket(family, socktype, proto)
            connection.settimeout(self.timeout)
            try:
                connection.connect(address)
                self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
                return
            except OSError:
                connection.close()
        raise OSError('Public audio host unavailable')


def download_mp3(url):
    deadline = time.monotonic() + 45
    for redirect in range(4):
        parts, addresses = checked_url(url)
        connection = PinnedHTTPS(parts.hostname, addresses)
        try:
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            connection.request('GET', path, headers={'Accept': 'audio/mpeg, application/octet-stream', 'User-Agent': 'Felm-Meme-Audio/1.0'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or redirect == 3:
                    raise ResponseError(400, 'The MP3 link redirects too many times.')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ResponseError(400, 'The audio host did not provide a downloadable file. Use a direct MP3 link.')
            length = response.getheader('Content-Length')
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise ResponseError(400, 'MP3 files must be at most 20 MB.')
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise ResponseError(400, 'The MP3 download timed out. Try uploading the file instead.')
                chunk = response.read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ResponseError(400, 'MP3 files must be at most 20 MB.')
            return b''.join(chunks)
        except (OSError, http.client.HTTPException, UnicodeError):
            raise ResponseError(400, 'Could not download the MP3 securely. Try uploading the file instead.') from None
        finally:
            connection.close()
    raise ResponseError(400, 'Use a direct MP3 link.')


def ffmpeg_path():
    installed = shutil.which('ffmpeg')
    bundled = ROOT.parent / '.tools/audio/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
    if installed:
        return installed
    if bundled.is_file():
        return str(bundled)
    raise ResponseError(503, 'The local MP3 converter is unavailable.')


def inspect_mp3(data):
    """Verify every encoded frame, including duration; reject truncation/tags."""
    offset, frames = 0, 0
    while offset < len(data):
        if offset + 4 > len(data):
            raise ResponseError(400, 'MP3 conversion produced incomplete audio.')
        header = int.from_bytes(data[offset:offset + 4], 'big')
        if (header >> 21 != 0x7ff or (header >> 19) & 3 != 2 or
                (header >> 17) & 3 != 1 or (header >> 12) & 15 != 6 or
                (header >> 10) & 3 != 1 or (header >> 6) & 3 == 3):
            raise ResponseError(400, 'MP3 conversion did not produce the expected Echo format.')
        length = 144 + ((header >> 9) & 1)
        if offset + length > len(data):
            raise ResponseError(400, 'MP3 conversion produced incomplete audio.')
        offset += length
        frames += 1
    duration = frames * 576 / 24000
    if not frames or duration > MAX_SECONDS + 0.1:
        raise ResponseError(400, 'Use an MP3 clip no longer than 90 seconds.')
    return round(duration, 3)


def convert_mp3(data):
    if not data or len(data) > MAX_BYTES:
        raise ResponseError(400, 'Upload an MP3 between 1 byte and 20 MB.')
    converter = ffmpeg_path()
    with tempfile.TemporaryDirectory(prefix='felm-meme-audio-') as folder:
        source, output = Path(folder) / 'input.mp3', Path(folder) / 'output.mp3'
        source.write_bytes(data)
        command = [converter, '-nostdin', '-hide_banner', '-loglevel', 'error', '-xerror',
                   '-protocol_whitelist', 'file,pipe', '-f', 'mp3', '-i', str(source),
                   '-map', '0:a:0', '-vn', '-map_metadata', '-1', '-ac', '2',
                   '-codec:a', 'libmp3lame', '-b:a', '48k', '-ar', '24000',
                   '-t', str(MAX_SECONDS + 1), '-write_xing', '0', '-id3v2_version', '0',
                   '-write_id3v1', '0', str(output)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=45,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            raise ResponseError(400, 'MP3 processing failed or timed out. Try a shorter valid MP3.') from None
        if result.returncode or not output.is_file():
            raise ResponseError(400, 'This file could not be decoded as MP3. Upload a valid MP3, or use its direct download link.')
        encoded = output.read_bytes()
        return encoded, inspect_mp3(encoded)


def process_clip(name, *, data=None, url=None):
    name = clip_name(name)
    if not LOCK.acquire(blocking=False):
        raise ResponseError(429, 'Another clip is being processed. Wait a moment.')
    try:
        clips = library()
        if len(clips) >= 100:
            raise ResponseError(400, 'The local audio library has reached its 100-clip limit.')
        if url is not None:
            data = download_mp3(url)
        encoded, duration = convert_mp3(data)
        identifier = hashlib.sha256(encoded).hexdigest()
        old = next((clip for clip in clips if clip['id'] == identifier), None)
        if old:
            return public_clip(old)
        CACHE.mkdir(exist_ok=True)
        (CACHE / (identifier + '.mp3')).write_bytes(encoded)
        clip = {'id': identifier, 'name': name, 'duration': duration, 'bytes': len(encoded),
                'created': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        clips.append(clip)
        save_library(clips)
        return public_clip(clip)
    finally:
        LOCK.release()


def github_api(endpoint, payload=None):
    gh = shutil.which('gh')
    if not gh:
        raise ResponseError(503, 'GitHub CLI is unavailable. The processed clip stays local.')
    args = [gh, 'api', endpoint]
    if payload is not None:
        args += ['--method', 'PUT', '--input', '-']
    try:
        result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, encoding='utf-8', timeout=35,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            if payload is None and '(HTTP 404)' in result.stderr:
                return None
            raise ResponseError(503, 'GitHub publishing was not confirmed. Your processed clip is still local; retry safely.')
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ResponseError(503, 'GitHub publishing was not confirmed. Your processed clip is still local; retry safely.') from None


def publish_clip(identifier):
    if not REPOSITORY:
        raise ResponseError(400, 'Set HOMELAB_AUDIO_REPOSITORY to your own public audio repository first.')
    identifier = clip_id(identifier)
    if not LOCK.acquire(blocking=False):
        raise ResponseError(429, 'Another clip is being processed or published. Wait a moment.')
    try:
        clips = library()
        clip = next((item for item in clips if item['id'] == identifier), None)
        if not clip:
            raise ResponseError(404, 'Processed clip not found. Process it again.')
        if clip.get('url'):
            return public_clip(clip)
        data = (CACHE / (identifier + '.mp3')).read_bytes()
        if hashlib.sha256(data).hexdigest() != identifier:
            raise ResponseError(400, 'Processed audio changed. Process the original MP3 again.')
        inspect_mp3(data)
        repository = github_api('repos/' + REPOSITORY)
        if not repository or repository.get('private') is not False or repository.get('default_branch') != BRANCH or not repository.get('permissions', {}).get('push'):
            raise ResponseError(503, 'The configured public meme-audio repository is not ready for publishing.')
        endpoint = 'repos/' + REPOSITORY + '/contents/custom/' + identifier + '.mp3'
        existing = github_api(endpoint + '?ref=' + BRANCH)
        if existing is not None:
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            if existing.get('sha') != blob:
                raise ResponseError(409, 'The hosted clip differs from the processed file. Nothing was overwritten.')
            commit = github_api('repos/' + REPOSITORY + '/commits/' + BRANCH)['sha']
        else:
            result = github_api(endpoint, {'message': 'Add custom meme audio',
                                          'content': base64.b64encode(data).decode('ascii'), 'branch': BRANCH})
            commit = result.get('commit', {}).get('sha')
        if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
            raise ResponseError(503, 'Publishing was not confirmed. Retry to recover the hosted clip.')
        clip['url'] = f'https://raw.githubusercontent.com/{REPOSITORY}/{commit}/custom/{identifier}.mp3'
        save_library(clips)
        return public_clip(clip)
    finally:
        LOCK.release()
