"""Local PC configuration. No account credentials belong in this module."""
import os
from pathlib import Path
import re

PI_HOST = os.environ.get('HOMELAB_PI_HOST', 'raspberrypi.local')
PI_USER = os.environ.get('HOMELAB_PI_USER', 'pi')
PI_HOST_ALIAS = os.environ.get('HOMELAB_PI_HOST_ALIAS', PI_HOST)
SSH_KEY = Path(os.environ.get('HOMELAB_SSH_KEY', str(Path.home() / '.ssh' / 'id_ed25519'))).expanduser()
NAS_HOST = os.environ.get('HOMELAB_NAS_HOST', 'nas.local')
AUDIO_REPOSITORY = os.environ.get('HOMELAB_AUDIO_REPOSITORY', '')
for value in (PI_HOST, PI_HOST_ALIAS, PI_USER, NAS_HOST):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', value) or value.startswith('-'):
        raise ValueError('Invalid local host/user configuration')
if AUDIO_REPOSITORY and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', AUDIO_REPOSITORY):
    raise ValueError('Use owner/repository for the optional public audio repo')
SSH_TARGET = PI_USER + '@' + PI_HOST
