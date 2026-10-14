import hashlib
import http.client
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from actions import parse_action
from control_state import ModeStore
from dispatch import execute_action
from hue_local import HueAPIError, HueLocal, PinnedHTTPS, hue_request, local_host


class ModeTests(unittest.TestCase):
    def test_corrupt_state_fails_quietly_to_normal_mode_and_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ModeStore(Path(directory)/'modes.json')
            self.assertTrue(store.read()['meme'])
            store.path.write_text('not json')
            self.assertFalse(store.read()['meme'])
            store.set_meme(True)
            self.assertTrue(ModeStore(store.path).read()['meme'])
            self.assertFalse(list(Path(directory).glob('.modes-*')))


class HueTests(unittest.IsolatedAsyncioTestCase):
    def test_api_error_exposes_only_numeric_code_not_secret_paths(self):
        error = HueAPIError([{'error': {'type': 101, 'address': '/api/PRIVATE_KEY',
                                      'description': 'private upstream text'}}])
        self.assertEqual(error.error_types, (101,))
        self.assertNotIn('PRIVATE_KEY', str(error))
        self.assertNotIn('upstream', str(error))

    def test_pin_mismatch_stops_http_before_credentials_are_sent(self):
        sock=Mock()
        sock.getpeercert.return_value=b'changed-cert'
        def connect(connection):connection.sock=sock
        with patch.object(http.client.HTTPSConnection,'connect',connect):
            connection=PinnedHTTPS('10.0.0.99','0'*64)
            with self.assertRaises(ssl.SSLError):
                connection.request('PUT','/api/dummy-secret/groups/0/action',b'{}')
            sock.sendall.assert_not_called()
            sock.close.assert_called_once()

    def test_matching_pin_allows_http_on_the_pinned_connection(self):
        sock=Mock();sock.getpeercert.return_value=b'known-cert'
        def connect(connection):connection.sock=sock
        with patch.object(http.client.HTTPSConnection,'connect',connect):
            connection=PinnedHTTPS('10.0.0.99',hashlib.sha256(b'known-cert').hexdigest())
            connection.request('GET','/api/test')
            sock.sendall.assert_called_once()

    def test_rejects_non_lan_addresses(self):
        for host in ('example.com','127.0.0.1','8.8.8.8','169.254.1.1','::1'):
            with self.assertRaises(ValueError):local_host(host)

    def test_rejects_redirects_and_hue_body_errors(self):
        connection=Mock()
        response=connection.getresponse.return_value
        with patch('hue_local.PinnedHTTPS',return_value=connection):
            response.status=302
            with self.assertRaises(RuntimeError):hue_request('10.0.0.99','0'*64,'GET','/api')
            response.read.assert_not_called()
            response.status=200;response.read.return_value=b'[{"error":{"type":1}}]'
            with self.assertRaises(RuntimeError):hue_request('10.0.0.99','0'*64,'GET','/api')

    async def test_color_is_one_local_group_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'hue.json'
            path.write_text(json.dumps({'host':'10.0.0.99','certificate_sha256':'0'*64,
                'username':'dummy-key','targets':{'all lights':{'type':'group','id':'0'}}}))
            with patch('hue_local.hue_request') as sent:
                await HueLocal(path).set_lights('all lights','red')
                sent.assert_called_once_with('10.0.0.99','0'*64,'PUT',
                    '/api/dummy-key/groups/0/action',{'on':True,'hue':0,'sat':254})

    async def test_router_keeps_lights_local_and_orders_before_audio(self):
        events=[]
        alexa=Mock();hue=Mock()
        async def lights(*args):events.append('local lights')
        async def amazon(action):events.append([s.kind for s in action.steps])
        hue.set_lights=AsyncMock(side_effect=lights)
        alexa.execute=AsyncMock(side_effect=amazon)
        action=parse_action({'action':'sequence','steps':[
            {'action':'lights','target':'all lights','color':'red'},
            {'action':'audio','url':'https://example.com/a.mp3'}]})
        await execute_action(action,alexa,hue)
        self.assertEqual(events,['local lights',['audio']])
        hue.set_lights.side_effect=RuntimeError('not linked')
        alexa.execute.reset_mock()
        with self.assertRaises(RuntimeError):await execute_action(action,alexa,hue)
        alexa.execute.assert_not_awaited()


if __name__=='__main__':unittest.main()
