from datetime import datetime, timezone
import json
import gzip
import os
import time
from types import SimpleNamespace
import sys
from urllib.parse import parse_qsl

import pytest

from web_replay import BrowserReplayUnavailable, _locations, _thin_records, build_browser_replay, load_browser_replay


class AccessError(ValueError):
    pass


START = '2026-03-08T04:00:00+00:00'


def date(seconds):
    return datetime.fromtimestamp(datetime.fromisoformat(START).timestamp() + seconds, timezone.utc).isoformat()


def replay(code='R', duration=20, **kwargs):
    session = {'date_start': START, 'date_end': date(duration), 'session_key': 123,
               'year': 2026, 'round': 1, 'code': code, 'location': 'Melbourne', 'session_name': 'Race'}
    drivers = [{'driver_number': 1, 'name_acronym': 'AAA', 'team_colour': '123456'},
               {'driver_number': 2, 'name_acronym': 'BBB', 'team_colour': '654321'}]
    locations = {1: [{'date': date(t), 'x': t, 'y': 2 * t} for t in range(duration + 1)],
                 2: [{'date': date(t), 'x': t + 2, 'y': t} for t in range(duration + 1)]}
    defaults = {'laps': [], 'locations': locations}
    defaults.update(kwargs)
    return build_browser_replay(session, drivers, **defaults)


def test_real_positions_share_one_clock_and_missing_gaps_are_not_extrapolated():
    payload = replay(locations={1: [{'date': date(t), 'x': t * 10, 'y': 50} for t in [1, 3, 10, 11]]})
    assert payload['cars']['AAA']['x'][:5] == [None, 10, 20, 30, None]
    assert payload['cars']['AAA']['x'][9:13] == [None, 100, 110, None]
    assert payload['unavailable'] == ['BBB']
    assert payload['metadata']['is_live'] is False
    json.dumps(payload, allow_nan=False)


def test_recorded_race_positions_and_neighbor_intervals_expire_after_ten_seconds():
    positions = [{'date': date(0), 'driver_number': 1, 'position': 2},
                 {'date': date(0), 'driver_number': 2, 'position': 1}]
    intervals = [{'date': date(2), 'driver_number': 1, 'interval': 1.25, 'gap_to_leader': '+1 LAP'}]
    payload = replay(positions=positions, intervals=intervals)
    a, b = payload['cars']['AAA'], payload['cars']['BBB']
    assert a['position'][0] == 2 and b['position'][0] == 1
    assert a['ahead'][1] is None
    assert a['ahead'][2] == 1.25 and b['behind'][2] == 1.25
    assert a['leader'][2] == '+1 LAP'
    assert a['ahead'][12] == 1.25 and a['ahead'][13] is None
    assert b['behind'][13] is None


@pytest.mark.parametrize('code', ['FP1', 'FP2', 'FP3', 'Q', 'SQ'])
def test_practice_and_qualifying_replay_recorded_clock_without_race_gaps(code):
    payload = replay(code, intervals=[{'date': date(0), 'driver_number': 1, 'interval': 4}])
    assert payload['metadata']['session_code'] == code
    assert 'classification' in payload['metadata']['position_definition']
    assert all(value is None for value in payload['cars']['AAA']['ahead'])
    assert payload['cars']['AAA']['x'][5] == 5


def test_tyres_and_age_follow_stint_start_and_completed_laps_only():
    laps = [{'driver_number': 1, 'lap_number': 1, 'date_start': date(0), 'lap_duration': 5},
            {'driver_number': 1, 'lap_number': 2, 'date_start': date(5), 'lap_duration': 5},
            {'driver_number': 1, 'lap_number': 3, 'date_start': date(10), 'lap_duration': 5}]
    stints = [{'driver_number': 1, 'lap_start': 1, 'compound': 'MEDIUM', 'tyre_age_at_start': 3},
              {'driver_number': 1, 'lap_start': 3, 'compound': 'HARD', 'tyre_age_at_start': 0}]
    a = replay(laps=laps, stints=stints)['cars']['AAA']
    assert a['compound'][0] == 'MEDIUM' and a['age'][0] == 3
    assert a['age'][4] == 3 and a['age'][5] == 4
    assert a['compound'][9] == 'MEDIUM'
    assert a['compound'][10] == 'HARD' and a['age'][10] == 0
    assert a['age'][15] == 1


def test_pit_counts_use_reported_dates_and_deduplicate_same_lap():
    pits = [{'driver_number': 1, 'lap_number': 2, 'date': date(7), 'lane_duration': 20},
            {'driver_number': 1, 'lap_number': 2, 'date': date(8), 'lane_duration': 20},
            {'driver_number': 1, 'lap_number': 4, 'date': date(15), 'lane_duration': 22}]
    payload = replay(pit=pits)
    a = payload['cars']['AAA']
    assert a['stops'][6] == 0 and a['stops'][7] == 1
    assert a['stops'][14] == 1 and a['stops'][15] == 2
    assert a['last_stop_lap'][15] == 4
    assert a['in_pit'][7] is None
    assert 'source timestamps' in payload['metadata']['pit_definition']
    assert replay(pit_available=False)['cars']['AAA']['stops'][0] is None


def test_car_telemetry_is_asof_and_stale_samples_become_missing():
    telemetry = {1: [{'date': date(2), 'speed': 255, 'n_gear': 7, 'throttle': 0, 'brake': 100, 'drs': 12}]}
    a = replay(car_data=telemetry)['cars']['AAA']
    assert a['speed'][1] is None and a['speed'][2] == 255
    assert a['gear'][2] == 7 and a['throttle'][2] == 0 and a['brake'][2] == 100
    assert a['speed'][5] == 255 and a['speed'][6] is None


def test_complete_non_pit_lap_produces_track_and_all_arrays_have_equal_lengths():
    laps = [{'driver_number': 1, 'lap_number': 1, 'date_start': date(0),
             'lap_duration': 20, 'is_pit_out_lap': False}]
    payload = replay(laps=laps)
    assert len(payload['track']['x']) == 21
    assert payload['track']['driver_number'] == 1
    assert all(len(values) == len(payload['times']) for car in payload['cars'].values() for values in car.values())
    assert all(value is None for value in payload['cars']['AAA']['leader'])


def test_duplicate_position_ranks_do_not_invent_driver_behind():
    payload = replay(positions=[{'date': date(0), 'driver_number': 1, 'position': 1},
                                {'date': date(0), 'driver_number': 2, 'position': 1}],
                     intervals=[{'date': date(0), 'driver_number': 2, 'interval': 2}])
    assert payload['cars']['AAA']['behind'][0] is None


def test_interval_from_before_position_change_is_not_assigned_to_new_neighbor():
    payload = replay(positions=[{'date': date(0), 'driver_number': 1, 'position': 1},
                                {'date': date(0), 'driver_number': 2, 'position': 2},
                                {'date': date(5), 'driver_number': 1, 'position': 2},
                                {'date': date(5), 'driver_number': 2, 'position': 1}],
                     intervals=[{'date': date(4), 'driver_number': 1, 'interval': 1},
                                {'date': date(7), 'driver_number': 1, 'interval': .7}])
    assert payload['cars']['AAA']['ahead'][5] is None
    assert payload['cars']['BBB']['behind'][5] is None
    assert payload['cars']['AAA']['ahead'][7] == .7
    assert payload['cars']['BBB']['behind'][7] == .7


def test_adaptive_sampling_caps_payload_and_does_not_make_up_location_coverage():
    payload = replay(duration=100, max_frames=8)
    assert len(payload['times']) <= 8
    assert payload['times'][0] == 0 and payload['times'][-1] == 100
    assert payload['metadata']['sample_seconds'] > 1
    with pytest.raises(BrowserReplayUnavailable, match='locations'):
        replay(locations={})


def test_weather_and_race_control_use_source_utc_on_same_clock():
    payload = replay(weather=[{'date': date(3), 'air_temperature': 25}],
                     race_control=[{'date': date(4), 'message': 'YELLOW', 'flag': 'YELLOW'},
                                   {'date': date(21), 'message': 'future'}])
    assert payload['weather'] == [{'air_temperature': 25, 't': 3}]
    assert payload['race_control'] == [{'message': 'YELLOW', 'flag': 'YELLOW', 't': 4}]


def test_thinning_retains_source_samples_and_long_gap_boundaries():
    rows = [{'date': date(t), 'x': t, 'y': 10, 'extra': 'not needed'}
            for t in [0, .2, .7, 1, 1.2, 1.8, 8.2, 8.4, 9]]
    thinned = _thin_records(rows, ('x', 'y'))
    assert all('extra' not in row for row in thinned)
    assert len(thinned) < len(rows)
    assert {row['x'] for row in thinned} >= {0, 1.8, 8.2, 9}
    xs, _ = _locations(thinned, datetime.fromisoformat(START).timestamp(), list(range(10)))
    assert xs[3:8] == [None] * 5


def test_loader_uses_paced_helper_with_driver_time_bounds_and_serializable_cache(monkeypatch, tmp_path):
    session = {'date_start': START, 'date_end': date(20), 'session_key': 123,
               'session_name': 'Practice 1', 'location': 'Melbourne'}
    calls = []
    def fetch(endpoint, **params):
        calls.append((endpoint, params))
        if endpoint == 'drivers':
            return [{'driver_number': 1, 'name_acronym': 'AAA', 'team_name': 'Red Bull Racing'}]
        if endpoint == 'location':
            return [{'date': date(t), 'x': t, 'y': t} for t in range(21)]
        return []
    monkeypatch.setitem(sys.modules, 'openf1_data', SimpleNamespace(fetch=fetch, OpenF1AccessError=AccessError,
        get_source_access_status=lambda *args, **kwargs: {},
        fetched_at=lambda *args, **kwargs: date(30),
        resolve_openf1_session=lambda *args, **kwargs: session))
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    payload = load_browser_replay(2026, 1, 'FP1', refresh=True)
    assert payload['drivers'][0]['team'] == 'Red Bull'
    assert payload['metadata']['source_retrieved_utc'] == date(30)
    for endpoint, params in calls:
        assert params['session_key'] == 123
        if endpoint in ('location', 'car_data'):
            assert params['driver_number'] == 1
            assert params['date>'] == START and params['date<'] == date(20)
    assert not any(endpoint == 'intervals' for endpoint, _ in calls)
    calls.clear()
    assert load_browser_replay(2026, 1, 'FP1') == payload
    assert not calls


def test_loader_optional_feed_failure_remains_visible(monkeypatch, tmp_path):
    import requests
    def fetch(endpoint, **params):
        if endpoint == 'drivers':
            return [{'driver_number': 1, 'name_acronym': 'AAA'}]
        if endpoint == 'location':
            return [{'date': date(t), 'x': t, 'y': t} for t in range(21)]
        if endpoint == 'pit':
            raise requests.HTTPError('source unavailable')
        return []
    monkeypatch.setitem(sys.modules, 'openf1_data', SimpleNamespace(fetch=fetch, OpenF1AccessError=AccessError,
        get_source_access_status=lambda *args, **kwargs: {},
        fetched_at=lambda *args, **kwargs: date(30),
        resolve_openf1_session=lambda *args, **kwargs: {'date_start': START, 'date_end': date(20), 'session_key': 123}))
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    payload = load_browser_replay(2026, 1, 'R')
    assert payload['cars']['AAA']['stops'][0] is None
    assert any('pit feed unavailable' in warning for warning in payload['metadata']['warnings'])


def test_requests_date_bounds_match_openf1_filter_parser_without_extra_equals():
    import requests
    query = requests.Request('GET', 'https://api.openf1.org/v1/location',
        params={'date>': START, 'date<': date(20)}).prepare().url.split('?', 1)[1]
    filters = [f'{key}={value}' for key, value in parse_qsl(query)]
    assert filters == ['date>=' + START, 'date<=' + date(20)]
    assert '==' not in ''.join(filters)


def _saved_replay(tmp_path, payload, age=7200):
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def _denied_adapter(monkeypatch, fetch=None, resolver=None, status=None):
    def denied(*args, **kwargs):
        raise AccessError('OpenF1 currently restricts public access')
    monkeypatch.setitem(sys.modules, 'openf1_data', SimpleNamespace(
        OpenF1AccessError=AccessError, fetch=fetch or denied,
        resolve_openf1_session=resolver or denied,
        fetched_at=lambda *args, **kwargs: date(30),
        get_source_access_status=lambda *args, **kwargs: status or {}))


@pytest.mark.parametrize('refresh', [False, True])
def test_access_denial_uses_expired_completed_replay_without_changing_source_times(monkeypatch, tmp_path, refresh):
    saved = replay()
    saved['metadata']['retrieved_utc'] = date(30)
    saved['metadata']['source_retrieved_utc'] = date(25)
    saved['metadata']['feed_retrieved_utc'] = {'location': date(25)}
    path = _saved_replay(tmp_path, saved)
    before, modified = path.read_bytes(), path.stat().st_mtime
    _denied_adapter(monkeypatch)
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    payload = load_browser_replay(2026, 1, 'R', refresh=refresh)
    assert payload['cars'] == saved['cars']
    assert payload['metadata']['access_limited'] and payload['metadata']['cached_fallback']
    assert payload['metadata']['retrieved_utc'] == date(30)
    assert payload['metadata']['source_retrieved_utc'] == date(25)
    assert payload['metadata']['feed_retrieved_utc'] == {'location': date(25)}
    assert any('previously saved completed replay' in warning for warning in payload['metadata']['warnings'])
    assert path.read_bytes() == before and path.stat().st_mtime == modified


def test_uncached_auth_denial_in_optional_feed_stops_before_subsequent_requests(monkeypatch, tmp_path):
    calls = []
    def fetch(endpoint, **params):
        calls.append(endpoint)
        if endpoint == 'drivers':
            return [{'driver_number': 1, 'name_acronym': 'AAA'}]
        if endpoint == 'laps':
            return []
        raise AccessError('OpenF1 currently restricts public access')
    _denied_adapter(monkeypatch, fetch=fetch, resolver=lambda *args, **kwargs: {
        'date_start': START, 'date_end': date(20), 'session_key': 123})
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    with pytest.raises(AccessError):
        load_browser_replay(2026, 1, 'R')
    assert calls == ['drivers', 'laps', 'position']
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('invalid', ['future', 'session', 'schema', 'live', 'coordinates', 'shape'])
def test_access_denial_does_not_fall_back_to_invalid_saved_payload(monkeypatch, tmp_path, invalid):
    payload = replay()
    if invalid == 'future':
        payload['metadata']['end_utc'] = '2999-01-01T00:00:00+00:00'
    elif invalid == 'session':
        payload['metadata']['session_code'] = 'S'
    elif invalid == 'schema':
        payload['metadata']['schema_version'] = 0
    elif invalid == 'live':
        payload['metadata']['is_live'] = True
    elif invalid == 'coordinates':
        for car in payload['cars'].values():
            car['x'] = [None] * len(payload['times'])
    else:
        payload['cars']['AAA']['x'] = []
    _saved_replay(tmp_path, payload)
    _denied_adapter(monkeypatch)
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    with pytest.raises(AccessError):
        load_browser_replay(2026, 1, 'R')


def test_unrelated_network_error_does_not_silently_use_expired_replay(monkeypatch, tmp_path):
    import requests
    _saved_replay(tmp_path, replay())
    def offline(*args, **kwargs):
        raise requests.ConnectionError('network unavailable')
    _denied_adapter(monkeypatch, resolver=offline)
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    with pytest.raises(requests.ConnectionError):
        load_browser_replay(2026, 1, 'R')


def test_source_rows_from_access_limited_cache_flag_whole_new_payload(monkeypatch, tmp_path):
    def fetch(endpoint, **params):
        if endpoint == 'drivers':
            return [{'driver_number': 1, 'name_acronym': 'AAA'}]
        if endpoint == 'location':
            return [{'date': date(t), 'x': t, 'y': t} for t in range(21)]
        return []
    _denied_adapter(monkeypatch, fetch=fetch, resolver=lambda *args, **kwargs: {
        'date_start': START, 'date_end': date(20), 'session_key': 123},
        status={'access_limited': True, 'cached_fallback': True, 'state': 'access_limited_cache'})
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    payload = load_browser_replay(2026, 1, 'R')
    assert payload['metadata']['access_limited'] and payload['metadata']['cached_fallback']
    assert 'location' in payload['metadata']['access_limited_feeds']
    assert payload['metadata']['source_retrieved_utc'] == date(30)
    assert any('saved source data' in warning for warning in payload['metadata']['warnings'])


def test_access_limited_payload_has_short_cache_lifetime(monkeypatch, tmp_path):
    payload = replay()
    payload['metadata'].update(access_limited=True, cached_fallback=True, warnings=[])
    _saved_replay(tmp_path, payload, age=70)
    calls = []
    def denied(*args, **kwargs):
        calls.append('resolve')
        raise AccessError('access restricted')
    _denied_adapter(monkeypatch, resolver=denied)
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    assert load_browser_replay(2026, 1, 'R')['metadata']['access_limited']
    assert calls == ['resolve']


@pytest.mark.parametrize('invalid', ['missing', 'naive', 'future', 'before_session_end',
                                    'future_source', 'future_feed', 'source_after_feed', 'nan', 'overflow'])
def test_auth_fallback_requires_finite_payload_and_consistent_original_retrieval_times(monkeypatch, tmp_path, invalid):
    payload = replay()
    payload['metadata']['retrieved_utc'] = date(30)
    payload['metadata']['source_retrieved_utc'] = date(25)
    payload['metadata']['feed_retrieved_utc'] = {'location': date(25)}
    if invalid == 'missing':
        del payload['metadata']['retrieved_utc']
    elif invalid == 'naive':
        payload['metadata']['retrieved_utc'] = '2026-03-08T04:00:30'
    elif invalid == 'future':
        payload['metadata']['retrieved_utc'] = '2999-01-01T00:00:00+00:00'
    elif invalid == 'before_session_end':
        payload['metadata']['retrieved_utc'] = date(10)
    elif invalid == 'future_source':
        payload['metadata']['source_retrieved_utc'] = date(31)
    elif invalid == 'future_feed':
        payload['metadata']['feed_retrieved_utc']['location'] = date(31)
    elif invalid == 'source_after_feed':
        payload['metadata']['feed_retrieved_utc']['location'] = date(24)
    if invalid in ('nan', 'overflow'):
        encoded = json.dumps(payload).replace('"x": [0.0,', '"x": [NaN,' if invalid == 'nan' else '"x": [1e999,', 1)
        assert 'NaN' in encoded or '1e999' in encoded
        path = tmp_path / '2026_1_R_v1.json.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as output:
            output.write(encoded)
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    else:
        _saved_replay(tmp_path, payload)
    _denied_adapter(monkeypatch)
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    with pytest.raises(AccessError):
        load_browser_replay(2026, 1, 'R')


def test_auth_fallback_preserves_aware_non_utc_source_time_strings_and_confirmed_reason(monkeypatch, tmp_path):
    payload = replay()
    payload['metadata']['retrieved_utc'] = '2026-03-08T06:00:30+02:00'
    payload['metadata']['source_retrieved_utc'] = '2026-03-08T06:00:25+02:00'
    payload['metadata']['feed_retrieved_utc'] = {'location': '2026-03-08T04:00:25Z'}
    _saved_replay(tmp_path, payload)
    def denied(*args, **kwargs):
        error = AccessError('OpenF1 temporarily restricts all API access during a live session.')
        error.access_reason = 'live_access_restricted'
        raise error
    _denied_adapter(monkeypatch, resolver=denied)
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    result = load_browser_replay(2026, 1, 'R')
    assert result['metadata']['retrieved_utc'] == payload['metadata']['retrieved_utc']
    assert result['metadata']['source_retrieved_utc'] == payload['metadata']['source_retrieved_utc']
    assert result['metadata']['feed_retrieved_utc'] == payload['metadata']['feed_retrieved_utc']
    assert result['metadata']['access_reason'] == 'live_access_restricted'
    assert 'all API access' in result['metadata']['access_message']


def test_resolver_query_provenance_and_access_status_flag_rebuilt_payload(monkeypatch, tmp_path):
    status_calls, stamp_calls = [], []
    def fetch(endpoint, **params):
        if endpoint == 'drivers':
            return [{'driver_number': 1, 'name_acronym': 'AAA'}]
        if endpoint == 'location':
            return [{'date': date(t), 'x': t, 'y': t} for t in range(21)]
        return []
    def status(endpoint, **params):
        status_calls.append((endpoint, params))
        if endpoint == 'sessions':
            return {'access_limited': True, 'cached_fallback': True,
                    'access_reason': 'live_access_restricted',
                    'access_message': 'OpenF1 restricts past sessions during its live window.'}
        return {}
    def stamp(endpoint, **params):
        stamp_calls.append((endpoint, params))
        return '2026-03-08T06:00:10+02:00' if endpoint == 'sessions' else date(30)
    _denied_adapter(monkeypatch, fetch=fetch, resolver=lambda *args, **kwargs: {
        'date_start': START, 'date_end': date(20), 'session_key': 123})
    sys.modules['openf1_data'].get_source_access_status = status
    sys.modules['openf1_data'].fetched_at = stamp
    monkeypatch.setattr('web_replay.CACHE', tmp_path)
    payload = load_browser_replay(2026, 1, 'R')
    for calls in (status_calls, stamp_calls):
        assert ('sessions', {'year': 2026}) in calls
        assert ('session_result', {'session_key': 123}) in calls
        assert not any(endpoint == 'sessions' and 'session_key' in params for endpoint, params in calls)
    assert payload['metadata']['access_limited_feeds'] == ['sessions']
    assert payload['metadata']['access_reason'] == 'live_access_restricted'
    assert 'past sessions' in payload['metadata']['access_message']
    assert payload['metadata']['source_retrieved_utc'] == '2026-03-08T06:00:10+02:00'
    assert payload['metadata']['feed_retrieved_utc']['sessions'] == '2026-03-08T06:00:10+02:00'
