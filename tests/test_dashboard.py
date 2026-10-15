"""Weekend dashboard checks."""
from pathlib import Path
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
            'LapNumber': [1.] * 7, 'Compound': ['SOFT'] * 7, 'TyreLife': [1.] * 7, 'Stint': [1.] * 7})
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
def hosted_dashboard(dashboard):
    _, called = dashboard
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
    assert any('desktop window' in info.value for info in app.info)


@pytest.mark.parametrize('code', ['FP1', 'Q', 'S', 'R'])
def test_hosted_dashboard_loads_weekend_sessions(hosted_dashboard, code):
    app, called = hosted_dashboard
    app.selectbox(key='weekend-session').set_value(code).run()
    next(button for button in app.button if button.label == 'Load session data').click().run()
    assert not app.exception
    assert (2, code) in called
    assert any(item.label == 'Download this data' for item in app.get('download_button'))


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
