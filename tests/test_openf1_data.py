from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
import threading
from types import SimpleNamespace
import time

import pandas as pd
import pytest
import requests

import openf1_data as api


@pytest.fixture
def feeds(monkeypatch):
    start = pd.Timestamp('2026-10-10T13:00:00Z')
    session = {'year': 2026, 'session_key': 900, 'meeting_key': 90,
               'session_name': 'Qualifying', 'date_start': start.isoformat(),
               'date_end': (start + timedelta(hours=1)).isoformat(), 'is_cancelled': False,
               'location': 'Example'}
    rows = []
    for number, count in ((1, 6), (2, 1)):
        for lap in range(1, count + 1):
            duration = 90.0 if number == 1 else 91.0
            rows.append({'driver_number': number, 'lap_number': lap,
                         'date_start': (start + timedelta(seconds=(lap - 1) * 90)).isoformat(),
                         'lap_duration': duration, 'duration_sector_1': 30.0,
                         'duration_sector_2': 30.0, 'duration_sector_3': duration - 60,
                         'is_pit_out_lap': number == 1 and lap == 3,
                         **{f'segments_sector_{index}': [2049, 2051] for index in (1, 2, 3)}})
    rows[5]['duration_sector_3'] = None
    values = {
        'sessions': [session],
        'session_result': [
            {'driver_number': 2, 'position': 1, 'number_of_laps': 1, 'duration': [93, 92, 91]},
            {'driver_number': 1, 'position': 2, 'number_of_laps': 6, 'duration': [92, 91, 90]}],
        'drivers': [{'driver_number': 1, 'name_acronym': 'AAA', 'full_name': 'Driver A', 'team_name': 'Red Bull Racing'},
                    {'driver_number': 2, 'name_acronym': 'BBB', 'full_name': 'Driver B', 'team_name': 'Ferrari'}],
        'laps': rows,
        'stints': [{'driver_number': 1, 'lap_start': 1, 'lap_end': 6, 'stint_number': 1,
                    'compound': 'MEDIUM', 'tyre_age_at_start': 3}],
        'pit': [{'driver_number': 1, 'lap_number': 4}], 'starting_grid': [],
        'race_control': [
            {'date': start.isoformat(), 'scope': 'Track', 'flag': 'GREEN', 'message': 'GREEN LIGHT'},
            {'date': (start + timedelta(seconds=181)).isoformat(), 'message': 'CAR 1 (AAA) LAP DELETED - TRACK LIMITS LAP 2'},
            {'date': (start + timedelta(seconds=360)).isoformat(), 'scope': 'Sector', 'sector': 1, 'flag': 'YELLOW'},
            {'date': (start + timedelta(seconds=449)).isoformat(), 'scope': 'Sector', 'sector': 1, 'flag': 'CLEAR'},
        ],
    }
    calls = []
    def fetch(endpoint, **params):
        calls.append((endpoint, params))
        payload = values[endpoint]
        if isinstance(payload, Exception):
            raise payload
        return payload
    monkeypatch.setattr(api, 'fetch', fetch)
    monkeypatch.setattr(api, 'resolve_session', lambda *args, **kwargs: {
        'round': 9, 'race': 'Example Grand Prix', 'session_utc': start.tz_convert('Europe/Stockholm')})
    monkeypatch.setattr(api, '_READ_STATE', threading.local())
    return values, calls


def test_session_maps_by_utc_and_name_without_treating_round_as_list_index(feeds):
    values, calls = feeds
    other = dict(values['sessions'][0], session_key=800, date_start='2026-10-03T13:00:00Z')
    values['sessions'].insert(0, other)
    matched = api.resolve_openf1_session(2026, 9, 'Q')
    assert matched['session_key'] == 900 and matched['round'] == 9
    assert matched['code'] == 'Q'
    assert matched['_results'] == values['session_result']


@pytest.mark.parametrize('changes,message', [
    ({'is_cancelled': True}, 'cancelled'),
    ({'date_end': '2099-01-01T00:00:00Z'}, 'not finished'),
    ({'date_end': None}, 'not finished'),
])
def test_cancelled_live_or_unknown_end_is_not_loaded(feeds, changes, message):
    values, calls = feeds
    values['sessions'][0].update(changes)
    with pytest.raises(api.SessionUnavailableError, match=message):
        api.resolve_openf1_session(2026, 9, 'Q')
    assert [name for name, _ in calls] == ['sessions']


def test_session_requires_published_classification(feeds):
    values, _ = feeds
    values['session_result'] = []
    with pytest.raises(api.SessionUnavailableError, match='results are not published'):
        api.resolve_openf1_session(2026, 9, 'Q')


def test_ambiguous_session_mapping_is_rejected(feeds):
    values, _ = feeds
    values['sessions'].append(dict(values['sessions'][0], session_key=901))
    with pytest.raises(api.SessionUnavailableError, match='ambiguous'):
        api.resolve_openf1_session(2026, 9, 'Q')


def test_qualifying_uses_published_stages_and_keeps_classification_order(feeds):
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    results = analysis['results']
    assert results.code.tolist() == ['BBB', 'AAA']
    assert results.Q1_seconds.tolist() == [93, 92]
    assert results.Q2_seconds.tolist() == [92, 91]
    assert results.Q3_seconds.tolist() == [91, 90]
    assert results.best_lap_seconds.tolist() == [91, 90]
    assert results.points.isna().all() and results.grid.isna().all()
    assert analysis['metadata']['source'] == 'OpenF1'
    assert analysis['metadata']['is_complete']


def test_scalar_qualifying_time_is_not_assigned_to_a_guessed_stage(feeds):
    values, _ = feeds
    values['session_result'][0]['duration'] = 89
    results = api.load_openf1_analysis(2026, 9, 'Q')['results']
    assert results.loc[0, ['Q1_seconds', 'Q2_seconds', 'Q3_seconds', 'best_lap_seconds']].isna().all()


def test_zero_qualifying_stage_is_missing_instead_of_a_zero_second_lap(feeds):
    values, _ = feeds
    values['session_result'][0]['duration'] = [93, 0, None]
    results = api.load_openf1_analysis(2026, 9, 'Q')['results']
    assert results.loc[0, 'Q1_seconds'] == 93
    assert results.loc[0, ['Q2_seconds', 'Q3_seconds']].isna().all()
    assert results.loc[0, 'best_lap_seconds'] == 93


def test_clean_subset_excludes_pits_deletions_yellows_and_bad_sectors(feeds):
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    assert list(zip(analysis['clean_laps'].Driver, analysis['clean_laps'].LapNumber)) == [('AAA', 1), ('BBB', 1)]
    laps = analysis['laps']
    assert laps.IsAccurate.isna().all()
    assert laps.PitInTime.isna().all() and laps.PitOutTime.isna().all()
    assert str(laps.LapTime.dtype) == 'timedelta64[ns]'
    assert laps.loc[laps.Driver.eq('AAA'), 'TyreLife'].tolist() == [4, 5, 6, 7, 8, 9]
    assert analysis['metadata']['deleted_laps_checked']
    assert 'FastF1 accuracy flags' in analysis['metadata']['quality_note']


def test_reinstated_deleted_lap_can_return_to_comparison(feeds):
    values, _ = feeds
    values['race_control'].append({'date': '2026-10-10T13:04:01Z',
                                   'message': 'CAR 1 (AAA) LAP 2 TIME REINSTATED'})
    clean = api.load_openf1_analysis(2026, 9, 'Q')['clean_laps']
    assert clean.loc[clean.Driver.eq('AAA'), 'LapNumber'].tolist() == [1, 2]


def test_safety_car_interval_excludes_a_lap_even_with_green_minisectors(feeds):
    values, _ = feeds
    values['race_control'].extend([
        {'date': '2026-10-10T13:00:30Z', 'message': 'SAFETY CAR DEPLOYED'},
        {'date': '2026-10-10T13:01:40Z', 'message': 'SAFETY CAR WITHDRAWN'},
    ])
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    assert analysis['clean_laps'].empty
    assert '4' in analysis['laps'].iloc[0].TrackStatus


def test_missing_lap_start_does_not_invent_session_times(feeds):
    values, _ = feeds
    values['laps'][0]['date_start'] = None
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    assert pd.isna(analysis['laps'].iloc[0].Time)
    assert pd.isna(analysis['laps'].iloc[0].LapStartTime)
    assert analysis['clean_laps'].Driver.tolist() == ['BBB']


def test_unknown_deletion_or_unavailable_control_feed_remains_visible(feeds):
    values, _ = feeds
    values['race_control'].append({'date': '2026-10-10T13:04:01Z', 'message': 'CAR 1 LAP DELETED'})
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    assert not analysis['metadata']['deleted_laps_checked']
    assert analysis['laps'].loc[analysis['laps'].Driver.eq('AAA'), 'Deleted'].isna().all()
    values['race_control'] = requests.ConnectionError('Unavailable')
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    assert analysis['clean_laps'].empty
    assert 'race_control' in analysis['metadata']['quality_note']
    assert not analysis['metadata']['deleted_laps_checked']


def test_missing_pit_feed_does_not_claim_clean_laps(feeds):
    values, _ = feeds
    values['pit'] = requests.ConnectionError('Unavailable')
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    assert analysis['clean_laps'].empty
    assert 'pit' in analysis['metadata']['quality_note']


def test_missing_pit_out_flag_is_not_assumed_false(feeds):
    values, _ = feeds
    values['laps'][0]['is_pit_out_lap'] = None
    clean = api.load_openf1_analysis(2026, 9, 'Q')['clean_laps']
    assert clean.Driver.tolist() == ['BBB']


def test_duplicate_laps_are_rejected(feeds):
    values, _ = feeds
    values['laps'].append(dict(values['laps'][0]))
    with pytest.raises(ValueError, match='Duplicate OpenF1 lap'):
        api.load_openf1_analysis(2026, 9, 'Q')


def test_published_race_grid_and_classification_do_not_invent_points(feeds):
    values, _ = feeds
    values['sessions'][0]['session_name'] = 'Race'
    values['session_result'][0].update(duration=6000, dnf=True)
    values['starting_grid'] = [{'driver_number': 2, 'position': 3}]
    results = api.load_openf1_analysis(2026, 9, 'R')['results']
    assert results.loc[0, 'grid'] == 3 and results.loc[0, 'status'] == 'Did not finish'
    assert results.points.isna().all()
    assert results[['Q1_seconds', 'Q2_seconds', 'Q3_seconds']].isna().all().all()


class Response:
    def __init__(self, data, status=200, headers=None):
        self.content = json.dumps(data).encode()
        self.status_code, self.headers = status, headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)
    def iter_content(self, chunk_size):
        yield self.content


@pytest.fixture
def network(monkeypatch, tmp_path):
    clock = {'now': 0, 'starts': [], 'waits': [], 'requests': []}
    def sleep(seconds):
        clock['waits'].append(seconds)
        clock['now'] += seconds
    monkeypatch.setattr(api, 'time', SimpleNamespace(time=time.time,
                        monotonic=lambda: clock['now'], sleep=sleep))
    monkeypatch.setattr(api, 'ROOT', tmp_path)
    monkeypatch.setattr(api, '_LAST_REQUEST', None)
    monkeypatch.setattr(api, '_READ_STATE', threading.local())
    monkeypatch.setattr(api, '_ACCESS_BLOCKED_UNTIL', 0)
    monkeypatch.setattr(api, '_ACCESS_TOKEN_KEY', None)
    monkeypatch.setattr(api, '_ACCESS_REASON', None)
    monkeypatch.delenv('OPENF1_ACCESS_TOKEN', raising=False)
    responses = []
    def get(url, **kwargs):
        clock['starts'].append(clock['now'])
        clock['requests'].append((url, kwargs))
        return responses.pop(0)
    monkeypatch.setattr(api.requests, 'get', get)
    return clock, responses, tmp_path


def test_fetch_caches_nonempty_data_and_preserves_source_timestamp(network):
    clock, responses, _ = network
    responses.extend([Response([{'driver_number': 1}]), Response([{'driver_number': 2}])])
    assert api.fetch('drivers', session_key=900) == [{'driver_number': 1}]
    retrieved = api.fetched_at('drivers', session_key=900)
    assert api.fetch('drivers', session_key=900) == [{'driver_number': 1}]
    assert api.fetched_at('drivers', session_key=900) == retrieved
    assert len(clock['starts']) == 1
    assert api.fetch('drivers', session_key=900, refresh=True) == [{'driver_number': 2}]
    assert clock['starts'] == [0, 2.1]


def test_fetch_does_not_cache_empty_or_error_responses(network):
    clock, responses, root = network
    responses.extend([Response([]), Response([], 503), Response([{'lap_number': 1}])])
    assert api.fetch('laps', session_key=900) == []
    assert not list(root.glob('cache/openf1/*.json'))
    with pytest.raises(requests.HTTPError):
        api.fetch('laps', session_key=900)
    assert api.fetch('laps', session_key=900) == [{'lap_number': 1}]
    assert clock['starts'] == [0, 2.1, 4.2]


def test_429_respects_retry_after_and_long_wait_is_not_retried_early(network):
    clock, responses, _ = network
    responses.extend([Response([], 429, {'Retry-After': '5'}), Response([{'ok': True}])])
    assert api.fetch('laps', session_key=900) == [{'ok': True}]
    assert clock['starts'] == [0, 5]
    responses.append(Response([], 429, {'Retry-After': '60'}))
    with pytest.raises(requests.HTTPError):
        api.fetch('laps', session_key=901)
    assert len(clock['starts']) == 3


def test_fetch_rejects_oversized_or_wrong_shape_json(network, monkeypatch):
    clock, responses, _ = network
    responses.extend([Response({'unexpected': 'object'}), Response([{'large': 'x' * 100}])])
    with pytest.raises(ValueError, match='Unexpected OpenF1 response'):
        api.fetch('laps', session_key=900)
    monkeypatch.setattr(api, 'MAX_RESPONSE_BYTES', 16)
    with pytest.raises(ValueError, match='too large'):
        api.fetch('laps', session_key=900)


LIVE_DENIAL = {'detail': 'Live F1 session in progress. Global API access (including past sessions) '
               'is restricted to authenticated users until the session ends.'}


def test_401_without_cache_is_typed_safe_and_stops_following_requests(network):
    clock, responses, _ = network
    responses.append(Response(LIVE_DENIAL, 401))
    with pytest.raises(api.OpenF1AccessError, match='including past sessions') as error:
        api.fetch('sessions', year=2026, refresh=True)
    assert error.value.access_reason == 'live_access_restricted'
    assert error.value.retry_after == 60
    assert api.get_source_access_status('sessions', year=2026)['state'] == 'access_limited'
    with pytest.raises(api.OpenF1AccessError):
        api.fetch('laps', session_key=900, refresh=True)
    assert len(clock['starts']) == 1 and clock['waits'] == []
    assert api.get_source_access_status()['retry_after'] == 60


def test_401_generic_body_does_not_claim_a_live_session_or_expose_details(network):
    _, responses, _ = network
    responses.append(Response({'detail': 'private credential value'}, 401))
    with pytest.raises(api.OpenF1AccessError, match='HTTP 401') as error:
        api.fetch('laps', session_key=900)
    assert error.value.access_reason == 'unauthorized'
    assert 'private credential' not in str(error.value)
    status = api.source_access_status('laps', session_key=900)
    assert status['access_reason'] == 'unauthorized'
    assert 'private credential' not in json.dumps(status)


def test_denied_refresh_uses_expired_saved_rows_without_rewriting_the_timestamp(network):
    clock, responses, root = network
    responses.extend([Response([{'lap_number': 1}]), Response(LIVE_DENIAL, 401)])
    assert api.fetch('laps', session_key=900) == [{'lap_number': 1}]
    retrieved = api.fetched_at('laps', session_key=900)
    path = next(root.glob('cache/openf1/*.json'))
    os.utime(path, (0, 0))
    content = path.read_bytes()
    assert api.fetch('laps', session_key=900, refresh=True) == [{'lap_number': 1}]
    assert api.fetched_at('laps', session_key=900) == retrieved
    assert path.read_bytes() == content and path.stat().st_mtime == 0
    status = api.get_source_access_status('laps', session_key=900)
    assert status['state'] == 'access_limited_cache'
    assert status['access_limited'] and status['cached_fallback']
    assert status['source_retrieved_utc'] == retrieved
    assert api.fetch('laps', session_key=900, refresh=True) == [{'lap_number': 1}]
    assert len(clock['starts']) == 2


@pytest.mark.parametrize('saved', [
    {'data': [], 'retrieved_utc': '2026-01-01T00:00:00+00:00'},
    {'data': {'invalid': 'object'}, 'retrieved_utc': '2026-01-01T00:00:00+00:00'},
    {'data': [42], 'retrieved_utc': '2026-01-01T00:00:00+00:00'},
    {'data': [{'lap_number': 1}], 'retrieved_utc': 'not a date'},
    {'data': [{'lap_number': 1}], 'retrieved_utc': '2026-01-01T00:00:00'},
    {'data': [{'lap_number': 1}], 'retrieved_utc': '2099-01-01T00:00:00+00:00'},
])
def test_denial_does_not_fall_back_to_invalid_or_empty_saved_response(network, saved):
    clock, responses, root = network
    path = root / 'cache' / 'openf1' / f'{api._key("laps", {"session_key": 900})}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved))
    responses.append(Response(LIVE_DENIAL, 401))
    with pytest.raises(api.OpenF1AccessError):
        api.fetch('laps', session_key=900, refresh=True)
    assert api.get_source_access_status('laps', session_key=900)['cached_fallback'] is False
    assert api.fetched_at('laps', session_key=900) is None


def test_access_guard_expires_and_a_success_clears_denial(network):
    clock, responses, _ = network
    responses.extend([Response(LIVE_DENIAL, 401), Response([{'lap_number': 1}])])
    with pytest.raises(api.OpenF1AccessError):
        api.fetch('laps', session_key=900)
    clock['now'] = 60
    assert api.fetch('laps', session_key=900) == [{'lap_number': 1}]
    assert clock['starts'] == [0, 60]
    assert not api.get_source_access_status()['access_limited']
    assert api.get_source_access_status('laps', session_key=900)['state'] == 'network'


def test_optional_server_token_is_only_sent_in_authorization_header(network, monkeypatch):
    clock, responses, root = network
    responses.extend([Response(LIVE_DENIAL, 401), Response([{'lap_number': 1}])])
    with pytest.raises(api.OpenF1AccessError):
        api.fetch('laps', session_key=900)
    monkeypatch.setenv('OPENF1_ACCESS_TOKEN', 'example.test.token')
    assert api.fetch('laps', session_key=900) == [{'lap_number': 1}]
    url, kwargs = clock['requests'][-1]
    assert kwargs['headers'] == {'Authorization': 'Bearer example.test.token'}
    assert kwargs['params'] == {'session_key': 900}
    assert 'example.test.token' not in url
    assert 'example.test.token' not in json.dumps(api.get_source_access_status('laps', session_key=900))
    assert 'example.test.token' not in next(root.glob('cache/openf1/*.json')).read_text()
    assert next(root.glob('cache/openf1/*.json')).name == f'{api._key("laps", {"session_key": 900})}.json'


def test_invalid_token_header_value_is_rejected_without_network_or_secret_echo(network, monkeypatch):
    clock, _, _ = network
    monkeypatch.setenv('OPENF1_ACCESS_TOKEN', 'private\ncredential')
    with pytest.raises(api.OpenF1AccessError, match='configured OpenF1 access token is invalid') as error:
        api.fetch('laps', session_key=900)
    assert 'private' not in str(error.value) and clock['starts'] == []


def test_optional_feeds_propagate_access_denial_and_stop_remaining_requests(feeds):
    values, calls = feeds
    values['stints'] = api.OpenF1AccessError(reason='live_access_restricted')
    with pytest.raises(api.OpenF1AccessError):
        api.load_openf1_analysis(2026, 9, 'Q')
    assert calls[-1][0] == 'stints'
    assert not any(name in ('pit', 'race_control') for name, _ in calls)


def test_analysis_marks_only_its_own_cached_fallback_and_preserves_retrieval_time(feeds):
    api._record_status(api._key('sessions', {'year': 2026}), 'access_limited_cache',
                       '2026-10-11T00:00:00+00:00', 'live_access_restricted')
    analysis = api.load_openf1_analysis(2026, 9, 'Q')
    metadata = analysis['metadata']
    assert metadata['access_limited'] and metadata['cached_fallback']
    assert metadata['access_reason'] == 'live_access_restricted'
    assert metadata['source_retrieved_utc'] == '2026-10-11T00:00:00+00:00'
    assert metadata['retrieved_utc'] != metadata['loaded_utc']
    api._read_statuses().clear()
    api._record_status(api._key('laps', {'session_key': 999}), 'access_limited_cache',
                       '2026-10-11T00:00:00+00:00', 'live_access_restricted')
    other = api.load_openf1_analysis(2026, 9, 'Q')['metadata']
    assert not other['access_limited'] and not other['cached_fallback']
    assert other['access_message'] is None


def test_cached_session_index_does_not_allow_a_future_session(feeds):
    values, calls = feeds
    values['sessions'][0]['date_end'] = '2099-01-01T00:00:00Z'
    api._record_status(api._key('sessions', {'year': 2026}), 'access_limited_cache',
                       '2026-10-11T00:00:00+00:00', 'live_access_restricted')
    with pytest.raises(api.SessionUnavailableError, match='not finished'):
        api.resolve_openf1_session(2026, 9, 'Q', refresh=True)
    assert [name for name, _ in calls] == ['sessions']


def test_concurrent_reads_keep_their_own_status_and_retrieval_time(network):
    clock, responses, _ = network
    responses.extend([Response([{'lap_number': 1}]), Response([{'lap_number': 2}]),
                      Response(LIVE_DENIAL, 401)])
    first_read, second_read = threading.Event(), threading.Event()
    def first():
        rows = api.fetch('laps', session_key=900)
        retrieved = api.fetched_at('laps', session_key=900)
        first_read.set()
        assert second_read.wait(3)
        return rows, retrieved, api.fetched_at('laps', session_key=900), api.get_source_access_status('laps', session_key=900)
    def second():
        assert first_read.wait(3)
        try:
            api.fetch('laps', session_key=900, refresh=True)
            rows = api.fetch('laps', session_key=900, refresh=True)
            return rows, api.fetched_at('laps', session_key=900), api.get_source_access_status('laps', session_key=900)
        finally:
            second_read.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(first), pool.submit(second)
        first_rows, original, final, first_status = a.result(timeout=5)
        second_rows, second_stamp, second_status = b.result(timeout=5)
    assert first_rows == [{'lap_number': 1}] and second_rows == [{'lap_number': 2}]
    assert first_status['state'] == 'network'
    assert second_status['state'] == 'access_limited_cache'
    assert original == final == first_status['source_retrieved_utc']
    assert second_stamp == second_status['source_retrieved_utc'] and second_stamp != original
    assert api.get_source_access_status('laps', session_key=900) == {}
    assert api.fetched_at('laps', session_key=900) is None
    assert len(clock['starts']) == 3
