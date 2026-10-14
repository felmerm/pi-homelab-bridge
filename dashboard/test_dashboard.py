import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'notification-bridge'))
import server
import remote_probe
import remote_responses


class DashboardTests(unittest.TestCase):
    def test_weight_events_stream_is_same_origin_and_readonly(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=2)
            with patch.object(server.weight_logs, 'next_event', side_effect=[(1, '{"rows":[],"stream_live":true}'), OSError('closed')]) as events:
                conn.request('GET', '/api/weight-events', headers={'Host':'127.0.0.1:8787'})
                response = conn.getresponse()
                self.assertEqual(response.getheader('Content-Type'), 'text/event-stream; charset=utf-8')
                self.assertEqual(response.readline(), b'event: weights\n')
                self.assertEqual(response.readline(), b'id: 1\n')
                self.assertIn(b'"stream_live":true', response.readline())
                conn.close()
            conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=2)
            conn.request('GET', '/api/weight-events', headers={'Host':'127.0.0.1:8787','Origin':'https://evil.example'})
            self.assertEqual(conn.getresponse().status,403); conn.close()
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(timeout=2)

    def test_offline_does_not_label_cached_values_live(self):
        previous = {'health': {'utc': '2026-10-07T01:00:00Z', 'temperature_c': 52}}
        state = {'remote': previous, 'collection_error': 'Old failure'}
        with patch.object(server, 'STATE', state), patch.object(server, 'probe', side_effect=[{'reachable': False}, {'reachable': True}]), patch.object(server, 'remote_status') as ssh, patch.object(server, 'history', return_value=[]), patch.object(server, 'backup', return_value=None):
            server.collect_once()
            ssh.assert_not_called()
        self.assertFalse(state['detail_live'])
        self.assertEqual(state['remote'], previous)
        self.assertIsNone(state['collection_error'])

    def test_open_ssh_port_does_not_claim_authenticated_details(self):
        state = {'remote': None}
        with patch.object(server, 'STATE', state), patch.object(server, 'probe', return_value={'reachable': True}), patch.object(server, 'remote_status', return_value=(None, 'SSH status check failed')), patch.object(server, 'history', return_value=[]), patch.object(server, 'backup', return_value=None):
            server.collect_once()
        self.assertFalse(state['detail_live'])
        self.assertEqual(state['ssh_error'], 'SSH status check failed')

    def test_partial_power_cut_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.jsonl'
            path.write_text('{"utc":"2026-10-07"}\n{"partial":')
            self.assertEqual(server.read_tail(path), [{'utc': '2026-10-07'}])

    def test_no_history_is_not_filled_with_dummy_data(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'WORKSPACE', Path(folder)):
            self.assertEqual(server.history(), [])

    def test_event_transitions_not_duplicate_poll_records(self):
        points = [{'utc': str(i), 'pi': pi, 'nas': True} for i, pi in enumerate([True, True, False, False, True])]
        result = server.events(points)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]['utc'], '4')

    def test_bridge_status_allowlist_never_returns_token_or_unknown_upstream_fields(self):
        upstream = {'announced': 3, 'modes': {'meme': True, 'private': 'SECRET'},
                    'token': 'SECRET', 'cookies': 'SECRET', 'username': 'SECRET'}
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(upstream).encode()
        with patch.object(remote_probe, 'read', return_value='BRIDGE_TOKEN=PRIVATE_TOKEN'), patch.object(remote_probe.ssl, 'create_default_context'), patch.object(remote_probe.urllib.request, 'urlopen', return_value=response):
            result = remote_probe.bridge_status()
        self.assertEqual(result, {'announced': 3, 'meme_mode': True})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_http_host_origin_file_allowlist_and_readonly(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        def request(path, method='GET', **headers):
            connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=2)
            connection.request(method, path, headers={'Host': '127.0.0.1:8787', **headers})
            response = connection.getresponse()
            body = response.read()
            code = response.status
            connection.close()
            return code, body
        try:
            self.assertEqual(request('/api/status')[0], 200)
            with patch.object(server.weight_logs, 'history', return_value={'rows': [], 'cached': False}) as weights:
                self.assertEqual(json.loads(request('/api/weights')[1])['rows'], [])
                self.assertEqual(request('/api/weights', Origin='https://evil.example')[0], 403)
                weights.assert_called_once()
            self.assertEqual(request('/api/weights', 'POST', Origin='http://127.0.0.1:8787', **{'X-Lab-Action':'response-test'})[0], 405)
            self.assertEqual(request('/api/status', Host='evil.example:8787')[0], 403)
            self.assertEqual(request('/api/status', Origin='https://evil.example')[0], 403)
            self.assertEqual(request('/api/status', **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
            self.assertEqual(request('/remote_probe.py')[0], 404)
            self.assertEqual(request('/../notification-bridge.env')[0], 404)
            self.assertEqual(request('/api/status', 'POST', Origin='http://127.0.0.1:8787', **{'X-Lab-Action':'response-test'})[0], 405)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)

    def test_response_play_requires_same_origin_valid_selection_and_rate_limit(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        body = {'pool':'bad','index':0,'weight':150,'revision':'a'*64}
        def request(payload=body, origin='http://127.0.0.1:8787', header=True):
            conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=2)
            headers = {'Host':'127.0.0.1:8787','Content-Type':'application/json'}
            if origin is not None: headers['Origin'] = origin
            if header: headers['X-Lab-Action'] = 'response-test'
            conn.request('POST','/api/responses/play',json.dumps(payload),headers)
            response=conn.getresponse();result=(response.status,response.read());conn.close();return result
        try:
            with patch.object(server,'remote_responses',return_value=(202,{'status':'queued'})) as remote, patch.object(server,'LAST_PLAY',0):
                self.assertEqual(request(origin='https://evil.example')[0],403)
                self.assertEqual(request(origin=None)[0],403)
                self.assertEqual(request(header=False)[0],403)
                self.assertEqual(request({**body,'pool':'special'})[0],400)
                self.assertEqual(request({**body,'action':'lights'})[0],400)
                remote.assert_not_called()
                self.assertEqual(request()[0],202)
                remote.assert_called_once_with('play',body)
                self.assertEqual(request()[0],429)
        finally:
            httpd.shutdown();httpd.server_close();worker.join(timeout=2)


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads((server.WORKSPACE/'renpho-scale/reactions.json').read_text())
        self.body={'pool':'bad','index':0,'weight':150.5,'revision':'a'*64}

    def test_selected_response_formats_exact_weight_and_uses_speak_for_audio_off(self):
        action=remote_responses.build_action(self.body,self.catalog,'a'*64)
        self.assertEqual(action['action'],'audio')
        self.assertEqual(action['before'],self.catalog['bad'][0]['before'].format(weight='150.5'))
        self.assertEqual(action['url'],self.catalog['sounds'][self.catalog['bad'][0]['sound']])
        self.catalog['audio_enabled']=False
        self.assertEqual(remote_responses.build_action(self.body,self.catalog,'a'*64)['action'],'speak')

    def test_stale_catalog_and_invalid_input_cannot_play(self):
        for body in ({**self.body,'weight':float('nan')},{**self.body,'weight':True},
                     {**self.body,'weight':397},{**self.body,'index':True},
                     {**self.body,'pool':'special'},{**self.body,'url':'https://example.com/a.mp3'},
                     {**self.body,'index':99},{**self.body,'revision':'b'*64}):
            with self.assertRaises(remote_responses.ResponseError):
                remote_responses.build_action(body,self.catalog,'a'*64)

    def test_catalog_returns_only_response_fields_and_play_only_queues_selected_action(self):
        public=remote_responses.public_catalog(self.catalog,'a'*64)
        self.assertEqual(len(public['entries']),20)
        self.assertEqual(set(public['entries'][0]),{'pool','index','sound','before','after'})
        with patch.object(remote_responses,'load_catalog',return_value=(self.catalog,'a'*64)), patch.object(remote_responses,'queue_action',return_value={'status':'queued'}) as queue:
            self.assertEqual(remote_responses.perform('catalog')[0],200)
            queue.assert_not_called()
            self.assertEqual(remote_responses.perform('play',self.body)[0],202)
            queue.assert_called_once_with(remote_responses.build_action(self.body,self.catalog,'a'*64))

    def test_draft_validation_and_playback_do_not_save_or_touch_live_catalog(self):
        draft=copy.deepcopy(self.catalog); draft['bad'][0]['before']='Draft says {weight}.'
        with patch.object(remote_responses,'load_catalog',return_value=(self.catalog,'a'*64)), patch.object(remote_responses,'queue_action',return_value={'status':'queued'}) as queue, patch.object(remote_responses,'apply_catalog') as apply:
            code,result=remote_responses.perform('validate',{'catalog':draft,'revision':'a'*64})
            self.assertEqual(code,200);self.assertEqual(result['catalog'],draft)
            queue.assert_not_called();apply.assert_not_called()
            remote_responses.perform('play-draft',{**self.body,'catalog':draft})
            self.assertEqual(queue.call_args.args[0]['before'],'Draft says 150.5.')
            apply.assert_not_called();self.assertNotEqual(self.catalog['bad'][0]['before'],draft['bad'][0]['before'])

    def test_invalid_catalogs_are_rejected_with_specific_errors(self):
        for mutate in (lambda c:c.update(roast_threshold_lb=True),
                       lambda c:c.update(roast_threshold_lb=float('nan')),
                       lambda c:c.update(audio_enabled='yes'),
                       lambda c:c.update(command='reboot'),
                       lambda c:c['sounds'].update(bruh='http://192.168.1.50/a.mp3'),
                       lambda c:c['bad'][0].update(before='Unsafe {weight.__class__}'),
                       lambda c:c.update(bad=[]),
                       lambda c:c['bad'][0].update(sound='missing')):
            draft=copy.deepcopy(self.catalog);mutate(draft)
            with self.assertRaises(remote_responses.ResponseError) as error:remote_responses.validate_catalog(draft)
            self.assertEqual(error.exception.code,400)

    def test_apply_backs_up_and_restarts_and_failed_restart_restores_both_files(self):
        from types import SimpleNamespace
        fake_fcntl=SimpleNamespace(LOCK_EX=1,flock=lambda *args:None)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);live=root/'live.json';installed=root/'installed.json';lock=root/'lock';backups=root/'backups'
            original=json.dumps(self.catalog).encode();live.write_bytes(original);installed.write_bytes(original)
            draft=copy.deepcopy(self.catalog);draft['bad'][0]['before']='Edited {weight}.'
            def atomic(path,data,*args):path.write_bytes(data)
            with patch.dict(sys.modules,{'fcntl':fake_fcntl}), patch.object(remote_responses,'CATALOG',live), patch.object(remote_responses,'INSTALLED_CATALOG',installed), patch.object(remote_responses,'CATALOG_BACKUPS',backups), patch.object(remote_responses,'CATALOG_LOCK',lock), patch.object(remote_responses,'atomic_catalog',side_effect=atomic), patch.object(remote_responses.subprocess,'run') as restart:
                revision=remote_responses.load_catalog()[1]
                result=remote_responses.apply_catalog({'catalog':draft,'revision':revision})
                self.assertEqual(json.loads(live.read_bytes()),draft);self.assertEqual(live.read_bytes(),installed.read_bytes())
                self.assertEqual(next(backups.glob('*.json')).read_bytes(),original)
                self.assertEqual(result['catalog'],draft);self.assertEqual(restart.call_args_list[0].args[0],['systemctl','restart','renpho-sheets'])
                with self.assertRaises(remote_responses.ResponseError):remote_responses.apply_catalog({'catalog':self.catalog,'revision':revision})
                saved=live.read_bytes();revision=remote_responses.load_catalog()[1]
                restart.side_effect=[RuntimeError('restart failed'),None]
                with self.assertRaises(remote_responses.ResponseError):remote_responses.apply_catalog({'catalog':self.catalog,'revision':revision})
                self.assertEqual(live.read_bytes(),saved);self.assertEqual(installed.read_bytes(),saved)


if __name__ == '__main__':
    unittest.main()
