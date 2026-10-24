from datetime import datetime, timezone
import gzip
import json
import os
import threading
import time

import pytest

from archive_source import ArchiveUnavailableError
from archive_replay import build_archive_replay, load_archive_replay


START = datetime(2026, 3, 8, 4, tzinfo=timezone.utc).timestamp()


def date(seconds):
    return datetime.fromtimestamp(START + seconds, timezone.utc).isoformat()


def archive(laps=2, code='R'):
    records = []
    for lap in range(1, laps + 1):
        start = (lap - 1) * 20
        records.append({'drv': 'AAA', 'dNum': '1', 'team': 'McLaren', 'lap': lap,
                        'lSD': date(start).replace('+00:00', ''), 'lST': 100 + start,
                        'sesT': 120 + start, 'time': 20, 'compound': 'MEDIUM', 'life': lap,
                        'stint': 1, 'pin': 'None', 'pout': 'None', 'pos': 1,
                        'del': False, 'iacc': True, 'ff1G': False, 'status': '1'})
    return {'year': 2026, 'scheduled': {'session_utc': date(-180), 'round': 1, 'code': code,
                                       'race': 'Test Grand Prix', 'location': 'Test Circuit'},
            'revision': '2026.1.123', 'published_utc': date(200), 'retrieved_utc': date(220),
            'source_url': 'https://github.com/TracingInsights/2026/tree/2026.1.123/Test/Race',
            'archive_event': 'Test Grand Prix', 'archive_session': 'Race',
            'drivers': [{'driver': 'AAA', 'dn': '1', 'fn': 'Test', 'ln': 'Driver',
                         'team': 'McLaren', 'tc': 'FF8700'}],
            'lap_columns': {key: [row[key] for row in records] for key in records[0]}}


def fetcher(info, path):
    if path == 'weather.json':
        return {'wT': [105], 'wAT': [25], 'wTT': [35], 'wH': [50], 'wP': [1000],
                'wR': [False], 'wWD': [100], 'wWS': [2]}, date(230)
    if path == 'rcm.json':
        return {'time': [date(7).replace('+00:00', '')], 'cat': ['Flag'], 'msg': ['YELLOW'],
                'flag': ['YELLOW'], 'scope': ['Track'], 'dNum': ['None']}, date(231)
    driver, filename = path.split('/')
    lap = int(filename.split('_')[0])
    times = [index / 4 for index in range(81)]
    return {'tel': {'dataKey': f'2026-{info["archive_event"]}-{info["archive_session"]}-{driver}-{lap}',
                    'time': times, 'x': [t + lap * 100 for t in times],
                    'y': [t * 2 for t in times], 'speed': [250] * len(times),
                    'gear': [7] * len(times), 'throttle': [50] * len(times),
                    'brake': [1] * len(times), 'drs': [1] * len(times)}}, date(240)


def build(info=None, fetch=fetcher, code='R', **kwargs):
    return build_archive_replay(info or archive(code=code), 2026, 1, code, fetch_file=fetch, **kwargs)


def test_archive_uses_real_utc_lap_clock_and_recorded_xy():
    payload = build()
    metadata = payload['metadata']
    assert metadata['start_utc'] == date(0)
    assert metadata['end_utc'] == date(40)
    assert payload['times'][0] == 0 and payload['times'][-1] == 40
    assert payload['cars']['AAA']['x'][5] == 105
    assert payload['cars']['AAA']['gear'][5] == 7
    assert payload['cars']['AAA']['brake'][5] == 100
    assert payload['cars']['AAA']['drs'][5] == 12
    assert metadata['provider'] == 'tracinginsights_archive'
    assert metadata['archived'] and metadata['archive_revision'] == '2026.1.123'
    assert metadata['cached_fallback'] is False
    assert 'OpenF1' not in metadata['source']
    assert metadata['source_retrieved_utc'] == date(220)
    assert metadata['revision'] == '2026.1.123'
    assert metadata['published_utc'] == date(200)
    json.dumps(payload, allow_nan=False)


def test_archive_fallback_metadata_and_unknown_drs_stay_explicit():
    info = archive()
    info['cached_fallback'] = True
    def unknown_drs(session, path):
        data, stamp = fetcher(session, path)
        if '_tel' in path:
            data['tel']['drs'] = [7] * len(data['tel']['drs'])
        return data, stamp
    payload = build(info, unknown_drs)
    assert payload['metadata']['cached_fallback'] is True
    assert all(value is None for value in payload['cars']['AAA']['drs'])


def test_positions_are_lap_end_checkpoints_without_invented_gaps():
    values = build()['cars']['AAA']
    assert values['position'][0] is None and values['position'][19] is None
    assert values['position'][20] == 1
    assert all(value is None for key in ('ahead', 'behind', 'leader') for value in values[key])


def test_missing_lap_remains_a_real_gap_without_reusing_another_lap():
    calls = []
    def missing(info, path):
        calls.append(path)
        if path == 'AAA/2_tel.json':
            raise ArchiveUnavailableError('not_published')
        return fetcher(info, path)
    payload = build(archive(laps=3), missing)
    assert payload['cars']['AAA']['x'][25:40] == [None] * 15
    assert payload['cars']['AAA']['x'][45] == 305
    assert payload['metadata']['missing_telemetry_laps'] == 1
    assert set(path for path in calls if '_tel' in path) == {'AAA/1_tel.json', 'AAA/2_tel.json', 'AAA/3_tel.json'}


@pytest.mark.parametrize('wrong', ['identity', 'alignment', 'clock'])
def test_wrong_telemetry_is_never_shown_as_this_lap(wrong):
    def bad(info, path):
        payload, stamp = fetcher(info, path)
        if '_tel' in path:
            if wrong == 'identity':
                payload['tel']['dataKey'] = '2025-Other Grand Prix-Race-AAA-1'
            elif wrong == 'alignment':
                payload['tel']['x'].pop()
            else:
                payload['tel']['time'][-1] = 999
        return payload, stamp
    with pytest.raises(ArchiveUnavailableError) as caught:
        build(fetch=bad)
    assert caught.value.reason == 'unavailable'


def test_tyre_and_completed_pit_visit_follow_recorded_entry_and_exit():
    info = archive()
    info['lap_columns']['pin'][0] = 119
    info['lap_columns']['pout'][1] = 125
    info['lap_columns']['compound'][1] = 'HARD'
    info['lap_columns']['life'][1] = 1
    values = build(info)['cars']['AAA']
    assert values['compound'][24] == 'MEDIUM'
    assert values['compound'][25] == 'HARD' and values['age'][25] == 0
    assert values['stops'][24] == 0 and values['in_pit'][24]
    assert values['stops'][25] == 1 and not values['in_pit'][25]
    assert values['last_stop_lap'][25] == 1


def test_initial_pit_exit_does_not_become_a_stop():
    info = archive()
    info['lap_columns']['pout'][0] = 102
    values = build(info)['cars']['AAA']
    assert values['stops'][3] == 0 and not values['in_pit'][3]


def test_weather_and_control_share_the_utc_clock():
    payload = build()
    assert payload['weather'][0]['t'] == 5
    assert payload['weather'][0]['air_temperature'] == 25
    assert payload['race_control'][0]['t'] == 7
    assert payload['race_control'][0]['message'] == 'YELLOW'


@pytest.mark.parametrize('code', ['FP1', 'FP2', 'FP3', 'Q', 'SQ', 'S', 'R'])
def test_all_session_types_use_the_same_recorded_timeline(code):
    payload = build(code=code)
    assert payload['metadata']['session_code'] == code
    assert payload['cars']['AAA']['x'][5] == 105
    if code not in ('R', 'S'):
        assert all(position is None for position in payload['cars']['AAA']['position'])
        assert payload['metadata']['position_definition'] == 'Session classification is unavailable from this archive.'


def test_clock_mismatch_or_bad_lap_schema_is_typed_unavailable():
    info = archive()
    info['lap_columns']['lST'][1] += 100
    with pytest.raises(ArchiveUnavailableError):
        build(info)
    info = archive()
    info['lap_columns']['drv'].pop()
    with pytest.raises(ArchiveUnavailableError):
        build(info)


def test_valid_recorded_lap_supplies_outline_and_invalid_lap_is_not_reference():
    payload = build()
    assert len(payload['track']['x']) >= 20
    info = archive()
    info['lap_columns']['del'] = [True, True]
    assert build(info)['track']['x'] == []


@pytest.mark.parametrize('reason', ['access_limited', 'unavailable'])
def test_systemic_failure_stops_bounded_workers_before_requesting_all_laps(reason):
    calls, lock = [], threading.Lock()
    def denied(info, path):
        if '_tel' not in path:
            return fetcher(info, path)
        with lock:
            calls.append(path)
        raise ArchiveUnavailableError(reason)
    with pytest.raises(ArchiveUnavailableError) as caught:
        build(archive(laps=40), denied, workers=4)
    assert caught.value.reason == reason
    assert len(calls) <= 4


def test_optional_systemic_failure_aborts_before_loading_lap_files():
    calls = []
    def unavailable(info, path):
        calls.append(path)
        raise ArchiveUnavailableError('unavailable')
    with pytest.raises(ArchiveUnavailableError) as caught:
        build(archive(laps=40), unavailable)
    assert caught.value.reason == 'unavailable'
    assert calls == ['weather.json']


def test_optional_missing_files_do_not_remove_available_telemetry():
    def missing(info, path):
        if '_tel' not in path:
            raise ArchiveUnavailableError('not_published')
        return fetcher(info, path)
    payload = build(fetch=missing)
    assert payload['cars']['AAA']['x'][5] == 105
    assert not payload['weather'] and not payload['race_control']


@pytest.mark.parametrize('reason', ['access_limited', 'unavailable'])
def test_valid_saved_archive_replay_survives_refresh_failure_without_new_timestamp(monkeypatch, tmp_path, reason):
    payload = build()
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    os.utime(path, (time.time() - 7200, time.time() - 7200))
    original, modified = path.read_bytes(), path.stat().st_mtime
    def denied(*args, **kwargs):
        raise ArchiveUnavailableError(reason)
    monkeypatch.setattr('archive_source.load_archive_session', denied)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    result = load_archive_replay(2026, 1, 'R', refresh=True)
    assert result['metadata']['cached_fallback']
    assert result['metadata']['access_limited'] is (reason == 'access_limited')
    assert result['metadata']['retrieved_utc'] == payload['metadata']['retrieved_utc']
    assert result['metadata']['source_retrieved_utc'] == payload['metadata']['source_retrieved_utc']
    assert path.read_bytes() == original and path.stat().st_mtime == modified


def test_different_provider_cache_cannot_be_used_for_archive_failure(monkeypatch, tmp_path):
    payload = build()
    payload['metadata']['provider'] = 'other'
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    def denied(*args, **kwargs):
        raise ArchiveUnavailableError('unavailable')
    monkeypatch.setattr('archive_source.load_archive_session', denied)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    with pytest.raises(ArchiveUnavailableError):
        load_archive_replay(2026, 1, 'R', refresh=True)


@pytest.mark.parametrize('refresh', [False, True])
def test_same_published_revision_reuses_aged_cache_despite_permanent_warnings(monkeypatch, tmp_path, refresh):
    payload = build()
    assert payload['metadata']['warnings']
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    os.utime(path, (time.time() - 7200, time.time() - 7200))
    original, modified, calls = path.read_bytes(), path.stat().st_mtime, []
    def metadata(*args, **kwargs):
        calls.append(kwargs)
        return archive()
    def unexpected(*args, **kwargs):
        raise AssertionError('An unchanged published version must not download lap telemetry again')
    monkeypatch.setattr('archive_source.load_archive_session', metadata)
    monkeypatch.setattr('archive_replay.build_archive_replay', unexpected)
    monkeypatch.setattr('archive_source.fetch_archive_file', unexpected)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    result = load_archive_replay(2026, 1, 'R', refresh=refresh)
    assert calls == [{'refresh': refresh}]
    assert result == payload
    assert path.read_bytes() == original and path.stat().st_mtime == modified


def test_new_published_revision_rebuilds_prepared_replay(monkeypatch, tmp_path):
    payload = build()
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    info, calls = archive(), []
    info['revision'] = '2026.1.124'
    def rebuilt(bundle, *args, **kwargs):
        calls.append(bundle['revision'])
        result = dict(payload, metadata=dict(payload['metadata']))
        result['metadata'].update(revision=bundle['revision'], archive_revision=bundle['revision'])
        return result
    monkeypatch.setattr('archive_source.load_archive_session', lambda *args, **kwargs: info)
    monkeypatch.setattr('archive_replay.build_archive_replay', rebuilt)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    result = load_archive_replay(2026, 1, 'R', refresh=True)
    assert calls == ['2026.1.124']
    assert result['metadata']['archive_revision'] == '2026.1.124'


@pytest.mark.parametrize('legacy', [False, True])
def test_unchanged_session_reuses_cache_across_archive_releases(monkeypatch, tmp_path, legacy):
    fingerprint = 'a' * 40
    payload = build()
    if not legacy:
        payload['metadata']['session_fingerprint'] = fingerprint
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    original_retrieved = payload['metadata']['source_retrieved_utc']
    info, checks = archive(), []
    info.update(revision='2026.1.124', session_fingerprint=fingerprint)
    def old_fingerprint(bundle):
        checks.append(bundle['revision'])
        return fingerprint
    def forbidden(*args, **kwargs):
        raise AssertionError('An unchanged session must not download telemetry again')
    monkeypatch.setattr('archive_source.load_archive_session', lambda *args, **kwargs: info)
    monkeypatch.setattr('archive_source.get_session_fingerprint', old_fingerprint)
    monkeypatch.setattr('archive_replay.build_archive_replay', forbidden)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    result = load_archive_replay(2026, 1, 'R', refresh=True)
    assert result['cars'] == payload['cars']
    assert result['metadata']['source_retrieved_utc'] == original_retrieved
    assert result['metadata']['retrieved_utc'] == payload['metadata']['retrieved_utc']
    assert result['metadata']['archive_revision'] == '2026.1.123'
    assert result['metadata']['checked_archive_revision'] == '2026.1.124'
    assert checks == (['2026.1.123'] if legacy else [])
    assert result['metadata']['session_fingerprint'] == fingerprint
    assert json.loads(gzip.decompress(path.read_bytes()))['metadata']['session_fingerprint'] == fingerprint


def test_changed_session_fingerprint_rebuilds_instead_of_using_stale_telemetry(monkeypatch, tmp_path):
    payload = build()
    payload['metadata']['session_fingerprint'] = 'a' * 40
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    info, calls = archive(), []
    info.update(revision='2026.1.124', session_fingerprint='b' * 40)
    def rebuilt(bundle, *args, **kwargs):
        calls.append(bundle['session_fingerprint'])
        result = dict(payload, metadata=dict(payload['metadata']))
        result['metadata'].update(archive_revision=bundle['revision'], session_fingerprint=bundle['session_fingerprint'])
        return result
    monkeypatch.setattr('archive_source.load_archive_session', lambda *args, **kwargs: info)
    monkeypatch.setattr('archive_replay.build_archive_replay', rebuilt)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    result = load_archive_replay(2026, 1, 'R', refresh=True)
    assert calls == ['b' * 40]
    assert result['metadata']['archive_revision'] == '2026.1.124'


def test_missing_session_fingerprint_preserves_revision_fallback(monkeypatch, tmp_path):
    payload = build()
    path = tmp_path / '2026_1_R_v1.json.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(payload, output, allow_nan=False)
    info = archive()
    info.update(revision='2026.1.124', session_fingerprint='b' * 40)
    calls = []
    monkeypatch.setattr('archive_source.load_archive_session', lambda *args, **kwargs: info)
    monkeypatch.setattr('archive_source.get_session_fingerprint', lambda *args, **kwargs: None)
    def rebuilt(*args, **kwargs):
        calls.append('rebuilt')
        return payload
    monkeypatch.setattr('archive_replay.build_archive_replay', rebuilt)
    monkeypatch.setattr('archive_replay.CACHE', tmp_path)
    load_archive_replay(2026, 1, 'R', refresh=True)
    assert calls == ['rebuilt']
