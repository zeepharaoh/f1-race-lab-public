from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path

import pytest
import requests

import jolpica_client as api


BASE = 'https://api.jolpi.ca/ergast/f1'


def page(offset=0, total=1, limit=100, *, endpoint='results', count=None):
    count = min(limit, max(0, total-offset)) if count is None else count
    payload = {'offset': str(offset), 'total': str(total), 'limit': str(limit)}
    if endpoint.endswith('Standings'):
        key = endpoint.rsplit('/',1)[-1]
        key = key[0].upper() + key[1:]
        payload['StandingsTable'] = {'season':'2026', 'StandingsLists':[{'season':'2026', key:[{} for _ in range(count)]}]}
    else:
        key = {'results':'Results','qualifying':'QualifyingResults','sprint':'SprintResults','calendar':None}[endpoint]
        payload['RaceTable'] = {'season':'2026','Races': ([{} for _ in range(count)] if key is None else [{'season':'2026',key:[{} for _ in range(count)]}])}
    return payload


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload or {'MRData':page()}
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def iter_content(self, size):
        yield json.dumps(self.payload).encode()

    def close(self):
        self.closed = True


@pytest.fixture
def network(monkeypatch, tmp_path):
    clock, sleeps, calls, replies = [1000.], [], [], []
    monkeypatch.setattr(api, '_LAST_REQUEST', None)
    monkeypatch.setattr(api, '_REQUEST_TIMES', api.deque())
    monkeypatch.setattr(api, '_BLOCKED_UNTIL', 0.)
    monkeypatch.setattr(api, '_ENDPOINT_LOCKS', {})
    monkeypatch.setattr(api, '_FAILURES', {})
    monkeypatch.setattr(api.time, 'monotonic', lambda:clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(api.time, 'sleep', sleep)
    def get(url, **kwargs):
        calls.append((url,kwargs))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(api.requests, 'get', get)
    return tmp_path, calls, replies, clock, sleeps


def saved(root, endpoint='results', *, age=3600, pages=None):
    url = f'{BASE}/2026/' if endpoint=='calendar' else f'{BASE}/2026/{endpoint}/'
    payload = {'retrieved_utc':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat(),
               'url':url, 'pages':pages or [page(endpoint=endpoint)]}
    path=root/'cache'/'results'/'2026'/f'{endpoint}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload))
    return path,payload


def test_pages_respect_returned_limit_and_are_paced_and_identified(network):
    root,calls,replies,_,sleeps=network
    replies.extend(Response({'MRData':page(offset,5,2)}) for offset in (0,2,4))
    payload=api.fetch_payload(root,BASE,2026,'results')
    assert [call[1]['params'] for call in calls]==[{'limit':100,'offset':offset} for offset in (0,2,4)]
    assert [item['offset'] for item in payload['pages']]==['0','2','4']
    assert all(call[1]['headers']['User-Agent']==api.USER_AGENT for call in calls)
    assert sleeps==[pytest.approx(.55),pytest.approx(.55)]
    assert api.fetch_payload(root,BASE,2026,'results')==payload
    assert len(calls)==3


def test_manual_refresh_uses_recent_shared_cache_then_refreshes_old_cache(network):
    root,calls,replies,_,_=network
    path,payload=saved(root,age=10)
    assert api.fetch_payload(root,BASE,2026,'results',refresh=True)==payload
    assert not calls
    _,old=saved(root,age=70)
    assert api.fetch_payload(root,BASE,2026,'results')==old
    replies.append(Response())
    current=api.fetch_payload(root,BASE,2026,'results',refresh=True)
    assert current['retrieved_utc']!=old['retrieved_utc']
    assert len(calls)==1
    assert json.loads(path.read_text())==current


def test_concurrent_same_endpoint_downloads_once(network):
    root,calls,replies,_,_=network
    replies.append(Response())
    with ThreadPoolExecutor(max_workers=6) as executor:
        payloads=list(executor.map(lambda _:api.fetch_payload(root,BASE,2026,'results',refresh=True),range(6)))
    assert len(calls)==1
    assert all(payload==payloads[0] for payload in payloads)


def test_429_keeps_saved_time_without_partial_cache_and_blocks_all_endpoints(network):
    root,calls,replies,clock,_=network
    path,old=saved(root,pages=[page(0,1)])
    original=path.read_bytes()
    response=Response(status=429,headers={'Retry-After':'120'})
    replies.extend([Response({'MRData':page(0,101)}),response])
    payload=api.fetch_payload(root,BASE,2026,'results',refresh=True)
    assert payload['retrieved_utc']==old['retrieved_utc']
    assert payload['pages']==old['pages']
    assert payload['cached_fallback'] is True
    assert payload['retry_after']==120
    assert path.read_bytes()==original
    assert response.closed
    with pytest.raises(api.JolpicaUnavailableError) as failure:
        api.fetch_payload(root,BASE,2026,'qualifying')
    assert failure.value.retry_after==120
    assert len(calls)==2
    clock[0]+=121
    replies.append(Response({'MRData':page(endpoint='qualifying')}))
    assert not api.fetch_payload(root,BASE,2026,'qualifying').get('cached_fallback')
    assert len(calls)==3


@pytest.mark.parametrize('retry_after', [None, 'bad', '-10', 'nan', '0'])
def test_429_missing_or_bad_retry_after_uses_shared_minimum(network,retry_after):
    root,calls,replies,_,_=network
    replies.append(Response(status=429,headers={'Retry-After':retry_after}))
    for _ in range(2):
        with pytest.raises(api.JolpicaUnavailableError) as failure:
            api.fetch_payload(root,BASE,2026,'results')
        assert failure.value.retry_after==60
    assert len(calls)==1


def test_http_date_retry_after_does_not_retry_early(network):
    root,calls,replies,_,_=network
    date=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=180))
    replies.append(Response(status=429,headers={'Retry-After':date}))
    with pytest.raises(api.JolpicaUnavailableError) as failure:
        api.fetch_payload(root,BASE,2026,'results')
    assert 178<=failure.value.retry_after<=180
    assert len(calls)==1


@pytest.mark.parametrize('bad_page', [page(0,2,count=1),page(1,2),page(0,1,0),page(0,4000)])
def test_malformed_pagination_never_saves_partial_results(network,bad_page):
    root,_,replies,_,_=network
    replies.append(Response({'MRData':bad_page}))
    with pytest.raises(api.JolpicaUnavailableError):
        api.fetch_payload(root,BASE,2026,'results')
    assert not (root/'cache'/'results'/'2026'/'results.json').exists()


def test_total_change_during_pagination_preserves_complete_saved_response(network):
    root,_,replies,_,_=network
    path,old=saved(root)
    replies.extend([Response({'MRData':page(0,101)}),Response({'MRData':page(100,102)})])
    payload=api.fetch_payload(root,BASE,2026,'results',refresh=True)
    assert payload['pages']==old['pages']
    assert payload['cached_fallback'] is True
    assert not json.loads(path.read_text()).get('cached_fallback')


def test_corrupt_saved_pages_do_not_become_fallback(network):
    root,_,replies,_,_=network
    saved(root,pages=[page(0,101)])
    replies.append(Response(status=429))
    with pytest.raises(api.JolpicaUnavailableError):
        api.fetch_payload(root,BASE,2026,'results')


def test_transient_network_failure_has_short_negative_cache(network):
    root,calls,replies,clock,_=network
    replies.append(requests.Timeout('private proxy details'))
    for _ in range(2):
        with pytest.raises(api.JolpicaUnavailableError) as failure:
            api.fetch_payload(root,BASE,2026,'results')
        assert 'private' not in str(failure.value)
    assert len(calls)==1
    clock[0]+=31
    replies.append(Response())
    assert api.fetch_payload(root,BASE,2026,'results')['pages']
    assert len(calls)==2


@pytest.mark.parametrize('endpoint', ['calendar','constructorStandings','driverStandings','3/driverStandings','3/constructorStandings','qualifying','sprint'])
def test_supported_endpoint_shapes(network,endpoint):
    root,calls,replies,_,_=network
    leaf=endpoint.rsplit('/',1)[-1]
    replies.append(Response({'MRData':page(endpoint=leaf)}))
    assert api.fetch_payload(root,BASE,2026,endpoint)['pages']
    assert calls[0][0]==(f'{BASE}/2026/' if endpoint=='calendar' else f'{BASE}/2026/{endpoint}/')


@pytest.mark.parametrize('endpoint', ['../results','3/../../results','results?token=bad','results.json','https://example.test'])
def test_unsafe_endpoints_never_reach_network(network,endpoint):
    root,calls,_,_,_=network
    with pytest.raises(ValueError):
        api.fetch_payload(root,BASE,2026,endpoint)
    assert not calls


def test_rate_limit_retry_after_counts_down_for_same_endpoint(network):
    root,calls,replies,clock,_=network
    replies.append(Response(status=429,headers={'Retry-After':'120'}))
    with pytest.raises(api.JolpicaUnavailableError):
        api.fetch_payload(root,BASE,2026,'results')
    clock[0]+=30
    with pytest.raises(api.JolpicaUnavailableError) as failure:
        api.fetch_payload(root,BASE,2026,'results')
    assert failure.value.retry_after==90
    assert len(calls)==1


def test_hourly_budget_is_shared_across_endpoints_and_expires(network, monkeypatch):
    root,calls,replies,clock,_=network
    monkeypatch.setattr(api,'HOURLY_REQUEST_BUDGET',2)
    monkeypatch.setattr(api,'REQUEST_WINDOW_SECONDS',10)
    replies.extend([Response(), Response({'MRData':page(endpoint='sprint')})])
    api.fetch_payload(root,BASE,2026,'results')
    api.fetch_payload(root,BASE,2026,'sprint')
    with pytest.raises(api.JolpicaUnavailableError) as failure:
        api.fetch_payload(root,BASE,2026,'qualifying')
    assert 9<=failure.value.retry_after<=10
    with pytest.raises(api.JolpicaUnavailableError):
        api.fetch_payload(root,BASE,2026,'calendar')
    assert len(calls)==2
    clock[0]+=11
    replies.append(Response({'MRData':page(endpoint='qualifying')}))
    assert api.fetch_payload(root,BASE,2026,'qualifying')['pages']
    assert len(calls)==3


def test_budget_counts_failed_network_requests_and_uses_saved_fallback(network,monkeypatch):
    root,calls,replies,_,_=network
    monkeypatch.setattr(api,'HOURLY_REQUEST_BUDGET',1)
    path,old=saved(root,endpoint='sprint')
    original=path.read_bytes()
    replies.append(requests.Timeout())
    with pytest.raises(api.JolpicaUnavailableError):
        api.fetch_payload(root,BASE,2026,'results')
    result=api.fetch_payload(root,BASE,2026,'sprint',refresh=True)
    assert result['cached_fallback'] is True
    assert result['retrieved_utc']==old['retrieved_utc']
    assert result['retry_after']>=3599
    assert path.read_bytes()==original
    assert len(calls)==1


def test_saved_bundle_validation_sanitizes_flags_without_mutation(network):
    root,_,_,_,_=network
    _,payload=saved(root)
    payload.update(cached_fallback=True,refresh_error='old',retry_after=99)
    result=api.validate_payload(payload,BASE,2026,'results')
    assert set(result)=={'retrieved_utc','url','pages'}
    assert result['retrieved_utc']==payload['retrieved_utc']
    assert payload['cached_fallback'] is True


@pytest.mark.parametrize('change', [
    {'url':'https://api.jolpi.ca/ergast/f1/2025/results/'},
    {'retrieved_utc':'2099-01-01T00:00:00Z'},
    {'retrieved_utc':'2026-01-01T00:00:00'},
    {'pages':[page(0,101)]},
    {'pages':[dict(page(),RaceTable={'season':'2025','Races':[]})]},
])
def test_saved_bundle_validation_rejects_wrong_source_time_year_or_incomplete_pages(network,change):
    root,_,_,_,_=network
    _,payload=saved(root)
    payload.update(change)
    with pytest.raises(ValueError):
        api.validate_payload(payload,BASE,2026,'results')


def test_saved_bundle_rejects_nonfinite_values_inside_records(network):
    root,_,_,_,_=network
    _,payload=saved(root)
    payload['pages'][0]['RaceTable']['Races'][0]['Results'][0]['points']=float('nan')
    with pytest.raises(ValueError):
        api.validate_payload(payload,BASE,2026,'results')
