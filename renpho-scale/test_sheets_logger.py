import asyncio
from contextlib import closing
import tempfile
import threading
import sqlite3
import json
import os
from datetime import datetime
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sheets_logger import HEADERS, Outbox, Sheets, InspectableQNScale, RawRecorder, run, announce


class RawRecorderTests(unittest.TestCase):
    def test_raw_bytes_and_timestamps_persist_privately_and_append_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = RawRecorder(directory, 'America/Vancouver')
            recorder.record('packet', direction='rx', hex='10 0b 15 21 f7 01 02 03 01 f7 46')
            recorder.close()
            recorder = RawRecorder(directory, 'America/Vancouver')
            recorder.record('packet', direction='tx', hex='01 02')
            recorder.close()
            files = list(Path(directory).glob('*.jsonl'))
            self.assertEqual(len(files), 1)
            rows = [json.loads(line) for line in files[0].read_text().splitlines()]
            packets = [row for row in rows if row['kind'] == 'packet']
            self.assertEqual([row['direction'] for row in packets], ['rx', 'tx'])
            self.assertEqual(packets[0]['hex'], '10 0b 15 21 f7 01 02 03 01 f7 46')
            for row in rows:
                self.assertIsNotNone(datetime.fromisoformat(row['timestamp_local']).utcoffset())
                self.assertEqual(datetime.fromisoformat(row['timestamp_utc']).utcoffset().total_seconds(), 0)
            if os.name == 'posix':
                self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)


class ModeAnnouncementTests(unittest.TestCase):
    def test_live_mode_change_switches_between_weight_only_and_media(self):
        with tempfile.TemporaryDirectory() as directory:
            token=Path(directory)/'token';token.write_text('dummy-token')
            modes=Path(directory)/'modes.json'
            config={'announcement_token':str(token),'announcement_certificate':'test-cert',
                    'reaction_catalog':str(Path(__file__).with_name('reactions.json')),
                    'modes_state':str(modes)}
            from unittest.mock import MagicMock
            response=MagicMock();response.__enter__.return_value.status=202
            with patch('sheets_logger.ssl.create_default_context'), patch('sheets_logger.urllib.request.urlopen',return_value=response) as send:
                modes.write_text(json.dumps({'version':1,'meme':False}))
                announce(config,80)
                body=json.loads(send.call_args.args[0].data)
                self.assertEqual(body['action'],'speak')
                self.assertTrue(send.call_args.args[0].full_url.endswith('/actions'))
                self.assertEqual(body['message'],'176.4 pounds.')
                modes.write_text(json.dumps({'version':1,'meme':True}))
                announce(config,80)
                body=json.loads(send.call_args.args[0].data)
                self.assertEqual(body['action'],'audio')
                self.assertIn('176.4',body['before'])


class OutboxTests(unittest.TestCase):
    def test_early_hook_ignores_settling_and_corrupt_packets_and_repeats(self):
        scale = InspectableQNScale.__new__(InspectableQNScale)
        weights = []
        scale.stable_callback = weights.append
        scale._stable_seen = False
        scale.early_announcement_ready = None
        def packet(status):
            payload = bytearray.fromhex('10 0b 15 21 f7 00 00 00 00 00 00')
            payload[5] = status
            payload[-1] = sum(payload[:-1]) % 256
            return payload
        with patch('sheets_logger.RenphoQNScale._handle_basic_measurement'):
            scale._handle_basic_measurement(packet(0), '', '')
            corrupt = packet(0x11)
            corrupt[-1] ^= 1
            scale._handle_basic_measurement(corrupt, '', '')
            self.assertEqual(weights, [])
            scale._handle_basic_measurement(packet(0x11), '', '')
            scale._handle_basic_measurement(packet(0x11), '', '')
            self.assertEqual(weights, [86.95])

    def test_duplicate_callbacks_restart_and_pending_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            box = Outbox(path)
            self.assertTrue(box.enqueue(80, now=1000))
            self.assertFalse(box.enqueue(80.01, now=1001))
            self.assertFalse(box.enqueue(float("nan"), now=1002))
            first = box.pending()
            box.db.close()
            box = Outbox(path)
            self.assertEqual(box.pending(), first)
            self.assertFalse(box.enqueue(80, now=1010))
            self.assertTrue(box.enqueue(80, now=1200))
            box.done(first[0])
            self.assertNotEqual(box.pending()[0], first[0])
            box.db.close()


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class SheetsTests(unittest.TestCase):
    def client(self):
        sheets = Sheets.__new__(Sheets)
        sheets.tz = ZoneInfo("America/Vancouver")
        sheets.base = "https://sheets.googleapis.com/v4/spreadsheets/example"
        sheets.tab = "'Weigh-ins'"
        return sheets

    def test_lost_ack_retry_checks_event_id_and_does_not_append_again(self):
        client = self.client()
        notes = []
        posts = []
        client.get = lambda rng: [HEADERS] if rng == "A1:E1" else notes if rng == "E2:E10000" else []

        class FakeSession:
            def post(self, url, **kwargs):
                posts.append(kwargs["json"]["values"][0])
                notes.append([posts[-1][4]])
                raise TimeoutError("server committed, response lost")

        client.session = FakeSession()
        reading = ("test-event", 1791356400, 80)
        with self.assertRaises(TimeoutError):
            client.upload(reading)
        self.assertEqual(client.upload(reading), "already_uploaded")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2], 176.4)
        self.assertEqual(posts[0][3], "Renpho scale / Pi")

    def test_changed_headers_prevent_write(self):
        client = self.client()
        client.get = lambda rng: [["wrong table"]]
        with self.assertRaises(ValueError):
            client.upload(("event", 1791356400, 80))


class ImmediateAnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def check_order(self, announcement_fails, early=False):
        loop = asyncio.get_running_loop()
        speech_started = asyncio.Event()
        upload_started = asyncio.Event()
        release_speech = threading.Event()
        order = []
        scales = []

        def fake_announce(config, kg):
            order.append('speech_started')
            loop.call_soon_threadsafe(speech_started.set)
            release_speech.wait(3)
            order.append('speech_finished')
            if announcement_fails:
                raise ConnectionError('bridge unavailable')

        class FakeSheets:
            def __init__(self, config):
                self.session = SimpleNamespace(close=lambda: None)

            def upload(self, reading):
                order.append('upload_started')
                loop.call_soon_threadsafe(upload_started.set)
                # Google is offline: a fresh punch line must still have run.
                raise ConnectionError('Google unavailable')

        class FakeScale:
            def __init__(self, address, callback, **kwargs):
                self.callback = callback
                self._bg_tasks = set()
                self.session_id = 'fake-session'
                self.stable_callback = kwargs['stable_callback']
                self.early_announcement_ready = None
                scales.append(self)

            async def async_start(self):
                if early:
                    self.early_announcement_ready = self.stable_callback(80)
                else:
                    self.fire_final()

            def fire_final(self):
                weight = 80.1 if early else 80
                self.callback(SimpleNamespace(measurements={'weight': weight}, address='fake', name='fake'))
                self.callback(SimpleNamespace(measurements={'weight': weight}, address='fake', name='fake'))

            async def async_stop(self):
                pass

            async def _teardown_client(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'outbox.sqlite3')
            config = {'outbox': path, 'address': 'fake', 'announce_on_reading': True, 'timezone': 'America/Vancouver'}
            with patch('sheets_logger.Sheets', FakeSheets), patch('sheets_logger.InspectableQNScale', FakeScale), patch('sheets_logger.announce', fake_announce), patch('sheets_logger.report'), patch.object(loop, 'add_signal_handler'):
                task = asyncio.create_task(run(config))
                try:
                    await asyncio.wait_for(speech_started.wait(), 2)
                    self.assertFalse(upload_started.is_set())
                    if early:
                        with closing(sqlite3.connect(path)) as db:
                            self.assertEqual(db.execute('SELECT COUNT(*) FROM readings').fetchone()[0], 0)
                        scales[0].fire_final()
                    release_speech.set()
                    await asyncio.wait_for(upload_started.wait(), 2)
                    # A pending wake signal may start a valid retry before the
                    # observer resumes on a fast runner. Ordering and outbox
                    # deduplication are the contract, not exactly one attempt.
                    self.assertEqual(order[:3], ['speech_started', 'speech_finished', 'upload_started'])
                    self.assertTrue(all(item == 'upload_started' for item in order[3:]))
                    with closing(sqlite3.connect(path)) as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM readings WHERE delivered=0').fetchone()[0], 1)
                        if early:
                            self.assertEqual(db.execute('SELECT kg FROM readings').fetchone()[0], 80.1)
                finally:
                    release_speech.set()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

    async def test_announcement_precedes_upload_even_when_google_is_offline(self):
        await self.check_order(False)

    async def test_failed_announcement_still_allows_logging_and_preserves_retry(self):
        await self.check_order(True)

    async def test_stable_speaks_before_final_without_logging_early_or_speaking_twice(self):
        await self.check_order(False, early=True)


if __name__ == "__main__":
    unittest.main()
