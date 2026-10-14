"""Run every two minutes from a low-priority timer; no traffic or app log reads."""
import re
import shutil
import sys
from pathlib import Path

# Isolated Python deliberately omits the script directory. Add only our root-owned folder.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnostics import STATE, UNITS, append_record, command, read, throttle_flags, timestamps


def number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect():
    result = timestamps()
    result['boot_id'] = read('/proc/sys/kernel/random/boot_id')
    try:
        result['uptime_seconds'] = float((read('/proc/uptime') or '').split()[0])
        result['load'] = [float(v) for v in (read('/proc/loadavg') or '').split()[:3]]
    except (ValueError, IndexError):
        result['uptime_seconds'] = None
    result['power'] = throttle_flags(command(['vcgencmd', 'get_throttled']))
    temperature = command(['vcgencmd', 'measure_temp'])
    match = re.search(r'temp=([\d.]+)', temperature or '')
    result['temperature_c'] = float(match[1]) if match else None
    clock = command(['vcgencmd', 'measure_clock', 'arm'])
    match = re.search(r'=(\d+)', clock or '')
    result['arm_hz'] = int(match[1]) if match else None
    keys = {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree', 'Dirty', 'Writeback'}
    result['memory_kib'] = {line.split(':')[0]: int(line.split()[1])
                            for line in (read('/proc/meminfo') or '').splitlines()
                            if line.split(':')[0] in keys}
    result['pressure'] = {kind: read('/proc/pressure/' + kind) for kind in ('cpu', 'memory', 'io')}
    result['storage'] = {}
    for path in ('/', '/var/log'):
        try:
            usage = shutil.disk_usage(path)
            result['storage'][path] = {'total_bytes': usage.total, 'free_bytes': usage.free}
        except OSError:
            result['storage'][path] = None
    result['sd_io_counters'] = (read('/sys/block/mmcblk0/stat') or '').split()
    result['links'] = {}
    for interface in ('wlan0', 'eth0'):
        base = '/sys/class/net/' + interface
        result['links'][interface] = {
            'state': read(base + '/operstate'), 'carrier': number(read(base + '/carrier')),
            **{key: number(read(base + '/statistics/' + key)) for key in
               ('rx_errors', 'tx_errors', 'rx_dropped', 'tx_dropped')},
        }
    # Parse only signal, never persist network name or access-point address.
    link = command(['iw', 'dev', 'wlan0', 'link'])
    match = re.search(r'signal:\s*(-?\d+)\s*dBm', link or '')
    result['links']['wlan0']['signal_dbm'] = int(match[1]) if match else None
    result['services'] = {}
    for unit in UNITS:
        output = command(['systemctl', 'show', unit, '--property=ActiveState,SubState,NRestarts,Result'])
        allowed = {'ActiveState', 'SubState', 'NRestarts', 'Result'}
        result['services'][unit] = {key: value[:80] for line in (output or '').splitlines()
                                   for key, separator, value in [line.partition('=')]
                                   if separator and key in allowed}
    return result


if __name__ == '__main__':
    append_record(STATE, 'health-', collect())
