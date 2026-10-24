"""Public session files published by TracingInsights."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import threading
import time
from urllib.parse import quote
import uuid

import pandas as pd
import requests

from data import ROOT
from weekend import SESSION_LABELS, SessionUnavailableError, normalize_session_code, resolve_session

RELEASE_URL = 'https://api.github.com/repos/TracingInsights/2026/releases/latest'
CONTENTS_BASE = 'https://api.github.com/repos/TracingInsights/2026/contents/'
CDN_BASE = 'https://cdn.jsdelivr.net/gh/TracingInsights/2026@'
CACHE_TTL = 15 * 60
MAX_CACHE_BYTES = 192 * 1024 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024
MAX_TELEMETRY_BYTES = 2 * 1024 * 1024
ACCESS_RETRY_SECONDS = 60
_HTTP_SLOTS = threading.BoundedSemaphore(4)
_STATE_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_BLOCKED_UNTIL = 0
_TAG = re.compile(r'2026\.\d{1,3}\.\d{1,6}\Z')
_FILE = re.compile(r'(?:drivers|session_laptimes|weather|rcm|corners)\.json\Z|[A-Z]{3}/(?:laptimes|[1-9]\d{0,3}_tel)\.json\Z')


class ArchiveUnavailableError(SessionUnavailableError):
    """An archive failure with a public safe reason."""

    def __init__(self, reason='unavailable'):
        self.reason = reason
        messages = {
            'not_published': 'This session is not published in the free archive yet. Try again after the session.',
            'access_limited': 'The free archive is temporarily limiting requests. Try again shortly.',
            'unavailable': 'The free session archive is temporarily unavailable. Try again later.',
        }
        super().__init__(messages.get(reason, messages['unavailable']))


class _PublicAuth(requests.auth.AuthBase):
    def __call__(self, request):
        return request


_PUBLIC_AUTH = _PublicAuth()


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('Missing timestamp')
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def _invalid_constant(value):
    raise ValueError('Invalid JSON number')


def _cache_path(url):
    return ROOT / 'cache' / 'tracinginsights' / (hashlib.sha256(url.encode()).hexdigest() + '.json')


def _read_cache(path, url, limit):
    try:
        if path.stat().st_size > limit + 4096:
            return None
        saved = json.loads(path.read_text(), parse_constant=_invalid_constant)
        if saved['url'] != url or not isinstance(saved['data'], (dict, list)):
            return None
        retrieved = _timestamp(saved['retrieved_utc'])
        if retrieved > _now():
            return None
        return saved['data'], retrieved.isoformat()
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def _write_cache(path, url, data, retrieved):
    with _CACHE_LOCK:
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({'url': url, 'retrieved_utc': retrieved, 'data': data},
                                            separators=(',', ':'), allow_nan=False))
            temporary.replace(path)
            files = []
            for item in path.parent.glob('*.json'):
                try:
                    stat = item.stat()
                    files.append((stat.st_mtime, stat.st_size, item))
                except OSError:
                    continue
            total = sum(size for _, size, _ in files)
            for _, size, item in sorted(files):
                if total <= MAX_CACHE_BYTES:
                    break
                try:
                    item.unlink()
                    total -= size
                except OSError:
                    pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _request_json(url, limit, *, block_on_access=True):
    global _BLOCKED_UNTIL
    with _HTTP_SLOTS:
        with _STATE_LOCK:
            if time.monotonic() < _BLOCKED_UNTIL:
                raise ArchiveUnavailableError('access_limited')
        response = None
        try:
            response = requests.get(url, timeout=(5, 30), stream=True, allow_redirects=False,
                                    headers={'Accept': 'application/json'}, auth=_PUBLIC_AUTH)
            if response.status_code in (401, 403, 429):
                if block_on_access:
                    with _STATE_LOCK:
                        _BLOCKED_UNTIL = time.monotonic() + ACCESS_RETRY_SECONDS
                raise ArchiveUnavailableError('access_limited')
            if response.status_code == 404:
                raise ArchiveUnavailableError('not_published')
            if response.status_code != 200:
                raise ArchiveUnavailableError()
            length = response.headers.get('Content-Length')
            if length and int(length) > limit:
                raise ArchiveUnavailableError()
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if len(body) + len(chunk) > limit:
                    raise ArchiveUnavailableError()
                body.extend(chunk)
            decoded = json.loads(body, parse_constant=_invalid_constant)
            if not isinstance(decoded, (dict, list)):
                raise ArchiveUnavailableError()
            return decoded, _now().isoformat()
        except ArchiveUnavailableError:
            raise
        except (requests.RequestException, ValueError, TypeError, OverflowError):
            raise ArchiveUnavailableError() from None
        finally:
            if response is not None:
                response.close()


def _fetch_json(url, limit, *, mutable=False, refresh=False, cache=True, status=None,
                block_on_access=True):
    path = _cache_path(url)
    saved = _read_cache(path, url, limit) if cache else None
    if saved and (not mutable or (not refresh and (_now() - _timestamp(saved[1])).total_seconds() < CACHE_TTL)):
        return saved
    try:
        data, retrieved = _request_json(url, limit, block_on_access=block_on_access)
    except ArchiveUnavailableError:
        if saved:
            if status is not None:
                status['cached_fallback'] = True
            return saved
        raise
    if cache:
        _write_cache(path, url, data, retrieved)
    return data, retrieved


def _release(*, refresh=False):
    status = {}
    release, retrieved = _fetch_json(RELEASE_URL, 256 * 1024, mutable=True, refresh=refresh, status=status)
    try:
        tag = release['tag_name']
        published = _timestamp(release['published_at'])
        if not isinstance(tag, str) or not _TAG.fullmatch(tag) or release.get('draft') or release.get('prerelease'):
            raise ValueError('Invalid release')
        if published > _now():
            raise ValueError('Future release')
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ArchiveUnavailableError() from None
    return tag, published.isoformat(), retrieved, bool(status.get('cached_fallback'))


def _event_folder(scheduled):
    event = str(scheduled.get('race', ''))
    location = str(scheduled.get('location', '')).casefold()
    if event == 'Spanish Grand Prix' and location in ('barcelona', 'montmeló', 'montmelo'):
        event = 'Barcelona Grand Prix'
    if (not 10 <= len(event) <= 100 or not event.endswith(' Grand Prix')
            or not all(character.isalpha() or character == ' ' for character in event)):
        raise ArchiveUnavailableError()
    return event


def _validate_session(session):
    try:
        if session['year'] != 2026 or not _TAG.fullmatch(session['revision']):
            raise ValueError('Invalid session')
        event, label = session['archive_event'], session['archive_session']
        if event != _event_folder(session['scheduled']) or label != SESSION_LABELS[normalize_session_code(session['scheduled']['code'])]:
            raise ValueError('Session mismatch')
        if _timestamp(session['scheduled']['session_utc']) > _now():
            raise ValueError('Future session')
        return event, label
    except (AttributeError, TypeError, KeyError, ValueError, OverflowError):
        raise ArchiveUnavailableError() from None


def fetch_archive_file(session, relative_path):
    """Read one public JSON file from the selected release."""
    event, label = _validate_session(session)
    if not isinstance(relative_path, str) or not _FILE.fullmatch(relative_path):
        raise ArchiveUnavailableError()
    telemetry = relative_path.endswith('_tel.json')
    path = '/'.join(quote(part, safe='') for part in (event, label, *relative_path.split('/')))
    url = CDN_BASE + session['revision'] + '/' + path
    return _fetch_json(url, MAX_TELEMETRY_BYTES if telemetry else MAX_SESSION_BYTES, cache=not telemetry)


def get_session_fingerprint(session):
    """Identify the selected session contents in a published release."""
    try:
        event, label = _validate_session(session)
        revision = session['revision']
        url = CONTENTS_BASE + quote(event, safe='') + '?ref=' + quote(revision, safe='')
        listing, _ = _fetch_json(url, 1024 * 1024, block_on_access=False)
        if not isinstance(listing, list) or any(not isinstance(item, dict) for item in listing):
            return None
        matches = [item for item in listing if item.get('name') == label]
        if len(matches) != 1:
            return None
        entry = matches[0]
        fingerprint = entry.get('sha')
        if (entry.get('type') != 'dir' or entry.get('path') != event + '/' + label
                or not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-fA-F]{40}', fingerprint)):
            return None
        return fingerprint.lower()
    except (ArchiveUnavailableError, AttributeError, TypeError, ValueError, KeyError, OverflowError):
        return None


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Invalid number')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Invalid number')
    return number


def _validate_drivers(drivers):
    if not isinstance(drivers, list) or not 1 <= len(drivers) <= 50:
        raise ValueError('Invalid roster')
    codes, numbers = set(), set()
    for row in drivers:
        code, number = row['driver'], _number(row['dn'])
        if not isinstance(code, str) or not re.fullmatch('[A-Z]{3}', code) or code in codes:
            raise ValueError('Invalid driver')
        if not number.is_integer() or not 1 <= number <= 999 or int(number) in numbers:
            raise ValueError('Invalid car number')
        if not isinstance(row.get('team'), str) or not row['team'] or len(row['team']) > 100:
            raise ValueError('Invalid team')
        codes.add(code)
        numbers.add(int(number))


def _validate_laps(columns, drivers, scheduled, published):
    if not isinstance(columns, dict) or not columns:
        raise ValueError('Invalid lap columns')
    sizes = {len(column) for column in columns.values() if isinstance(column, list)}
    if len(sizes) != 1 or any(not isinstance(column, list) for column in columns.values()):
        raise ValueError('Unaligned lap columns')
    count = sizes.pop()
    required = {'drv', 'dNum', 'team', 'lap', 'time', 'lST', 'lSD', 'sesT'}
    if not required.issubset(columns) or not 1 <= count <= 20000:
        raise ValueError('Missing lap fields')
    roster = {row['driver']: (int(row['dn']), row['team']) for row in drivers}
    start, release, current = _timestamp(scheduled['session_utc']), _timestamp(published), _timestamp(_now())
    identities, dated = set(), 0
    for index in range(count):
        code = columns['drv'][index]
        number, lap = _number(columns['dNum'][index]), _number(columns['lap'][index])
        if not number.is_integer() or not lap.is_integer() or not 1 <= lap <= 2000:
            raise ValueError('Invalid lap identity')
        if code not in roster or roster[code] != (int(number), columns['team'][index]):
            raise ValueError('Lap roster mismatch')
        identity = (code, int(lap))
        if identity in identities:
            raise ValueError('Duplicate lap')
        identities.add(identity)
        date = columns['lSD'][index]
        if date in (None, '', 'None'):
            continue
        stamp = _timestamp(date)
        if abs((stamp - start).total_seconds()) > 86400:
            raise ValueError('Lap date mismatch')
        duration = _number(columns['sesT'][index]) - _number(columns['lST'][index])
        if not 0 <= duration <= 24 * 60 * 60:
            raise ValueError('Invalid lap clock')
        end = stamp + timedelta(seconds=duration)
        if end > current or end > release:
            raise ValueError('Unpublished future lap')
        dated += 1
    if dated < count * 0.8:
        raise ValueError('Missing session dates')


def load_archive_session(year, round_number, session_code, *, refresh=False):
    """Load published timing without a provider account."""
    try:
        if isinstance(year, bool) or int(year) != 2026 or isinstance(round_number, bool) or int(round_number) < 1:
            raise ValueError('Unsupported year or round')
        code = normalize_session_code(session_code)
        scheduled = resolve_session(int(round_number), code, int(year), refresh=refresh)
        scheduled = dict(scheduled, code=code)
        if _timestamp(scheduled['session_utc']) > _now() or int(scheduled['round']) != int(round_number):
            raise ValueError('Session mismatch')
        event = _event_folder(scheduled)
        revision, published, release_retrieved, cached_fallback = _release(refresh=refresh)
        if _timestamp(published) < _timestamp(scheduled['session_utc']):
            raise ArchiveUnavailableError('not_published')
        label = SESSION_LABELS[code]
        source_path = '/'.join(quote(part, safe='') for part in (event, label))
        result = {
            'year': 2026, 'scheduled': scheduled, 'revision': revision, 'published_utc': published,
            'archive_event': event, 'archive_session': label,
            'cached_fallback': cached_fallback,
            'source_url': 'https://github.com/TracingInsights/2026/tree/' + revision + '/' + source_path,
        }
        drivers, driver_retrieved = fetch_archive_file(result, 'drivers.json')
        columns, laps_retrieved = fetch_archive_file(result, 'session_laptimes.json')
        if isinstance(drivers, dict):
            drivers = drivers['drivers']
        _validate_drivers(drivers)
        _validate_laps(columns, drivers, scheduled, published)
        result.update(drivers=drivers, lap_columns=columns,
                      retrieved_utc=min((release_retrieved, driver_retrieved, laps_retrieved), key=_timestamp))
        fingerprint = get_session_fingerprint(result)
        if fingerprint is not None:
            result['session_fingerprint'] = fingerprint
        return result
    except ArchiveUnavailableError:
        raise
    except SessionUnavailableError:
        raise ArchiveUnavailableError('not_published') from None
    except (requests.RequestException, AttributeError, ValueError, TypeError, KeyError, OverflowError):
        raise ArchiveUnavailableError() from None
