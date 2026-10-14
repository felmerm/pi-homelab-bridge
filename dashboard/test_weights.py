import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import tempfile
import threading
import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch
import remote_weights
import weight_logs
import remote_weight_watch


class WeightLogsTests(unittest.TestCase):
    def setUp(self):
        self.live = patch.object(weight_logs, 'LIVE_ROWS', None)
        self.online = patch.object(weight_logs, 'STREAM_LIVE', False)
        self.revision = patch.object(weight_logs, 'REVISION', 0)
        self.live.start(); self.online.start(); self.revision.start()
        self.addCleanup(self.live.stop); self.addCleanup(self.online.stop); self.addCleanup(self.revision.stop)

    def test_sqlite_watcher_signals_new_pending_reading_then_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'readings.sqlite3'
            config = Path(folder) / 'config.json'
            config.write_text(json.dumps({'timezone': 'America/Vancouver'}))
            db = sqlite3.connect(database)
            db.execute('CREATE TABLE readings(id TEXT,timestamp REAL,kg REAL,delivered INTEGER)')
            db.commit()
            ticks = 0
            def tick(_):
                nonlocal ticks
                ticks += 1
                if ticks == 1:
                    db.execute('INSERT INTO readings VALUES (?,?,?,?)', ('new', 1791356646, 88.3, 0))
                elif ticks == 2:
                    db.execute('UPDATE readings SET delivered=1')
                else:
                    raise KeyboardInterrupt()
                db.commit()
            output = io.StringIO()
            try:
                with patch.object(remote_weight_watch, 'DATABASE', database), patch.object(remote_weight_watch, 'CONFIG', config), patch.object(remote_weight_watch, 'ZoneInfo', return_value=timezone(timedelta(hours=-7))), patch.object(remote_weight_watch.time, 'sleep', side_effect=tick), redirect_stdout(output):
                    with self.assertRaises(KeyboardInterrupt): remote_weight_watch.watch()
            finally:
                db.close()
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            readings = [event for event in events if event['kind'] == 'readings']
            self.assertEqual(len(readings), 3)
            self.assertEqual(readings[0]['rows'], [])
            self.assertEqual(readings[1]['rows'][0]['status'], 'pending')
            self.assertEqual(readings[2]['rows'][0]['status'], 'uploaded')
            self.assertEqual(readings[1]['rows'][0]['id'], readings[2]['rows'][0]['id'])

    def test_live_update_does_not_wait_for_google_or_disappear_after_stale_fetch(self):
        old = {'id': 'sheet:2', 'date': '2025-08-01', 'time': '', 'lb': 211, 'source': 'tracker', 'status': 'synced'}
        pending = {'id': 'renpho:new', 'date': '2026-10-08', 'time': '15:00:00', 'utc': '2026-10-08T22:00:00Z', 'lb': 193, 'source': 'Pi', 'status': 'pending'}
        entered, release = threading.Event(), threading.Event()
        def delayed_read():
            entered.set(); release.wait(3)
            return {'rows': [old], 'timezone': 'America/Vancouver', 'sheet_ok': True, 'outbox_ok': True, 'synced_at': 'now'}
        with tempfile.TemporaryDirectory() as folder, patch.object(weight_logs, 'CACHE', Path(folder) / 'cache.json'), patch.object(weight_logs, 'SNAPSHOT', {'rows': [old], 'cached': False}), patch.object(weight_logs, 'LAST_CHECK', 0), patch.object(weight_logs, 'remote', side_effect=delayed_read):
            thread = threading.Thread(target=weight_logs.history); thread.start()
            try:
                self.assertTrue(entered.wait(1))
                weight_logs.stream_state(True, [pending], 'America/Vancouver')
                revision, payload = weight_logs.next_event(None, timeout=0)
                self.assertEqual(revision, 1)
                data = json.loads(payload)
                self.assertEqual(data['rows'][0]['id'], 'renpho:new')
                self.assertTrue(data['stream_live'])
            finally:
                release.set(); thread.join(timeout=3)
            self.assertEqual(weight_logs.history()['rows'][0]['id'], 'renpho:new')
            weight_logs.stream_state(True, [{**pending, 'status': 'uploaded'}], 'America/Vancouver')
            _, payload = weight_logs.next_event(revision, timeout=0)
            self.assertEqual(json.loads(payload)['rows'][0]['status'], 'uploaded')
            weight_logs.stream_state(False)
            self.assertFalse(json.loads(weight_logs.next_event(None, timeout=0)[1])['stream_live'])

    def test_sheet_parsing_drops_notes_and_invalid_rows_preserves_unknown_times(self):
        rows, skipped = remote_weights.sheet_rows([remote_weights.HEADERS,
            ['2026-10-07', '00:04:06', 194.7, 'Renpho scale / Pi', 'Renpho event 2440d879-213f-409f-b13d-4abda6067d3b'],
            ['2025-08-01', '', 211, 'prior tracker', 'PRIVATE NOTES'],
            ['2026-02-31', '', 180], ['2026-10-07', '', 'nan'], []])
        self.assertEqual(skipped, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['time'], '')
        self.assertNotIn('PRIVATE', json.dumps(rows))
        self.assertTrue(rows[0]['id'].startswith('renpho:'))
        with self.assertRaises(ValueError):
            remote_weights.sheet_rows([['Changed header']])

    def test_readonly_outbox_timezone_and_sheet_confirmation_deduplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'readings.sqlite3'
            db = sqlite3.connect(database)
            db.execute('CREATE TABLE readings(id TEXT,timestamp REAL,kg REAL,delivered INTEGER)')
            timestamp = datetime(2026, 10, 7, 7, 4, 6, tzinfo=timezone.utc).timestamp()
            db.execute('INSERT INTO readings VALUES (?,?,?,?)', ('event', timestamp, 88.3, 0))
            db.commit(); db.close()
            # Windows has no system IANA database; the production helper runs on Linux.
            with patch.object(remote_weights, 'ZoneInfo', return_value=timezone(timedelta(hours=-7))) as zone:
                rows = remote_weights.outbox_rows(database, 'America/Vancouver')
                zone.assert_called_with('America/Vancouver')
            self.assertEqual(rows[0]['time'], '00:04:06')
            self.assertEqual(rows[0]['date'], '2026-10-07')
            self.assertEqual(rows[0]['status'], 'pending')
            merged = remote_weights.merge_rows([{**rows[0], 'status': 'synced'}], rows)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]['status'], 'synced')
            self.assertFalse((Path(folder) / 'readings.sqlite3-journal').exists())

    def test_minute_cache_and_restart_outage_preserve_history(self):
        data = {'rows': [{'id': 'sheet:2', 'date': '2026-10-07', 'time': '', 'lb': 194.7, 'source': 'tracker', 'status': 'synced'}],
                'timezone': 'America/Vancouver', 'sheet_ok': True, 'outbox_ok': True, 'synced_at': '2026-10-07T10:00:00Z'}
        with tempfile.TemporaryDirectory() as folder, patch.object(weight_logs, 'CACHE', Path(folder) / 'cache.json'), patch.object(weight_logs, 'SNAPSHOT', None), patch.object(weight_logs, 'LAST_CHECK', 0), patch.object(weight_logs, 'remote', return_value=data) as remote:
            first = weight_logs.history()
            self.assertFalse(first['cached'])
            self.assertEqual(weight_logs.history()['rows'], data['rows'])
            remote.assert_called_once()
            weight_logs.SNAPSHOT = None; weight_logs.LAST_CHECK = 0
            remote.side_effect = ValueError('unavailable')
            cached = weight_logs.history()
            self.assertTrue(cached['cached'])
            self.assertEqual(cached['rows'], data['rows'])
            self.assertIn('saved', cached['error'])

    def test_partial_sheet_failure_keeps_old_history_and_new_pending_readings(self):
        old = {'id': 'sheet:2', 'date': '2025-08-01', 'time': '', 'lb': 211, 'source': 'tracker', 'status': 'synced'}
        pending = {'id': 'renpho:pending', 'date': '2026-10-08', 'time': '14:00:00', 'lb': 195, 'source': 'Pi', 'status': 'pending'}
        with tempfile.TemporaryDirectory() as folder, patch.object(weight_logs, 'CACHE', Path(folder) / 'cache.json'), patch.object(weight_logs, 'SNAPSHOT', {'rows': [old], 'synced_at': 'yesterday'}), patch.object(weight_logs, 'LAST_CHECK', 0), patch.object(weight_logs, 'remote', return_value={'rows': [pending], 'timezone': 'America/Vancouver', 'sheet_ok': False, 'outbox_ok': True, 'synced_at': 'today'}):
            result = weight_logs.history()
            self.assertEqual([r['id'] for r in result['rows']], ['renpho:pending', 'sheet:2'])
            self.assertEqual(result['synced_at'], 'yesterday')
            self.assertTrue(result['cached'])
