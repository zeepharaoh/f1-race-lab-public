"""Weekend dashboard checks."""
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import data
import weekend


@pytest.fixture
def dashboard(monkeypatch, tmp_path):
    st.cache_data.clear()
    project_file = Path(data.ROOT) / 'dashboard.py'
    monkeypatch.setattr(data, 'ROOT', tmp_path)
    codes = ['NOR', 'PIA', 'LEC', 'HAM', 'RUS', 'ANT']
    teams = ['McLaren', 'McLaren', 'Ferrari', 'Ferrari', 'Mercedes', 'Mercedes']
    points = [25., 18., 15., 12., 10., 8.]
    gp = pd.DataFrame([dict(round=1, race='Test Grand Prix', date='2026-01-01',
        kind='Grand Prix', team=team, team_id=team.lower(), driver_id=code.lower(),
        code=code, driver=code, position=index + 1, points=point, grid=index + 1,
        status='Finished', Q1=None, Q2=None, Q3=None)
        for index, (code, team, point) in enumerate(zip(codes, teams, points))])
    constructors = gp.groupby(['team', 'team_id'], as_index=False).points.sum().sort_values('points', ascending=False)
    constructors['rank'] = range(1, 4)
    constructors['wins'] = [1, 0, 0]
    drivers = gp[['driver_id', 'driver', 'code', 'team', 'points']].copy()
    drivers['rank'] = range(1, 7)
    drivers['wins'] = [1, 0, 0, 0, 0, 0]
    checks = pd.DataFrame({'team': constructors.team, 'official_points': constructors.points,
        'results_points': constructors.points, 'difference': 0.})
    driver_checks = pd.DataFrame({'driver': drivers.driver, 'official_points': drivers.points,
        'results_points': drivers.points, 'difference': 0.})
    season = dict(refresh_error=None, gp=gp, results=gp, qualifying=gp,
        teams=constructors, drivers=drivers, year=2026, round=1,
        calendar=pd.DataFrame({'round': [1, 2, 3], 'race': ['Test Grand Prix', 'Sprint Weekend', 'Future Weekend'],
            'date': ['2026-01-01', '2026-01-08', '2099-01-01'], 'circuit': ['Track'] * 3}),
        reconciliation=checks, driver_checks=driver_checks,
        sources={'results': {'url': 'https://api.jolpi.ca/ergast/f1/2026/', 'retrieved_utc': '2026-01-01T00:00:00+00:00'}})
    schedule = pd.DataFrame([dict(round=number, race=f'Weekend {number}', location='Track', code=code,
        session=weekend.SESSION_LABELS[code], session_utc=pd.Timestamp(f'{year}-01-0{day}T10:00Z'))
        for number, year, session_codes in [(1, 2000, ['FP1', 'FP2', 'FP3', 'Q', 'R']),
                                           (2, 2000, ['FP1', 'SQ', 'S', 'Q', 'R']),
                                           (3, 2099, ['FP1', 'FP2', 'FP3', 'Q', 'R'])]
        for day, code in enumerate(session_codes, 1)])
    called = []

    def analysis(year, round_number, code, *, refresh=False):
        called.append((round_number, code))
        participants = codes + ['RES']
        laps = pd.DataFrame({'DriverNumber': [str(i) for i in range(7)],
            'Driver': participants, 'Team': teams + ['McLaren'],
            'LapTime': pd.to_timedelta(range(90, 97), unit='s'),
            'PitInTime': pd.to_timedelta([None] * 7), 'PitOutTime': pd.to_timedelta([None] * 7),
            'TrackStatus': ['1'] * 7, 'IsAccurate': [True] * 7, 'Deleted': [False] * 7,
            'LapNumber': [1.] * 7, 'Compound': ['SOFT'] * 7, 'TyreLife': [1.] * 7, 'Stint': [1.] * 7,
            'QualifyingSegment': ['Q1', 'Q1', 'Q1', 'Q2', 'Q2', 'Q3', 'Q3'] if code in ('Q', 'SQ') else [''] * 7})
        source = pd.DataFrame({'DriverNumber': laps.DriverNumber, 'Abbreviation': participants,
            'FullName': participants, 'TeamName': laps.Team, 'Position': range(1, 8),
            'Points': [8., 7., 6., 5., 4., 3., 2.], 'GridPosition': range(1, 8), 'Status': ['Finished'] * 7,
            'Q1': pd.to_timedelta(range(90, 97), unit='s'), 'Q2': pd.to_timedelta([None] * 7),
            'Q3': pd.to_timedelta([None] * 7)})
        results = weekend.session_results(SimpleNamespace(results=source), laps, code)
        return {'results': results, 'laps': laps, 'clean_laps': data.clean_laps(laps),
            'metadata': {'round': round_number, 'session_code': code, 'retrieved_utc': '2026-01-01T00:00:00+00:00',
                         'deleted_laps_checked': True}}

    monkeypatch.setattr(data, 'load_season', lambda *args, **kwargs: season)
    monkeypatch.setattr(weekend, 'load_weekend_schedule', lambda *args, **kwargs: schedule)
    monkeypatch.setattr(weekend, 'load_session_analysis', analysis)
    app = AppTest.from_file(str(project_file), default_timeout=20).run()
    assert not app.exception
    yield app, called
    st.cache_data.clear()


@pytest.mark.parametrize('code', ['FP1', 'Q', 'SQ', 'S', 'R'])
def test_weekend_session_loads_before_grand_prix_standings(dashboard, code):
    app, called = dashboard
    assert app.selectbox(key='weekend-round').value == 2
    app.selectbox(key='weekend-session').set_value(code).run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    assert not app.exception
    assert (2, code) in called
    assert any(item.label == 'Download this data' for item in app.get('download_button'))


def test_schedule_controls_show_only_actual_sessions_and_disable_future(dashboard):
    app, called = dashboard
    assert 'Sprint' in app.selectbox(key='weekend-session').options
    assert 'Practice 2' not in app.selectbox(key='weekend-session').options
    app.selectbox(key='weekend-round').set_value(1).run()
    assert 'Practice 2' in app.selectbox(key='weekend-session').options
    assert 'Sprint' not in app.selectbox(key='weekend-session').options
    app.selectbox(key='weekend-round').set_value(3).run()
    assert next(button for button in app.button if button.label == 'Load session data').disabled
    assert not called


def test_practice_lap_analysis_includes_substitute_driver(dashboard):
    app, called = dashboard
    app.selectbox(key='pace-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load lap analysis').click().run()
    assert not app.exception
    assert 'RES' in next(control for control in app.multiselect if control.label == 'Drivers on lap charts').options


def test_qualifying_replay_uses_session_drivers_and_window_speed(dashboard):
    app, called = dashboard
    app.selectbox(key='replay-session').set_value('Q').run()
    assert app.select_slider[0].disabled
    next(control for control in app.radio if control.label == 'Drivers to show').set_value('Choose drivers').run()
    next(button for button in app.button if button.label == 'Load session drivers').click().run()
    assert not app.exception
    assert 'RES' in next(control for control in app.multiselect if control.label == 'Replay drivers').options


def test_practice_team_scope_filters_both_summary_and_laps(dashboard):
    app, called = dashboard
    next(control for control in app.radio if control.label == 'Comparison scope').set_value('Choose teams').run()
    next(control for control in app.multiselect if control.label == 'Teams').set_value(['Ferrari']).run()
    app.selectbox(key='weekend-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    session_tables = [item.value for item in app.dataframe if 'best_lap_seconds' in item.value.columns]
    assert len(session_tables) == 1
    assert set(session_tables[0]['team']) == {'Ferrari'}
    app.selectbox(key='pace-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load lap analysis').click().run()
    assert not app.exception
    assert next(control for control in app.multiselect if control.label == 'Drivers on lap charts').options == ['HAM', 'LEC']


@pytest.mark.parametrize('code', ['S', 'SQ'])
def test_replay_launch_passes_the_selected_session(dashboard, monkeypatch, code):
    app, called = dashboard
    commands = []

    def launch(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr('subprocess.Popen', launch)
    app.selectbox(key='replay-session').set_value(code).run()
    next(button for button in app.button if button.label == 'Open animated replay').click().run()
    assert not app.exception
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index('--session') + 1] == code
    assert command[command.index('--round') + 1] == '2'


def test_session_views_remain_available_during_partial_championship_publication(dashboard, monkeypatch):
    app, called = dashboard

    def partial_championship(*args, **kwargs):
        raise ValueError('Race results do not cover every standings round')

    monkeypatch.setattr(data, 'load_season', partial_championship)
    st.cache_data.clear()
    app.run()
    assert not app.exception
    assert 'Weekend sessions' in [tab.label for tab in app.tabs]
    app.selectbox(key='weekend-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    assert not app.exception
    assert (2, 'FP1') in called
    assert any('best_lap_seconds' in item.value.columns for item in app.dataframe)


def test_failed_refresh_preserves_loaded_session_with_warning(dashboard, monkeypatch):
    app, called = dashboard
    app.selectbox(key='weekend-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load session data').click().run()

    def unavailable(*args, **kwargs):
        raise weekend.SessionUnavailableError('Timing feed unavailable')

    monkeypatch.setattr(weekend, 'load_session_analysis', unavailable)
    next(button for button in app.button if button.label == 'Refresh now').click().run()
    assert not app.exception
    assert any('last loaded timing' in warning.value for warning in app.warning)
    assert any('best_lap_seconds' in item.value.columns for item in app.dataframe)


@pytest.fixture
def hosted_dashboard(dashboard, monkeypatch):
    import archive_analysis
    import archive_replay
    from archive_source import ArchiveUnavailableError

    def no_archive(*args, **kwargs):
        raise ArchiveUnavailableError('not_published')

    monkeypatch.setattr(archive_analysis, 'load_archive_analysis', no_archive)
    monkeypatch.setattr(archive_replay, 'load_archive_replay', no_archive)
    import openf1_data

    _, called = dashboard
    local_analysis = weekend.load_session_analysis

    def online_analysis(*args, **kwargs):
        analysis = local_analysis(*args, **kwargs)
        analysis['metadata'].update(source='OpenF1 historical timing',
                                    quality_note='Historical timing quality note')
        return analysis

    def unavailable_local(*args, **kwargs):
        pytest.fail('The hosted dashboard must use its online timing source')

    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', online_analysis)
    monkeypatch.setattr(weekend, 'load_session_analysis', unavailable_local)
    st.cache_data.clear()
    st.cache_resource.clear()
    entrypoint = Path(__file__).resolve().parents[1] / 'online.py'
    app = AppTest.from_file(str(entrypoint), default_timeout=20).run()
    assert not app.exception
    return app, called


def test_hosted_dashboard_excludes_desktop_processes_and_logs(hosted_dashboard, monkeypatch):
    app, _ = hosted_dashboard
    launched = []
    monkeypatch.setattr('subprocess.Popen', lambda *args, **kwargs: launched.append(args))
    app.run()
    assert not app.exception
    assert not launched
    assert 'Open animated replay' not in [button.label for button in app.button]
    assert 'Load session drivers' not in [button.label for button in app.button]
    assert 'Replay log' not in [expander.label for expander in app.expander]
    assert not app.code
    assert 'Load browser replay' in [button.label for button in app.button]


@pytest.mark.parametrize('code', ['FP1', 'Q', 'SQ', 'S', 'R'])
def test_hosted_dashboard_loads_weekend_sessions(hosted_dashboard, code):
    app, called = hosted_dashboard
    app.selectbox(key='weekend-session').set_value(code).run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    assert not app.exception
    assert (2, code) in called
    assert any(item.label == 'Download this data' for item in app.get('download_button'))
    assert any('OpenF1 historical timing' in caption.value for caption in app.caption)
    assert any('Historical timing quality note' in caption.value for caption in app.caption)


def test_hosted_lap_analysis_and_reruns_remain_interactive(hosted_dashboard):
    app, called = hosted_dashboard
    app.selectbox(key='pace-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load lap analysis').click().run()
    assert not app.exception
    assert (2, 'FP1') in called
    assert 'RES' in next(control for control in app.multiselect if control.label == 'Drivers on lap charts').options
    next(control for control in app.radio if control.label == 'Comparison scope').set_value('Choose teams').run()
    next(control for control in app.multiselect if control.label == 'Teams').set_value(['Ferrari']).run()
    assert not app.exception
    assert set(next(control for control in app.multiselect if control.label == 'Drivers on lap charts').options) == {'HAM', 'LEC'}


@pytest.mark.parametrize('session_code, official_round, expected_points', [
    ('R', 2, True), ('S', 2, False), ('R', 1, False),
])
def test_hosted_race_points_match_official_session_without_changing_classification(
        hosted_dashboard, monkeypatch, session_code, official_round, expected_points):
    import openf1_data

    app, _ = hosted_dashboard
    season = data.load_season(2026)
    published = season['results'].copy()
    published['round'] = official_round
    official_before = published.copy(deep=True)
    monkeypatch.setattr(data, 'load_season', lambda *args, **kwargs: {**season, 'results': published})
    loader = openf1_data.load_openf1_analysis
    originals = []

    def missing_points(*args, **kwargs):
        analysis = loader(*args, **kwargs)
        analysis['results']['points'] = float('nan')
        analysis['results']['grid'] = float('nan')
        analysis['results'].loc[analysis['results'].code.eq('NOR'), 'position'] = 2
        analysis['results'].loc[analysis['results'].code.eq('PIA'), 'position'] = 1
        originals.append((analysis, analysis['results'].copy(deep=True)))
        return analysis

    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', missing_points)
    st.cache_data.clear()
    app.run()
    app.selectbox(key='weekend-session').set_value(session_code).run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    assert not app.exception
    table = next(item.value for item in app.dataframe
                 if {'status', 'points', 'lap_count'}.issubset(item.value.columns)).set_index('driver')
    assert table.loc['NOR', 'position'] == 2
    assert table.loc['PIA', 'position'] == 1
    if expected_points:
        for row in published.itertuples():
            assert table.loc[row.code, 'points'] == row.points
            assert table.loc[row.code, 'grid'] == row.grid
        assert pd.isna(table.loc['RES', 'points'])
        assert pd.isna(table.loc['RES', 'grid'])
        assert any('published Jolpica race results' in item.value for item in app.caption)
    else:
        assert table.points.isna().all()
        assert table.grid.isna().all()
        assert not any('published Jolpica race results' in item.value for item in app.caption)
    session_points_charts = [item for item in app.get('plotly_chart')
        if json.loads(item.proto.spec).get('layout', {}).get('title', {}).get('text') == 'Session points']
    assert bool(session_points_charts) is expected_points
    assert app.session_state['loaded_session'][1]['results'].points.isna().all()
    assert app.session_state['loaded_session'][1]['results'].grid.isna().all()
    for analysis, before in originals:
        pd.testing.assert_frame_equal(analysis['results'], before)
    pd.testing.assert_frame_equal(published, official_before)


def test_hosted_refresh_reuses_cache_during_shared_cooldown(hosted_dashboard, monkeypatch):
    app, _ = hosted_dashboard
    loader = data.load_season
    calls = []

    def load(*args, **kwargs):
        calls.append(True)
        return loader(*args, **kwargs)

    monkeypatch.setattr(data, 'load_season', load)
    next(button for button in app.button if button.label == 'Refresh now').click().run()
    assert not app.exception
    assert len(calls) == 1
    next(button for button in app.button if button.label == 'Refresh now').click().run()
    assert not app.exception
    assert len(calls) == 1
    assert any('requested recently' in info.value for info in app.info)


def test_hosted_session_refresh_bypasses_the_source_disk_cache(hosted_dashboard, monkeypatch):
    import openf1_data

    app, _ = hosted_dashboard
    loader = openf1_data.load_openf1_analysis
    refresh_requests = []

    def load(*args, **kwargs):
        refresh_requests.append(kwargs.get('refresh'))
        return loader(*args, **kwargs)

    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', load)
    app.selectbox(key='weekend-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    next(button for button in app.button if button.label == 'Refresh now').click().run()
    assert not app.exception
    assert refresh_requests == [True, True]


@pytest.fixture
def hosted_replay(hosted_dashboard, monkeypatch):
    import web_replay

    app, _ = hosted_dashboard
    calls = []

    def load(year, round_number, code, *, refresh=False):
        calls.append((year, round_number, code, refresh))
        drivers = [dict(code=code, number=number, name=code, team=team, color='#ff0000')
                   for number, (code, team) in enumerate([
                       ('NOR', 'McLaren'), ('LEC', 'Ferrari'), ('RUS', 'Mercedes'),
                       ('RES', 'McLaren')], 1)]
        cars = {driver['code']: {field: [value, value] for field, value in {
            'x': 1, 'y': 2, 'lap': 1, 'position': index, 'speed': 200, 'gear': 7,
            'throttle': 100, 'brake': 0, 'drs': 0, 'compound': 'SOFT', 'age': 1,
            'stops': 0, 'last_stop_lap': None, 'in_pit': False,
            'ahead': None, 'behind': None, 'leader': None}.items()}
            for index, driver in enumerate(drivers, 1)}
        return dict(metadata=dict(event=f'Weekend {round_number}', location='Track',
            session=weekend.SESSION_LABELS[code], year=year, round=round_number,
            session_code=code, start_utc='2026-01-01T10:00:00+00:00',
            source='OpenF1 recorded timing', source_url='https://api.openf1.org/v1/',
            retrieved_utc='2026-01-01T12:00:00+00:00'), track={'x': [0, 1, 0], 'y': [0, 1, 1]},
            drivers=drivers, times=[0, 60], cars=cars, weather=[], race_control=[], unavailable=[])

    monkeypatch.setattr(web_replay, 'load_browser_replay', load)
    return app, calls


def replay_data(app):
    frames = list(app.get('iframe'))
    assert len(frames) == 1
    assert frames[0].proto.alt == 'Animated session replay'
    match = re.search(r'<script id="replay-data" type="application/json">(.*?)</script>',
                      frames[0].proto.srcdoc, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


@pytest.mark.parametrize('code', ['FP1', 'Q', 'SQ', 'S', 'R'])
def test_hosted_replay_embeds_selected_session_without_desktop_process(hosted_replay, monkeypatch, code):
    app, calls = hosted_replay
    launched = []
    monkeypatch.setattr('subprocess.Popen', lambda *args, **kwargs: launched.append(args))
    app.selectbox(key='browser-replay-session').set_value(code).run()
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    assert not app.exception
    assert calls == [(2026, 2, code, False)]
    payload = replay_data(app)
    assert payload['metadata']['session_code'] == code
    assert payload['metadata']['round'] == 2
    assert payload['visible_codes'] == ['NOR', 'LEC', 'RUS', 'RES']
    assert not launched


def test_hosted_replay_scope_reruns_without_redownloading(hosted_replay):
    app, calls = hosted_replay
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    next(control for control in app.radio if control.label == 'Comparison scope').set_value('Choose teams').run()
    next(control for control in app.multiselect if control.label == 'Teams').set_value(['Ferrari']).run()
    assert not app.exception
    assert replay_data(app)['visible_codes'] == ['LEC']
    assert calls == [(2026, 2, 'R', False)]
    next(control for control in app.multiselect if control.label == 'Teams').set_value([]).run()
    assert not app.exception
    assert not app.get('iframe')
    assert any('Choose at least one team' in item.value for item in app.info)


def test_hosted_replay_never_shows_another_session_or_future_replay(hosted_replay):
    app, calls = hosted_replay
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    assert replay_data(app)['metadata']['session_code'] == 'R'
    app.selectbox(key='browser-replay-session').set_value('FP1').run()
    assert not app.exception
    assert not app.get('iframe')
    app.selectbox(key='browser-replay-round').set_value(3).run()
    assert not app.exception
    assert next(button for button in app.button if button.label == 'Load browser replay').disabled
    assert not app.get('iframe')
    assert calls == [(2026, 2, 'R', False)]


def test_hosted_refresh_discards_replay_then_downloads_again(hosted_replay):
    app, calls = hosted_replay
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    assert app.get('iframe')
    next(button for button in app.button if button.label == 'Refresh now').click().run()
    assert not app.exception
    assert not app.get('iframe')
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    assert not app.exception
    assert replay_data(app)['metadata']['round'] == 2
    assert calls == [(2026, 2, 'R', False), (2026, 2, 'R', True)]


def test_hosted_replay_failure_is_visible_and_cannot_reuse_another_session(hosted_replay, monkeypatch):
    import web_replay

    app, _ = hosted_replay
    next(button for button in app.button if button.label == 'Load browser replay').click().run()

    def unavailable(*args, **kwargs):
        raise web_replay.BrowserReplayUnavailable('Recorded positions are not published')

    monkeypatch.setattr(web_replay, 'load_browser_replay', unavailable)
    app.selectbox(key='browser-replay-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    assert not app.exception
    assert not app.get('iframe')
    assert any('Neither source has usable recorded data' in item.value for item in app.warning)


def limited_source_metadata():
    from openf1_data import OpenF1AccessError

    return dict(access_limited=True, cached_fallback=True,
        access_reason='live_access_restricted',
        access_message=str(OpenF1AccessError(reason='live_access_restricted')),
        source_retrieved_utc='2026-01-01T08:15:00+00:00',
        retrieved_utc='2026-01-02T03:00:00+00:00',
        loaded_utc='2026-10-24T12:30:00+00:00')


def assert_saved_source_notice(app):
    assert any('OpenF1' in item.value and 'saved data' in item.value.lower()
               for item in app.warning)
    captions = [item.value for item in app.caption if 'source retrieved' in item.value.lower()]
    assert any('2026-01-01 08:15' in value for value in captions)
    assert all('2026-10-24 12:30' not in value and '2026-01-02 03:00' not in value
               for value in captions)
    warnings = ' '.join(item.value for item in app.warning)
    assert 'https://api.openf1.org' not in warnings
    assert 'access_token=' not in warnings


@pytest.mark.parametrize('view, button_label, state_key', [
    ('weekend', 'Load session data', 'loaded_session'),
    ('pace', 'Load lap analysis', 'lap_analysis'),
])
def test_hosted_saved_timing_keeps_source_time_scope_and_session(
        hosted_dashboard, monkeypatch, view, button_label, state_key):
    import openf1_data

    app, _ = hosted_dashboard
    loader = openf1_data.load_openf1_analysis

    def saved_analysis(*args, **kwargs):
        analysis = loader(*args, **kwargs)
        analysis['metadata'].update(limited_source_metadata())
        return analysis

    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', saved_analysis)
    next(control for control in app.radio if control.label == 'Comparison scope').set_value('Choose teams').run()
    next(control for control in app.multiselect if control.label == 'Teams').set_value(['Ferrari']).run()
    app.selectbox(key=f'{view}-session').set_value('FP1').run()
    next(button for button in app.button if button.label == button_label).click().run()
    assert not app.exception
    assert_saved_source_notice(app)
    assert app.session_state[state_key][0] == (2, 'FP1')
    if view == 'weekend':
        table = next(item.value for item in app.dataframe if 'best_lap_seconds' in item.value.columns)
        assert set(table.team) == {'Ferrari'}
    else:
        assert set(next(control for control in app.multiselect
                        if control.label == 'Drivers on lap charts').options) == {'HAM', 'LEC'}
    app.selectbox(key=f'{view}-session').set_value('Q').run()
    assert not app.exception
    assert not any('best_lap_seconds' in item.value.columns for item in app.dataframe)
    assert not any(control.label == 'Drivers on lap charts' for control in app.multiselect)
    assert next(control for control in app.multiselect if control.label == 'Teams').value == ['Ferrari']
    assert not any('2026-01-01 08:15' in item.value for item in app.caption)


def test_hosted_saved_replay_keeps_source_time_and_selected_scope(hosted_replay, monkeypatch):
    import web_replay

    app, calls = hosted_replay
    loader = web_replay.load_browser_replay

    def saved_replay(*args, **kwargs):
        payload = loader(*args, **kwargs)
        payload['metadata'].update(limited_source_metadata())
        return payload

    monkeypatch.setattr(web_replay, 'load_browser_replay', saved_replay)
    next(control for control in app.radio if control.label == 'Comparison scope').set_value('Choose teams').run()
    next(control for control in app.multiselect if control.label == 'Teams').set_value(['Ferrari']).run()
    next(button for button in app.button if button.label == 'Load browser replay').click().run()
    assert not app.exception
    assert_saved_source_notice(app)
    assert replay_data(app)['visible_codes'] == ['LEC']
    assert calls == [(2026, 2, 'R', False)]
    app.selectbox(key='browser-replay-session').set_value('Q').run()
    assert not app.exception
    assert not app.get('iframe')
    assert next(control for control in app.multiselect if control.label == 'Teams').value == ['Ferrari']
    assert not any('2026-01-01 08:15' in item.value for item in app.caption)


@pytest.mark.parametrize('view, button_label', [
    ('weekend', 'Load session data'), ('pace', 'Load lap analysis'),
    ('browser-replay', 'Load browser replay'),
])
def test_hosted_uncached_access_failure_is_friendly_and_does_not_show_saved_data(
        hosted_replay, monkeypatch, view, button_label):
    import openf1_data
    import requests
    import web_replay

    app, _ = hosted_replay

    def denied(*args, **kwargs):
        error = openf1_data.OpenF1AccessError(reason='live_access_restricted')
        error.__cause__ = requests.HTTPError(
            '401 Client Error for https://api.openf1.org/v1/laps?access_token=secret_value')
        raise error

    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', denied)
    monkeypatch.setattr(web_replay, 'load_browser_replay', denied)
    next(button for button in app.button if button.label == button_label).click().run()
    assert not app.exception
    warnings = ' '.join(item.value for item in app.warning)
    assert 'OpenF1' in warnings
    assert 'live' in warnings.lower()
    assert 'https://api.openf1.org' not in warnings
    assert 'secret_value' not in warnings
    assert 'access_token=' not in warnings
    assert 'free archive has not published' in warnings.lower()
    assert 'OpenF1 temporarily restricts' in warnings
    assert 'saved data' not in warnings.lower()
    assert not app.get('iframe')
    assert not any('best_lap_seconds' in item.value.columns for item in app.dataframe)
    assert not any(control.label == 'Drivers on lap charts' for control in app.multiselect)


@pytest.mark.parametrize('session_code', ['FP1', 'R'])
def test_hosted_archive_source_dates_and_classification_are_honest(hosted_dashboard, monkeypatch, session_code):
    import archive_analysis
    import openf1_data
    app, _ = hosted_dashboard
    fixture_analysis = openf1_data.load_openf1_analysis
    def archive(*args, **kwargs):
        result = fixture_analysis(*args, **kwargs)
        result['metadata'].update(archived=True, source='TracingInsights / FastF1 archive',
            archive_revision='2026.297.2', published_utc='2026-10-24T19:00:38+00:00',
            classification_available=session_code.startswith('FP'))
        if session_code == 'R':
            result['results']['position'] = float('nan')
        return result
    def forbidden(*args, **kwargs):
        raise AssertionError('The archive is available and must not call OpenF1')
    monkeypatch.setattr(archive_analysis, 'load_archive_analysis', archive)
    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', forbidden)
    st.cache_data.clear()
    app.selectbox(key='weekend-session').set_value(session_code).run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    assert not app.exception
    assert any('TracingInsights / FastF1 archive' in item.value for item in app.caption)
    assert any('Archive release 2026.297.2 published 2026-10-24 19:00 UTC' in item.value for item in app.caption)
    if session_code == 'R':
        assert any('Official classification is not available' in item.value for item in app.info)
        assert not any(item.value.startswith('Published session classification.') for item in app.caption)


def test_qualifying_lap_analysis_filters_stage_and_restores_full_field(dashboard):
    app, _ = dashboard
    app.selectbox(key='pace-session').set_value('Q').run()
    next(button for button in app.button if button.label == 'Load lap analysis').click().run()
    stage = app.selectbox(key='pace-part-2-Q')
    assert stage.options == ['All qualifying', 'Q1', 'Q2', 'Q3']
    stage.set_value('Q2').run()
    assert not app.exception
    assert next(control for control in app.multiselect if control.label == 'Drivers on lap charts').options == ['HAM', 'RUS']
    summaries = [item.value for item in app.dataframe if 'median_lap_seconds' in item.value]
    assert len(summaries) == 1
    assert dict(zip(summaries[0].Driver, summaries[0].median_lap_seconds)) == {'HAM': 93., 'RUS': 94.}
    app.selectbox(key='pace-part-2-Q').set_value('All qualifying').run()
    assert not app.exception
    assert len(next(control for control in app.multiselect if control.label == 'Drivers on lap charts').options) == 7
    app.selectbox(key='pace-session').set_value('FP1').run()
    next(button for button in app.button if button.label == 'Load lap analysis').click().run()
    assert not any(control.label == 'Qualifying part' for control in app.selectbox)
