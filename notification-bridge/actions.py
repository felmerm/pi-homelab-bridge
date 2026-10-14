"""Validated bridge actions, shared by HTTP, SSH, and the Alexa adapter.

Add future capabilities here and in AlexaDirect.execute; transports never
interpret shell commands or accept arbitrary Amazon operation payloads.
"""
from dataclasses import dataclass
import ipaddress
import json
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr


def text(value, limit):
    if not isinstance(value, str):
        raise ValueError("expected text")
    return " ".join(value.split())[:limit]


def xml_text(value):
    if any(not (c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF
                or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
           for c in value):
        raise ValueError("invalid XML character")
    return value


def audio_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("expected HTTPS audio URL")
    if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("invalid audio URL")
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    if (url.scheme != "https" or not host or url.port not in (None, 443)
            or url.username is not None or url.password is not None or url.fragment):
        raise ValueError("expected HTTPS audio URL on port 443")
    # Amazon needs an internet-hosted file. Never use this to address LAN devices.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("use an internet hostname")
    if ("." not in host or host.endswith((".local", ".localhost", ".internal", ".lan"))
            or not all(c.isascii() and (c.isalnum() or c in "-.") for c in host)):
        raise ValueError("use an internet hostname")
    return xml_text(value)


@dataclass(frozen=True, repr=False)
class Action:
    kind: str
    message: str = ""
    url: str = ""
    before: str = ""
    after: str = ""
    target: str = ""
    color: str = ""
    steps: tuple = ()
    name: str = ""
    enabled: bool | None = None

    def fingerprint(self):
        from dataclasses import asdict
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)

    def audio_ssml(self):
        return ("<speak>" + escape(self.before) + "<audio src="
                + quoteattr(self.url) + "/>" + escape(self.after) + "</speak>")

    def speech_ssml(self):
        return "<speak>" + escape(xml_text(self.message)) + "</speak>"


def parse_action(body, legacy=False, *, nested=False):
    if not isinstance(body, dict) or type(body.get("dry_run", False)) is not bool:
        raise ValueError("invalid action")
    kind = "announce" if legacy else body.get("action")
    if kind in ("announce", "speak"):
        allowed = {"action", "app", "title", "message", "dry_run"}
        message = ". ".join(part for part in (
            text(body.get(key, ""), limit)
            for key, limit in (("app", 100), ("title", 200), ("message", 1500))) if part)
        if not message:
            raise ValueError("empty speech")
        if kind == 'speak':
            xml_text(message)
        action = Action(kind, message=message)
    elif kind == "audio":
        allowed = {"action", "url", "before", "after", "dry_run"}
        before, after = (xml_text(text(body.get(key, ""), 1500)) for key in ("before", "after"))
        if len(before) + len(after) > 1500:
            raise ValueError("audio speech too long")
        action = Action("audio", url=audio_url(body.get("url")), before=before, after=after)
    elif kind == "mode" and not nested:
        allowed = {"action", "name", "enabled", "dry_run"}
        if body.get('name') != 'meme' or type(body.get('enabled')) is not bool:
            raise ValueError('invalid mode setting')
        action = Action('mode', name='meme', enabled=body['enabled'])
    elif kind == "lights":
        allowed = {"action", "target", "color", "dry_run"}
        target = body.get("target", "")
        color = body.get("color", "")
        if (not isinstance(target, str) or not 1 <= len(target) <= 80
                or not all(c.isalnum() or c in " -'" for c in target)
                or color not in ("red", "green", "blue", "purple", "yellow", "white")):
            raise ValueError("invalid lights target/color")
        action = Action("lights", target=target, color=color)
    elif kind == "sequence" and not nested:
        allowed = {"action", "steps", "dry_run"}
        steps = body.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 5:
            raise ValueError("invalid sequence")
        if any(not isinstance(step, dict) or "dry_run" in step for step in steps):
            raise ValueError("invalid sequence step")
        action = Action("sequence", steps=tuple(parse_action(step, nested=True) for step in steps))
    else:
        raise ValueError("unknown action")
    # Legacy clients historically permitted additional metadata.
    if not legacy and set(body) - allowed:
        raise ValueError("unknown field")
    return action
