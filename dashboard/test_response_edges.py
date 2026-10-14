import copy
import json
from pathlib import Path
import random
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'notification-bridge'))
sys.path.insert(0, str(ROOT.parent / 'renpho-scale'))
import remote_responses
from reactions import ReactionSelector
from actions import parse_action


class ResponseEdgeTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT.parent / 'renpho-scale' / 'reactions.json').read_text())
        self.catalog['special']['enabled'] = False
        self.catalog['audio_enabled'] = True
        self.cases = json.loads((ROOT / 'response-edge-cases.json').read_text())

    def test_audio_edges_no_audio_weights_xml_and_real_scale_path_agree(self):
        for item in self.cases:
            if not item['valid']:
                continue
            for weight in (7, 99.9, 100, 193, 395.9, 396):
                with self.subTest(case=item['name'], weight=weight):
                    entry = {'sound':'bruh','before':item['before'],'after':item['after']}
                    if item.get('audio') is False:
                        entry['audio_enabled'] = False
                    catalog = copy.deepcopy(self.catalog)
                    catalog['good'] = [copy.deepcopy(entry), copy.deepcopy(entry)]
                    catalog['bad'] = [copy.deepcopy(entry), copy.deepcopy(entry)]
                    remote_responses.validate_catalog(catalog)
                    action = remote_responses.build_action({'pool':'bad','index':0,'weight':weight,'revision':'a'*64},catalog,'a'*64)
                    actual_scale = ReactionSelector(catalog, random.Random(1)).choose(weight / 2.20462262185)
                    self.assertEqual(action, actual_scale)
                    parsed = parse_action(action)
                    root = ET.fromstring(parsed.audio_ssml() if parsed.kind == 'audio' else parsed.speech_ssml())
                    self.assertEqual(root.tag, 'speak')
                    self.assertEqual(len(root.findall('audio')), 0 if item.get('audio') is False else 1)
                    self.assertEqual(len(list(root)), 0 if item.get('audio') is False else 1)
                    # Literal XML examples must not create injected break/audio elements.
                    self.assertIsNone(root.find('break'))
                    self.assertNotIn('{weight}', ''.join(root.itertext()))

    def test_invalid_backend_placeholders_controls_and_empty_speech_are_rejected(self):
        for item in self.cases:
            if item['valid']:
                continue
            with self.subTest(case=item['name']):
                catalog = copy.deepcopy(self.catalog)
                text = item['text']
                entry = {'sound':'bruh','before':text,'after':''}
                if item.get('audio') is False:
                    entry.update(audio_enabled=False, before='' if text == '{audio}' else text)
                catalog['bad'][0] = entry
                with self.assertRaises(remote_responses.ResponseError):
                    remote_responses.validate_catalog(catalog)

    def test_limits_and_boolean_flags_are_enforced(self):
        for field in ('before','after'):
            catalog = copy.deepcopy(self.catalog)
            catalog['bad'][0][field] = 'a' * 700
            remote_responses.validate_catalog(catalog)
            catalog['bad'][0][field] += 'a'
            with self.assertRaises(remote_responses.ResponseError): remote_responses.validate_catalog(catalog)
        for value in ('false', 0, None):
            catalog = copy.deepcopy(self.catalog)
            catalog['bad'][0]['audio_enabled'] = value
            with self.assertRaises(remote_responses.ResponseError): remote_responses.validate_catalog(catalog)
            with self.assertRaises(ValueError): ReactionSelector(catalog)
