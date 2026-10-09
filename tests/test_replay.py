from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys

import pandas as pd
import pytest

import main as replay


def test_session_choices_keep_race_default():
    assert replay.build_parser().parse_args([]).session == 'R'
    for code in replay.SESSION_CODES:
        assert replay.build_parser().parse_args(['--session', code.lower()]).session == code
    with pytest.raises(SystemExit):
        replay.build_parser().parse_args(['--session', 'INVALID'])


def test_practice_constructor_filter_keeps_reserve_drivers():
    results = pd.DataFrame({'Abbreviation': ['AAA', 'RES', 'BBB'],
                            'TeamName': ['A', 'A', 'B']})
    assert list(replay.select_drivers(results, ['A']).Abbreviation) == ['AAA', 'RES']
    with pytest.raises(ValueError, match='scope'):
        replay.select_drivers(results, ['A'], ['BBB'])


def test_race_driver_filter_does_not_modify_cached_payload():
    data = {'frames': [{'t': 1.0, 'drivers': {'AAA': {'x': 1}, 'BBB': {'x': 2}}}],
            'driver_colors': {'AAA': 'red', 'BBB': 'blue'}}
    filtered, codes, count = replay.filter_replay_data(data, 'S', {'BBB'})
    assert codes == {'BBB'} and count == 1
    assert set(filtered['frames'][0]['drivers']) == {'BBB'}
    assert set(data['frames'][0]['drivers']) == {'AAA', 'BBB'}
    assert filtered['driver_colors'] == {'BBB': 'blue'}


@pytest.mark.parametrize('session_code,segment', [('FP1', 'best_lap'), ('Q', 'Q1'), ('SQ', 'Q2')])
def test_lap_replay_omits_drivers_without_position_telemetry(session_code, segment):
    data = {'results': [{'code': 'AAA'}, {'code': 'BBB'}, {'code': 'CCC'}],
            'telemetry': {'AAA': {segment: {'frames': [{'t': 0}]}},
                          'BBB': {segment: {'frames': []}},
                          'CCC': {segment: {'frames': [{'t': 0}, {'t': 1}]}}}}
    filtered, codes, count = replay.filter_replay_data(data, session_code, {'AAA', 'BBB'})
    assert filtered['results'] == [{'code': 'AAA'}]
    assert set(filtered['telemetry']) == codes == {'AAA'}
    assert count == 1
    assert len(data['results']) == 3


@pytest.mark.parametrize('session_code,data', [('R', {'frames': []}), ('S', {'frames': [{'drivers': {}}]}),
                                              ('FP2', {'results': [], 'telemetry': {}}),
                                              ('Q', {'results': [{'code': 'AAA'}], 'telemetry': {}})])
def test_missing_telemetry_has_clear_error(session_code, data):
    with pytest.raises(RuntimeError, match='telemetry'):
        replay.filter_replay_data(data, session_code, {'AAA'})


def test_sprint_uses_sprint_telemetry_cache():
    engine = SimpleNamespace(get_race_telemetry=Mock(return_value={'frames': [{'drivers': {'AAA': {}}}]}))
    session = object()
    replay.prepare_replay(session, 'S', pd.DataFrame({'Abbreviation': ['AAA']}), engine)
    engine.get_race_telemetry.assert_called_once_with(session, session_type='S')


@pytest.mark.parametrize('code,method', [('FP1', 'get_practice_telemetry'), ('FP2', 'get_practice_telemetry'),
                                       ('FP3', 'get_practice_telemetry'), ('Q', 'get_quali_telemetry'),
                                       ('SQ', 'get_quali_telemetry')])
def test_session_type_reaches_dedicated_engine_loader(code, method):
    segment = 'best_lap' if code.startswith('FP') else 'Q1'
    payload = {'results': [{'code': 'AAA'}], 'telemetry': {'AAA': {segment: {'frames': [{'t': 0}], 'lap_time': '0 days 00:01:30'}}}}
    engine = SimpleNamespace(**{method: Mock(return_value=payload)})
    session = SimpleNamespace(laps=pd.DataFrame())
    replay.prepare_replay(session, code, pd.DataFrame({'Abbreviation': ['AAA']}), engine)
    loader = getattr(engine, method)
    if code.startswith('FP'):
        assert loader.call_args.args[0].source is session
        assert loader.call_args.kwargs == {'session_type': code}
    else:
        loader.assert_called_once_with(session, session_type=code)


@pytest.mark.parametrize('published', [True, False])
def test_prepare_only_loads_mid_weekend_session_and_requires_completion(monkeypatch, tmp_path, published):
    engine_dir = tmp_path / 'engine'
    monkeypatch.setattr(replay, 'ROOT', tmp_path)
    monkeypatch.setattr(replay, 'ENGINE', engine_dir)
    monkeypatch.setattr(sys, 'prefix', str(engine_dir / '.venv'))
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.chdir(tmp_path)
    for asset in ('images', 'resources'):
        (tmp_path / asset).mkdir()
    session = SimpleNamespace(load=Mock(), laps=pd.DataFrame(),
                              results=pd.DataFrame({'Abbreviation': ['AAA'], 'TeamName': ['A']}))
    fastf1 = ModuleType('fastf1')
    fastf1.Cache = SimpleNamespace(enable_cache=Mock())
    fastf1.get_session = Mock(return_value=session)
    monkeypatch.setitem(sys.modules, 'fastf1', fastf1)
    weekend = ModuleType('weekend')
    weekend.resolve_session = Mock(return_value={'round': 17, 'race': 'Event', 'session': 'Practice 1'})
    weekend.valid_timed_laps = Mock(return_value=session.laps)
    weekend.load_completed_session = Mock(side_effect=None if published else RuntimeError('Session still in progress'))
    monkeypatch.setitem(sys.modules, 'weekend', weekend)
    engine = SimpleNamespace(get_practice_telemetry=Mock(return_value={
        'results': [{'code': 'AAA'}], 'telemetry': {'AAA': {'best_lap': {'frames': [{'t': 0}], 'lap_time': '0 days 00:01:30'}}}}))
    engine.get_settings = Mock(return_value=SimpleNamespace())
    src = ModuleType('src')
    src.f1_data = engine
    monkeypatch.setitem(sys.modules, 'src', src)
    if published:
        replay.main(['--round', '17', '--session', 'FP1', '--prepare-only'])
        assert engine.get_practice_telemetry.call_args.args[0].source is session
        assert engine.get_practice_telemetry.call_args.kwargs == {'session_type': 'FP1'}
    else:
        with pytest.raises(RuntimeError, match='in progress'):
            replay.main(['--round', '17', '--session', 'FP1', '--prepare-only'])
        engine.get_practice_telemetry.assert_not_called()
    fastf1.get_session.assert_called_once_with(2026, 17, 'FP1')
    weekend.load_completed_session.assert_called_once_with(session, telemetry=True, weather=True, messages=True)


def test_top_three_team_labels_match_timing_feed_names():
    results = pd.DataFrame({'Abbreviation': ['AAA', 'BBB', 'CCC'],
                            'TeamName': ['Red Bull Racing', 'Haas', 'Ferrari']})
    assert list(replay.select_drivers(results, ['Red Bull', 'Haas F1 Team']).Abbreviation) == ['AAA', 'BBB']



def test_practice_leaderboard_uses_replayed_valid_lap_and_reorders_positions():
    laps = pd.DataFrame({'Driver': ['AAA', 'AAA', 'AAA', 'BBB'],
                         'LapTime': pd.to_timedelta([80, 85, 90, 88], unit='s'),
                         'PitInTime': pd.to_timedelta([None, 1, None, None], unit='s'),
                         'PitOutTime': pd.to_timedelta([None] * 4, unit='s'),
                         'Deleted': [True, False, False, False]})
    session = SimpleNamespace(laps=laps)
    payload = {'results': [{'code': 'AAA', 'position': 1, 'time': 80, 'driver_name': 'Driver A'},
                           {'code': 'BBB', 'position': 2, 'time': 88, 'driver_name': 'Driver B'}],
               'telemetry': {'AAA': {'best_lap': {'lap_time': '0 days 00:01:30', 'frames': [{'t': 0}]}},
                             'BBB': {'best_lap': {'lap_time': '0 days 00:01:28', 'frames': [{'t': 0}]}}}}
    engine = SimpleNamespace(get_practice_telemetry=Mock(return_value=payload))
    data, codes, frames = replay.prepare_replay(session, 'FP1', pd.DataFrame({'Abbreviation': ['AAA', 'BBB']}), engine)
    actual_laps = engine.get_practice_telemetry.call_args.args[0].laps
    assert list(actual_laps.lap_seconds) == [90.0, 88.0]
    assert codes == {'AAA', 'BBB'} and frames == 2
    assert [(row['code'], row['position'], row['time']) for row in data['results']] == [('BBB', 1, 88.0), ('AAA', 2, 90.0)]
    assert data['results'][1]['full_name'] == 'Driver A'
    assert payload['results'][0]['time'] == 80
