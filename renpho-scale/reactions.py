"""Local, editable scale reaction selection. No AI call on the weigh-in path."""
from functools import lru_cache
import json
import math
from pathlib import Path
import random
import threading


class ReactionSelector:
    def __init__(self, catalog, rng=None):
        self.catalog = catalog
        self.rng = rng or random.SystemRandom()
        self.last = {}
        self.lock = threading.Lock()
        if catalog.get('version') != 1:
            raise ValueError('Unknown reaction catalog version')
        self.roast_threshold_lb = catalog.get('roast_threshold_lb', 100)
        if (type(self.roast_threshold_lb) not in (int, float)
                or not math.isfinite(self.roast_threshold_lb) or not 7 <= self.roast_threshold_lb <= 396):
            raise ValueError('Invalid roast threshold')
        for pool in ('good', 'bad'):
            entries = catalog.get(pool)
            if not isinstance(entries, list) or len(entries) < 2:
                raise ValueError('Reaction pool needs multiple choices')
            for entry in entries:
                if 'audio_enabled' in entry and type(entry['audio_enabled']) is not bool:
                    raise ValueError('Invalid per-card audio setting')
                if entry.get('sound') not in catalog['sounds']:
                    raise ValueError('Unknown reaction sound')
                for key in ('before', 'after'):
                    value = entry.get(key, '')
                    if not isinstance(value, str) or len(value) > 700:
                        raise ValueError('Invalid reaction speech')
                    value.format(weight='170')
                if (not catalog.get('audio_enabled', False) or entry.get('audio_enabled') is False) and not ' '.join(entry.get(key, '') for key in ('before', 'after')).strip():
                    raise ValueError('Speech-only reaction needs text')
        for url in catalog['sounds'].values():
            if not isinstance(url, str) or not url.startswith('https://'):
                raise ValueError('Reaction audio requires HTTPS')
        if catalog['special']['sound'] not in catalog['sounds']:
            raise ValueError('Unknown special sound')

    def choose(self, kg, *, meme_mode=True):
        if not isinstance(kg, (int, float)) or not math.isfinite(kg) or not 3 <= kg <= 180:
            raise ValueError('Invalid scale weight')
        pounds = round(kg * 2.20462262185, 1)
        weight = f'{pounds:g}'
        if not meme_mode:
            return {'action': 'speak', 'message': f'{weight} pounds.'}
        if pounds > 230 and self.catalog['special'].get('enabled', False):
            special = self.catalog['special']
            audio = {'action': 'audio', 'url': self.catalog['sounds'][special['sound']]}
            return {'action': 'sequence', 'steps': [
                {'action': 'lights', 'target': special['lights_target'], 'color': 'red'}, audio]}
        pool = 'good' if pounds < self.roast_threshold_lb else 'bad'
        with self.lock:
            choices = [i for i in range(len(self.catalog[pool])) if i != self.last.get(pool)]
            index = self.rng.choice(choices)
            self.last[pool] = index
            entry = self.catalog[pool][index]
        before = entry.get('before', '').format(weight=weight)
        after = entry.get('after', '').format(weight=weight)
        if not self.catalog.get('audio_enabled', False) or entry.get('audio_enabled') is False:
            return {'action': 'speak', 'message': ' '.join(p for p in (before, after) if p)}
        return {'action': 'audio', 'url': self.catalog['sounds'][entry['sound']],
                'before': before, 'after': after}


@lru_cache(maxsize=4)
def selector(path):
    return ReactionSelector(json.loads(Path(path).read_text(encoding='utf-8')))


def meme_enabled(path='/var/lib/homelab-controls/modes.json'):
    try:
        state = json.loads(Path(path).read_text())
        return state.get('version') == 1 and state.get('meme') is True
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False
