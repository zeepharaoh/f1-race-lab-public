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
    assert filtered['full_field_frames'] is data['frames']


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


class ReplayTestLaps(pd.DataFrame):
    @property
    def _constructor(self):
        return ReplayTestLaps

    def pick_fastest(self):
        return SimpleNamespace(get_telemetry=lambda: pd.DataFrame({
            'X': [0.0, 10.0], 'Y': [0.0, 20.0], 'Distance': [0.0, 100.0]}))


@pytest.fixture
def race_render_context(monkeypatch):
    from contextlib import nullcontext
    import subprocess
    import championship_data
    import championship_overlays
    import replay_overlays
    laps = ReplayTestLaps({'Driver': ['AAA', 'BBB', 'AAA'], 'LapNumber': [1, 1, 2],
                           'Sector1Time': pd.to_timedelta([30, 31, 29], unit='s')})
    results = pd.DataFrame({'Abbreviation': ['AAA', 'BBB'], 'DriverNumber': ['1', '2'],
                            'TeamName': ['A', 'B']})
    session = SimpleNamespace(laps=laps, results=results, drivers=['1', '2'],
                              event={'EventName': 'Event', 'Country': 'Country', 'Location': 'Circuit'},
                              date=pd.Timestamp('2026-10-04T12:00:00'))
    selected = results.loc[results.Abbreviation.eq('AAA')].copy()
    data = {'frames': [{'t': 0.0, 'drivers': {'AAA': {'x': 0.0, 'y': 0.0}}}],
            'track_statuses': [], 'driver_colors': {'AAA': (255, 0, 0)}, 'total_laps': 2,
            'race_control_messages': []}
    child = Mock()
    child.poll.return_value = None
    lifecycle = []
    start_child = Mock(side_effect=lambda *args, **kwargs: (lifecycle.append('child'), child)[1])
    monkeypatch.setattr(subprocess, 'Popen', start_child)
    stream = Mock()
    stream.server_socket.getsockname.return_value = ('127.0.0.1', 49152)
    start_stream = Mock(side_effect=lambda: lifecycle.append('listen'))
    stream.start = start_stream
    stream_factory = Mock(return_value=stream)
    interface = ModuleType('src.interfaces.race_replay')
    interface.TelemetryStreamServer = stream_factory
    render_control = {'error': None}

    def native_constructor(**kwargs):
        try:
            interface.TelemetryStreamServer().start()
        except OSError:
            pass
        if render_control['error'] is not None:
            raise render_control['error']

    run_replay = Mock(side_effect=native_constructor)
    src = ModuleType('src')
    src.__path__ = []
    interfaces = ModuleType('src.interfaces')
    interfaces.__path__ = []
    interfaces.race_replay = interface
    runner = ModuleType('src.run_session')
    runner.run_arcade_replay = run_replay
    monkeypatch.setitem(sys.modules, 'src', src)
    monkeypatch.setitem(sys.modules, 'src.run_session', runner)
    monkeypatch.setitem(sys.modules, 'src.interfaces', interfaces)
    monkeypatch.setitem(sys.modules, 'src.interfaces.race_replay', interface)
    engine = SimpleNamespace(get_circuit_rotation=Mock(return_value=0.0))
    snapshot = {'drivers': {'BBB': 30.0, 'AAA': 20.0}, 'constructors': {'B': 30.0, 'A': 20.0},
                'label': 'Before Sprint · through round 15', 'warning': ''}
    load_championship = Mock(return_value=snapshot)
    championship_hook = Mock(side_effect=lambda *args: nullcontext())
    gap_hook = Mock(side_effect=lambda *args: nullcontext())
    monkeypatch.setattr(championship_data, 'load_championship_snapshot', load_championship)
    monkeypatch.setattr(championship_overlays, 'install_championship_tables', championship_hook)
    monkeypatch.setattr(replay_overlays, 'install_replay_overlays', gap_hook)
    return SimpleNamespace(session=session, selected=selected, data=data, child=child,
                           start_child=start_child, run_replay=run_replay, engine=engine,
                           stream=stream, start_stream=start_stream, stream_factory=stream_factory,
                           interface=interface, render_control=render_control, lifecycle=lifecycle,
                           load_championship=load_championship, championship_hook=championship_hook,
                           gap_hook=gap_hook, snapshot=snapshot)


@pytest.mark.parametrize('session_code', ['R', 'S'])
@pytest.mark.parametrize('engine_error', [False, True])
def test_race_insights_publish_selected_laps_and_clean_up_on_close(race_render_context, session_code, engine_error):
    context = race_render_context
    original_laps = context.session.laps.copy()
    original_results = context.session.results.copy()
    original_drivers = list(context.session.drivers)
    if engine_error:
        context.render_control['error'] = RuntimeError('Renderer failed')
        with pytest.raises(RuntimeError, match='Renderer failed'):
            replay.render_race(context.session, context.selected, context.data, 'Title', 4.0, 16,
                               session_code, context.engine)
    else:
        replay.render_race(context.session, context.selected, context.data, 'Title', 4.0, 16,
                           session_code, context.engine)
    arguments = context.run_replay.call_args.kwargs
    assert arguments['enable_telemetry'] is True
    assert arguments['drivers'] == ['1']
    view = arguments['session']
    assert view.source is context.session
    assert list(view.laps.Driver) == ['AAA', 'AAA']
    assert list(view.laps.LapNumber) == [1, 2]
    assert list(view.results.Abbreviation) == ['AAA']
    assert view.drivers == ['1']
    assert arguments['session_info']['session'] == session_code
    context.load_championship.assert_called_once_with(context.session, 16, session_code)
    assert arguments['current_driver_standings'] == {'BBB': 30.0, 'AAA': 20.0}
    assert arguments['current_constructors_standings'] == {'B': 30.0, 'A': 20.0}
    context.championship_hook.assert_called_once_with(context.interface, context.snapshot)
    context.gap_hook.assert_called_once_with(context.interface, context.data['frames'])
    pd.testing.assert_frame_equal(context.session.laps, original_laps)
    pd.testing.assert_frame_equal(context.session.results, original_results)
    assert context.session.drivers == original_drivers
    command = context.start_child.call_args.args[0]
    assert command == [sys.executable, str(replay.ROOT / 'replay_insights.py'), '--port', '49152']
    assert context.start_child.call_args.kwargs['cwd'] == replay.ENGINE
    assert context.lifecycle[:2] == ['listen', 'child']
    context.stream_factory.assert_called_once_with(port=0)
    context.start_stream.assert_called_once()
    context.stream.stop.assert_called_once()
    assert context.interface.TelemetryStreamServer is context.stream_factory
    context.child.terminate.assert_called_once()
    context.child.wait.assert_called_once()


def test_insights_start_failure_keeps_native_replay_available(race_render_context, capsys):
    context = race_render_context
    context.start_child.side_effect = OSError('Cannot open insights')
    replay.render_race(context.session, context.selected, context.data, 'Title', 4.0, 16, 'R', context.engine)
    context.run_replay.assert_called_once()
    assert context.run_replay.call_args.kwargs['enable_telemetry'] is True
    assert 'insights' in capsys.readouterr().out.lower()
    context.child.terminate.assert_not_called()


def test_insights_closed_early_is_not_terminated_again(race_render_context):
    context = race_render_context
    context.child.poll.return_value = 0
    replay.render_race(context.session, context.selected, context.data, 'Title', 4.0, 16, 'S', context.engine)
    context.run_replay.assert_called_once()
    context.child.terminate.assert_not_called()


def test_insights_process_that_does_not_close_is_killed(race_render_context):
    import subprocess
    context = race_render_context
    context.child.wait.side_effect = [subprocess.TimeoutExpired('insights', 5), None]
    replay.render_race(context.session, context.selected, context.data, 'Title', 4.0, 16, 'R', context.engine)
    context.child.terminate.assert_called_once()
    context.child.kill.assert_called_once()
    assert context.child.wait.call_count == 2



def test_telemetry_listen_failure_does_not_launch_insights(race_render_context):
    context = race_render_context
    context.start_stream.side_effect = OSError('Cannot listen')
    replay.render_race(context.session, context.selected, context.data, 'Title', 4.0, 16, 'R', context.engine)
    context.run_replay.assert_called_once()
    context.start_child.assert_not_called()
    context.stream.stop.assert_called_once()
    assert context.interface.TelemetryStreamServer is context.stream_factory
