"""Browser replay payloads from recorded OpenF1 timing and location feeds."""
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import re
import time

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'cache' / 'browser_replay'
SCHEMA_VERSION = 1
MAX_POSITION_GAP = 3.0
MAX_INTERVAL_AGE = 10.0


class BrowserReplayUnavailable(ValueError):
    pass


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _integer(value):
    value = _number(value)
    return int(value) if value is not None else None


def _epoch(value):
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
            return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
        except ValueError:
            return None
    stamp = pd.to_datetime(value, utc=True, errors='coerce')
    return stamp.timestamp() if pd.notna(stamp) else None


def _group(rows):
    result = defaultdict(list)
    for row in rows:
        number = _integer(row.get('driver_number'))
        if number is not None:
            result[number].append(row)
    return result


def _thin_records(rows, fields):
    """Keep source samples at one Hz plus every long-gap boundary."""
    parsed = [(stamp, row) for row in rows if (stamp := _epoch(row.get('date'))) is not None]
    parsed.sort(key=lambda item: item[0])
    keep = set()
    for index, (stamp, _) in enumerate(parsed):
        if index == len(parsed) - 1 or math.floor(stamp) != math.floor(parsed[index + 1][0]):
            keep.add(index)
        if index and stamp - parsed[index - 1][0] > MAX_POSITION_GAP:
            keep.update((index - 1, index))
    if parsed:
        keep.update((0, len(parsed) - 1))
    return [{key: parsed[index][1].get(key) for key in ('date', *fields)} for index in sorted(keep)]


def _dated(rows, origin, date_field='date'):
    result = []
    for row in rows:
        stamp = _epoch(row.get(date_field))
        if stamp is not None:
            result.append((stamp - origin, row))
    return sorted(result, key=lambda item: item[0])


def _asof(events, times, max_age=None):
    if not events:
        return [None] * len(times)
    stamps = np.asarray([item[0] for item in events])
    indices = np.searchsorted(stamps, times, side='right') - 1
    result = []
    for current, index in zip(times, indices):
        if index < 0 or (max_age is not None and current - stamps[index] > max_age):
            result.append(None)
        else:
            result.append(events[index][1])
    return result


def _locations(rows, origin, times):
    valid = [(stamp, row) for stamp, row in _dated(rows, origin)
             if _number(row.get('x')) is not None and _number(row.get('y')) is not None]
    if not valid:
        return [None] * len(times), [None] * len(times)
    unique = {stamp: row for stamp, row in valid}
    stamps = np.asarray(sorted(unique))
    rows = [unique[stamp] for stamp in stamps]
    right = np.searchsorted(stamps, times, side='left')
    xs, ys = [], []
    for current, index in zip(times, right):
        if index < len(stamps) and abs(stamps[index] - current) < 1e-5:
            point = rows[index]
            xs.append(round(float(point['x']), 1))
            ys.append(round(float(point['y']), 1))
        elif index == 0 or index == len(stamps) or stamps[index] - stamps[index - 1] > MAX_POSITION_GAP:
            xs.append(None)
            ys.append(None)
        else:
            fraction = (current - stamps[index - 1]) / (stamps[index] - stamps[index - 1])
            xs.append(round(float(rows[index - 1]['x']) + fraction * (float(rows[index]['x']) - float(rows[index - 1]['x'])), 1))
            ys.append(round(float(rows[index - 1]['y']) + fraction * (float(rows[index]['y']) - float(rows[index - 1]['y'])), 1))
    return xs, ys


def _gap(value):
    number = _number(value)
    if number is not None:
        return round(number, 3) if number >= 0 else None
    if isinstance(value, str) and re.fullmatch(r'\+\d+\s+LAPS?', value.strip().upper()):
        return value.strip().upper()
    return None


def _track(laps, locations, origin):
    candidates = sorted((row for row in laps if (_number(row.get('lap_duration')) or 0) > 0
                         and not row.get('is_pit_out_lap')), key=lambda row: float(row['lap_duration']))
    indexed = {}
    for lap in candidates:
        start = _epoch(lap.get('date_start'))
        number = _integer(lap.get('driver_number'))
        if start is None or number not in locations:
            continue
        start -= origin
        finish = start + float(lap['lap_duration'])
        if number not in indexed:
            indexed[number] = _dated(locations[number], origin)
        points = [(stamp, row) for stamp, row in indexed[number]
                  if start <= stamp <= finish and _number(row.get('x')) is not None
                  and _number(row.get('y')) is not None]
        if len(points) < 20 or points[0][0] - start > 3 or finish - points[-1][0] > 3:
            continue
        if any(right[0] - left[0] > MAX_POSITION_GAP for left, right in zip(points, points[1:])):
            continue
        indices = np.linspace(0, len(points) - 1, min(600, len(points))).astype(int)
        return {'x': [round(float(points[index][1]['x']), 1) for index in indices],
                'y': [round(float(points[index][1]['y']), 1) for index in indices],
                'driver_number': number, 'lap_number': _integer(lap.get('lap_number'))}
    return {'x': [], 'y': [], 'driver_number': None, 'lap_number': None}


def _strategy(laps, stints, pit, origin, times, pit_available=True):
    lap_events = _dated(laps, origin, 'date_start')
    current_laps = _asof(lap_events, times)
    completed = []
    for start, row in lap_events:
        duration = _number(row.get('lap_duration'))
        lap = _integer(row.get('lap_number'))
        if duration is not None and duration > 0 and lap is not None:
            completed.append((start + duration, lap))
    completed.sort()
    completions = np.asarray([item[0] for item in completed])
    stint_events = []
    for stint in stints:
        lap_start = _integer(stint.get('lap_start'))
        starts = [stamp for stamp, row in lap_events if _integer(row.get('lap_number')) == lap_start]
        if starts:
            stint_events.append((min(starts), stint))
    stint_at = _asof(sorted(stint_events, key=lambda item: item[0]), times)
    visits = {}
    for stamp, row in _dated(pit, origin):
        lap = _integer(row.get('lap_number'))
        if stamp >= 0 and lap is not None and lap > 0:
            visits.setdefault(lap, (stamp, row))
    visit_events = sorted(visits.values(), key=lambda item: item[0])
    visit_dates = np.asarray([item[0] for item in visit_events])
    result = {key: [] for key in ('lap', 'compound', 'age', 'stops', 'last_stop_lap', 'in_pit')}
    for index, current in enumerate(times):
        lap = _integer(current_laps[index].get('lap_number')) if current_laps[index] else None
        stint = stint_at[index]
        compound, age = None, None
        if stint:
            candidate = str(stint.get('compound', '')).upper()
            compound = candidate if candidate in {'SOFT', 'MEDIUM', 'HARD', 'INTERMEDIATE', 'WET'} else None
            initial = _integer(stint.get('tyre_age_at_start'))
            beginning = _integer(stint.get('lap_start'))
            count = int(np.searchsorted(completions, current, side='right'))
            last_completed = completed[count - 1][1] if count else 0
            if initial is not None and initial >= 0 and beginning is not None:
                age = initial + max(0, last_completed - beginning + 1)
        stops = int(np.searchsorted(visit_dates, current, side='right')) if pit_available else None
        last = _integer(visit_events[stops - 1][1].get('lap_number')) if stops else None
        for key, value in {'lap': lap, 'compound': compound, 'age': age,
                           'stops': stops, 'last_stop_lap': last, 'in_pit': None}.items():
            result[key].append(value)
    return result


def build_browser_replay(session, drivers, laps, locations, *, positions=(), intervals=(),
                         stints=(), pit=(), car_data=None, weather=(), race_control=(),
                         sample_seconds=1.0, max_frames=12000, warnings=(), pit_available=True):
    """Build one clock; every car column has exactly len(times) entries."""
    from weekend import canonical_team
    origin, ending = _epoch(session.get('date_start')), _epoch(session.get('date_end'))
    if origin is None or ending is None or ending <= origin:
        raise BrowserReplayUnavailable('The session replay time range is unavailable')
    duration = ending - origin
    step = max(float(sample_seconds), duration / max(1, max_frames - 1))
    times = np.round(np.arange(0, duration + 1e-6, step), 3)
    if not len(times) or duration - times[-1] > 1e-3:
        times = np.r_[times, round(duration, 3)]
    groups = {key: _group(value) for key, value in {'laps': laps, 'position': positions,
              'intervals': intervals, 'stints': stints, 'pit': pit}.items()}
    location_groups = locations if isinstance(locations, dict) else _group(locations)
    car_groups = car_data or {}
    race = session.get('code', session.get('session_code')) in ('R', 'S')
    cars, roster, unavailable = {}, [], []
    position_dates, interval_dates = {}, {}
    warning_list = list(dict.fromkeys(warnings))
    for driver in drivers:
        number = _integer(driver.get('driver_number'))
        if number is None:
            continue
        code = str(driver.get('name_acronym') or number)
        if code in cars:
            code = str(number)
        color = str(driver.get('team_colour') or 'A7B4C6').lstrip('#')
        if not re.fullmatch('[0-9A-Fa-f]{6}', color):
            color = 'A7B4C6'
        roster.append({'code': code, 'number': number, 'name': str(driver.get('full_name') or code),
                       'team': canonical_team(str(driver.get('team_name') or 'Unknown')), 'color': '#' + color})
        x, y = _locations(location_groups.get(number, []), origin, times)
        if not any(value is not None for value in x):
            unavailable.append(code)
        columns = {'x': x, 'y': y}
        columns.update(_strategy(groups['laps'].get(number, []), groups['stints'].get(number, []),
                                 groups['pit'].get(number, []), origin, times, pit_available))
        position_at = _asof(_dated(groups['position'].get(number, []), origin), times)
        columns['position'] = [_integer(row.get('position')) if row else None for row in position_at]
        gap_at = _asof(_dated(groups['intervals'].get(number, []), origin), times, MAX_INTERVAL_AGE) if race else [None] * len(times)
        position_dates[code] = [_epoch(row.get('date')) if row else None for row in position_at]
        interval_dates[code] = [_epoch(row.get('date')) if row else None for row in gap_at]
        columns['ahead'] = [_gap(row.get('interval')) if row else None for row in gap_at]
        columns['leader'] = [_gap(row.get('gap_to_leader')) if row else None for row in gap_at]
        telemetry_at = _asof(_dated(car_groups.get(number, []), origin), times, MAX_POSITION_GAP)
        for field, source in {'speed': 'speed', 'gear': 'n_gear', 'throttle': 'throttle',
                              'brake': 'brake', 'drs': 'drs'}.items():
            columns[field] = [round(value, 1) if row and (value := _number(row.get(source))) is not None else None
                              for row in telemetry_at]
        columns['behind'] = [None] * len(times)
        cars[code] = columns
    if not cars or len(unavailable) == len(cars):
        raise BrowserReplayUnavailable('Recorded car locations are not available for this session yet')
    if race:
        for index in range(len(times)):
            ranks = defaultdict(list)
            for code, columns in cars.items():
                position = columns['position'][index]
                if position is not None and position > 0:
                    ranks[position].append(code)
            for position, codes in ranks.items():
                previous = ranks.get(position - 1, [])
                if len(codes) == len(previous) == 1:
                    code = codes[0]
                    source_time = interval_dates[code][index]
                    changes = [position_dates[neighbor][index] for neighbor in (code, previous[0])
                               if position_dates[neighbor][index] is not None]
                    if source_time is not None and changes and source_time < max(changes):
                        cars[code]['ahead'][index] = None
            for position, codes in ranks.items():
                next_codes = ranks.get(position + 1, [])
                if len(codes) == len(next_codes) == 1:
                    cars[codes[0]]['behind'][index] = cars[next_codes[0]]['ahead'][index]
    track = _track(laps, location_groups, origin)
    if not track['x']:
        warning_list.append('A complete lap outline is unavailable; only recorded car positions can be shown.')
    if unavailable:
        warning_list.append('No location coverage: ' + ', '.join(unavailable))
    no_telemetry = [code for code, car in cars.items() if all(value is None for value in car['speed'])]
    if no_telemetry:
        warning_list.append('No car telemetry coverage: ' + ', '.join(no_telemetry))
    if all(value is None for car in cars.values() for value in car['position']):
        warning_list.append('Recorded classification positions are unavailable.')
    if race and all(value is None for car in cars.values() for value in car['ahead']):
        warning_list.append('Recorded race intervals are unavailable.')
    metadata = {'schema_version': SCHEMA_VERSION, 'source': 'OpenF1 recorded timing and location',
                'source_url': 'https://openf1.org/docs/', 'session_key': session.get('session_key'),
                'meeting_key': session.get('meeting_key'), 'year': session.get('year'),
                'round': session.get('round'), 'event': session.get('scheduled', {}).get('race') or session.get('meeting_name') or session.get('location', ''),
                'location': session.get('location', ''), 'session': session.get('session_name', ''),
                'session_code': session.get('code', session.get('session_code')), 'is_live': False,
                'start_utc': pd.Timestamp(origin, unit='s', tz='UTC').isoformat(),
                'end_utc': pd.Timestamp(ending, unit='s', tz='UTC').isoformat(),
                'retrieved_utc': datetime.now(timezone.utc).isoformat(),
                'duration_seconds': round(duration, 3), 'sample_seconds': round(step, 6),
                'total_laps': max((_integer(row.get('lap_number')) or 0 for row in laps), default=0),
                'max_position_gap_seconds': MAX_POSITION_GAP, 'max_interval_age_seconds': MAX_INTERVAL_AGE,
                'position_definition': 'Recorded race position' if race else 'Recorded session classification; not track order',
                'gap_definition': 'Recorded race intervals; missing after 10 seconds without an update' if race else 'Race gaps are unavailable outside race and Sprint sessions',
                'tyre_definition': 'Compound follows recorded stint lap; age counts completed laps from stint start; lap starts are approximate',
                'pit_definition': 'Reported pit-lane visits counted at source timestamps, including drive-throughs; entry/exit timing is unavailable',
                'coverage': {code: {'located_frames': sum(value is not None for value in car['x']),
                                    'telemetry_frames': sum(value is not None for value in car['speed']),
                                    'position_frames': sum(value is not None for value in car['position'])}
                             for code, car in cars.items()},
                'warnings': warning_list}
    def dated_output(rows):
        return [{**{key: value for key, value in row.items() if key not in ('date', 'meeting_key', 'session_key')},
                 't': round(stamp, 3)} for stamp, row in _dated(rows, origin) if 0 <= stamp <= duration]
    return {'metadata': metadata, 'track': track, 'drivers': roster, 'times': times.tolist(),
            'cars': cars, 'weather': dated_output(weather), 'race_control': dated_output(race_control),
            'unavailable': unavailable}


def _finite_json_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Invalid JSON number')
    return number


def _retrieval_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None or timestamp > datetime.now(timezone.utc):
            return None
        return timestamp
    except (ValueError, TypeError, OverflowError):
        return None


def _cached_completed_replay(path, year, round_number, code):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            payload = json.load(source, parse_constant=_finite_json_number, parse_float=_finite_json_number)
        metadata = payload['metadata']
        start, end = _epoch(metadata.get('start_utc')), _epoch(metadata.get('end_utc'))
        matches = (metadata.get('schema_version') == SCHEMA_VERSION
                   and metadata.get('year') == int(year) and metadata.get('round') == int(round_number)
                   and metadata.get('session_code') == code and metadata.get('is_live') is False)
        if not matches or start is None or end is None or not start < end <= datetime.now(timezone.utc).timestamp():
            return None
        retrieved = _retrieval_timestamp(metadata.get('retrieved_utc'))
        if retrieved is None or end > retrieved.timestamp():
            return None
        source_stamp = metadata.get('source_retrieved_utc')
        source_time = _retrieval_timestamp(source_stamp) if source_stamp is not None else None
        if source_stamp is not None and (source_time is None or source_time > retrieved):
            return None
        feeds = metadata.get('feed_retrieved_utc', {})
        if not isinstance(feeds, dict):
            return None
        for stamp in feeds.values():
            timestamp = _retrieval_timestamp(stamp)
            if timestamp is None or timestamp > retrieved or (source_time is not None and source_time > timestamp):
                return None
        times, cars = payload['times'], payload['cars']
        if not isinstance(times, list) or not times or not isinstance(cars, dict) or not cars:
            return None
        if not isinstance(payload.get('drivers'), list) or not payload['drivers']:
            return None
        if any(not isinstance(car, dict) or any(not isinstance(values, list) or len(values) != len(times)
                                               for values in car.values())
               or len(car.get('x', [])) != len(times) or len(car.get('y', [])) != len(times)
               for car in cars.values()):
            return None
        if not any(value is not None for car in cars.values() for value in car['x']):
            return None
        return payload
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        return None


def _access_limited_replay(payload, *, completed=False, reason=None, message=None):
    metadata = payload['metadata']
    metadata.update(access_limited=True, cached_fallback=True)
    if reason is not None:
        metadata['access_reason'] = reason
    if message is not None:
        metadata['access_message'] = message
    warning = ('OpenF1 access is restricted. Showing a previously saved completed replay.' if completed else
               'OpenF1 access is restricted. This replay uses saved source data.')
    metadata['warnings'] = list(dict.fromkeys([*metadata.get('warnings', []), warning]))
    return payload


def load_browser_replay(year, round_number, session_code, *, refresh=False):
    from openf1_data import OpenF1AccessError
    code = str(session_code).upper()
    cache_file = CACHE / f'{int(year)}_{int(round_number)}_{code}_v{SCHEMA_VERSION}.json.gz'
    if not refresh and cache_file.exists() and time.time() - cache_file.stat().st_mtime < 3600:
        cached = _cached_completed_replay(cache_file, year, round_number, code)
        if cached is not None:
            ttl = 60 if cached['metadata'].get('warnings') or cached['metadata'].get('access_limited') else 3600
            if time.time() - cache_file.stat().st_mtime < ttl:
                return cached
        del cached
    try:
        return _load_browser_replay(year, round_number, code, cache_file, refresh=refresh)
    except OpenF1AccessError as error:
        cached = _cached_completed_replay(cache_file, year, round_number, code)
        if cached is None:
            raise
        return _access_limited_replay(cached, completed=True, reason=getattr(error, 'access_reason', None),
                                      message=str(error))


def _load_browser_replay(year, round_number, code, cache_file, *, refresh=False):
    from openf1_data import OpenF1AccessError, fetch, fetched_at, get_source_access_status, resolve_openf1_session
    session = resolve_openf1_session(year, round_number, code, refresh=refresh)
    session = {**session, 'year': int(year), 'round': int(round_number), 'code': code}
    end = _epoch(session.get('date_end'))
    if end is None or end > datetime.now(timezone.utc).timestamp():
        raise BrowserReplayUnavailable('Replay becomes available after the session finishes and recorded data is published')
    key = session['session_key']
    warnings = []
    source_stamps = []
    feed_stamps = {}
    limited_feeds = set()
    limited_statuses = []
    def remember(endpoint, **params):
        status = get_source_access_status(endpoint, **params)
        if status.get('access_limited'):
            limited_feeds.add(endpoint)
            limited_statuses.append(status)
        stamp = fetched_at(endpoint, **params)
        if stamp is not None:
            source_stamps.append(stamp)
            if endpoint not in feed_stamps or _epoch(stamp) < _epoch(feed_stamps[endpoint]):
                feed_stamps[endpoint] = stamp
    remember('sessions', year=int(year))
    remember('session_result', session_key=int(key))
    def recorded(endpoint, **params):
        rows = fetch(endpoint, session_key=key, refresh=refresh, **params)
        remember(endpoint, session_key=key, **params)
        return rows
    def optional(endpoint, **params):
        try:
            return recorded(endpoint, **params), True
        except OpenF1AccessError:
            raise
        except (requests.RequestException, ValueError) as error:
            warnings.append(f'{endpoint} feed unavailable ({type(error).__name__}).')
            return [], False
    drivers = recorded('drivers')
    laps = recorded('laps')
    feeds = {}
    availability = {}
    endpoints = ['position', 'stints', 'pit', 'weather', 'race_control']
    if code in ('R', 'S'):
        endpoints.append('intervals')
    for endpoint in endpoints:
        feeds[endpoint], availability[endpoint] = optional(endpoint)
    locations, telemetry = {}, {}
    # Requests adds '='; OpenF1 reconstructs these as date>= and date<=.
    bounds = {'date>': session['date_start'], 'date<': session['date_end']}
    for driver in drivers:
        number = _integer(driver.get('driver_number'))
        if number is None:
            continue
        rows, _ = optional('location', driver_number=number, **bounds)
        locations[number] = _thin_records(rows, ('x', 'y'))
        del rows
        rows, _ = optional('car_data', driver_number=number, **bounds)
        telemetry[number] = _thin_records(rows, ('speed', 'n_gear', 'throttle', 'brake', 'drs'))
        del rows
    payload = build_browser_replay(session, drivers, laps, locations, positions=feeds['position'],
        intervals=feeds.get('intervals', []), stints=feeds['stints'], pit=feeds['pit'],
        car_data=telemetry, weather=feeds['weather'], race_control=feeds['race_control'],
        warnings=warnings, pit_available=availability['pit'])
    payload['metadata']['source_retrieved_utc'] = min(source_stamps, key=_epoch) if source_stamps else None
    payload['metadata']['feed_retrieved_utc'] = feed_stamps
    if limited_feeds:
        status = next((item for item in limited_statuses if item.get('access_reason') == 'live_access_restricted'),
                      limited_statuses[0])
        payload = _access_limited_replay(payload, reason=status.get('access_reason'),
                                         message=status.get('access_message'))
        payload['metadata']['access_limited_feeds'] = sorted(limited_feeds)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_suffix('.tmp')
        with gzip.open(temporary, 'wt', encoding='utf-8') as destination:
            json.dump(payload, destination, separators=(',', ':'), allow_nan=False)
        temporary.replace(cache_file)
    except OSError:
        pass
    return payload
