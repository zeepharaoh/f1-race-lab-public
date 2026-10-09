from types import SimpleNamespace

import pandas as pd
import pytest

import weekend
from data import load_laps


def lap_frame():
    return pd.DataFrame({
        'DriverNumber': ['1', '1', '1', '1', '2', '2', '3'],
        'Driver': ['ONE'] * 4 + ['TWO'] * 2 + ['THR'],
        'Team': ['Red Bull Racing'] * 4 + ['Ferrari'] * 2 + ['Racing Bulls'],
        'LapNumber': [1, 2, 3, 4, 1, 2, 1],
        'LapTime': pd.to_timedelta([90, 85, 89, 88, 91, 92, None], unit='s'),
        'PitInTime': pd.to_timedelta([None, None, 1, None, None, None, None], unit='s'),
        'PitOutTime': pd.to_timedelta([None] * 7, unit='s'),
        'Deleted': [False, True, False, False, False, False, False],
        'TrackStatus': ['1', '1', '1', '2', '1', '1', '1'],
        'IsAccurate': [True] * 7,
    })


def fake_session(code='FP1', statuses=('Started', 'Finalised', 'Ends')):
    results = pd.DataFrame({
        'DriverNumber': ['1', '2', '3'], 'Abbreviation': ['ONE', 'TWO', 'THR'],
        'FullName': ['Driver One', 'Driver Two', 'Driver Three'],
        'TeamName': ['Red Bull Racing', 'Ferrari', 'Racing Bulls'],
        'Position': [2.0, 1.0, 3.0], 'GridPosition': [3.0, 1.0, 2.0],
        'Points': [6.0, 8.0, 0.0], 'Status': ['Finished'] * 3,
        'Laps': [4.0, 2.0, 0.0],
        'Q1': pd.to_timedelta([90, 91, None], unit='s'),
        'Q2': pd.to_timedelta([89, 90, None], unit='s'),
        'Q3': pd.to_timedelta([88, 89, None], unit='s'),
    })
    return SimpleNamespace(
        name=weekend.SESSION_LABELS[code], results=results, laps=lap_frame(),
        event={'EventName': 'Example Grand Prix', 'Location': 'Example Circuit'},
        session_status=pd.DataFrame({'Status': list(statuses)}),
    )


def test_schedule_uses_real_session_names_for_regular_and_sprint_weekends(monkeypatch):
    regular = {'RoundNumber': 1, 'EventName': 'Regular', 'Location': 'A'}
    sprint = {'RoundNumber': 17, 'EventName': 'Sprint Weekend', 'Location': 'B'}
    for event, codes in ((regular, ('FP1', 'FP2', 'FP3', 'Q', 'R')),
                         (sprint, ('FP1', 'SQ', 'S', 'Q', 'R'))):
        for number, code in enumerate(codes, 1):
            event[f'Session{number}'] = weekend.SESSION_LABELS[code]
            event[f'Session{number}DateUtc'] = f'2026-10-0{number} 12:00:00'
    testing = dict(regular, RoundNumber=0)
    calls = []
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_event_schedule', lambda *args, **kwargs: calls.append((args, kwargs)) or pd.DataFrame([regular, sprint, testing]))
    schedule = weekend.load_weekend_schedule(2026)
    assert schedule.loc[schedule['round'].eq(1), 'code'].tolist() == ['FP1', 'FP2', 'FP3', 'Q', 'R']
    assert schedule.loc[schedule['round'].eq(17), 'code'].tolist() == ['FP1', 'SQ', 'S', 'Q', 'R']
    assert str(schedule.session_utc.dtype) == 'datetime64[ns, UTC]'
    assert calls == [((2026,), {'include_testing': False, 'backend': 'fastf1'})]


def test_eligible_sessions_handles_timezones_missing_dates_and_future_starts():
    schedule = pd.DataFrame({'session_utc': pd.to_datetime(['2026-10-09 09:30Z', '2026-10-09 12:30Z', None], utc=True)})
    eligible = weekend.eligible_sessions(schedule, pd.Timestamp('2026-10-09 12:00:00+02:00'))
    assert eligible.index.tolist() == [0]


def test_resolve_does_not_gate_current_weekend_by_last_race_standings(monkeypatch):
    schedule = pd.DataFrame([
        {'round': 16, 'code': 'FP1', 'session_utc': pd.Timestamp('2026-10-02 08:00Z')},
        {'round': 17, 'code': 'FP1', 'session_utc': pd.Timestamp('2026-10-09 09:30Z')},
        {'round': 17, 'code': 'SQ', 'session_utc': pd.Timestamp('2026-10-09 12:30Z')},
    ])
    monkeypatch.setattr(weekend, 'load_weekend_schedule', lambda year, **kwargs: schedule)
    assert weekend.resolve_session(None, 'Practice 1', now='2026-10-09 10:00Z')['round'] == 17
    with pytest.raises(weekend.SessionUnavailableError, match='scheduled start'):
        weekend.resolve_session(17, 'SQ', now='2026-10-09 10:00Z')
    with pytest.raises(weekend.SessionUnavailableError, match='not scheduled'):
        weekend.resolve_session(17, 'FP2', now='2026-10-09 10:00Z')


def test_qualifying_segment_finished_is_not_session_completion():
    for code in ('Q', 'SQ'):
        with pytest.raises(weekend.SessionUnavailableError, match='not complete'):
            weekend.require_session_complete(fake_session(code, ('Started', 'Finished')))
        assert weekend.require_session_complete(fake_session(code)) == 'Ends'
    with pytest.raises(weekend.SessionUnavailableError, match='not complete'):
        weekend.require_session_complete(fake_session('R', ('Started', 'Aborted')))
    assert weekend.require_session_complete(fake_session('FP1', ('Started', 'Finished'))) == 'Finished'


def test_practice_bests_exclude_deleted_pit_and_unconfirmed_laps_but_allow_yellow():
    laps = lap_frame()
    results = weekend.session_results(fake_session(), laps, 'FP1').set_index('code')
    assert results.loc['ONE', 'best_lap_seconds'] == 88
    assert results.loc['ONE', 'position'] == 1
    assert results.loc['TWO', 'gap_seconds'] == 3
    assert results.loc['THR', 'position'] != results.loc['THR', 'position']
    assert results.loc['ONE', 'lap_count'] == 4
    assert results.loc['ONE', 'timed_lap_count'] == 2
    assert results.loc['ONE', 'team'] == 'Red Bull'
    assert results.loc['THR', 'team'] == 'RB F1 Team'
    assert results.points.isna().all()
    laps['Deleted'] = laps['Deleted'].astype('boolean')
    laps.loc[3, 'Deleted'] = pd.NA
    assert weekend.valid_timed_laps(laps).lap_seconds.tolist() == [90, 91, 92]


def test_qualifying_keeps_segment_times_and_does_not_award_points():
    result = weekend.session_results(fake_session('SQ'), lap_frame(), 'SQ').set_index('code')
    assert result.loc['ONE', 'position'] == 2
    assert result.loc['ONE', 'Q1_seconds'] == 90
    assert result.loc['ONE', 'Q2_seconds'] == 89
    assert result.loc['ONE', 'Q3_seconds'] == 88
    assert result.points.isna().all()
    assert result.grid.isna().all()


def test_sprint_classification_preserves_supplied_positions_points_and_laps():
    result = weekend.session_results(fake_session('S'), lap_frame(), 'S').set_index('code')
    assert result.loc['TWO', 'position'] == 1
    assert result.loc['TWO', 'points'] == 8
    assert result.loc['TWO', 'grid'] == 1
    assert result.loc['TWO', 'lap_count'] == 2
    assert result.loc['ONE', 'position'] == 2
    assert result.loc['ONE', 'best_lap_seconds'] == 88


def test_missing_race_classification_does_not_invent_points_or_finish_order():
    session = fake_session('R')
    session.results = session.results.drop(columns=['Position', 'Points', 'Laps'])
    result = weekend.session_results(session, session.laps, 'R')
    assert result.position.isna().all()
    assert result.points.isna().all()


def test_loader_checks_completion_and_loads_deleted_lap_messages(monkeypatch):
    session = fake_session('FP1')
    calls = []
    session.load = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setattr(weekend, 'resolve_session', lambda *args, **kwargs: {'session_utc': pd.Timestamp('2026-10-09 09:30Z')})
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_session', lambda *args, **kwargs: session)
    analysis = weekend.load_session_analysis(2026, 17, 'FP1')
    assert calls == [{'telemetry': False, 'weather': False, 'messages': True}]
    assert analysis['metadata']['round'] == 17
    assert analysis['metadata']['session_code'] == 'FP1'
    assert analysis['metadata']['deleted_laps_checked']
    assert analysis['metadata']['is_complete']
    assert analysis['laps']['Team'].iloc[0] == 'Red Bull'
    assert 88 in analysis['results'].best_lap_seconds.tolist()
    assert 88 not in analysis['clean_laps'].lap_seconds.tolist()
    assert 85 not in analysis['clean_laps'].lap_seconds.tolist()
    laps, metadata = load_laps(2026, 17, 'FP1')
    assert laps.equals(analysis['clean_laps'])
    assert metadata['session_code'] == 'FP1'


def test_loader_reports_missing_published_laps(monkeypatch):
    session = fake_session('FP1')
    session.laps = session.laps.iloc[:0]
    session.load = lambda **kwargs: None
    monkeypatch.setattr(weekend, 'resolve_session', lambda *args, **kwargs: {'session_utc': pd.Timestamp('2026-10-09 09:30Z')})
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_session', lambda *args, **kwargs: session)
    with pytest.raises(weekend.SessionUnavailableError, match='not published'):
        weekend.load_session_analysis(2026, 17, 'FP1')


def test_loader_excludes_unknown_deleted_status_from_clean_pace(monkeypatch):
    session = fake_session('FP1')
    session.laps['Deleted'] = session.laps['Deleted'].astype('boolean')
    session.laps.loc[0, 'Deleted'] = pd.NA
    session.load = lambda **kwargs: None
    monkeypatch.setattr(weekend, 'resolve_session', lambda *args, **kwargs: {'session_utc': pd.Timestamp('2026-10-09 09:30Z')})
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_session', lambda *args, **kwargs: session)
    analysis = weekend.load_session_analysis(2026, 17, 'FP1')
    assert not analysis['metadata']['deleted_laps_checked']
    assert 90 not in analysis['clean_laps'].lap_seconds.tolist()


def test_incomplete_cached_session_retries_fresh_feed_without_deleting_cache(monkeypatch):
    from contextlib import contextmanager
    session = fake_session('SQ', ('Started', 'Finished'))
    calls = []
    fresh = {'active': False}
    def load(**kwargs):
        calls.append((fresh['active'], kwargs))
        if fresh['active']:
            session.session_status = pd.DataFrame({'Status': ['Started', 'Finished', 'Finalised', 'Ends']})
    @contextmanager
    def disabled():
        fresh['active'] = True
        try:
            yield
        finally:
            fresh['active'] = False
    session.load = load
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', disabled)
    assert weekend.load_completed_session(session, telemetry=True) == 'Ends'
    assert calls == [
        (False, {'telemetry': True, 'weather': False, 'messages': True}),
        (True, {'telemetry': True, 'weather': False, 'messages': True}),
    ]
    assert not fresh['active']


def test_fresh_feed_still_incomplete_reports_availability(monkeypatch):
    from contextlib import nullcontext
    session = fake_session('SQ', ('Started', 'Finished'))
    session.load = lambda **kwargs: None
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', nullcontext)
    with pytest.raises(weekend.SessionUnavailableError, match='not complete'):
        weekend.load_completed_session(session)


def test_empty_cached_lap_feed_retries_and_reports_missing_publication(monkeypatch):
    from contextlib import nullcontext
    session = fake_session('FP1')
    calls = []
    def load(**kwargs):
        calls.append(kwargs)
        raise weekend.NoLapDataError('No laps loaded')
    session.load = load
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', nullcontext)
    with pytest.raises(weekend.SessionUnavailableError, match='timing data is not published'):
        weekend.load_completed_session(session)
    assert len(calls) == 2


def test_library_exit_for_missing_endpoint_becomes_session_availability_error(monkeypatch):
    from contextlib import nullcontext
    session = fake_session('FP1')
    calls = []
    def load(**kwargs):
        calls.append(kwargs)
        raise SystemExit(0)
    session.load = load
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', nullcontext)
    with pytest.raises(weekend.SessionUnavailableError, match='timing data is not published'):
        weekend.load_completed_session(session)
    assert len(calls) == 2


def test_explicit_refresh_bypasses_completed_cache_once(monkeypatch):
    from contextlib import contextmanager
    session = fake_session('Q')
    calls = []
    fresh = {'active': False}
    def load(**kwargs):
        calls.append((fresh['active'], kwargs))
        if fresh['active']:
            session.laps.loc[0, 'Deleted'] = True
    @contextmanager
    def disabled():
        fresh['active'] = True
        try:
            yield
        finally:
            fresh['active'] = False
    session.load = load
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', disabled)
    assert weekend.load_completed_session(session, refresh=True) == 'Ends'
    assert calls == [(True, {'telemetry': False, 'weather': False, 'messages': True})]
    assert session.laps.loc[0, 'Deleted']
    assert not fresh['active']


def test_explicit_refresh_has_no_duplicate_retry_when_feed_unavailable(monkeypatch):
    from contextlib import nullcontext
    session = fake_session('FP1')
    calls = []
    def load(**kwargs):
        calls.append(kwargs)
        raise weekend.NoLapDataError('No laps loaded')
    session.load = load
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', nullcontext)
    with pytest.raises(weekend.SessionUnavailableError, match='timing data is not published'):
        weekend.load_completed_session(session, refresh=True)
    assert len(calls) == 1


def test_analysis_refresh_passes_fresh_option_and_labels_load_time(monkeypatch):
    session = fake_session('Q')
    calls = []
    monkeypatch.setattr(weekend, 'resolve_session', lambda *args, **kwargs: {'session_utc': pd.Timestamp('2026-10-09 09:30Z')})
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_session', lambda *args, **kwargs: session)
    monkeypatch.setattr(weekend, 'load_completed_session', lambda loaded, **kwargs: calls.append((loaded, kwargs)) or 'Ends')
    analysis = weekend.load_session_analysis(2026, 17, 'Q', refresh=True)
    assert calls == [(session, {'refresh': True})]
    assert analysis['metadata']['refresh_requested']
    assert analysis['metadata']['loaded_utc'] == analysis['metadata']['retrieved_utc']
    assert pd.Timestamp(analysis['metadata']['loaded_utc']).tzinfo is not None


def test_schedule_explicit_refresh_reads_revised_start_time(monkeypatch):
    from contextlib import contextmanager
    fresh = {'active': False}
    calls = []
    def get_schedule(*args, **kwargs):
        calls.append(fresh['active'])
        return pd.DataFrame([{
            'RoundNumber': 17, 'EventName': 'Example Grand Prix', 'Location': 'Example Circuit',
            'Session1': 'Practice 1',
            'Session1DateUtc': '2026-10-09 10:00' if fresh['active'] else '2026-10-09 09:30',
        }])
    @contextmanager
    def disabled():
        fresh['active'] = True
        try:
            yield
        finally:
            fresh['active'] = False
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_event_schedule', get_schedule)
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', disabled)
    cached = weekend.load_weekend_schedule(2026)
    updated = weekend.load_weekend_schedule(2026, refresh=True)
    assert cached.iloc[0]['session_utc'] == pd.Timestamp('2026-10-09 09:30Z')
    assert updated.iloc[0]['session_utc'] == pd.Timestamp('2026-10-09 10:00Z')
    assert calls == [False, True]
    assert not fresh['active']


def test_resolve_refresh_checks_rescheduled_start_before_allowing_analysis(monkeypatch):
    calls = []
    def schedule(year, *, refresh=False):
        calls.append(refresh)
        return pd.DataFrame([{
            'round': 17, 'code': 'FP1',
            'session_utc': pd.Timestamp('2026-10-09 10:00Z' if refresh else '2026-10-09 09:30Z'),
        }])
    monkeypatch.setattr(weekend, 'load_weekend_schedule', schedule)
    assert weekend.resolve_session(17, 'FP1', now='2026-10-09 09:45Z')['round'] == 17
    with pytest.raises(weekend.SessionUnavailableError, match='scheduled start'):
        weekend.resolve_session(17, 'FP1', now='2026-10-09 09:45Z', refresh=True)
    assert calls == [False, True]


def test_analysis_refresh_bypasses_session_schedule_lookup_for_current_api_path(monkeypatch):
    from contextlib import contextmanager
    session = fake_session('FP1')
    fresh = {'active': False}
    resolutions = []
    lookups = []
    loads = []
    def resolve(*args, **kwargs):
        resolutions.append((args, kwargs))
        return {'session_utc': pd.Timestamp('2026-10-09 10:00Z')}
    def get_session(*args, **kwargs):
        lookups.append((fresh['active'], args, kwargs))
        return session
    @contextmanager
    def disabled():
        fresh['active'] = True
        try:
            yield
        finally:
            fresh['active'] = False
    monkeypatch.setattr(weekend, 'resolve_session', resolve)
    monkeypatch.setattr(weekend, '_enable_cache', lambda: None)
    monkeypatch.setattr(weekend.fastf1, 'get_session', get_session)
    monkeypatch.setattr(weekend.fastf1.Cache, 'disabled', disabled)
    monkeypatch.setattr(weekend, 'load_completed_session', lambda loaded, **kwargs: loads.append(kwargs) or 'Ends')
    analysis = weekend.load_session_analysis(2026, 17, 'FP1', refresh=True)
    assert resolutions == [((17, 'FP1', 2026), {'refresh': True})]
    assert lookups == [(True, (2026, 17, 'FP1'), {'backend': 'fastf1'})]
    assert loads == [{'refresh': True}]
    assert analysis['metadata']['session_utc'] == '2026-10-09T10:00:00+00:00'
    assert not fresh['active']
