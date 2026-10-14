"""Browser-based Amazon mobile authorization. Never asks for a password or OTP."""
import argparse
import asyncio
import getpass
import json
import os
import time

from aiohttp import ClientSession
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.structures import AmazonSaveDataConfig
from yarl import URL

from alexa_direct import STATE, AmazonCookieJar, discover_devices, save_auth, setup_trace

PENDING = STATE / "browser-pending.json"


def parse_callback(value):
    if len(value) > 8192:
        raise ValueError("callback too long")
    url = URL(value.strip())
    if (url.scheme != "https" or url.host != "www.amazon.com"
            or url.path != "/ap/maplanding" or url.port != 443 or url.user):
        raise ValueError("unexpected callback origin")
    codes = url.query.getall("openid.oa2.authorization_code", [])
    if len(codes) != 1 or not codes[0]:
        raise ValueError("authorization code missing")
    return codes[0]


async def prepare():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    async with ClientSession() as session:
        api = AmazonEchoApi(session, "", "", save_data=AmazonSaveDataConfig(path=STATE))
        login = api.login
        verifier = login._create_code_verifier()
        url = login._build_oauth_url(verifier, login._build_client_id())
        if url.scheme != "https" or url.host != "www.amazon.com" or url.path != "/ap/signin":
            raise ValueError("unexpected login origin")
        pending = {"created": time.time(), "verifier": verifier.decode(),
                   "serial": login._serial, "cookies": login._http_wrapper.cookies}
        fd = os.open(PENDING, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(pending, output)
        print(str(url))


async def finish():
    print("Sign in only in the Amazon browser window. This terminal does not need your password or 2FA.")
    print("After sign-in, copy the final address starting https://www.amazon.com/ap/maplanding")
    print("It may show a blank/error page. The address must contain openid.oa2.authorization_code.")
    print("Paste that address here, not into chat; it will be hidden. Right-click may paste in this terminal.")
    code = parse_callback(getpass.getpass("Final Amazon address: "))
    pending = json.loads(PENDING.read_text())
    if not 0 <= time.time() - pending["created"] <= 900:
        raise ValueError("browser setup expired")
    PENDING.unlink()
    diagnostic = {"stage": "exchange_browser_code"}
    async with ClientSession(cookie_jar=AmazonCookieJar(), trace_configs=[setup_trace(diagnostic)]) as session:
        api = AmazonEchoApi(session, "", "", save_data=AmazonSaveDataConfig(path=STATE))
        login = api.login
        login._serial = pending["serial"]
        login._http_wrapper._cookies = pending["cookies"]
        try:
            login_data = await login._register_device({
                "authorization_code": code, "code_verifier": pending["verifier"].encode(),
                "domain": "com"})
            login._session_state_data.login_stored_data = login_data
            diagnostic["stage"] = "initialize_amazon_session"
            await login._domain_refresh_auth_cookies()
            await login._register_device_capabilities()
            await login.obtain_account_customer_id()
            login_data["site"] = login._session_state_data.retail_site_url.human_repr().rstrip("/")
            diagnostic["stage"] = "find_echo_devices"
            devices = await discover_devices(api)
            choices = [d for d in devices.values() if d.online and d.notifications_supported]
            if not choices:
                raise RuntimeError("no online Echo found")
            for index, device in enumerate(choices, 1):
                print(f"{index}: {device.account_name}")
            selected = int(input("Echo number to announce on: ")) - 1
            if not 0 <= selected < len(choices):
                raise ValueError("invalid selection")
            save_auth({"email": "", "login_data": login_data,
                       "device_serial": choices[selected].serial_number})
            print("Linked. No announcement sent.")
        except Exception as exc:
            print("Browser linking did not finish:", type(exc).__name__)
            print("Stage:", diagnostic["stage"], "| Request:",
                  diagnostic.get("last_request", "none"), "| HTTP:",
                  diagnostic.get("http_status", "unknown"))
            raise SystemExit(1) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "finish"])
    args = parser.parse_args()
    try:
        asyncio.run(prepare() if args.mode == "prepare" else finish())
    except Exception as exc:
        print("Browser setup stopped:", type(exc).__name__, "— no secret details printed.")
        raise SystemExit(1) from None
