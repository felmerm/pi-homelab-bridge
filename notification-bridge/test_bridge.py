"""Checks authentication, dry runs, queuing, and direct adapter without Amazon calls."""
import asyncio
import json
import os
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from unittest.mock import AsyncMock, Mock, patch

os.environ["BRIDGE_TOKEN"] = "test-only-token-" + "x" * 32
import bridge
from alexa_direct import AlexaDirect, AmazonCookieJar, discover_devices, auth_failure_hint, send_speech, send_actions
from aioamazondevices.implementation.sequence import AmazonSequenceHandler
from aioamazondevices.structures import AmazonSequenceType
from yarl import URL
from browser_link import parse_callback
from actions import Action, parse_action
from ssh_receive import payload
from control_state import ModeStore


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/notify"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def send(self, body, authorized=True, path="/notify"):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer " + bridge.TOKEN
        req = urllib.request.Request(self.url.removesuffix("/notify") + path, json.dumps(body).encode(), headers)
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_unauthorized_does_not_queue(self):
        self.assertEqual(self.send({"message": "test"}, False)[0], 401)
        self.assertTrue(bridge.PENDING.empty())

    def test_dry_run_without_account(self):
        with patch.object(bridge, "configured", return_value=False):
            self.assertEqual(self.send({"message": "test", "dry_run": True})[0], 200)
            self.assertEqual(self.send({"message": "test"})[0], 503)
        self.assertTrue(bridge.PENDING.empty())

    def test_bad_field(self):
        self.assertEqual(self.send({"message": 123})[0], 400)

    def test_queue_and_deduplicate(self):
        with patch.object(bridge, "configured", return_value=True):
            body = {"app": "Messages", "message": "hello"}
            self.assertEqual(self.send(body)[0], 202)
            self.assertTrue(self.send(body)[1]["duplicate"])
        self.assertEqual(bridge.PENDING.get_nowait(), Action("announce", message="Messages. hello"))
        bridge.PENDING.task_done()
        self.assertTrue(bridge.PENDING.empty())

    def test_audio_uses_authenticated_shared_queue(self):
        body = {"action": "audio", "url": "https://example.com/clip.mp3"}
        self.assertEqual(self.send(body, False, "/actions")[0], 401)
        with patch.object(bridge, "configured", return_value=True):
            self.assertEqual(self.send(body, path="/actions")[0], 202)
            self.assertTrue(self.send(body, path="/actions")[1]["duplicate"])
        self.assertEqual(bridge.PENDING.get_nowait(), parse_action(body))
        bridge.PENDING.task_done()

    def test_invalid_audio_and_dry_run_never_queue(self):
        self.assertEqual(self.send({"action": "audio", "url": "http://192.168.1.50/a.mp3"}, path="/actions")[0], 400)
        self.assertEqual(self.send({"action": "audio", "url": "https://example.com/a.mp3", "dry_run": True}, path="/actions")[0], 200)
        self.assertTrue(bridge.PENDING.empty())

    def test_mode_is_authenticated_persistent_and_independent_of_amazon(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ModeStore(Path(directory) / 'modes.json')
            body = {'action':'mode','name':'meme','enabled':False}
            with patch.object(bridge,'MODES',store), patch.object(bridge,'configured',return_value=False):
                self.assertEqual(self.send(body,False,'/actions')[0],401)
                self.assertTrue(store.read()['meme'])
                self.assertEqual(self.send({**body,'dry_run':True},path='/actions')[0],200)
                self.assertTrue(store.read()['meme'])
                for enabled in (False,True,False):
                    self.assertEqual(self.send({**body,'enabled':enabled},path='/actions')[0],200)
                    self.assertIs(ModeStore(store.path).read()['meme'],enabled)
                self.assertTrue(bridge.PENDING.empty())

    def test_unlinked_hue_has_no_amazon_fallback(self):
        body={'action':'lights','target':'all lights','color':'red'}
        with patch.object(bridge,'configured',return_value=False), patch.object(bridge.HueLocal,'configured',return_value=False):
            self.assertEqual(self.send(body,path='/actions')[1]['error'],'hue_not_linked')
        with patch.object(bridge,'configured',return_value=False), patch.object(bridge.HueLocal,'configured',return_value=True):
            self.assertEqual(self.send(body,path='/actions')[0],202)
        self.assertEqual(bridge.PENDING.get_nowait(),parse_action(body))
        bridge.PENDING.task_done()


class ActionTests(unittest.TestCase):
    def test_modes_accept_only_explicit_boolean_and_no_nested_setting(self):
        body={'action':'mode','name':'meme','enabled':False}
        self.assertFalse(parse_action(body).enabled)
        for bad in ({**body,'enabled':'off'},{**body,'name':'shell'},
                    {'action':'sequence','steps':[body]}):
            with self.assertRaises(ValueError):parse_action(bad)
    def test_sequences_are_bounded_non_nested_and_validate_every_step(self):
        body = {'action':'sequence','steps':[{'action':'lights','target':'all lights','color':'red'},
                                            {'action':'audio','url':'https://example.com/test.mp3'}]}
        self.assertEqual(len(parse_action(body).steps),2)
        for steps in ([],[body],[{'action':'shell'}],body['steps']*3,
                      [{'action':'lights','target':'all lights','color':'invisible'}],
                      [{'action':'announce','message':'hello','dry_run':True}]):
            with self.assertRaises(ValueError):parse_action({'action':'sequence','steps':steps})
    def test_url_and_action_validation(self):
        for url in ("http://example.com/a.mp3", "https://user:pass@example.com/a.mp3",
                    "https://192.168.1.50/a.mp3", "https://pi.local/a.mp3",
                    "https://example.com:8765/a.mp3", "https://example.com/a.mp3#x",
                    "https://example.com/a\n.mp3", "https://example.com\\evil/a.mp3"):
            with self.assertRaises(ValueError):
                parse_action({"action": "audio", "url": url})
        for body in ({"action": "execute", "command": "ls"},
                     {"action": "audio", "url": "https://example.com/a.mp3", "ssml": "raw"},
                     {"action": "audio", "url": "https://example.com/a.mp3", "dry_run": "yes"}):
            with self.assertRaises(ValueError):
                parse_action(body)

    def test_generated_ssml_escapes_text_and_url(self):
        action = parse_action({"action": "audio", "url": "https://example.com/a.mp3?a=1&b=2",
                               "before": '<audio src="evil"/>', "after": "A & B"})
        self.assertIn('a=1&amp;b=2', action.audio_ssml())
        self.assertIn('&lt;audio', action.audio_ssml())
        self.assertEqual(action.audio_ssml().count('<audio '), 1)

    def test_ssh_text_compatibility_and_structured_action(self):
        self.assertEqual(payload("echo hello"), {"message": "echo hello", "dry_run": False})
        self.assertTrue(payload("bridge-dry-run:hello")["dry_run"])
        body = {"action": "audio", "url": "https://example.com/a.mp3"}
        self.assertEqual(payload("bridge-action:" + json.dumps(body)), body)
        with self.assertRaises(ValueError):
            payload('bridge-action:{"action":"shell"}')


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_amazon_adapter_refuses_lighting_instead_of_interpreting_commands(self):
        wrapper = SimpleNamespace(session_request=AsyncMock())
        state = SimpleNamespace(login_stored_data={'test':True},account_customer_id='dummy',
                                language='en-US',alexa_website_url=URL('https://alexa.amazon.com'))
        handler = AmazonSequenceHandler(wrapper,state)
        device = SimpleNamespace(serial_number='dummy',device_type='type',device_cluster_members={'dummy':'type'})
        action = parse_action({'action':'sequence','steps':[
            {'action':'lights','target':'all lights','color':'red'},
            {'action':'audio','url':'https://example.com/test.mp3'}]})
        with self.assertRaises(ValueError):
            await send_actions(SimpleNamespace(_sequence_handler=handler),device,action)
        wrapper.session_request.assert_not_awaited()
    async def test_waits_for_actual_library_http_submission(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def http_request(**kwargs):
            started.set()
            await release.wait()
        wrapper = SimpleNamespace(session_request=AsyncMock(side_effect=http_request))
        state = SimpleNamespace(login_stored_data={"test": True},
                                account_customer_id="dummy", language="en-US",
                                alexa_website_url=URL("https://alexa.amazon.com"))
        handler = AmazonSequenceHandler(wrapper, state)
        device = SimpleNamespace(serial_number="dummy", device_type="dummy-type",
                                 device_cluster_members={"dummy": "dummy-type"})
        task = asyncio.create_task(send_speech(SimpleNamespace(_sequence_handler=handler), device, "<speak>test</speak>"))
        await asyncio.wait_for(started.wait(), timeout=2)
        self.assertFalse(task.done())
        self.assertEqual(handler._buffer, [])
        self.assertEqual(handler._tasks, set())
        release.set()
        await task
        wrapper.session_request.assert_awaited_once()
        self.assertEqual(wrapper.session_request.call_args.kwargs["url"].path, "/api/behaviors/preview")

    async def test_amazon_cookie_scope(self):
        jar = AmazonCookieJar()
        jar.update_cookies({"test_cookie": "dummy-value"}, URL("https://.amazon.com"))
        self.assertIn("test_cookie", jar.filter_cookies(URL("https://alexa.amazon.com/api/welcome")))
        self.assertNotIn("test_cookie", jar.filter_cookies(URL("https://amazon.com.evil.invalid/")))
        self.assertNotIn("test_cookie", jar.filter_cookies(URL("https://example.invalid/")))
        self.assertNotIn("test_cookie", jar.filter_cookies(URL("http://alexa.amazon.com/")))

    async def test_browser_callback_origin(self):
        self.assertEqual(parse_callback("https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=test"), "test")
        for url in ("https://www.amazon.com.evil.invalid/ap/maplanding?openid.oa2.authorization_code=test",
                    "http://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=test",
                    "https://www.amazon.com/ap/maplanding",
                    "https://www.amazon.com/ap/maplanding?openid.oa2.authorization_code=a&openid.oa2.authorization_code=b"):
            with self.assertRaises(ValueError):
                parse_callback(url)

    async def test_discovery_avoids_unrelated_features(self):
        api = AsyncMock()
        api._device_handler.devices = {"test": object()}
        result = await discover_devices(api)
        self.assertEqual(result, api._device_handler.devices)
        api._device_handler.update_devices.assert_awaited_once()
        api.get_devices_data.assert_not_awaited()

    async def test_error_hint_never_prints_raw_url(self):
        sensitive = "Unable to extract authorization code from url: https://example.invalid/?token=SECRET"
        hint = auth_failure_hint(RuntimeError(sensitive))
        self.assertNotIn("SECRET", hint)
        self.assertNotIn("https://", hint)

    async def test_message_and_session_persistence(self):
        adapter = AlexaDirect()
        adapter.api = AsyncMock()
        adapter.api._sequence_handler._build_operation_node = Mock(return_value={"operationPayload": {"content": [{}], "target": {}}})
        adapter.device = SimpleNamespace(serial_number='dummy', device_type='type')
        adapter.auth = {"email": "test@example.invalid"}
        adapter.api.login.login_mode_stored_data.return_value = {"test": "session"}
        with patch("alexa_direct.save_auth") as saved:
            await adapter.announce("hello")
            adapter.api._sequence_handler._post_sequences.assert_awaited_once()
            node = adapter.api._sequence_handler._post_sequences.call_args.args[0][0]
            self.assertEqual(node.message_type, AmazonSequenceType.Announcement)
            self.assertEqual(node.message_body, "hello")
            self.assertIs(node.device, adapter.device)
            adapter.api.call_alexa_announcement.assert_not_awaited()
            saved.assert_called_once()

    async def test_failure_closes_session_without_retry(self):
        adapter = AlexaDirect()
        api = adapter.api = AsyncMock()
        adapter.session = AsyncMock()
        session = adapter.session
        adapter.device = SimpleNamespace(serial_number='dummy', device_type='type')
        api._sequence_handler._build_operation_node = Mock(return_value={"operationPayload": {"content": [{}], "target": {}}})
        api._sequence_handler._post_sequences.side_effect = RuntimeError("test")
        with self.assertRaises(RuntimeError):
            await adapter.announce("hello")
        api._sequence_handler._post_sequences.assert_awaited_once()
        session.close.assert_awaited_once()
        self.assertIsNone(adapter.api)

    async def test_audio_submits_ssml_through_same_amazon_route(self):
        wrapper = SimpleNamespace(session_request=AsyncMock())
        state = SimpleNamespace(login_stored_data={"test": True}, account_customer_id="dummy",
                                language="en-US", alexa_website_url=URL("https://alexa.amazon.com"))
        handler = AmazonSequenceHandler(wrapper, state)
        device = SimpleNamespace(serial_number="dummy", device_type="type",
                                 device_cluster_members={"dummy": "type"})
        adapter = AlexaDirect()
        adapter.api = SimpleNamespace(_sequence_handler=handler,
                                     login=SimpleNamespace(login_mode_stored_data=AsyncMock(return_value={})))
        adapter.device, adapter.auth = device, {"email": "dummy"}
        with patch("alexa_direct.save_auth"):
            await adapter.play_audio("https://example.com/a.mp3?a=1&b=2", before="Test.")
        kwargs = wrapper.session_request.call_args.kwargs
        self.assertEqual(kwargs["url"].path, "/api/behaviors/preview")
        node = json.loads(kwargs["input_data"]["sequenceJson"])["startNode"]["nodesToExecute"][0]
        self.assertEqual(node["type"], AmazonSequenceType.Speak)
        self.assertIn("<audio src=", node["operationPayload"]["textToSpeak"])
        self.assertNotIn("content", node["operationPayload"])

    async def test_text_and_mixed_sequences_use_speak_and_escape_markup(self):
        wrapper = SimpleNamespace(session_request=AsyncMock())
        state = SimpleNamespace(login_stored_data={'test': True}, account_customer_id="dummy",
                                language="en-US", alexa_website_url=URL("https://alexa.amazon.com"))
        handler = AmazonSequenceHandler(wrapper, state)
        device = SimpleNamespace(serial_number="dummy", device_type="type",
                                 device_cluster_members={"dummy": "type", "other": "type"})
        adapter = AlexaDirect()
        adapter.api = SimpleNamespace(_sequence_handler=handler,
                                     login=SimpleNamespace(login_mode_stored_data=AsyncMock(return_value={})))
        adapter.device, adapter.auth = device, {}
        text_action = parse_action({'action': 'speak', 'message': 'A & B <audio src="evil"/>'})
        with patch('alexa_direct.save_auth'):
            await adapter.execute(text_action)
            node = json.loads(wrapper.session_request.call_args.kwargs['input_data']['sequenceJson'])['startNode']['nodesToExecute'][0]
            self.assertEqual(node['type'], AmazonSequenceType.Speak)
            self.assertEqual(node['operationPayload']['textToSpeak'], '<speak>A &amp; B &lt;audio src="evil"/&gt;</speak>')
            self.assertNotIn('content', node['operationPayload'])
            self.assertEqual(node['operationPayload']['target']['devices'],
                             [{'deviceSerialNumber': 'dummy', 'deviceTypeId': 'type'}])
            await adapter.execute(Action('sequence', steps=(text_action,
                parse_action({'action': 'audio', 'url': 'https://example.com/a.mp3'}))))
        nodes = json.loads(wrapper.session_request.call_args.kwargs['input_data']['sequenceJson'])['startNode']['nodesToExecute']
        self.assertEqual(len(nodes), 2)
        self.assertTrue(all(node['type'] == AmazonSequenceType.Speak for node in nodes))
        self.assertIn('&lt;audio', nodes[0]['operationPayload']['textToSpeak'])
        self.assertIn('<audio src=', nodes[1]['operationPayload']['textToSpeak'])
        with patch('alexa_direct.save_auth'):
            await adapter.execute(parse_action({'message': 'Phone notification'}, legacy=True))
        node = json.loads(wrapper.session_request.call_args.kwargs['input_data']['sequenceJson'])['startNode']['nodesToExecute'][0]
        self.assertEqual(node['type'], AmazonSequenceType.Announcement)
        self.assertEqual(node['operationPayload']['content'][0]['speak']['value'], 'Phone notification')
        self.assertEqual(node['operationPayload']['target']['devices'],
                         [{'deviceSerialNumber': 'dummy', 'deviceTypeId': 'type'}])


if __name__ == "__main__":
    unittest.main()
