from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
import time

import pandas as pd
import pytest
import requests

import archive_source as archive


class Response:
    def __init__(self, data=None, status=200, *, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = json.dumps(data).encode() if body is None else body
        self.closed = False

    def iter_content(self, size):
        for index in range(0, len(self.body), size):
            yield self.body[index:index + size]

    def close(self):
        self.closed = True


@pytest.fixture
def feeds(monkeypatch, tmp_path):
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    start = pd.Timestamp('2026-10-24T17:30:00Z')
    scheduled = {'round': 18, 'race': 'United States Grand Prix', 'location': 'Austin',
                 'code': 'FP3', 'session_utc': start}
    drivers = [
        {'driver': 'AAA', 'dn': 1, 'team': 'Ferrari', 'fn': 'Driver', 'ln': 'A', 'tc': 'FF0000'},
        {'driver': 'BBB', 'dn': 2, 'team': 'Mercedes', 'fn': 'Driver', 'ln': 'B', 'tc': '00FF00'},
    ]
    laps = {'drv': ['AAA', 'AAA', 'BBB'], 'dNum': ['1', '1', '2'],
            'team': ['Ferrari', 'Ferrari', 'Mercedes'], 'lap': [1, 2, 1], 'time': [90, 91, 92],
            'lST': [120, 210, 120], 'sesT': [210, 301, 212],
            'lSD': ['2026-10-24T17:32:00.000000000', '2026-10-24T17:33:30Z', '2026-10-24T17:32:00Z']}
    release = {'tag_name': '2026.297.2', 'published_at': '2026-10-24T19:00:00Z',
               'draft': False, 'prerelease': False}
    responses = {'release': release, 'drivers.json': {'drivers': drivers}, 'session_laptimes.json': laps,
                 'contents': [{'name': 'Practice 3', 'path': 'United States Grand Prix/Practice 3',
                               'type': 'dir', 'sha': 'a' * 40}]}
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        key = ('release' if url == archive.RELEASE_URL else 'contents'
               if url.startswith(archive.CONTENTS_BASE) else url.rsplit('/', 1)[-1])
        data = responses[key]
        if isinstance(data, BaseException):
            raise data
        return data if isinstance(data, Response) else Response(data)

    monkeypatch.setattr(archive, 'ROOT', tmp_path)
    monkeypatch.setattr(archive, '_now', lambda: now)
    monkeypatch.setattr(archive, '_BLOCKED_UNTIL', 0)
    monkeypatch.setattr(archive, 'resolve_session', lambda *args, **kwargs: scheduled)
    monkeypatch.setattr(archive.requests, 'get', get)
    return responses, calls, scheduled, now


def test_loads_one_pinned_release_and_preserves_original_cache_times(feeds, monkeypatch):
    _, calls, _, now = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert session['revision'] == '2026.297.2'
    assert session['published_utc'] == '2026-10-24T19:00:00+00:00'
    assert session['retrieved_utc'] == now.isoformat()
    assert session['archive_event'] == 'United States Grand Prix'
    assert session['archive_session'] == 'Practice 3'
    assert session['session_fingerprint'] == 'a' * 40
    assert session['drivers'][0]['driver'] == 'AAA'
    assert session['lap_columns']['drv'] == ['AAA', 'AAA', 'BBB']
    assert '/tree/2026.297.2/United%20States%20Grand%20Prix/Practice%203' in session['source_url']
    assert len(calls) == 4
    for url, options in calls:
        assert options['timeout'] == (5, 30) and options['stream']
        assert options['allow_redirects'] is False
        assert options['auth'] is archive._PUBLIC_AUTH
        assert 'Authorization' not in options['headers']
        assert ('@2026.297.2/' in url or url == archive.RELEASE_URL
                or url == archive.CONTENTS_BASE + 'United%20States%20Grand%20Prix?ref=2026.297.2')
    monkeypatch.setattr(archive, '_now', lambda: now + timedelta(minutes=10))
    cached = archive.load_archive_session(2026, 18, 'FP3')
    assert len(calls) == 4
    assert cached['retrieved_utc'] == session['retrieved_utc']
    assert not cached['cached_fallback']


def test_refresh_updates_release_but_reuses_immutable_files(feeds, monkeypatch):
    _, calls, _, now = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    monkeypatch.setattr(archive, '_now', lambda: now + timedelta(hours=1))
    refreshed = archive.load_archive_session(2026, 18, 'FP3', refresh=True)
    assert len(calls) == 5
    assert refreshed['retrieved_utc'] == session['retrieved_utc']
    assert not refreshed['cached_fallback']


def test_saved_release_remains_usable_during_temporary_denial(feeds, monkeypatch):
    responses, calls, _, now = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    responses['release'] = Response(status=403)
    monkeypatch.setattr(archive, '_now', lambda: now + timedelta(hours=1))
    cached = archive.load_archive_session(2026, 18, 'FP3', refresh=True)
    assert cached['retrieved_utc'] == session['retrieved_utc']
    assert cached['cached_fallback']
    assert len(calls) == 5


@pytest.mark.parametrize('status', [401, 403, 429])
def test_access_denials_stop_following_requests_without_exposing_provider_body(feeds, status):
    responses, calls, _, _ = feeds
    response = Response(status=status, body=b'private response with https://secret.example/token=abc')
    responses['release'] = response
    for _ in range(2):
        with pytest.raises(archive.ArchiveUnavailableError) as caught:
            archive.load_archive_session(2026, 18, 'FP3')
        assert caught.value.reason == 'access_limited'
        assert 'secret' not in str(caught.value) and 'token=' not in str(caught.value)
    assert len(calls) == 1 and response.closed


def test_one_missing_file_does_not_block_other_files(feeds):
    responses, calls, _, _ = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    responses['1_tel.json'] = Response(status=404)
    responses['weather.json'] = {'wT': [10], 'wAT': [22]}
    with pytest.raises(archive.ArchiveUnavailableError) as caught:
        archive.fetch_archive_file(session, 'AAA/1_tel.json')
    assert caught.value.reason == 'not_published'
    weather, _ = archive.fetch_archive_file(session, 'weather.json')
    assert weather['wAT'] == [22]
    assert len(calls) == 6


@pytest.mark.parametrize('response', [
    Response(status=302, headers={'Location': 'https://secret.example'}),
    Response(body=b'NaN'), Response(body=b'not json'), Response(data='scalar'),
    Response(data={}, headers={'Content-Length': '99999999'}),
    Response(data={}, headers={'Content-Length': 'not a number'}),
])
def test_bad_responses_are_safe_and_not_cached(feeds, response):
    responses, _, _, _ = feeds
    responses['release'] = response
    with pytest.raises(archive.ArchiveUnavailableError) as caught:
        archive.load_archive_session(2026, 18, 'FP3')
    assert caught.value.reason == 'unavailable'
    assert response.closed


def test_streaming_limit_is_enforced_without_content_length(feeds, monkeypatch):
    responses, _, _, _ = feeds
    responses['release'] = Response(body=b'x' * 30)
    with pytest.raises(archive.ArchiveUnavailableError):
        archive._request_json(archive.RELEASE_URL, 20)


@pytest.mark.parametrize('field,value', [
    ('tag_name', 'main'), ('tag_name', '../secret'), ('tag_name', '2025.297.2'),
    ('draft', True), ('prerelease', True), ('published_at', '2026-10-26T00:00:00Z'),
])
def test_invalid_release_is_rejected_before_session_files(feeds, field, value):
    responses, calls, _, _ = feeds
    responses['release'][field] = value
    with pytest.raises(archive.ArchiveUnavailableError):
        archive.load_archive_session(2026, 18, 'FP3')
    assert len(calls) == 1


def test_session_not_yet_in_release_is_not_published(feeds):
    responses, calls, _, _ = feeds
    responses['release']['published_at'] = '2026-10-24T16:00:00Z'
    with pytest.raises(archive.ArchiveUnavailableError) as caught:
        archive.load_archive_session(2026, 18, 'FP3')
    assert caught.value.reason == 'not_published' and len(calls) == 1


def test_future_session_is_rejected_before_any_http(feeds):
    _, calls, scheduled, _ = feeds
    scheduled['session_utc'] = pd.Timestamp('2026-10-26T00:00:00Z')
    with pytest.raises(archive.ArchiveUnavailableError):
        archive.load_archive_session(2026, 18, 'FP3')
    assert not calls


def test_schedule_network_failure_is_public_safe(feeds, monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError('https://private.example/token=secret')

    monkeypatch.setattr(archive, 'resolve_session', unavailable)
    with pytest.raises(archive.ArchiveUnavailableError) as caught:
        archive.load_archive_session(2026, 18, 'FP3')
    assert caught.value.reason == 'unavailable'
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize('year,round_number,code', [(2025, 18, 'R'), (2026, 0, 'R'), (2026, 18, 'bad')])
def test_unsupported_scope_is_rejected_without_http(feeds, year, round_number, code):
    _, calls, _, _ = feeds
    with pytest.raises(archive.ArchiveUnavailableError):
        archive.load_archive_session(year, round_number, code)
    assert not calls


def test_barcelona_alias_does_not_change_madrid_event(feeds):
    _, _, scheduled, _ = feeds
    scheduled.update(race='Spanish Grand Prix', location='Barcelona')
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert session['archive_event'] == 'Barcelona Grand Prix'
    scheduled['location'] = 'Madrid'
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert session['archive_event'] == 'Spanish Grand Prix'


def test_unicode_event_name_is_preserved(feeds):
    _, _, scheduled, _ = feeds
    scheduled.update(race='São Paulo Grand Prix', location='São Paulo')
    assert archive.load_archive_session(2026, 18, 'FP3')['archive_event'] == 'São Paulo Grand Prix'


@pytest.mark.parametrize('change', [
    lambda data: data['drivers.json']['drivers'].append(dict(data['drivers.json']['drivers'][0])),
    lambda data: data['drivers.json']['drivers'][0].update(dn=2),
    lambda data: data['drivers.json']['drivers'][0].update(driver='../secret'),
    lambda data: data['session_laptimes.json']['lap'].pop(),
    lambda data: data['session_laptimes.json']['drv'].__setitem__(0, 'CCC'),
    lambda data: data['session_laptimes.json']['dNum'].__setitem__(0, '2'),
    lambda data: data['session_laptimes.json']['lap'].__setitem__(1, 1),
    lambda data: data['session_laptimes.json']['lSD'].__setitem__(0, '2026-09-01T00:00:00Z'),
    lambda data: data['session_laptimes.json']['lSD'].__setitem__(0, '2099-01-01T00:00:00Z'),
    lambda data: data['session_laptimes.json'].update(lSD=['None'] * 3),
    lambda data: data['session_laptimes.json']['sesT'].__setitem__(0, 100),
])
def test_malformed_or_wrong_session_data_cannot_be_returned(feeds, change):
    responses, _, _, _ = feeds
    change(responses)
    with pytest.raises(archive.ArchiveUnavailableError) as caught:
        archive.load_archive_session(2026, 18, 'FP3')
    assert caught.value.reason == 'unavailable'


def test_telemetry_is_bounded_but_not_written_as_raw_cache(feeds):
    responses, calls, _, _ = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    responses['1_tel.json'] = {'time': [0, 1], 'x': [1, 2], 'y': [1, 2]}
    first, _ = archive.fetch_archive_file(session, 'AAA/1_tel.json')
    second, _ = archive.fetch_archive_file(session, 'AAA/1_tel.json')
    assert first == second
    assert len(calls) == 6
    assert len(list((archive.ROOT / 'cache' / 'tracinginsights').glob('*.json'))) == 4


@pytest.mark.parametrize('path', ['../drivers.json', '/drivers.json', 'AAA/../../drivers.json',
                                 'https://secret.example', 'AAA/0_tel.json', 'AAA/radio/clip.mp3'])
def test_file_paths_are_restricted_to_archive_json(feeds, path):
    _, calls, _, _ = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    with pytest.raises(archive.ArchiveUnavailableError):
        archive.fetch_archive_file(session, path)
    assert len(calls) == 4


def test_cache_budget_is_enforced(feeds, monkeypatch):
    monkeypatch.setattr(archive, 'MAX_CACHE_BYTES', 550)
    for index in range(8):
        url = f'https://public.example/{index}'
        archive._write_cache(archive._cache_path(url), url, {'value': 'x' * 100}, archive._now().isoformat())
    files = list((archive.ROOT / 'cache' / 'tracinginsights').glob('*.json'))
    assert sum(path.stat().st_size for path in files) <= 550


def test_http_concurrency_is_bounded_to_four(feeds, monkeypatch):
    ready, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    counts = {'active': 0, 'maximum': 0}

    class HeldResponse(Response):
        def iter_content(self, size):
            assert release.wait(5)
            yield self.body

        def close(self):
            with lock:
                counts['active'] -= 1

    def get(*args, **kwargs):
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
            if counts['active'] == 4:
                ready.set()
        return HeldResponse(data={})

    monkeypatch.setattr(archive.requests, 'get', get)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(archive._request_json, 'https://public.example/file', 100) for _ in range(8)]
        try:
            assert ready.wait(5)
        finally:
            release.set()
        assert all(future.result()[0] == {} for future in futures)
    assert counts['maximum'] == 4 and counts['active'] == 0


def test_session_fingerprint_survives_unrelated_release_updates(feeds, monkeypatch):
    responses, calls, _, now = feeds
    first = archive.load_archive_session(2026, 18, 'FP3')
    responses['release']['tag_name'] = '2026.298.1'
    monkeypatch.setattr(archive, '_now', lambda: now + timedelta(hours=1))
    updated = archive.load_archive_session(2026, 18, 'FP3', refresh=True)
    assert first['revision'] != updated['revision']
    assert first['session_fingerprint'] == updated['session_fingerprint'] == 'a' * 40
    listings = [url for url, _ in calls if url.startswith(archive.CONTENTS_BASE)]
    assert listings == [archive.CONTENTS_BASE + 'United%20States%20Grand%20Prix?ref=' + tag
                        for tag in ('2026.297.2', '2026.298.1')]


def test_fingerprint_uses_pinned_immutable_listing_and_can_check_older_tag(feeds):
    responses, calls, _, _ = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    responses['contents'][0]['sha'] = 'b' * 40
    assert archive.get_session_fingerprint(session) == 'a' * 40
    assert len(calls) == 4
    assert archive.get_session_fingerprint({**session, 'revision': '2026.298.1'}) == 'b' * 40
    assert archive.get_session_fingerprint(session) == 'a' * 40
    assert len(calls) == 5


@pytest.mark.parametrize('listing', [
    {}, [], [None], [{'name': 'Practice 2', 'type': 'dir', 'path': 'United States Grand Prix/Practice 2', 'sha': 'a' * 40}],
    [{'name': 'Practice 3', 'type': 'file', 'path': 'United States Grand Prix/Practice 3', 'sha': 'a' * 40}],
    [{'name': 'Practice 3', 'type': 'dir', 'path': 'Singapore Grand Prix/Practice 3', 'sha': 'a' * 40}],
    [{'name': 'Practice 3', 'type': 'dir', 'path': 'United States Grand Prix/Practice 3', 'sha': 'a' * 39}],
    [{'name': 'Practice 3', 'type': 'dir', 'path': 'United States Grand Prix/Practice 3', 'sha': 'g' * 40}],
    [{'name': 'Practice 3', 'type': 'dir', 'path': 'United States Grand Prix/Practice 3', 'sha': 123}],
    [{'name': 'Practice 3', 'type': 'dir', 'path': 'United States Grand Prix/Practice 3', 'sha': 'a' * 40}] * 2,
])
def test_optional_malformed_fingerprint_does_not_break_session_data(feeds, listing):
    responses, _, _, _ = feeds
    responses['contents'] = listing
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert 'session_fingerprint' not in session
    assert session['drivers'][0]['driver'] == 'AAA'
    assert session['lap_columns']['lap'] == [1, 2, 1]


@pytest.mark.parametrize('response', [
    Response(status=401), Response(status=403), Response(status=429), Response(status=404),
    Response(status=500), Response(body=b'bad json'), Response(body=b'x' * (1024 * 1024 + 1)),
    requests.ConnectionError('https://private.example/token=secret'),
])
def test_optional_fingerprint_failure_does_not_block_cdn_data(feeds, response):
    responses, calls, _, _ = feeds
    responses['contents'] = response
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert 'session_fingerprint' not in session
    assert archive._BLOCKED_UNTIL == 0
    responses['weather.json'] = {'wAT': [22]}
    weather, _ = archive.fetch_archive_file(session, 'weather.json')
    assert weather == {'wAT': [22]} and len(calls) == 5
    if isinstance(response, Response):
        assert response.closed


def test_optional_fingerprint_respects_existing_request_block(feeds, monkeypatch):
    _, calls, _, _ = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    monkeypatch.setattr(archive, '_BLOCKED_UNTIL', time.monotonic() + 60)
    assert archive.get_session_fingerprint({**session, 'revision': '2026.298.1'}) is None
    assert len(calls) == 4


@pytest.mark.parametrize('change', [
    {'year': 2025}, {'revision': 'main'}, {'revision': '../2026.1.2'},
    {'archive_session': 'Practice 2'}, {'archive_event': '../United States Grand Prix'},
])
def test_fingerprint_rejects_invalid_scope_without_http(feeds, change):
    _, calls, _, _ = feeds
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert archive.get_session_fingerprint({**session, **change}) is None
    assert len(calls) == 4


def test_optional_fingerprint_does_not_advance_source_retrieval_time(feeds, monkeypatch):
    _, _, _, now = feeds
    original_get = archive.requests.get

    def get(url, **kwargs):
        if url.startswith(archive.CONTENTS_BASE):
            monkeypatch.setattr(archive, '_now', lambda: now + timedelta(hours=1))
        return original_get(url, **kwargs)

    monkeypatch.setattr(archive.requests, 'get', get)
    session = archive.load_archive_session(2026, 18, 'FP3')
    assert session['session_fingerprint'] == 'a' * 40
    assert session['retrieved_utc'] == now.isoformat()
