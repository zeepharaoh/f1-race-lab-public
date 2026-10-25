"""Shared cache and request pacing for public Jolpica results."""
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

import requests


CACHE_TTL = 15 * 60
REFRESH_COOLDOWN = 60
REQUEST_INTERVAL = .55
HOURLY_REQUEST_BUDGET = 400
REQUEST_WINDOW_SECONDS = 3600
ACCESS_RETRY_SECONDS = 60
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_PAGES = 32
FAILURE_COOLDOWN = 30
PAGE_LIMIT = 100
USER_AGENT = 'F1RaceLab/1.0 (+https://github.com/zeepharaoh/f1-race-lab-public)'
_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_ENDPOINT_LOCKS = {}
_FAILURES = {}
_LAST_REQUEST = None
_REQUEST_TIMES = deque()
_BLOCKED_UNTIL = 0.


class _PublicAuth(requests.auth.AuthBase):
    def __call__(self, request):
        return request


_PUBLIC_AUTH = _PublicAuth()


class JolpicaUnavailableError(requests.RequestException):
    def __init__(self, message='The results provider is temporarily unavailable. Try again later.', *, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _limited(seconds):
    return JolpicaUnavailableError('Results requests are temporarily paused to respect the provider limits. Try again later.',
                                   retry_after=max(1, math.ceil(seconds)))


def _integer(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise ValueError('Invalid results pagination')
    return int(value)


def _timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError('Invalid results retrieval time')
    return stamp


def _record_count(page, endpoint, year):
    leaf = endpoint.rsplit('/', 1)[-1]
    if leaf in ('driverStandings', 'constructorStandings'):
        table, items, key = page['StandingsTable'], 'StandingsLists', leaf[0].upper() + leaf[1:]
    else:
        table, items = page['RaceTable'], 'Races'
        key = {'results': 'Results', 'sprint': 'SprintResults', 'qualifying': 'QualifyingResults', 'calendar': None}[leaf]
    if 'season' in table and _integer(table['season']) != year:
        raise ValueError('Results season does not match')
    rows = table[items]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid results table')
    for row in rows:
        if 'season' in row and _integer(row['season']) != year:
            raise ValueError('Results season does not match')
    if key is None:
        return len(rows)
    if any(not isinstance(row.get(key), list) or any(not isinstance(value, dict) for value in row[key]) for row in rows):
        raise ValueError('Invalid result records')
    return sum(len(row[key]) for row in rows)


def _validate_pages(pages, endpoint, year, *, complete):
    if not isinstance(pages, list) or not pages or len(pages) > MAX_PAGES:
        raise ValueError('Invalid results pages')
    expected, total = 0, None
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError('Invalid results page')
        limit, offset, count = (_integer(page[key]) for key in ('limit', 'offset', 'total'))
        if not 1 <= limit <= PAGE_LIMIT or offset != expected or count > PAGE_LIMIT * MAX_PAGES:
            raise ValueError('Invalid results pagination')
        if total is not None and total != count:
            raise ValueError('Results changed during pagination')
        total = count
        size = _record_count(page, endpoint, year)
        if size != min(limit, max(0, total - offset)) or (offset > 0 and offset >= total):
            raise ValueError('Incomplete results page')
        expected += limit
    if complete and expected < total:
        raise ValueError('Incomplete results download')
    return expected >= total


def _invalid_number(value):
    raise ValueError('Invalid JSON number')


def _query(year, endpoint):
    year = _integer(year)
    if not 1950 <= year <= datetime.now(timezone.utc).year + 1 or not isinstance(endpoint, str):
        raise ValueError('Invalid results query')
    if not re.fullmatch(r'(?:(?:[1-9]|[1-9]\d)/)?(?:constructorStandings|driverStandings|results|sprint|qualifying|calendar)', endpoint):
        raise ValueError('Invalid results endpoint')
    return year


def validate_payload(payload, base, year, endpoint):
    """Validate complete saved results and discard transient cache flags."""
    try:
        year = _query(year, endpoint)
        url = f'{base}/{year}/' if endpoint == 'calendar' else f'{base}/{year}/{endpoint}/'
        if not isinstance(payload, dict) or payload['url'] != url:
            raise ValueError('Results source does not match the query')
        _timestamp(payload['retrieved_utc'])
        _validate_pages(payload['pages'], endpoint, year, complete=True)
        encoded = json.dumps(payload['pages'], separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_CACHE_BYTES:
            raise ValueError('Saved results exceeded their size limit')
        return {'retrieved_utc': payload['retrieved_utc'], 'url': url, 'pages': payload['pages']}
    except (AttributeError, KeyError, TypeError, OverflowError):
        raise ValueError('Invalid saved results') from None


def _saved(path, base, endpoint, year):
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        payload = json.loads(path.read_text(), parse_constant=_invalid_number)
        return validate_payload(payload, base, year, endpoint)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None


def _save(path, payload):
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, separators=(',', ':'), allow_nan=False)
        if len(serialized.encode('utf-8')) > MAX_CACHE_BYTES:
            return
        temporary.write_text(serialized)
        temporary.replace(path)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _retry_after(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = ACCESS_RETRY_SECONDS
    return max(ACCESS_RETRY_SECONDS, seconds) if math.isfinite(seconds) else ACCESS_RETRY_SECONDS


def _request(url, offset):
    global _LAST_REQUEST, _BLOCKED_UNTIL
    with _REQUEST_LOCK:
        remaining = _BLOCKED_UNTIL - time.monotonic()
        if remaining > 0:
            raise _limited(remaining)
        if _LAST_REQUEST is not None:
            time.sleep(max(0., REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST)))
        now = time.monotonic()
        while _REQUEST_TIMES and now - _REQUEST_TIMES[0] >= REQUEST_WINDOW_SECONDS:
            _REQUEST_TIMES.popleft()
        if len(_REQUEST_TIMES) >= HOURLY_REQUEST_BUDGET:
            _BLOCKED_UNTIL = _REQUEST_TIMES[0] + REQUEST_WINDOW_SECONDS
            raise _limited(_BLOCKED_UNTIL - now)
        _LAST_REQUEST = now
        _REQUEST_TIMES.append(now)
        response = None
        try:
            response = requests.get(url, params={'limit': PAGE_LIMIT, 'offset': offset}, timeout=(5, 30),
                                    headers={'Accept': 'application/json', 'User-Agent': USER_AGENT},
                                    stream=True, allow_redirects=False, auth=_PUBLIC_AUTH)
            if response.status_code == 429:
                wait = _retry_after(response.headers.get('Retry-After'))
                _BLOCKED_UNTIL = time.monotonic() + wait
                raise _limited(wait)
            if 300 <= response.status_code < 400:
                raise JolpicaUnavailableError()
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise JolpicaUnavailableError('The results response exceeded its size limit.')
            value = json.loads(content, parse_constant=_invalid_number)
            if not isinstance(value, dict) or not isinstance(value.get('MRData'), dict):
                raise ValueError('Missing results table')
            return value['MRData']
        except JolpicaUnavailableError:
            raise
        except (requests.RequestException, ValueError, TypeError, KeyError):
            raise JolpicaUnavailableError() from None
        finally:
            if response is not None:
                response.close()


def fetch_payload(root, base, year, endpoint, refresh=False):
    """Read only complete endpoint responses and retain their source dates."""
    year = _query(year, endpoint)
    path = Path(root) / 'cache' / 'results' / str(year) / f'{endpoint}.json'
    url = f'{base}/{year}/' if endpoint == 'calendar' else f'{base}/{year}/{endpoint}/'
    with _LOCK:
        endpoint_lock = _ENDPOINT_LOCKS.setdefault(str(path.resolve()), threading.Lock())
    with endpoint_lock:
        saved = _saved(path, base, endpoint, year)
        if saved:
            age = (datetime.now(timezone.utc) - _timestamp(saved['retrieved_utc'])).total_seconds()
            if age < (REFRESH_COOLDOWN if refresh else CACHE_TTL):
                return saved
        try:
            previous_failure = _FAILURES.get(str(path.resolve()))
            if previous_failure and previous_failure[0] > time.monotonic():
                if previous_failure[1].retry_after is not None:
                    raise _limited(previous_failure[0] - time.monotonic())
                raise previous_failure[1]
            pages, offset, downloaded_bytes = [], 0, 0
            while len(pages) < MAX_PAGES:
                page = _request(url, offset)
                downloaded_bytes += len(json.dumps(page, separators=(',', ':'), allow_nan=False).encode('utf-8'))
                if downloaded_bytes > MAX_CACHE_BYTES:
                    raise JolpicaUnavailableError('The results download exceeded its size limit.')
                pages.append(page)
                if _validate_pages(pages, endpoint, year, complete=False):
                    break
                offset += _integer(page['limit'])
            _validate_pages(pages, endpoint, year, complete=True)
            payload = {'retrieved_utc': datetime.now(timezone.utc).isoformat(), 'url': url, 'pages': pages}
            _save(path, payload)
            _FAILURES.pop(str(path.resolve()), None)
            return payload
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            failure = error if isinstance(error, JolpicaUnavailableError) else JolpicaUnavailableError()
            if not previous_failure or previous_failure[0] <= time.monotonic():
                _FAILURES[str(path.resolve())] = (time.monotonic() + (failure.retry_after or FAILURE_COOLDOWN), failure)
            if not saved:
                raise failure from None
            result = dict(saved, cached_fallback=True, refresh_error=str(failure))
            if failure.retry_after is not None:
                result['retry_after'] = failure.retry_after
            return result
