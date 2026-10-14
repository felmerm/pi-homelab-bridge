"""Threshold, selection, and bridge-contract regression tests."""
import json
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'notification-bridge'))
from actions import parse_action
from reactions import ReactionSelector, meme_enabled


def kg(lb):
    return lb / 2.20462262185


class ReactionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(Path(__file__).with_name('reactions.json').read_text())
        self.catalog['audio_enabled'] = True
        self.selector = ReactionSelector(self.catalog, random.Random(1))

    def test_thresholds_and_special_priority(self):
        for pounds, pool in ((99.9, 'good'), (100, 'bad'), (100.1, 'bad'), (230, 'bad'), (230.1, 'bad'), (300, 'bad')):
            action = self.selector.choose(kg(pounds))
            self.assertEqual(action['action'], 'audio')
            self.assertIn(action['url'], [self.catalog['sounds'][e['sound']] for e in self.catalog[pool]])
            self.assertIn(f'{pounds:g}', re.findall(r'\d+(?:\.\d+)?', action['before'] + action['after']))
        self.catalog['special']['enabled'] = True
        special = self.selector.choose(kg(230.1))
        self.assertEqual(special['steps'][0], {'action':'lights','target':'all lights','color':'red'})
        self.assertEqual(special['steps'][1]['url'], self.catalog['sounds']['damn_son'])
        self.assertNotIn('before', special['steps'][1])
        parse_action(special)

    def test_random_pool_has_no_immediate_repeat_and_all_contracts_valid(self):
        for pounds in (90, 190):
            previous = None
            seen = set()
            for _ in range(300):
                body = self.selector.choose(kg(pounds))
                parse_action(body)
                signature = json.dumps(body,sort_keys=True)
                self.assertNotEqual(signature,previous)
                seen.add(signature)
                previous=signature
            self.assertEqual(len(seen),len(self.catalog['good' if pounds<100 else 'bad']))

    def test_invalid_measurements_do_not_create_reaction(self):
        for value in (float('nan'), float('inf'), -1, 200, '80', None):
            with self.assertRaises(ValueError):self.selector.choose(value)

    def test_editable_cutoff_changes_pool_and_rejects_invalid_thresholds(self):
        self.catalog['roast_threshold_lb']=200
        selector=ReactionSelector(self.catalog,random.Random(1))
        for pounds,pool in ((199.9,'good'),(200,'bad')):
            body=selector.choose(kg(pounds))
            self.assertIn(body['url'],[self.catalog['sounds'][entry['sound']] for entry in self.catalog[pool]])
        for value in (True,float('nan'),0,500,'100'):
            self.catalog['roast_threshold_lb']=value
            with self.assertRaises(ValueError):ReactionSelector(self.catalog)

    def test_disabled_audio_preserves_reaction_speech_without_media_or_lights(self):
        self.catalog['audio_enabled'] = False
        self.catalog['special']['enabled'] = False
        for pounds in (160, 190, 300):
            body = self.selector.choose(kg(pounds))
            self.assertEqual(body['action'], 'speak')
            self.assertIn(f'{pounds:g}', re.findall(r'\d+(?:\.\d+)?', body['message']))
            self.assertNotIn('url', body)
            self.assertNotIn('steps', body)
            parse_action(body)

    def test_meme_off_returns_weight_only_even_if_special_is_enabled(self):
        self.catalog['special']['enabled']=True
        for pounds in (160,190,300):
            self.assertEqual(self.selector.choose(kg(pounds),meme_mode=False),
                {'action':'speak','message':f'{pounds} pounds.'})

    def test_mode_file_is_reloaded_without_service_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'modes.json'
            self.assertTrue(meme_enabled(path))
            for value in (False,True,False):
                path.write_text(json.dumps({'version':1,'meme':value}))
                self.assertIs(meme_enabled(path),value)
            path.write_text('broken')
            self.assertFalse(meme_enabled(path))


if __name__=='__main__':
    unittest.main()
