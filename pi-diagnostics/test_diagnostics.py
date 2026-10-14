import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnostics as health
import pc_monitor
import report
import snapshot


class DiagnosticsTests(unittest.TestCase):
    def test_normal_watchdog_startup_is_not_a_kernel_fault(self):
        self.assertEqual(health.categories('Watchdog running with a hardware timeout of 1min.'), [])
        self.assertIn('kernel', health.categories('watchdog: BUG: soft lockup'))

    def test_current_and_historical_power_distinguished(self):
        current = health.throttle_flags('throttled=0x50005')
        self.assertTrue(current['undervoltage_now'])
        self.assertTrue(current['throttled_now'])
        historical = health.throttle_flags('throttled=0x50000')
        self.assertFalse(historical['undervoltage_now'])
        self.assertFalse(historical['throttled_now'])
        self.assertTrue(historical['undervoltage_since_boot'])
        self.assertIsNone(health.throttle_flags(None))

    def test_fault_events_and_redaction(self):
        messages = ['Under-voltage detected!', 'Out of memory: Killed process 123 (python3)',
                    'mmc0: Timeout waiting for hardware interrupt.', 'EXT4-fs error (device mmcblk0p2)',
                    'watchdog: BUG: soft lockup', 'wlan0: deauth from aa:bb:cc:dd:ee:ff at 192.168.1.50',
                    'Normal kernel message']
        raw = '\n'.join(json.dumps({'MESSAGE': message, '__REALTIME_TIMESTAMP': '1791331200000000'})
                        for message in messages) + '\ninvalid JSON'
        events = health.kernel_events(raw)
        self.assertEqual(len(events), 6)
        self.assertEqual({category for event in events for category in event['categories']},
                         {'power', 'memory', 'storage', 'kernel', 'wifi'})
        self.assertNotIn('aa:bb:cc:dd:ee:ff', json.dumps(events))
        self.assertNotIn('192.168.1.50', json.dumps(events))

    def test_cleanup_preserves_unrelated_logs_and_recent_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / '2020-01-01.jsonl'
            unrelated.write_text('RAW SCALE DATA')
            old = root / 'health-2020-01-01.jsonl'
            old.write_text('{}\n')
            os.utime(old, (1, 1))
            health.append_record(root, 'health-', {'number': 7})
            self.assertFalse(old.exists())
            self.assertEqual(unrelated.read_text(), 'RAW SCALE DATA')
            current = next(root.glob('health-*'))
            self.assertEqual(json.loads(current.read_text())['number'], 7)
            if os.name != 'nt':
                self.assertEqual(current.stat().st_mode & 0o777, 0o600)

    def test_size_cap_trims_complete_records_not_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('health-' + dt.datetime.now(health.UTC).strftime('%Y-%m-%d') + '.jsonl')
            path.write_text(''.join(json.dumps({'number': i, 'padding': 'x' * 50}) + '\n' for i in range(100)))
            health.prune(directory, 'health-', max_bytes=1000)
            self.assertLessEqual(path.stat().st_size, 1000)
            data = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(data[-1]['number'], 99)

    def test_missing_hardware_commands_keep_other_health_data_and_exclude_private_wifi(self):
        def command(args, **kwargs):
            if args[0] == 'iw':
                return 'Connected to aa:bb:cc:dd:ee:ff\nSSID: SECRET NETWORK\nsignal: -66 dBm\n'
            if args[0] == 'systemctl':
                return 'ActiveState=active\nSubState=running\nNRestarts=2\nResult=success\nEnvironment=SECRET TOKEN\n'
            return None
        files = {'/proc/uptime': '123.5 40.0', '/proc/loadavg': '0.1 0.2 0.3 1/100 4',
                 '/proc/meminfo': 'MemTotal: 1024 kB\nMemAvailable: 512 kB\n'}
        with patch.object(snapshot, 'command', side_effect=command), patch.object(snapshot, 'read', side_effect=files.get):
            result = snapshot.collect()
        self.assertIsNone(result['power'])
        self.assertEqual(result['memory_kib']['MemAvailable'], 512)
        self.assertEqual(result['links']['wlan0']['signal_dbm'], -66)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('aa:bb:cc:dd:ee:ff', json.dumps(result))

    def test_report_missing_previous_boot_and_corrupt_last_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'health-2026-10-07.jsonl').write_text('{"retained":true}\n{"interrupted":')
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if '--list-boots' in args:
                    return ' 0 ' + 'a' * 32 + ' Wed 2026-10-07 00:00:00 UTC—Wed 2026-10-07 01:00:00 UTC'
                return ''
            with patch.object(report, 'STATE', root), patch.object(report, 'command', side_effect=command), patch.object(report, 'collect', return_value={}):
                result = report.report()
            self.assertFalse(result['kernel']['-1']['available'])
            self.assertEqual(result['recent_snapshots'], [{'retained': True}])
            self.assertTrue(all('--list-boots' in call or '-k' in call for call in calls))

    def test_reachability_failure_is_not_called_crash(self):
        with patch('socket.create_connection', side_effect=TimeoutError('must not log this private text')):
            result = pc_monitor.probe('192.168.1.50', 22)
        self.assertEqual(result, {'reachable': False, 'error': 'TimeoutError'})


if __name__ == '__main__':
    unittest.main()
