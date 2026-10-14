"""Minimal Amazon action adapter and private, interactive account setup."""
import asyncio
import json
import logging
import os
from http.cookies import SimpleCookie
from pathlib import Path

from aiohttp import ClientSession, CookieJar, TraceConfig
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.structures import AmazonSaveDataConfig, AmazonSequenceNode, AmazonSequenceType
from yarl import URL
from actions import Action, parse_action

STATE = Path(os.environ.get("ALEXA_STATE_DIR", "/var/lib/notification-bridge"))
AUTH = STATE / "amazon.json"
# Library HTTP diagnostics can contain account data; keep them out of the journal.
logging.getLogger("aioamazondevices").disabled = True
logging.getLogger("aioamazondevices").setLevel(logging.CRITICAL)


class AmazonCookieJar(CookieJar):
    """Handle the pinned client's synthetic dot-domain cookie URLs correctly."""
    def update_cookies(self, cookies, response_url=URL()):
        hostname = response_url.host or ""
        if hostname.startswith("."):
            domain = hostname.lstrip(".")
            normalized = SimpleCookie()
            incoming = SimpleCookie()
            incoming.load(cookies)
            for name, value in incoming.items():
                normalized[name] = value.value
                normalized[name]["domain"] = domain
                normalized[name]["path"] = "/"
                normalized[name]["secure"] = True
            cookies = normalized
            response_url = response_url.with_host(domain)
        super().update_cookies(cookies, response_url)


def save_auth(data):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = AUTH.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(data, output)
    os.replace(temp, AUTH)


def configured():
    return AUTH.is_file()


def auth_failure_hint(exc):
    """Map known errors to fixed text; never display raw URLs or server data."""
    detail = str(exc)
    if detail in ("Forbidden", "Unauthorized", "Proxy Authentication Required"):
        return "Amazon rejected an HTTP request. The stage/status below identifies where; it does not by itself prove your password is wrong."
    if detail == "Authentication failed, check credentials":
        return "An Amazon endpoint returned a login page instead of account data. Check the stage shown below."
    if "MFA OTP code not found" in detail:
        return ("Amazon did not present the expected authenticator-code screen. "
                "Check your password and make authenticator-app 2FA your preferred "
                "method. Amazon may also require verification in its website.")
    if "Unable to extract authorization code" in detail:
        return ("Amazon did not complete the code-verification step. Try a fresh "
                "authenticator code and check Amazon's website for extra verification.")
    if "Unable to find form" in detail or "Unable to extract form data" in detail:
        return "Amazon returned an unexpected login page; check account access in a private browser window."
    return "Verify your Amazon password and preferred authenticator-app 2FA using a private browser window."


async def discover_devices(api):
    # Pinned 16.3.1 internals: avoid the full Home Assistant refresh, which also
    # requests music, routines, shopping lists, alarms and sensor preferences.
    await api._device_handler.update_devices()
    return api._device_handler.devices


async def send_speech(api, device, ssml):
    """Submit text and audio through Speak, awaiting actual HTTP submission."""
    handler = api._sequence_handler
    operation = handler._build_operation_node(device, AmazonSequenceType.Speak, ssml)
    await handler._post_sequences([AmazonSequenceNode(
        message_type=AmazonSequenceType.Speak, message_body=ssml,
        music_provider_id=None, device=device, operation_node=operation)])


def announcement_operation(handler, device, speech):
    operation = handler._build_operation_node(device, AmazonSequenceType.Announcement, speech)
    operation['operationPayload']['content'][0]['display'] = {'title': "Felm's bridge", 'body': speech}
    # Explicitly target this Echo, even if Amazon supplies cluster members.
    operation['operationPayload']['target']['devices'] = [
        {'deviceSerialNumber': device.serial_number, 'deviceTypeId': device.device_type}]
    return operation


async def send_announcement(api, device, speech):
    handler = api._sequence_handler
    operation = announcement_operation(handler, device, speech)
    await handler._post_sequences([AmazonSequenceNode(
        message_type=AmazonSequenceType.Announcement, message_body=speech,
        music_provider_id=None, device=device, operation_node=operation)])


async def send_actions(api, device, action):
    """One serial Amazon sequence for a small, validated action group."""
    handler = api._sequence_handler
    nodes = []
    for step in action.steps if action.kind == "sequence" else (action,):
        if step.kind == "audio":
            operations = [(AmazonSequenceType.Speak, step.audio_ssml())]
        elif step.kind == "announce":
            operations = [(AmazonSequenceType.Announcement, step.message)]
        elif step.kind == "speak":
            operations = [(AmazonSequenceType.Speak, step.speech_ssml())]
        else:
            raise ValueError("unsupported sequence step")
        for kind, body in operations:
            operation = (announcement_operation(handler, device, body)
                         if kind == AmazonSequenceType.Announcement
                         else handler._build_operation_node(device, kind, body))
            nodes.append(AmazonSequenceNode(message_type=kind, message_body=body,
                         music_provider_id=None, device=device, operation_node=operation))
    await handler._post_sequences(nodes)


def setup_trace(diagnostic):
    trace = TraceConfig()
    routes = {"/ap/signin": "sign_in", "/auth/register": "register_session",
              "/auth/token": "refresh_session", "/api/users/me": "account_lookup",
              "/api/welcome": "region_lookup", "/api/devices-v2/device": "device_list",
              "/nexus/v1/graphql": "device_details",
              "/v1/devices/@self/capabilities": "device_registration"}

    async def response_end(session, context, params):
        diagnostic["last_request"] = routes.get(params.url.path, "amazon_request")
        diagnostic["http_status"] = params.response.status
        # Never retain headers, cookies, bodies, URL strings or query parameters.

    trace.on_request_end.append(response_end)
    return trace


class AlexaDirect:
    def __init__(self):
        self.api = None
        self.session = None
        self.device = None
        self.auth = None

    async def connect(self):
        self.auth = json.loads(AUTH.read_text())
        self.session = ClientSession(cookie_jar=AmazonCookieJar())
        self.api = AmazonEchoApi(
            self.session, self.auth["email"], "",
            login_data=self.auth["login_data"],
            save_data=AmazonSaveDataConfig(path=STATE),
        )
        await self.api.login.login_mode_stored_data()
        devices = await discover_devices(self.api)
        self.device = next((d for d in devices.values()
                            if d.serial_number == self.auth["device_serial"]), None)
        if self.device is None or not self.device.online:
            raise RuntimeError("selected Echo unavailable")

    async def close(self):
        if self.session:
            await self.session.close()
        self.api = self.session = self.device = None

    async def announce(self, speech):
        # Existing callers can send the combined app/title/message (up to 1,800).
        if not isinstance(speech, str) or not speech.strip() or len(speech) > 1800:
            raise ValueError("invalid announcement")
        await self.execute(Action("announce", message=speech))

    async def play_audio(self, url, before="", after=""):
        await self.execute(parse_action({"action": "audio", "url": url, "before": before, "after": after}))

    async def execute(self, action: Action):
        if action.kind not in ("announce", "speak", "audio", "sequence"):
            raise ValueError("unsupported action")
        try:
            if self.api is None:
                await self.connect()
            if action.kind == "announce":
                send = send_announcement(self.api, self.device, action.message)
            elif action.kind == "speak":
                send = send_speech(self.api, self.device, action.speech_ssml())
            elif action.kind == "audio":
                send = send_speech(self.api, self.device, action.audio_ssml())
            else:
                send = send_actions(self.api, self.device, action)
            await asyncio.wait_for(send, timeout=25)
            self.auth["login_data"] = await self.api.login.login_mode_stored_data()
            save_auth(self.auth)
        except Exception:
            await self.close()
            raise
