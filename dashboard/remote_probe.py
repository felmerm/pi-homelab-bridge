"""Read-only Pi probe, sent over pinned SSH. Never return credentials or packets."""
import datetime as dt
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import subprocess
import urllib.request

UNITS = ('ssh.service', 'pihole-FTL.service', 'notification-bridge.service', 'renpho-sheets.service',
         'pi-diagnostics.timer')


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return result.stdout if result.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def bridge_status():
    try:
        token = next(line.partition('=')[2].strip().strip('"') for line in
                     read('/etc/notification-bridge.env').splitlines() if line.startswith('BRIDGE_TOKEN='))
        tls = ssl.create_default_context(cafile='/etc/notification-bridge-tls/server.crt')
        request = urllib.request.Request('https://raspberrypi.local:8765/status',
                                         headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request, context=tls, timeout=3) as response:
            status = json.loads(response.read(16_384))
        allowed = ('received', 'announced', 'speak_submitted', 'audio_submitted', 'failed', 'queued',
                   'alexa_configured', 'hue_configured', 'backend', 'audio_backend', 'lights_backend')
        result = {key: status[key] for key in allowed if key in status}
        if isinstance(status.get('modes', {}).get('meme'), bool):
            result['meme_mode'] = status['modes']['meme']
        return result
    except Exception:
        return None


def scale_status():
    result = {'available': False}
    database = Path('/var/lib/renpho-scale/readings.sqlite3')
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
                row = connection.execute('SELECT timestamp,kg,delivered FROM readings ORDER BY timestamp DESC LIMIT 1').fetchone()
                pending = connection.execute('SELECT count(*) FROM readings WHERE delivered=0').fetchone()[0]
            result.update(available=True, pending=pending)
            if row:
                result['latest'] = {'utc': dt.datetime.fromtimestamp(row[0], dt.timezone.utc).isoformat(),
                                    'lb': round(row[1] * 2.20462262185, 1), 'delivered': bool(row[2])}
        except (sqlite3.Error, OSError, ValueError):
            result['database_unavailable'] = True
    raw = Path('/var/lib/renpho-scale/raw')
    files = sorted(raw.glob('????-??-??.jsonl')) if raw.is_dir() else []
    result['raw_log_files'] = len(files)
    if files:
        result['raw_last_write_utc'] = dt.datetime.fromtimestamp(files[-1].stat().st_mtime, dt.timezone.utc).isoformat()
    try:
        config = json.loads(read('/etc/renpho-scale/reactions.json'))
        result['reactions'] = {'good': len(config.get('good', [])), 'bad': len(config.get('bad', [])),
                               'audio_enabled': bool(config.get('audio_enabled')),
                               'special_enabled': bool(config.get('special', {}).get('enabled', False))}
    except (ValueError, AttributeError, TypeError):
        pass
    return result


def collect():
    memory = {line.split(':')[0]: int(line.split()[1]) for line in read('/proc/meminfo').splitlines()
              if line.split(':')[0] in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}}
    temp = re.search(r'temp=([\d.]+)', command(['vcgencmd', 'measure_temp']))
    throttle = re.search(r'0x[0-9a-fA-F]+', command(['vcgencmd', 'get_throttled']))
    mask = int(throttle[0], 16) if throttle else None
    usage = shutil.disk_usage('/')
    health = {'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'boot_id': read('/proc/sys/kernel/random/boot_id'),
              'uptime_seconds': float(read('/proc/uptime').split()[0]),
              'temperature_c': float(temp[1]) if temp else None, 'memory_kib': memory,
              'storage': {'/': {'total_bytes': usage.total, 'free_bytes': usage.free}},
              'power': None if mask is None else {'mask': hex(mask),
                         'undervoltage_now': bool(mask & 1), 'throttled_now': bool(mask & 4),
                         'undervoltage_since_boot': bool(mask & 65536)}}
    services = {}
    for unit in UNITS:
        output = command(['systemctl', 'show', unit, '--property=ActiveState,SubState,NRestarts,Result'])
        services[unit] = {key: value[:80] for line in output.splitlines()
                          for key, sep, value in [line.partition('=')] if sep and key in
                          {'ActiveState', 'SubState', 'NRestarts', 'Result'}}
    health['services'] = services
    signal = re.search(r'signal:\s*(-?\d+)\s*dBm', command(['iw', 'dev', 'wlan0', 'link']))
    health['wifi_signal_dbm'] = int(signal[1]) if signal else None
    enabled_lists = None
    try:
        with closing(sqlite3.connect('file:/etc/pihole/gravity.db?mode=ro', uri=True, timeout=1)) as connection:
            enabled_lists = connection.execute('SELECT count(*) FROM adlist WHERE enabled=1').fetchone()[0]
    except sqlite3.Error:
        pass
    return {'health': health, 'bridge': bridge_status(), 'scale': scale_status(),
            'pihole_enabled_lists': enabled_lists,
            'diagnostics_installed': Path('/opt/pi-diagnostics/snapshot.py').is_file()}


if __name__ == '__main__':
    try:
        print(json.dumps(collect()))
    except Exception:
        print(json.dumps({'probe_error': 'Pi status collection unavailable'}))
