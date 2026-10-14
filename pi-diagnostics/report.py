"""Print health-only JSON to stdout for a private local report."""
import json
from collections import deque
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnostics import STATE, command, kernel_events, timestamps
from snapshot import collect


def report():
    boots_raw = command(['journalctl', '--list-boots', '--no-pager'])
    boots = []
    for line in (boots_raw or '').splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) == 3 and parts[0].lstrip('-').isdigit() and len(parts[1]) == 32:
            boots.append({'index': int(parts[0]), 'boot_id': parts[1], 'range': parts[2]})
    kernel = {}
    for boot in (0, -1):
        if not any(item['index'] == boot for item in boots):
            kernel[str(boot)] = {'available': False, 'events': []}
            continue
        raw = command(['journalctl', '-k', '--boot=' + str(boot), '--no-pager',
                       '--output=json', '--lines=5000'], timeout=12)
        kernel[str(boot)] = {'available': raw is not None, 'events': kernel_events(raw)}
    recent = deque(maxlen=120)
    if STATE.exists():
        # Up to two most recent files, streaming rather than loading everything.
        for path in sorted(STATE.glob('health-????-??-??.jsonl'))[-2:]:
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        recent.append(json.loads(line))
                    except ValueError:
                        continue
    return {**timestamps(), 'current': collect(), 'boots': boots,
            'kernel': kernel, 'recent_snapshots': list(recent),
            'limits': ['A lost SSH connection alone does not prove a crash.',
                       'Abrupt power loss can prevent the last records being saved.',
                       'Historical power flags apply to this boot, not an exact failure time.',
                       'Kernel events cover the last 5000 messages per boot; at most 200 matches.',
                       'Application content, scale packets, DNS query logs and credentials are excluded.']}


if __name__ == '__main__':
    print(json.dumps(report(), indent=2))
