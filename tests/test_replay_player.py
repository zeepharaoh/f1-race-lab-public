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


_BROWSER_STUB = r"""
const fs = require('fs');
class Element {
  constructor(tag='div') {
    this.tagName=tag.toUpperCase();this.children=[];this.parent=null;this.handlers={};this.attrs={};
    this.value='';this.hidden=false;this.disabled=false;this._text='';this.style={setProperty(){}};
    const names=new Set();this.classList={toggle(name,value){value?names.add(name):names.delete(name)},contains(name){return names.has(name)}};
  }
  set textContent(value){this._text=String(value);this.children=[]}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('')}
  append(...children){for(const child of children){if(child.parent)child.parent.children=child.parent.children.filter(item=>item!==child);this.children.push(child);child.parent=this}}
  replaceChildren(...children){this._text='';this.children=[];this.append(...children)}
  setAttribute(name,value){this.attrs[name]=value}
  addEventListener(name,callback){this.handlers[name]=callback}
  getBoundingClientRect(){return {width:600,height:570}}
  getContext(){return new Proxy({},{get:()=>()=>{}})}
}
const elements=new Map();
function node(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)}
node('replay-data').textContent=JSON.stringify(PAYLOAD);
node('speed').value='8';node('gapMode').value='interval';node('sessionSegment').value='all';
node('sessionSegment').append(new Element('option'));
node('segmentControls').hidden=true;node('thirdTimingHeading').hidden=true;node('timingNote').hidden=true;
const document={getElementById:node,createElement:tag=>new Element(tag),addEventListener(){},hidden:false};
const window={devicePixelRatio:1};const ResizeObserver=class{observe(){}};
let pendingFrame;const requestAnimationFrame=callback=>pendingFrame=callback;
function action(id,event,value){const target=node(id);if(value!==undefined)target.value=value;target.handlers[event]({target});}
function state(){return {
  rows:node('leaderboard').children.map(row=>({code:row.children[1].textContent,rank:row.children[0].textContent,
    first:row.children[2].textContent,second:row.children[3].textContent,third:row.children[4].textContent,
    thirdHidden:row.children[4].hidden,strategy:row.children[5].textContent})),
  stageHidden:node('segmentControls').hidden,stages:node('sessionSegment').children.slice(1).map(option=>({value:option.value,disabled:option.disabled})),
  start:Number(node('seek').min||0),end:Number(node('seek').max),clock:node('clock').textContent,
  title:node('boardTitle').textContent,heading:node('gapHeading').textContent,note:node('timingNote').textContent,
  coverage:node('coverage').textContent,play:node('play').textContent,
}}
"""


def browser_state(payload, actions=''):
    from pathlib import Path
    import shutil
    import subprocess

    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for browser behavior checks')
    script = (Path(__file__).resolve().parents[1] / 'replay_player.js').read_text()
    result = subprocess.run([node, '-e', 'const PAYLOAD=' + json.dumps(payload) + ';\n'
                             + _BROWSER_STUB + '\n' + script + '\n' + actions
                             + '\nprocess.stdout.write(JSON.stringify(state()));'],
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.fixture
def timing_payload():
    times = [0, 100, 200, 300, 400, 500, 600]
    return {
        'metadata': {'event': 'Test Grand Prix', 'session': 'Qualifying', 'session_code': 'Q', 'archived': True},
        'times': times, 'track': {'x': [0, 10, 20], 'y': [0, 10, 0]},
        'drivers': [{'code': code, 'name': code, 'team': code, 'color': '#123456'} for code in ['NOR', 'LEC', 'HAM']],
        'cars': {code: {'x': [0]*7, 'y': [0]*7, 'position': [pos]*7,
                        'ahead': [1.234]*7, 'behind': [2.345]*7, 'leader': [4.567]*7,
                        'stops': [2]*7, 'last_stop_lap': [14]*7, 'compound': ['SOFT']*7, 'age': [3]*7}
                 for pos, code in enumerate(['NOR', 'LEC', 'HAM'], 1)},
        'lap_timing': {'available': True, 'records': [
            {'code': 'NOR', 'lap': 1, 'end': 60, 'time': 90, 'segment': 1, 'valid': True},
            {'code': 'LEC', 'lap': 1, 'end': 80, 'time': 91, 'segment': 1, 'valid': True},
            {'code': 'NOR', 'lap': 2, 'end': 90, 'time': 80, 'segment': 1, 'valid': False},
            {'code': 'LEC', 'lap': 2, 'end': 95, 'time': 70, 'segment': 1, 'valid': None},
            {'code': 'NOR', 'lap': 3, 'end': 240, 'time': 88, 'segment': 2, 'valid': True},
            {'code': 'LEC', 'lap': 3, 'end': 260, 'time': 87, 'segment': 2, 'valid': True},
            {'code': 'NOR', 'lap': 4, 'end': 450, 'time': 86, 'segment': 3, 'valid': True},
            {'code': 'LEC', 'lap': 4, 'end': 480, 'time': 85, 'segment': 3, 'valid': True},
        ], 'segments': [{'segment': 1, 'start': 0, 'end': 150},
                        {'segment': 2, 'start': 200, 'end': 350},
                        {'segment': 3, 'start': 400, 'end': 600}],
           'note': 'Stages use recorded lap coverage.'},
    }


def test_qualifying_shows_completed_times_for_every_stage_without_future_laps(timing_payload):
    state = browser_state(timing_payload, "action('seek','input','300');")
    assert state['rows'][0] == {'code': 'LEC', 'rank': '1', 'first': '1:31.000', 'second': '1:27.000',
                                'third': 'N/A', 'thirdHidden': False, 'strategy': 'S3'}
    assert state['rows'][1]['first'] == '1:30.000'
    assert state['rows'][1]['second'] == '1:28.000'
    assert state['rows'][2]['rank'] == 'N/A'
    assert state['stageHidden'] is False
    assert state['stages'] == [{'value': str(stage), 'disabled': False} for stage in [1, 2, 3]]
    assert 'Stages use recorded lap coverage.' in state['coverage']
    assert 'Session classification is unavailable' not in state['coverage']


def test_seeking_back_removes_later_times_and_ignores_deleted_or_unknown_laps(timing_payload):
    state = browser_state(timing_payload, "action('seek','input','500');action('seek','input','100');")
    assert [(row['code'], row['rank'], row['first'], row['second'], row['third']) for row in state['rows']] == [
        ('NOR', '1', '1:30.000', 'N/A', 'N/A'), ('LEC', '2', '1:31.000', 'N/A', 'N/A'),
        ('HAM', 'N/A', 'N/A', 'N/A', 'N/A')]


def test_qualifying_stage_seeks_and_constrains_timeline_and_restart(timing_payload):
    state = browser_state(timing_payload, "action('sessionSegment','change','2');")
    assert (state['start'], state['end'], state['clock']) == (200, 350, '03:20')
    assert all(row['rank'] == 'N/A' for row in state['rows'])
    assert 'Rank uses Q2 laps.' in state['note']
    state = browser_state(timing_payload, "action('sessionSegment','change','2');action('seek','input','999');")
    assert state['clock'] == '05:50'
    assert state['rows'][0]['code'] == 'LEC'
    assert state['rows'][0]['third'] == 'N/A'
    state = browser_state(timing_payload, "action('sessionSegment','change','2');action('seek','input','999');action('restart','click');")
    assert state['clock'] == '03:20'
    state = browser_state(timing_payload, "action('sessionSegment','change','2');action('sessionSegment','change','all');")
    assert (state['start'], state['end']) == (0, 600)


def test_playback_stops_at_selected_stage_end(timing_payload):
    state = browser_state(timing_payload, "action('sessionSegment','change','1');action('seek','input','149');action('play','click');pendingFrame(0);pendingFrame(250);")
    assert state['clock'] == '02:30'
    assert state['play'] == 'Play'
    assert state['rows'][0]['second'] == 'N/A'


def test_missing_stage_boundaries_disable_selection_without_guessing(timing_payload):
    timing_payload['lap_timing']['segments'] = [{'segment': 1, 'start': 0, 'end': 150}]
    state = browser_state(timing_payload, "action('sessionSegment','change','3');action('seek','input','500');")
    assert state['stages'] == [{'value': '1', 'disabled': False}, {'value': '2', 'disabled': True}, {'value': '3', 'disabled': True}]
    assert (state['start'], state['end']) == (0, 600)
    assert 'no recorded time boundaries' in state['note']
    assert state['rows'][0]['third'] == '1:25.000'


def test_practice_shows_best_and_last_valid_lap_without_qualifying_controls(timing_payload):
    timing_payload['metadata'].update(session='Practice 3', session_code='FP3')
    timing_payload['lap_timing']['records'].append({'code': 'NOR', 'lap': 5, 'end': 290,
                                                   'time': 92.004, 'segment': None, 'valid': True})
    state = browser_state(timing_payload, "action('seek','input','300');")
    assert state['title'] == 'Practice lap times'
    assert state['heading'] == 'Best'
    assert state['stageHidden'] is True
    assert state['rows'][0]['code'] == 'LEC'
    assert state['rows'][1]['first'] == '1:28.000'
    assert state['rows'][1]['second'] == '1:32.004'
    assert all(row['thirdHidden'] for row in state['rows'])


def test_absent_lap_timing_keeps_missing_times_and_all_drivers(timing_payload):
    timing_payload.pop('lap_timing')
    state = browser_state(timing_payload, "action('seek','input','500');")
    assert len(state['rows']) == 3
    assert all(row['rank'] == 'N/A' and row['first'] == 'N/A' for row in state['rows'])
    assert all(option['disabled'] for option in state['stages'])


def test_race_gap_modes_and_pit_stop_toggle_are_preserved(timing_payload):
    timing_payload['metadata'].update(session='Race', session_code='R')
    state = browser_state(timing_payload, "action('seek','input','300');")
    assert state['rows'][0]['rank'] == '1'
    assert state['rows'][0]['first'] == 'LEADER'
    assert state['rows'][1]['first'] == '+1.234'
    assert state['rows'][1]['second'] == '+2.345'
    state = browser_state(timing_payload, "action('gapMode','change','leader');action('strategy','click');")
    assert state['rows'][1]['first'] == '+4.567'
    assert state['rows'][1]['strategy'] == '2 · L14'
    assert all(row['thirdHidden'] for row in state['rows'])
