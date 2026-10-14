"""Fixed, read-only weight history query. Runs inside the Pi's scale venv."""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
from zoneinfo import ZoneInfo

SHEET_ID = 'YOUR_SPREADSHEET_ID'
HEADERS = ['Date', 'Time', 'Weight (lb)', 'Status / Source', 'Notes']
DATABASE = Path('/var/lib/renpho-scale/readings.sqlite3')
CONFIG = Path('/etc/renpho-scale/sheets.json')


def sheet_rows(values):
    if not values or values[0] != HEADERS:
        raise ValueError('Unexpected weight table')
    rows, skipped = [], 0
    for index, cells in enumerate(values[1:], 2):
        cells = list(cells) + [''] * max(0, 5 - len(cells))
        if not any(str(value).strip() for value in cells):
            continue
        try:
            day = datetime.strptime(str(cells[0]), '%Y-%m-%d').date().isoformat()
            lb = float(cells[2])
            if not math.isfinite(lb) or not 0 < lb <= 1500:
                raise ValueError()
        except (ValueError, TypeError):
            skipped += 1
            continue
        # Return only display fields. Private free-form notes never leave the Pi.
        match = re.fullmatch(r'Renpho event ([a-f0-9-]{36})', str(cells[4]))
        rows.append({'id': 'renpho:' + match[1] if match else 'sheet:' + str(index),
                     'date': day, 'time': str(cells[1])[:32], 'lb': round(lb, 1),
                     'source': str(cells[3])[:160], 'status': 'synced'})
    return rows, skipped


def outbox_rows(database, timezone_name):
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
        readings = connection.execute('SELECT id,timestamp,kg,delivered FROM readings ORDER BY timestamp DESC LIMIT 10000').fetchall()
    result = []
    for identifier, timestamp, kg, delivered in readings:
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo(timezone_name))
        result.append({'id': 'renpho:' + identifier, 'date': local.strftime('%Y-%m-%d'),
                       'time': local.strftime('%H:%M:%S'), 'lb': round(kg * 2.20462262185, 1),
                       'source': 'Renpho scale / Pi', 'status': 'uploaded' if delivered else 'pending'})
    return result


def merge_rows(sheet, outbox):
    # A sheet event is confirmed even if the outbox hasn't marked it delivered yet.
    rows = {row['id']: row for row in outbox}
    rows.update({row['id']: row for row in sheet})
    return sorted(rows.values(), key=lambda row: (row['date'], row['time'], row['id']), reverse=True)


def collect():
    config = json.loads(CONFIG.read_text())
    if config.get('spreadsheet_id') != SHEET_ID or config.get('sheet_name') != 'Weigh-ins':
        raise ValueError('Weight destination changed')
    tz = config['timezone']
    ZoneInfo(tz)
    sheet, outbox, skipped = [], [], 0
    sheet_ok = outbox_ok = False
    sheet_truncated = False
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        key = json.loads(Path(config['credentials']).read_text())
        if key.get('type') != 'service_account' or key.get('token_uri') != 'https://oauth2.googleapis.com/token':
            raise ValueError('Unexpected credential type')
        credentials = service_account.Credentials.from_service_account_info(key, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        with AuthorizedSession(credentials, refresh_timeout=10) as session:
            base = 'https://sheets.googleapis.com/v4/spreadsheets/' + SHEET_ID
            metadata = session.get(base, params={'fields': 'sheets(properties(title,gridProperties(rowCount)))'}, timeout=10)
            metadata.raise_for_status()
            tab = next(item['properties'] for item in metadata.json()['sheets'] if item['properties']['title'] == 'Weigh-ins')
            limit = min(10000, tab['gridProperties']['rowCount'])
            sheet_truncated = tab['gridProperties']['rowCount'] > 10000
            response = session.get(base + '/values/' + quote("'Weigh-ins'!A1:E" + str(limit), safe=''), timeout=10)
            response.raise_for_status()
            sheet, skipped = sheet_rows(response.json().get('values', []))
            sheet_ok = True
    except Exception:
        pass  # Never print credential or HTTP exception details.
    try:
        outbox = outbox_rows(DATABASE, tz)
        outbox_ok = True
    except Exception:
        pass
    return {'rows': merge_rows(sheet, outbox), 'timezone': tz, 'sheet_ok': sheet_ok,
            'outbox_ok': outbox_ok, 'skipped': skipped, 'truncated': sheet_truncated,
            'synced_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


if __name__ == '__main__':
    try:
        print(json.dumps(collect()))
    except Exception:
        print(json.dumps({'error': 'Weight history unavailable on the Pi.'}))
