"""Small LAN action receiver with a shared queue and direct Amazon adapter."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import queue
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from alexa_direct import AlexaDirect, configured
from actions import parse_action
from control_state import ModeStore
from dispatch import execute_action, needs_alexa
from hue_local import HueLocal

TOKEN = os.environ.get("BRIDGE_TOKEN", "")
PENDING = queue.Queue(maxsize=20)
LOCK = threading.Lock()
RECENT = {}
MODES = ModeStore(os.environ.get('BRIDGE_MODES_PATH', '/var/lib/homelab-controls/modes.json'))
COUNTS = {"received": 0, "announced": 0, "speak_submitted": 0, "audio_submitted": 0, "lights_submitted": 0,
          "sequence_submitted": 0, "failed": 0, "tested": 0}
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    alexa = AlexaDirect()
    hue = HueLocal()
    while True:
        action = PENDING.get()
        try:
            loop.run_until_complete(asyncio.wait_for(execute_action(action, alexa, hue), timeout=45))
            with LOCK:
                COUNTS["announced" if action.kind == "announce" else action.kind + "_submitted"] += 1
            logging.info("Action backend accepted %s request", action.kind)
        except Exception as exc:
            loop.run_until_complete(alexa.close())
            with LOCK:
                COUNTS["failed"] += 1
            # Do not log text, credentials, upstream response bodies, or URLs.
            logging.warning("Action %s failed (%s)", action.kind, type(exc).__name__)
        finally:
            PENDING.task_done()
        time.sleep(2)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(),
                                   ("Bearer " + TOKEN).encode())

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if self.path == "/modes":
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            return self.reply(200, MODES.read())
        if self.path == "/status":
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            with LOCK:
                status = dict(COUNTS)
            status.update(alexa_configured=configured(), backend="amazon_direct", queued=PENDING.qsize(),
                          actions=["announce", "speak", "audio", "lights", "sequence", "mode"],
                          audio_backend="speak_ssml", lights_backend="hue_local", modes=MODES.read(),
                          hue_configured=HueLocal().configured())
            return self.reply(200, status)
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path not in ("/notify", "/actions"):
            return self.reply(404, {"error": "not_found"})
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                return self.reply(413, {"error": "body_size"})
            body = json.loads(self.rfile.read(size))
            action = parse_action(body, legacy=self.path == "/notify")
        except (ValueError, UnicodeError, TimeoutError):
            return self.reply(400, {"error": "invalid_json_or_fields"})
        with LOCK:
            COUNTS["received"] += 1
        if body.get("dry_run", False):
            with LOCK:
                COUNTS["tested"] += 1
            return self.reply(200, {"ok": True, "dry_run": True})
        if action.kind == 'mode':
            try:
                result = MODES.set_meme(action.enabled)
            except OSError:
                return self.reply(503, {"error": "mode_state_unavailable"})
            return self.reply(200, {"ok": True, "modes": result})
        if needs_alexa(action) and not configured():
            return self.reply(503, {"error": "alexa_not_configured"})
        if (action.kind == 'lights' or any(s.kind == 'lights' for s in action.steps)) and not HueLocal().configured():
            return self.reply(503, {"error": "hue_not_linked"})
        digest = hashlib.sha256(action.fingerprint().encode()).hexdigest()
        with LOCK:
            now = time.monotonic()
            for key in list(RECENT):
                if now - RECENT[key] > 10:
                    del RECENT[key]
            if digest in RECENT:
                return self.reply(200, {"ok": True, "duplicate": True})
            try:
                PENDING.put_nowait(action)
            except queue.Full:
                return self.reply(429, {"error": "queue_full"})
            RECENT[digest] = now
        self.reply(202, {"ok": True, "queued": True})


class LimitedHTTPServer(ThreadingHTTPServer):
    """Bound simultaneous connections so slow clients cannot exhaust Pi RAM."""
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


if __name__ == "__main__":
    if len(TOKEN) < 32:
        raise SystemExit("Set BRIDGE_TOKEN to a random token of at least 32 characters")
    threading.Thread(target=worker, daemon=True).start()
    server = LimitedHTTPServer((os.environ.get("BRIDGE_HOST", "0.0.0.0"),
                                 int(os.environ.get("BRIDGE_PORT", "8765"))), Handler)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ.get("BRIDGE_TLS_CERT", "/etc/notification-bridge-tls/server.crt"),
                        os.environ.get("BRIDGE_TLS_KEY", "/etc/notification-bridge-tls/server.key"))
    # Perform TLS handshake in the request thread, subject to its timeout.
    server.socket = tls.wrap_socket(server.socket, server_side=True,
                                   do_handshake_on_connect=False)
    logging.info("Notification receiver listening on port %s", server.server_port)
    server.serve_forever()
