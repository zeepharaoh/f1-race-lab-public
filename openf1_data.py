"""Historical session timing from OpenF1."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time

import pandas as pd
import requests

from data import ROOT
from weekend import RESULT_COLUMNS, SESSION_LABELS, SessionUnavailableError, canonical_team, normalize_session_code, resolve_session

BASE = 'https://api.openf1.org/v1'
REQUEST_INTERVAL = 2.1
CACHE_TTL = 24 * 60 * 60
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_CACHE_BYTES = 192 * 1024 * 1024
_LOCK = threading.Lock()
_LAST_REQUEST = None
_FETCHED_AT = {}


def _key(endpoint, params):
    return hashlib.sha256(json.dumps([endpoint, params], sort_keys=True, default=str).encode()).hexdigest()


def _retry_wait(value, attempt):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = 3 * (attempt + 1)
    return max(0, seconds) if math.isfinite(seconds) else 60


def fetch(endpoint, *, refresh=False, **params):
    """Cache bounded JSON responses and share a paced request queue."""
    global _LAST_REQUEST
    if not re.fullmatch(r'[a-z_]+', endpoint):
        raise ValueError('Invalid OpenF1 endpoint')
    params = {key: value for key, value in params.items() if value is not None}
    key = _key(endpoint, params)
    cache = Path(ROOT) / 'cache' / 'openf1'
    path = cache / f'{key}.json'
    ttl = 900 if endpoint in ('sessions', 'meetings') else CACHE_TTL
    with _LOCK:
        if path.exists() and not refresh:
            try:
                saved = json.loads(path.read_text())
                if time.time() - path.stat().st_mtime < ttl and saved['data']:
                    _FETCHED_AT[key] = saved['retrieved_utc']
                    return saved['data']
            except (ValueError, KeyError, OSError):
                pass
        for attempt in range(3):
            if _LAST_REQUEST is not None:
                time.sleep(max(0, REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
            with requests.get(f'{BASE}/{endpoint}', params=params, timeout=(10, 45), stream=True) as response:
                if response.status_code == 429 and attempt < 2:
                    wait = _retry_wait(response.headers.get('Retry-After'), attempt)
                    if wait > 30:
                        response.raise_for_status()
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > MAX_RESPONSE_BYTES:
                        raise ValueError('OpenF1 response is too large; request a smaller time range')
                    chunks.append(chunk)
                payload = json.loads(b''.join(chunks))
            if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                raise ValueError('Unexpected OpenF1 response')
            retrieved = datetime.now(timezone.utc).isoformat()
            _FETCHED_AT[key] = retrieved
            if payload:
                try:
                    cache.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'retrieved_utc': retrieved, 'data': payload}, allow_nan=False))
                    temporary.replace(path)
                    files = sorted(cache.glob('*.json'), key=lambda item: item.stat().st_mtime)
                    total = sum(item.stat().st_size for item in files)
                    for item in files:
                        if total <= MAX_CACHE_BYTES:
                            break
                        size = item.stat().st_size
                        item.unlink()
                        total -= size
                except (OSError, ValueError):
                    pass
            return payload


def _utc(value):
    result = pd.to_datetime(value, utc=True, errors='coerce')
    return pd.NaT if result is None else result


def fetched_at(endpoint, **params):
    """Return the source retrieval time, including disk cache hits."""
    return _FETCHED_AT.get(_key(endpoint, {key: value for key, value in params.items() if value is not None}))


def resolve_openf1_session(year, round_number, session_code, *, refresh=False):
    """Match dates and names, including calendars with cancelled rounds."""
    code = normalize_session_code(session_code)
    scheduled = resolve_session(round_number, code, year, refresh=refresh)
    start = _utc(scheduled['session_utc'])
    candidates = []
    for row in fetch('sessions', year=int(year), refresh=refresh):
        try:
            matched_code = normalize_session_code(row.get('session_name', ''))
        except ValueError:
            continue
        actual_start = _utc(row.get('date_start'))
        if (matched_code != code or int(row.get('year', 0)) != int(year)
                or pd.isna(actual_start) or pd.isna(start)):
            continue
        distance = abs((actual_start - start).total_seconds())
        if distance <= 24 * 60 * 60:
            candidates.append((distance, row))
    if not candidates:
        raise SessionUnavailableError('OpenF1 has not published this scheduled session')
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise SessionUnavailableError('The OpenF1 session match is ambiguous')
    row = dict(candidates[0][1])
    if row.get('is_cancelled'):
        raise SessionUnavailableError('This session was cancelled')
    end = _utc(row.get('date_end'))
    if pd.isna(end) or end > pd.Timestamp.now(tz='UTC'):
        raise SessionUnavailableError('This session has not finished yet')
    if pd.isna(_utc(row.get('date_start'))) or _utc(row['date_start']) > pd.Timestamp.now(tz='UTC'):
        raise SessionUnavailableError('This session has not started yet')
    classification = fetch('session_result', session_key=int(row['session_key']), refresh=refresh)
    if not classification:
        raise SessionUnavailableError('OpenF1 session results are not published yet')
    row.update(round=int(scheduled['round']), code=code, scheduled=scheduled,
               _results=classification)
    return row


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except (ValueError, TypeError):
        return math.nan


def _driver_number(value):
    number = _number(value)
    return str(int(number)) if pd.notna(number) and number == int(number) else ''


def _track_events(controls):
    events, sectors, global_state = [], set(), None
    for item in sorted(controls, key=lambda row: str(row.get('date') or '')):
        date = _utc(item.get('date'))
        if pd.isna(date):
            continue
        flag = str(item.get('flag') or '').upper()
        scope = str(item.get('scope') or '').upper()
        message = str(item.get('message') or '').upper()
        if 'VIRTUAL SAFETY CAR' in message:
            global_state = '1' if 'ENDED' in message else '6'
        elif 'SAFETY CAR' in message and any(word in message for word in ('DEPLOYED', 'IN THIS LAP', 'WITHDRAWN')):
            global_state = '1' if 'WITHDRAWN' in message else '4'
        elif scope == 'TRACK' and flag in ('GREEN', 'CLEAR', 'YELLOW', 'DOUBLE YELLOW', 'RED'):
            global_state = {'GREEN': '1', 'CLEAR': '1', 'YELLOW': '2', 'DOUBLE YELLOW': '2', 'RED': '5'}[flag]
            if flag in ('GREEN', 'CLEAR'):
                sectors.clear()
        elif scope == 'SECTOR':
            sector = item.get('sector')
            if flag in ('YELLOW', 'DOUBLE YELLOW', 'RED'):
                sectors.add(sector)
            elif flag in ('CLEAR', 'GREEN'):
                sectors.discard(sector)
        state = '2' if sectors and global_state in (None, '1', '2') else global_state
        events.append((date, state))
    return events


def _lap_track_status(start, end, source, events):
    if pd.isna(start) or pd.isna(end):
        return ''
    current = None
    during = []
    for date, state in events:
        if date <= start:
            current = state
        elif date < end:
            during.append(state)
    states = [current, *during]
    adverse = {state for state in states if state not in (None, '1')}
    if adverse:
        return ''.join(sorted(adverse))
    segments = [source.get(f'segments_sector_{sector}') for sector in (1, 2, 3)]
    green_segments = all(isinstance(values, list) and values
                         and all(value in (2049, 2051) for value in values) for values in segments)
    if green_segments or all(state == '1' for state in states):
        return '1'
    return ''


def _deleted_laps(laps, controls):
    deleted = pd.Series(False if controls else pd.NA, index=laps.index, dtype='boolean')
    unresolved = False
    for item in sorted(controls, key=lambda row: str(row.get('date') or '')):
        message = str(item.get('message') or '').upper()
        if not any(word in message for word in ('DELETED', 'REINSTATED')) or not any(word in message for word in ('LAP', 'TIME')):
            continue
        driver = re.search(r'\bCAR\s+(\d{1,3})\b', message)
        if not driver:
            deleted[:] = pd.NA
            unresolved = True
            continue
        mask = laps.DriverNumber.eq(_driver_number(driver.group(1)))
        numbers = re.findall(r'\bLAP\s+(\d+)\b', message)
        if numbers:
            mask &= laps.LapNumber.eq(int(numbers[-1]))
        else:
            duration = re.search(r'\bTIME\s+(\d{1,2}):(\d{2}(?:\.\d+)?)', message)
            if duration:
                seconds = int(duration.group(1)) * 60 + float(duration.group(2))
                mask &= laps.lap_seconds.sub(seconds).abs().lt(.002)
            else:
                deleted.loc[mask] = pd.NA
                unresolved = True
                continue
        if mask.sum() != 1:
            deleted.loc[laps.DriverNumber.eq(_driver_number(driver.group(1)))] = pd.NA
            unresolved = True
            continue
        deleted.loc[mask] = 'REINSTATED' not in message
    return deleted, bool(controls) and not unresolved


def _normalise_laps(rows, drivers, stints, pits, controls, session_start):
    events = _track_events(controls)
    pit_laps = {(_driver_number(row.get('driver_number')), _number(row.get('lap_number')))
                for row in pits}
    records = []
    seen = set()
    for source in rows:
        number = _driver_number(source.get('driver_number'))
        lap = _number(source.get('lap_number'))
        if not number or pd.isna(lap) or lap <= 0 or lap != int(lap):
            continue
        identity = (number, int(lap))
        if identity in seen:
            raise ValueError('Duplicate OpenF1 lap record')
        seen.add(identity)
        driver = drivers.get(number, {})
        duration = _number(source.get('lap_duration'))
        start = _utc(source.get('date_start'))
        finish = start + pd.to_timedelta(duration, unit='s') if pd.notna(start) and pd.notna(duration) else pd.NaT
        stint = [item for item in stints if _driver_number(item.get('driver_number')) == number
                 and _number(item.get('lap_start')) <= lap <= _number(item.get('lap_end'))]
        stint = stint[0] if len(stint) == 1 else {}
        tyre_age = _number(stint.get('tyre_age_at_start'))
        tyre_life = tyre_age + lap - _number(stint.get('lap_start')) + 1 if pd.notna(tyre_age) else math.nan
        sectors = [_number(source.get(f'duration_sector_{index}')) for index in (1, 2, 3)]
        consistent = pd.notna(duration) and duration > 0 and all(pd.notna(value) and value > 0 for value in sectors)
        consistent = consistent and abs(sum(sectors) - duration) <= .05
        records.append({
            'DriverNumber': number, 'Driver': str(driver.get('name_acronym') or number),
            'Team': canonical_team(driver.get('team_name')), 'LapNumber': int(lap),
            'LapTime': pd.to_timedelta(duration, unit='s') if pd.notna(duration) else pd.NaT,
            'lap_seconds': duration, 'LapStartTime': start - session_start,
            'Time': finish - session_start, 'date_start': start,
            'Compound': stint.get('compound'), 'TyreLife': tyre_life,
            'Stint': _number(stint.get('stint_number')), 'IsAccurate': pd.NA,
            'PitInTime': pd.NaT, 'PitOutTime': pd.NaT,
            'IsPitInLap': identity in pit_laps,
            'IsPitOutLap': source.get('is_pit_out_lap') if isinstance(source.get('is_pit_out_lap'), bool) else pd.NA,
            'TrackStatus': _lap_track_status(start, finish, source, events),
            'SectorTimesConsistent': bool(consistent),
            **{f'Sector{index}Time': pd.to_timedelta(value, unit='s') if pd.notna(value) else pd.NaT
               for index, value in enumerate(sectors, 1)},
        })
    columns = ['DriverNumber', 'Driver', 'Team', 'LapNumber', 'LapTime', 'lap_seconds', 'LapStartTime', 'Time',
               'date_start', 'Compound', 'TyreLife', 'Stint', 'IsAccurate', 'PitInTime', 'PitOutTime',
               'IsPitInLap', 'IsPitOutLap', 'TrackStatus', 'SectorTimesConsistent',
               'Sector1Time', 'Sector2Time', 'Sector3Time']
    laps = pd.DataFrame(records, columns=columns)
    for name in ('LapTime', 'LapStartTime', 'Time', 'PitInTime', 'PitOutTime', 'Sector1Time', 'Sector2Time', 'Sector3Time'):
        laps[name] = pd.Series(laps[name].tolist(), index=laps.index, dtype='timedelta64[ns]')
    laps['IsAccurate'] = laps.IsAccurate.astype('boolean')
    laps['IsPitOutLap'] = laps.IsPitOutLap.astype('boolean')
    laps['Deleted'], checked = _deleted_laps(laps, controls)
    return laps, checked


def _results(rows, drivers, grid, laps, code):
    records, seen = [], set()
    for source in rows:
        number = _driver_number(source.get('driver_number'))
        if not number or number in seen:
            raise ValueError('Invalid or duplicate OpenF1 classification')
        seen.add(number)
        driver = drivers.get(number, {})
        own_laps = laps[laps.DriverNumber.eq(number)]
        valid = own_laps[own_laps.lap_seconds.gt(0) & own_laps.Deleted.eq(False).fillna(False)
                         & ~own_laps.IsPitInLap & ~own_laps.IsPitOutLap]
        duration = source.get('duration')
        segments = [math.nan] * 3
        if code in ('Q', 'SQ'):
            if isinstance(duration, list):
                segments = [_number(value) if _number(value) > 0 else math.nan
                            for value in (duration + [None] * 3)[:3]]
            best = min((value for value in segments if pd.notna(value) and value > 0), default=math.nan)
        elif code.startswith('FP'):
            best = _number(duration)
        else:
            best = valid.lap_seconds.min()
        if not best > 0:
            best = math.nan
        lap_count = _number(source.get('number_of_laps'))
        records.append({
            'position': _number(source.get('position')), 'driver_number': number,
            'code': str(driver.get('name_acronym') or number),
            'driver': str(driver.get('full_name') or driver.get('name_acronym') or number),
            'team': canonical_team(driver.get('team_name')), 'best_lap_seconds': best,
            'gap_seconds': math.nan, 'lap_count': lap_count if pd.notna(lap_count) else len(own_laps),
            'timed_lap_count': len(valid), 'points': math.nan,
            'grid': grid.get(number, math.nan) if code in ('R', 'S') else math.nan,
            'status': ('Disqualified' if source.get('dsq') else 'Did not start' if source.get('dns')
                       else 'Did not finish' if source.get('dnf') else 'Classified') if code in ('R', 'S') else '',
            **{f'Q{index}_seconds': value for index, value in enumerate(segments, 1)},
        })
    results = pd.DataFrame(records, columns=RESULT_COLUMNS).sort_values('position', na_position='last').reset_index(drop=True)
    results['gap_seconds'] = results.best_lap_seconds - results.best_lap_seconds.min()
    return results


def load_openf1_analysis(year, round_number, session_code, *, refresh=False):
    """Return published classification and conservative lap comparisons."""
    info = resolve_openf1_session(year, round_number, session_code, refresh=refresh)
    key, code = int(info['session_key']), info['code']
    drivers = {}
    for row in fetch('drivers', session_key=key, refresh=refresh):
        number = _driver_number(row.get('driver_number'))
        if not number or number in drivers:
            raise ValueError('Invalid or duplicate OpenF1 driver')
        drivers[number] = row
    if not drivers:
        raise SessionUnavailableError('OpenF1 driver records are unavailable')
    rows = fetch('laps', session_key=key, refresh=refresh)
    if not rows:
        raise SessionUnavailableError('OpenF1 lap timing is not published yet')
    missing = []
    def optional(endpoint):
        try:
            return fetch(endpoint, session_key=key, refresh=refresh)
        except (requests.RequestException, ValueError):
            missing.append(endpoint)
            return []
    stints, pits, controls = optional('stints'), optional('pit'), optional('race_control')
    grid_rows = optional('starting_grid') if code in ('R', 'S') else []
    grid = {_driver_number(row.get('driver_number')): _number(row.get('position')) for row in grid_rows}
    laps, checked = _normalise_laps(rows, drivers, stints, pits, controls, _utc(info['date_start']))
    if laps.empty:
        raise SessionUnavailableError('OpenF1 has no usable lap records')
    keep = (laps.lap_seconds.gt(0) & laps.Deleted.eq(False).fillna(False)
            & ~laps.IsPitInLap & ~laps.IsPitOutLap & laps.TrackStatus.eq('1')
            & laps.SectorTimesConsistent)
    if 'pit' in missing:
        keep &= False
    results = _results(info['_results'], drivers, grid, laps, code)
    loaded = datetime.now(timezone.utc).isoformat()
    source_times = [_FETCHED_AT.get(_key(endpoint, {'session_key': key}))
                    for endpoint in ('session_result', 'drivers', 'laps', 'stints', 'pit', 'race_control')]
    retrieved = min((value for value in source_times if value), default=loaded)
    note = ('OpenF1 lap comparisons use published lap and sector times, pit flags, race control flags '
            'and identifiable lap deletions. FastF1 accuracy flags and exact pit entry or exit times '
            'are not supplied. Points are not supplied by this classification source.')
    if missing:
        note += f' Unavailable feeds: {", ".join(missing)}.'
    return {'results': results, 'laps': laps, 'clean_laps': laps.loc[keep].copy(),
            'metadata': {'event': info['scheduled']['race'], 'location': info.get('location', ''),
                         'session_code': code, 'session': SESSION_LABELS[code], 'year': int(year),
                         'round': int(info['round']), 'session_key': key,
                         'session_utc': str(info['date_start']), 'source': 'OpenF1',
                         'source_url': f'{BASE}/sessions?session_key={key}',
                         'loaded_utc': loaded, 'retrieved_utc': retrieved,
                         'source_retrieved_utc': retrieved,
                         'refresh_requested': bool(refresh), 'timing_status': 'Published session results',
                         'is_complete': True, 'classification_kind': 'Published session classification',
                         'has_results': bool(results.position.notna().any()),
                         'deleted_laps_checked': checked, 'quality_note': note}}
