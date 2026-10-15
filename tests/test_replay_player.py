"""Browser replay embedding checks."""
from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from replay_player import replay_html


class ReplayDocument(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.payload = None
        self.active = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = dict(attrs)
            self.scripts.append(self.active)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = None

    def handle_data(self, text):
        if self.active and self.active.get('id') == 'replay-data':
            self.payload = json.loads(text)


@pytest.fixture
def payload():
    return {'metadata': {'event': 'Test Grand Prix', 'session': 'Race', 'session_code': 'R'},
            'drivers': [{'code': 'NOR', 'team': 'McLaren'}, {'code': 'LEC', 'team': 'Ferrari'}],
            'times': [0, 1], 'track': {'x': [0, 1], 'y': [0, 1]},
            'cars': {'NOR': {'x': [None, 1]}, 'LEC': {'x': [2, None]}},
            'unavailable': []}


def test_embedded_labels_cannot_close_the_data_script(payload):
    unsafe = '</script><script src="https://example.invalid/payload.js">&\u2028\u2029'
    payload['metadata']['event'] = unsafe
    payload['drivers'][0]['team'] = unsafe
    document = ReplayDocument(replay_html(payload))
    assert document.payload['metadata']['event'] == unsafe
    assert document.payload['drivers'][0]['team'] == unsafe
    assert len(document.scripts) == 2
    assert all('src' not in script for script in document.scripts)


def test_scope_keeps_recorded_values_and_does_not_mutate_cached_payload(payload):
    original = deepcopy(payload)
    document = ReplayDocument(replay_html(payload, ['Ferrari']))
    assert document.payload['visible_codes'] == ['LEC']
    assert document.payload['cars'] == payload['cars']
    assert payload == original
    assert ReplayDocument(replay_html(payload)).payload['visible_codes'] == ['NOR', 'LEC']
    assert ReplayDocument(replay_html(payload, [])).payload['visible_codes'] == []


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_recorded_values_cannot_be_embedded_as_invalid_json(payload, invalid):
    payload['cars']['NOR']['x'][0] = invalid
    with pytest.raises(ValueError):
        replay_html(payload)
