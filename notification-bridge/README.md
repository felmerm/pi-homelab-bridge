# Alexa action bridge

The shared authenticated HTTPS queue and Amazon session adapter. Phone text
uses targeted announcements; scale speech/audio uses Speak. Local Hue and meme
mode share the typed action interface. See the [project README](../README.md),
[setup](../docs/SETUP.md), [action API](docs/ACTIONS.md), and
[security boundaries](../SECURITY.md).

`requirements.lock` pins the reviewed dependency versions. Amazon linking uses
the real browser sign-in page and stores only reusable session state privately
on the Pi. No credentials are supplied in this repository. The PowerShell
launchers contain example SSH values to configure before use.

MIT applies to custom code. Upstream attribution is in [credits](../CREDITS.md)
and the retained `licenses/aioamazondevices-LICENSE`.
