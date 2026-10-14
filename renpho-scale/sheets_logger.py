"""Felm's QN scale → existing Weigh-ins table. No weight in console logs."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import queue
import signal
import sqlite3
import ssl
import time
import threading
import sys
from urllib.parse import quote
import urllib.request
import uuid
from zoneinfo import ZoneInfo

# systemd uses -I; only import siblings from the root-owned installation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reactions import selector, meme_enabled

from renpho_escs20m import RenphoQNScale, WEIGHT_KEY, WeightUnit
from renpho_escs20m.qn.protocol import parse_basic_measurement, parse_extended_measurement

HEADERS = ["Date", "Time", "Weight (lb)", "Status / Source", "Notes"]
SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class RawRecorder:
    """Private daily JSONL files; disk writes run off the Bluetooth callback."""
    def __init__(self, directory, timezone_name):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.timezone = ZoneInfo(timezone_name)
        self.queue = queue.Queue()
        self.failure = None
        self.thread = threading.Thread(target=self._writer, name='scale-raw-log', daemon=True)
        self.thread.start()
        self.record('logging_started')

    def record(self, kind, **fields):
        if self.failure:
            return
        now = datetime.now(timezone.utc)
        self.queue.put({'timestamp_utc': now.isoformat(timespec='milliseconds'),
                        'timestamp_local': now.astimezone(self.timezone).isoformat(timespec='milliseconds'),
                        'monotonic_ns': time.monotonic_ns(), 'kind': kind, **fields})

    def _writer(self):
        file = None
        current_day = None
        try:
            while True:
                try:
                    entry = self.queue.get(timeout=1)
                except queue.Empty:
                    if file:
                        file.flush()
                    continue
                if entry is None:
                    break
                day = entry['timestamp_local'][:10]
                if day != current_day:
                    if file:
                        file.close()
                    path = self.directory / (day + '.jsonl')
                    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                    os.fchmod(fd, 0o600)
                    file = os.fdopen(fd, 'a', encoding='utf-8')
                    current_day = day
                file.write(json.dumps(entry, separators=(',', ':')) + '\n')
                if self.queue.empty():
                    file.flush()
        except Exception as exc:
            self.failure = type(exc).__name__
            report('raw_logging_failed', error_type=self.failure)
        finally:
            if file:
                file.close()

    def close(self):
        self.queue.put(None)
        self.thread.join(timeout=10)
        if self.thread.is_alive() or self.failure:
            raise RuntimeError('Raw logger did not finish successfully')


class InspectableQNScale(RenphoQNScale):
    """Optional, explicitly armed one-packet inspection; off by default."""
    def __init__(self, *args, inspection_dir=None, stable_callback=None, recorder=None, **kwargs):
        self.recorder = recorder
        self.session_id = None
        self.inspection_dir = Path(inspection_dir) if inspection_dir else None
        self.stable_callback = stable_callback
        self.early_announcement_ready = None
        self._stable_seen = False
        super().__init__(*args, **kwargs)

    async def _start_scale_session(self, ble_device):
        self.session_id = str(uuid.uuid4())
        self._stable_seen = False
        self.early_announcement_ready = None
        await super()._start_scale_session(ble_device)

    def _record_advertisement(self, advertisement_data):
        super()._record_advertisement(advertisement_data)
        if self.recorder:
            self.recorder.record('advertisement', address=self.address, session_id=self.session_id,
                rssi=advertisement_data.rssi,
                tx_power=advertisement_data.tx_power, service_uuids=advertisement_data.service_uuids,
                manufacturer_data={f'0x{k:04x}': v.hex(' ') for k, v in advertisement_data.manufacturer_data.items()},
                service_data={k: v.hex(' ') for k, v in advertisement_data.service_data.items()})

    def _trace_event(self, event, *, marker=False):
        super()._trace_event(event, marker=marker)
        if self.recorder:
            self.recorder.record('session_event', event=event, session_id=self.session_id)

    def _emit_stable(self, kg):
        if self._stable_seen or not self.stable_callback or not 3 <= kg <= 180:
            return
        self._stable_seen = True
        self.early_announcement_ready = self.stable_callback(kg)

    def _handle_basic_measurement(self, payload, name, address):
        # Our actual unit sends status 0x11 when weight is stable but BIA is
        # still running. Validate the complete packet before speaking early.
        if len(payload) == 11 and payload[:2] == b'\x10\x0b' and sum(payload[:-1]) % 256 == payload[-1]:
            frame = parse_basic_measurement(payload)
            if frame.status == 0x11:
                self._emit_stable(frame.weight_kg)
        super()._handle_basic_measurement(payload, name, address)

    def _handle_extended_measurement(self, payload, name, address):
        if len(payload) in (14, 15) and payload[0] == 0x10 and payload[1] == len(payload) and sum(payload[:-1]) % 256 == payload[-1]:
            frame = parse_extended_measurement(payload)
            if frame.status == 1:
                self._emit_stable(frame.weight_kg)
        super()._handle_extended_measurement(payload, name, address)

    def _trace_frame(self, direction, data, note=None):
        super()._trace_frame(direction, data, note)
        if self.recorder:
            fields = {'direction': direction, 'hex': bytes(data).hex(' '),
                      'byte_count': len(data), 'session_id': self.session_id, 'note': note}
            if len(data) >= 2 and data[0] == 0x10:
                if len(data) == 11 and data[1] == 11:
                    fields.update(decoded=parse_basic_measurement(data)._asdict())
                elif len(data) in (14, 15) and data[1] == len(data):
                    fields.update(decoded=parse_extended_measurement(data)._asdict())
            self.recorder.record('packet', **fields)
        if self.inspection_dir is None or direction != 'rx' or len(data) < 2 or data[0] != 0x10:
            return
        request = self.inspection_dir / 'inspect-packet.request'
        if not request.exists():
            return
        try:
            if time.time() > float(request.read_text()):
                request.unlink()
                return
            if len(data) == 11 and data[1] == 11:
                frame = parse_basic_measurement(data)
                if frame.status not in (0x11, 0x01):
                    return
                flavor = 'basic'
                stage = 'stable, impedance running' if frame.status == 0x11 else 'final'
            elif len(data) in (14, 15) and data[1] == len(data):
                frame = parse_extended_measurement(data)
                if frame.status not in (1, 2):
                    return
                flavor = 'extended'
                stage = 'stable' if frame.status == 1 else 'final'
            else:
                return
            capture = {'hex': bytes(data).hex(' '), 'byte_count': len(data),
                       'flavor': flavor, 'stage': stage, 'decoded': frame._asdict()}
            output = self.inspection_dir / 'packet-inspection.json'
            with output.open('w', encoding='utf-8') as file:
                json.dump(capture, file, indent=2)
            output.chmod(0o600)
            request.unlink()
            report('one_packet_captured')
        except Exception as exc:
            report('packet_inspection_failed', error_type=type(exc).__name__)


def report(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def announcement_text(kg):
    pounds = round(kg * 2.20462262185, 1)
    return f"GOD DAMN YOU'RE {pounds:g} pounds. Um... Okay then."


def announcements_enabled(config):
    return config.get("announce_on_reading", config.get("announce_after_upload", False))


def announce(config, kg, dry_run=False):
    token = Path(config["announcement_token"]).read_text().strip()
    context = ssl.create_default_context(cafile=config["announcement_certificate"])
    if config.get('reaction_catalog'):
        body = selector(config['reaction_catalog']).choose(kg,
            meme_mode=meme_enabled(config.get('modes_state', '/var/lib/homelab-controls/modes.json')))
        body['dry_run'] = dry_run
        endpoint = '/actions'
    else:
        body = {"action": "speak", "message": announcement_text(kg), "dry_run": dry_run}
        endpoint = '/actions'
    request = urllib.request.Request(
        "https://127.0.0.1:8765" + endpoint,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, context=context, timeout=8) as response:
        if response.status not in (200, 202):
            raise ValueError("Unexpected bridge response")


class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS readings (id TEXT PRIMARY KEY, timestamp REAL NOT NULL, kg REAL NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)")
        self.db.commit()

    def is_duplicate(self, kg, now=None):
        now = time.time() if now is None else now
        previous = self.db.execute("SELECT timestamp, kg FROM readings ORDER BY timestamp DESC LIMIT 1").fetchone()
        return bool(previous and 0 <= now - previous[0] < 120 and abs(kg - previous[1]) < 0.025)

    def enqueue(self, kg, now=None):
        if not isinstance(kg, (int, float)) or not math.isfinite(kg) or not 3 <= kg <= 180:
            return False
        now = time.time() if now is None else now
        if self.is_duplicate(kg, now):
            return False
        with self.db:
            event_id = str(uuid.uuid4())
            self.db.execute("INSERT INTO readings(id,timestamp,kg) VALUES (?,?,?)", (event_id, now, kg))
        return event_id

    def pending(self):
        return self.db.execute("SELECT id,timestamp,kg FROM readings WHERE delivered=0 ORDER BY timestamp LIMIT 1").fetchone()

    def done(self, event_id):
        with self.db:
            self.db.execute("UPDATE readings SET delivered=1 WHERE id=?", (event_id,))
            self.db.execute("DELETE FROM readings WHERE delivered=1 AND timestamp < ?", (time.time() - 7 * 86400,))


class Sheets:
    def __init__(self, config):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        with open(config["credentials"], encoding="utf-8") as file:
            key = json.load(file)
        # Do not send assertions to a token endpoint supplied in an arbitrary file.
        if key.get("type") != "service_account" or key.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise ValueError("Expected a genuine Google service-account JSON key")
        creds = service_account.Credentials.from_service_account_info(key, scopes=[SCOPE])
        self.session = AuthorizedSession(creds, refresh_timeout=20)
        self.base = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(config["spreadsheet_id"], safe="")
        self.tab = "'" + config["sheet_name"].replace("'", "''") + "'"
        self.tz = ZoneInfo(config["timezone"])

    def get(self, cell_range):
        response = self.session.get(self.base + "/values/" + quote(self.tab + "!" + cell_range, safe=""), timeout=20)
        response.raise_for_status()
        return response.json().get("values", [])

    def verify(self):
        if self.get("A1:E1") != [HEADERS]:
            raise ValueError("Destination headers changed; stop rather than write into the wrong table")

    def upload(self, reading):
        event_id, timestamp, kg = reading
        self.verify()
        marker = "Renpho event " + event_id
        # A single uploader checks IDs before each attempt, including retries
        # after an ambiguous timeout. Bound growth rather than scan whole grids.
        notes = self.get("E2:E10000")
        if any(row and row[0] == marker for row in notes):
            return "already_uploaded"
        dates = self.get("A2:A10000")
        if len(dates) >= 9999:
            raise ValueError("10,000-row safety limit reached; increase deliberately")
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(self.tz)
        values = [[local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S"),
                   round(kg * 2.20462262185, 1), "Renpho scale / Pi", marker]]
        response = self.session.post(
            self.base + "/values/" + quote(self.tab + "!A1:E10000", safe="") + ":append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": values}, timeout=20,
        )
        response.raise_for_status()
        if response.json().get("updates", {}).get("updatedRows") != 1:
            raise RuntimeError("Unexpected append response")
        return "uploaded"


async def run(config, check_only=False):
    logging.disable(logging.CRITICAL)
    sheets = Sheets(config)
    if check_only:
        await asyncio.to_thread(sheets.verify)
        report("google_read_access_verified", writes_tested=False)
        if announcements_enabled(config):
            await asyncio.to_thread(announce, config, 80, True)
            report("announcement_connection_verified", dry_run=True)
        sheets.session.close()
        return
    outbox = Outbox(config["outbox"])
    recorder = RawRecorder(Path(config['outbox']).parent / 'raw', config['timezone'])
    stopped = asyncio.Event()
    upload_ready = {}
    announcement_tasks = set()
    wake_uploader = asyncio.Event()
    wake_uploader.set()  # Resume any older outbox entries without replaying speech.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)

    async def announce_first(kg, ready):
        try:
            await asyncio.to_thread(announce, config, kg)
            report("announcement_queued")
        except Exception as exc:
            report("announcement_failed", error_type=type(exc).__name__)
        finally:
            # The local bridge enqueue completes before this reading can upload.
            # Amazon playback happens independently in the existing bridge.
            ready.set()
            wake_uploader.set()

    def schedule_announcement(kg):
        ready = asyncio.Event()
        task = asyncio.create_task(announce_first(kg, ready))
        announcement_tasks.add(task)
        task.add_done_callback(announcement_tasks.discard)
        return ready

    last_stable = None
    def on_stable(kg):
        nonlocal last_stable
        if not announcements_enabled(config) or outbox.is_duplicate(kg):
            return None
        now = time.time()
        if last_stable and 0 <= now - last_stable[0] < 120 and abs(kg - last_stable[1]) < 0.025:
            # A reconnect before final completion must not replay speech.
            return last_stable[2]
        ready = schedule_announcement(kg)
        last_stable = (now, kg, ready)
        report('stable_weight_received')
        return ready

    def on_reading(data):
        recorder.record('final_reading', session_id=scale.session_id,
                        address=data.address, display_unit='lb',
                        measurements=data.measurements)
        kg = data.measurements.get(WEIGHT_KEY)
        event_id = outbox.enqueue(kg)
        if event_id:
            report("reading_queued")
            if announcements_enabled(config):
                ready = getattr(scale, 'early_announcement_ready', None)
                if ready is None:
                    # Some sessions omit the early stage: speak on final then.
                    ready = schedule_announcement(kg)
                upload_ready[event_id] = ready
            else:
                wake_uploader.set()

    scale = InspectableQNScale(config["address"], on_reading, display_unit=WeightUnit.LB,
                          profile=None, clear_stored_measurements=False, max_connect_attempts=1,
                          inspection_dir=str(Path(config['outbox']).parent), stable_callback=on_stable,
                          recorder=recorder)
    async def uploader():
        delay = 5
        while not stopped.is_set():
            wake_uploader.clear()
            reading = outbox.pending()
            if reading:
                ready = upload_ready.pop(reading[0], None)
                if ready is not None:
                    await ready.wait()
                try:
                    result = await asyncio.to_thread(sheets.upload, reading)
                    outbox.done(reading[0])
                    report(result)
                    delay = 5
                except Exception as exc:
                    # Do not expose responses, tokens, IDs, or weight values.
                    report("upload_deferred", error_type=type(exc).__name__)
                    delay = min(delay * 2, 300)
            try:
                await asyncio.wait_for(wake_uploader.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    worker = None
    try:
        await scale.async_start()
        worker = asyncio.create_task(uploader())
        report("listening", display_unit="lb")
        await stopped.wait()
    finally:
        stopped.set()
        wake_uploader.set()
        await scale.async_stop()
        tasks = list(scale._bg_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wait_for(scale._teardown_client(), timeout=10)
        await asyncio.gather(*announcement_tasks, return_exceptions=True)
        if worker:
            await worker
        sheets.session.close()
        outbox.db.close()
        await asyncio.to_thread(recorder.close)


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/renpho-scale/sheets.json")
    parser.add_argument("--check", action="store_true", help="Read headers and dry-run announcements; no scale scan or spoken announcement")
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as file:
            config = json.load(file)
        asyncio.run(run(config, args.check))
    except Exception as exc:
        report("stopped_with_error", error_type=type(exc).__name__)
        raise SystemExit(1)
