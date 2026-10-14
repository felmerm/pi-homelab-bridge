"""Read back a delivered event without printing its weight or credentials."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sheets_logger import Sheets, report


def verify(wait):
    with open('/etc/renpho-scale/sheets.json', encoding='utf-8') as file:
        config = json.load(file)
    sheets = Sheets(config)
    sheets.verify()
    # Verify the duplicate-check range is accepted before an actual upload.
    sheets.get('E2:E10000')
    report('waiting_for_delivered_reading', seconds=wait)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        with sqlite3.connect('file:' + config['outbox'] + '?mode=ro', uri=True) as db:
            reading = db.execute('SELECT id,timestamp,kg FROM readings WHERE delivered=1 ORDER BY timestamp DESC LIMIT 1').fetchone()
        if reading:
            marker = 'Renpho event ' + reading[0]
            matches = [i + 2 for i, row in enumerate(sheets.get('E2:E10000')) if row and row[0] == marker]
            if len(matches) != 1:
                raise ValueError('Expected exactly one matching event')
            row = matches[0]
            values = sheets.get(f'A{row}:E{row}')[0]
            local = datetime.fromtimestamp(reading[1], timezone.utc).astimezone(sheets.tz)
            assert values[0] == local.strftime('%Y-%m-%d')
            assert values[1] == local.strftime('%H:%M:%S')
            assert abs(float(values[2]) - round(reading[2] * 2.20462262185, 1)) < 0.001
            assert values[3:] == ['Renpho scale / Pi', marker]
            report('sheet_readback_verified', row=row, matching_rows=1, pounds_conversion_verified=True)
            sheets.session.close()
            return
        time.sleep(3)
    sheets.session.close()
    report('no_delivered_reading_yet')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait', type=int, default=55)
    args = parser.parse_args()
    if not 1 <= args.wait <= 55:
        parser.error('wait must be between 1 and 55')
    try:
        verify(args.wait)
    except Exception as exc:
        report('verification_error', error_type=type(exc).__name__)
        raise SystemExit(1)
