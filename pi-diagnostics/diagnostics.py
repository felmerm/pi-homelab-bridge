"""Small, private, bounded health records. No application data or dependencies."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess

UTC = dt.timezone.utc
STATE = Path('/var/lib/pi-diagnostics')
UNITS = ('ssh.service', 'pihole-FTL.service', 'notification-bridge.service',
         'renpho-sheets.service', 'pi-diagnostics.service')
FLAGS = {0: 'undervoltage_now', 1: 'frequency_capped_now', 2: 'throttled_now',
         3: 'temperature_limit_now', 16: 'undervoltage_since_boot',
         17: 'frequency_capped_since_boot', 18: 'throttled_since_boot',
         19: 'temperature_limit_since_boot'}
PATTERNS = {
    'power': r'under.?voltage|voltage normal|throttl',
    'memory': r'out of memory|killed process|oom-kill|oom_reaper|invoked oom',
    'storage': r'mmc.*(?:error|timeout|timed out|fail)|I/O error|EXT4-fs.*(?:error|warning|abort)|remount.*read-only|Buffer I/O',
    'kernel': r'kernel panic|watchdog.*(?:lockup|BUG|failure|timed out|pretimeout|did not stop)|soft lockup|hard lockup|hung task|blocked for more than',
    'wifi': r'brcmfmac.*(?:error|fail|timeout)|wlan\d.*(?:disconnect|deauth|link.*down|timed out)',
}


def timestamps():
    now = dt.datetime.now(UTC)
    return {'utc': now.isoformat(timespec='seconds'),
            'local': now.astimezone().isoformat(timespec='seconds')}


def command(args, timeout=4):
    """Never include failed-command output in diagnostics (it can contain secrets)."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout[-2_000_000:] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def throttle_flags(value):
    match = re.search(r'0x[0-9a-fA-F]+', value or '')
    if not match:
        return None
    number = int(match[0], 16)
    return {'mask': hex(number), **{name: bool(number & (1 << bit)) for bit, name in FLAGS.items()}}


def prune(directory, prefix, days=14, max_bytes=32 * 1024 * 1024):
    """Only own dated JSONL files; never touch scale readings or unrelated files."""
    directory = Path(directory)
    pattern = re.compile(re.escape(prefix) + r'\d{4}-\d{2}-\d{2}\.jsonl$')
    files = sorted(p for p in directory.iterdir() if pattern.fullmatch(p.name)
                   and p.is_file() and not p.is_symlink())
    cutoff = dt.datetime.now(UTC).timestamp() - days * 86400
    for path in list(files):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            files.remove(path)
    total = sum(p.stat().st_size for p in files)
    while total > max_bytes and len(files) > 1:
        oldest = files.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink()
    # Keep the newest file, trimming old complete records if it alone hits the cap.
    if files and files[-1].stat().st_size > max_bytes:
        path = files[-1]
        with path.open('rb') as stream:
            stream.seek(-max_bytes // 2, os.SEEK_END)
            stream.readline()
            tail = stream.read()
        temporary = path.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            stream.write(tail)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)


def append_record(directory, prefix, record, max_bytes=32 * 1024 * 1024):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (prefix + dt.datetime.now(UTC).strftime('%Y-%m-%d') + '.jsonl')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    prune(directory, prefix, max_bytes=max_bytes)


def categories(message):
    return [name for name, pattern in PATTERNS.items() if re.search(pattern, message, re.I)]


def redact_kernel(message):
    message = re.sub(r'\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b', '[MAC]', message)
    message = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP]', message)
    # OOM records can expose process names; avoid command lines/user arguments.
    return message[:800]


def kernel_events(raw):
    events = []
    for line in (raw or '').splitlines():
        try:
            item = json.loads(line)
            message = item.get('MESSAGE', '')
            if not isinstance(message, str):
                continue
            matched = categories(message)
            if matched:
                stamp = int(item['__REALTIME_TIMESTAMP']) / 1_000_000
                events.append({'utc': dt.datetime.fromtimestamp(stamp, UTC).isoformat(),
                               'categories': matched, 'message': redact_kernel(message)})
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return events[-200:]
