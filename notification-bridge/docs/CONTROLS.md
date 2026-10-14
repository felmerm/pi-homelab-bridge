# Local controls

`mode` stores the meme toggle locally under `/var/lib/homelab-controls/modes.json`.
Use a `homelab-controls` group shared by the bridge and scale service users. The
state directory is owned by `notification-bridge`, group `homelab-controls`,
mode 2750; its mode file is 640. The scale reloads the toggle without restarting.

`lights` uses `hue_local.py` and a linked Hue Bridge. Run `link_hue.py` with your
bridge's private LAN address, then press its physical pairing button when asked.
It pins the observed TLS certificate and stores the application credential
privately in `/etc/notification-bridge-hue/hue.json`. No credential is bundled.
Use `set_hue_target.py` to select intended light/group targets and inspect names
with `verify_hue.py` before enabling a scene. The sample special scene is off.

Adjacent speech/media steps share an Amazon sequence; local light steps execute
in order through Hue. A sequence is ordered, not atomic, and upstream acceptance
does not prove physical completion. See [action API](ACTIONS.md).

`install_controls.py` upgrades an EXISTING configured bridge/scale installation
from a staged tree, with backup/rollback. It is not the first-install script.
Review ownership, paths, and source before invoking it as root.
