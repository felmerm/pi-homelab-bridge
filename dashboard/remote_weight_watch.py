"""Read-only SQLite change watcher, streamed over one pinned SSH session."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

DATABASE = Path('/var/lib/renpho-scale/readings.sqlite3')
CONFIG = Path('/etc/renpho-scale/sheets.json')


def watch():
    config = json.loads(CONFIG.read_text())
    tz = ZoneInfo(config['timezone'])
    with closing(sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
        previous_version, previous_rows, heartbeat = None, None, 0
        while True:
            version = connection.execute('PRAGMA data_version').fetchone()[0]
            if version != previous_version:
                readings = connection.execute('SELECT id,timestamp,kg,delivered FROM readings ORDER BY timestamp DESC LIMIT 10000').fetchall()
                if readings != previous_rows:
                    rows = []
                    for identifier, timestamp, kg, delivered in readings:
                        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(tz)
                        rows.append({'id': 'renpho:' + identifier, 'date': local.strftime('%Y-%m-%d'),
                                     'utc': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                                     'time': local.strftime('%H:%M:%S'), 'lb': round(kg * 2.20462262185, 1),
                                     'source': 'Renpho scale / Pi', 'status': 'uploaded' if delivered else 'pending'})
                    print(json.dumps({'kind': 'readings', 'rows': rows, 'timezone': config['timezone']}), flush=True)
                    previous_rows = readings
                previous_version = version
            if time.monotonic() - heartbeat >= 15:
                print('{"kind":"heartbeat"}', flush=True)
                heartbeat = time.monotonic()
            time.sleep(1)


if __name__ == '__main__':
    try:
        watch()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    except Exception:
        # Fixed error: don't expose paths, config or database details.
        print('{"kind":"unavailable"}', flush=True)
