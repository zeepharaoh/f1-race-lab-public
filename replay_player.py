"""Embed the browser replay without remote scripts."""
import json
from pathlib import Path


def replay_html(payload, teams=None):
    root = Path(__file__).resolve().parent
    view = dict(payload)
    view['visible_codes'] = [driver['code'] for driver in payload['drivers']
                             if teams is None or driver['team'] in teams]
    serialized = json.dumps(view, allow_nan=False, separators=(',', ':'))
    for character, escaped in [('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                               ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        serialized = serialized.replace(character, escaped)
    template = (root / 'replay_player.html').read_text()
    script = (root / 'replay_player.js').read_text()
    return template.replace('__REPLAY_SCRIPT__', script).replace('__REPLAY_DATA__', serialized)
