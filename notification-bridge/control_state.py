"""Persistent local settings, shared read-only with the scale service."""
import json
import os
from pathlib import Path
import tempfile
import threading


class ModeStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def read(self):
        try:
            value = json.loads(self.path.read_text())
            if value.get('version') != 1 or type(value.get('meme')) is not bool:
                raise ValueError('Invalid mode state')
            return {'meme': value['meme']}
        except FileNotFoundError:
            return {'meme': True}
        except (OSError, ValueError, TypeError, AttributeError):
            # An unreadable/corrupt setting suppresses jokes, not scale logging.
            return {'meme': False}

    def set_meme(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Expected boolean')
        with self.lock:
            # Installer creates the setgid directory. Do not grant the scale
            # service access to private Amazon session or Hue credentials.
            fd, name = tempfile.mkstemp(prefix='.modes-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w') as output:
                    os.chmod(name, 0o640)
                    json.dump({'version': 1, 'meme': enabled}, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return {'meme': enabled}
