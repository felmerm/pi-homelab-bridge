import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custom_audio as audio
import server
from remote_responses import ResponseError


def frames(count=10):
    header = ((0x7ff << 21) | (2 << 19) | (1 << 17) | (1 << 16) | (6 << 12) | (1 << 10)).to_bytes(4, 'big')
    return (header + bytes(140)) * count


def addresses(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]


class AudioTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(audio, 'REPOSITORY', 'example-owner/meme-audio')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reject_private_dns_mixed_answers_credentials_and_other_protocols(self):
        for ip in ('127.0.0.1', '192.168.1.50', '169.254.169.254', '224.0.0.1'):
            with patch.object(socket, 'getaddrinfo', return_value=addresses(ip)):
                with self.assertRaises(ResponseError): audio.checked_url('https://example.com/clip.mp3')
        with patch.object(socket, 'getaddrinfo', return_value=addresses('1.1.1.1') + addresses('10.0.0.1')):
            with self.assertRaises(ResponseError): audio.checked_url('https://example.com/clip.mp3')
        for url in ('file:///secret.mp3', 'http://example.com/c.mp3', 'https://user:pass@example.com/c.mp3', 'https://example.com:444/c.mp3', 'https://example.com/c.mp3#fragment'):
            with self.assertRaises(ResponseError): audio.checked_url(url)

    def test_redirects_are_revalidated_before_connecting(self):
        class Redirect:
            status = 302
            def getheader(self, key): return 'https://192.168.1.50/private.mp3'
        class Connection:
            def __init__(self, *args): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Redirect()
            def close(self): pass
        with patch.object(socket, 'getaddrinfo', side_effect=[addresses('1.1.1.1'), addresses('192.168.1.50')]), patch.object(audio, 'PinnedHTTPS', side_effect=Connection) as transport:
            with self.assertRaises(ResponseError): audio.download_mp3('https://example.com/c.mp3')
            self.assertEqual(transport.call_count, 1)

    def test_stream_limit_applies_without_content_length(self):
        class Response(io.BytesIO):
            status = 200
            def getheader(self, key): return None
        class Connection:
            def __init__(self, *args): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response(b'x' * 11)
            def close(self): pass
        with patch.object(socket, 'getaddrinfo', return_value=addresses('1.1.1.1')), patch.object(audio, 'PinnedHTTPS', Connection), patch.object(audio, 'MAX_BYTES', 10):
            with self.assertRaises(ResponseError): audio.download_mp3('https://example.com/c.mp3')

    def test_frame_validation_rejects_wrong_format_truncation_and_long_audio(self):
        self.assertEqual(audio.inspect_mp3(frames()), .24)
        for data in (b'', frames()[:-1], b'ID3' + frames(), frames(4000)):
            with self.assertRaises(ResponseError): audio.inspect_mp3(data)

    def test_real_conversion_and_invalid_mp3(self):
        source = Path(__file__).resolve().parent.parent / 'examples/tone.mp3'
        encoded, duration = audio.convert_mp3(source.read_bytes())
        self.assertGreater(duration, 0)
        self.assertEqual(audio.inspect_mp3(encoded), duration)
        self.assertNotIn(b'ID3', encoded[:10])
        with self.assertRaises(ResponseError): audio.convert_mp3(b'<html>not an mp3</html>')

    def test_process_is_local_deduplicated_and_does_not_store_source_urls(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(audio, 'CACHE', Path(folder)), patch.object(audio, 'convert_mp3', return_value=(frames(), .24)), patch.object(audio, 'download_mp3', return_value=b'source'), patch.object(audio, 'github_api') as publish:
            first = audio.process_clip('air_horn', url='https://example.com/c.mp3?private_query=secret')
            second = audio.process_clip('air_horn', data=b'source')
            self.assertEqual(first, second)
            self.assertEqual(len(audio.list_clips()), 1)
            self.assertNotIn('url', first)
            self.assertNotIn('secret', (Path(folder) / 'library.json').read_text())
            publish.assert_not_called()

    def test_publish_only_uploads_processed_bytes_to_fixed_repo_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(audio, 'CACHE', Path(folder)), patch.object(audio, 'convert_mp3', return_value=(frames(), .24)):
            clip = audio.process_clip('air_horn', data=b'source')
            repo = {'private': False, 'default_branch': 'main', 'permissions': {'push': True}}
            with patch.object(audio, 'github_api', side_effect=[repo, None, {'commit': {'sha': 'a' * 40}}]) as api:
                published = audio.publish_clip(clip['id'])
                self.assertIn('/' + 'a' * 40 + '/custom/', published['url'])
                self.assertEqual(api.call_args.args[0], 'repos/example-owner/meme-audio/contents/custom/' + clip['id'] + '.mp3')
                payload = api.call_args.args[1]
                import base64
                self.assertEqual(base64.b64decode(payload['content']), frames())
                self.assertNotIn('air_horn', payload['message'])
            with patch.object(audio, 'github_api') as api:
                self.assertEqual(audio.publish_clip(clip['id']), published); api.assert_not_called()

    def test_publish_recovers_after_timeout_without_overwriting_hosted_audio(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(audio, 'CACHE', Path(folder)), patch.object(audio, 'convert_mp3', return_value=(frames(), .24)):
            clip = audio.process_clip('air_horn', data=b'source')
            repo = {'private': False, 'default_branch': 'main', 'permissions': {'push': True}}
            data = frames(); sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            with patch.object(audio, 'github_api', side_effect=[repo, {'sha': sha}, {'sha': 'b' * 40}]) as api:
                self.assertIn('/' + 'b' * 40 + '/', audio.publish_clip(clip['id'])['url'])
                self.assertTrue(all(len(call.args) == 1 for call in api.call_args_list))

    def test_http_upload_origin_limits_preview_allowlist_and_explicit_publication(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        def request(path, data=b'', headers=None, method='POST'):
            conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            conn.request(method, path, data, {'Host': '127.0.0.1:8787', 'Origin': 'http://127.0.0.1:8787', 'X-Lab-Action': 'response-test', 'Content-Type': 'audio/mpeg', **(headers or {})})
            response = conn.getresponse(); result = (response.status, response.read()); conn.close(); return result
        try:
            with patch.object(audio, 'process_clip', return_value={'id': 'a' * 64}) as process:
                self.assertEqual(request('/api/audio/file', b'mp3', {'Origin': 'https://evil.example', 'X-Clip-Name': 'test'})[0], 403)
                self.assertEqual(request('/api/audio/file', b'mp3', {'X-Clip-Name': '../private'})[0], 400)
                process.assert_not_called()
                self.assertEqual(request('/api/audio/file', b'mp3', {'X-Clip-Name': 'test'})[0], 200)
                process.assert_called_once_with('test', data=b'mp3')
            with patch.object(audio, 'publish_clip') as publish:
                body = json.dumps({'id': 'a' * 64, 'publish_publicly': False}).encode()
                self.assertEqual(request('/api/audio/publish', body, {'Content-Type': 'application/json'})[0], 400)
                publish.assert_not_called()
            self.assertEqual(request('/audio/../../server.py', method='GET')[0], 404)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=2)


if __name__ == '__main__': unittest.main()
