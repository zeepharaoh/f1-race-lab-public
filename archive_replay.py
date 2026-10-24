"""Recorded browser replays from the public TracingInsights archive."""
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import json
import math
import re
from pathlib import Path
import threading
import uuid
from types import SimpleNamespace

import pandas as pd

from replay_strategy import StrategyTimeline
from replay_timing import build_lap_timing, lap_validity, unavailable_timing
from web_replay import build_browser_replay, _cached_completed_replay, _thin_records, _track


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'cache' / 'archive_replay'
PROVIDER = 'tracinginsights_archive'
SCHEMA_VERSION = 1


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('Missing archive date')
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def _date(seconds):
    return pd.Timestamp(seconds, unit='s', tz='UTC').round('us').isoformat()


def _rows(columns):
    if not isinstance(columns, dict) or not columns or any(not isinstance(value, list) for value in columns.values()):
        raise ValueError('Invalid archive columns')
    sizes = {len(value) for value in columns.values()}
    if len(sizes) != 1:
        raise ValueError('Unaligned archive columns')
    return [dict(zip(columns, values)) for values in zip(*columns.values())]


def _clock(archive, rows):
    scheduled = _timestamp(archive['scheduled']['session_utc'])
    dated, offsets = [], []
    for row in rows:
        if row.get('lSD') in (None, '', 'None'):
            continue
        start = _timestamp(row['lSD']).timestamp()
        relative, finish = _number(row.get('lST')), _number(row.get('sesT'))
        if relative is None or finish is None or not 0 <= finish - relative <= 86400:
            raise ValueError('Invalid archive lap clock')
        if abs(start - scheduled.timestamp()) > 86400:
            raise ValueError('Archive lap date does not match session')
        offsets.append(start - relative)
        dated.append((row, start, start + finish - relative))
    if not dated or max(offsets) - min(offsets) > .05:
        raise ValueError('Archive clock is unavailable or inconsistent')
    origin, ending = min(item[1] for item in dated), max(item[2] for item in dated)
    if ending <= origin or ending > datetime.now(timezone.utc).timestamp():
        raise ValueError('Archive replay is not completed')
    return dated, offsets[0], origin, ending


def _telemetry(archive, year, row, start, finish, payload):
    tel = payload.get('tel') if isinstance(payload, dict) else None
    expected = f'{year}-{archive["archive_event"]}-{archive["archive_session"]}-{row["drv"]}-{int(row["lap"])}'
    if not isinstance(tel, dict) or tel.get('dataKey') != expected:
        raise ValueError('Archive telemetry identity mismatch')
    fields = ('time', 'x', 'y')
    if any(not isinstance(tel.get(key), list) for key in fields):
        raise ValueError('Missing archive position channels')
    count = len(tel['time'])
    if not count or any(len(value) != count for value in tel.values() if isinstance(value, list)):
        raise ValueError('Unaligned archive telemetry')
    samples, previous = [], -math.inf
    for index, value in enumerate(tel['time']):
        elapsed = _number(value)
        if elapsed is None or elapsed < 0 or elapsed < previous or start + elapsed > finish + .5:
            raise ValueError('Invalid archive telemetry clock')
        previous = elapsed
        sample = {'date': _date(start + elapsed)}
        for key in ('x', 'y', 'speed', 'gear', 'throttle', 'brake', 'drs'):
            values = tel.get(key)
            sample[key] = _number(values[index]) if isinstance(values, list) else None
        if sample['brake'] is not None:
            sample['brake'] = sample['brake'] * 100 if 0 <= sample['brake'] <= 1 else sample['brake']
        sample['drs'] = {0: 0, 1: 12}.get(sample['drs'])
        sample['n_gear'] = sample.pop('gear')
        samples.append(sample)
    return _thin_records(samples, ('x', 'y', 'speed', 'n_gear', 'throttle', 'brake', 'drs'))


def _strategy(rows, t0, origin):
    laps = []
    for row in rows:
        lap = {'Driver': row['drv'], 'LapNumber': _integer(row.get('lap')),
               'Compound': row.get('compound'), 'TyreLife': _number(row.get('life'))}
        for source, target in (('lST', 'LapStartTime'), ('sesT', 'Time'),
                               ('pin', 'PitInTime'), ('pout', 'PitOutTime')):
            seconds = _number(row.get(source))
            lap[target] = pd.to_timedelta(seconds, unit='s') if seconds is not None else pd.NaT
        laps.append(lap)
    relative = origin - t0
    # The marker calibrates clocks only; it is not emitted as a track flag.
    session = SimpleNamespace(laps=pd.DataFrame(laps), session_start_time=pd.to_timedelta(relative, unit='s'),
                              track_status=pd.DataFrame({'Time': [pd.to_timedelta(0., unit='s')], 'Status': ['1']}))
    return StrategyTimeline.from_session(session, {'track_statuses': [{'status': '1', 'start_time': -relative}]})


def _lap_timing(archive, rows, dated, origin, ending, code):
    roster = {row['driver']: _integer(row['dn']) for row in archive['drivers']}
    timed = []
    for row, start, finish in dated:
        if row.get('drv') not in roster or _integer(row.get('dNum')) != roster[row['drv']]:
            continue
        pit = {}
        for key in ('pin', 'pout'):
            value = row.get(key)
            pit[key] = (False if value in (None, '', 'None') else True if _number(value) is not None else None) if key in row else None
        coverage_start = _number(row.get('pout'))
        if coverage_start is not None:
            coverage_start = start + coverage_start - _number(row['lST'])
        timed.append({'code': row['drv'], 'lap': row.get('lap'), 'start': start - origin,
                      'end': finish - origin, 'time': _number(row.get('time')),
                      'segment': row.get('qs'),
                      'coverage_start': coverage_start - origin if coverage_start is not None else None,
                      'valid': lap_validity(deleted=row.get('del'), generated=row.get('ff1G'),
                                            pit_in=pit['pin'], pit_out=pit['pout'], accurate=row.get('iacc'))})
    return build_lap_timing(timed, ending - origin, qualifying=code in ('Q', 'SQ'),
                           note='Unknown deletion, generated lap or pit flags are excluded from valid lap timing.')


def _enrich_timing(payload, archive, code):
    try:
        rows = _rows(archive['lap_columns'])
        dated, _, origin, ending = _clock(archive, rows)
        if (abs(_timestamp(payload['metadata']['start_utc']).timestamp() - origin) > .002
                or abs(_timestamp(payload['metadata']['end_utc']).timestamp() - ending) > .002):
            return unavailable_timing('Saved replay and lap timing clocks do not match.')
        return _lap_timing(archive, rows, dated, origin, ending, code)
    except (ValueError, KeyError, TypeError, OverflowError):
        return unavailable_timing()


def build_archive_replay(archive, year, round_number, session_code, *, fetch_file=None, workers=4):
    from archive_source import ArchiveUnavailableError, fetch_archive_file
    try:
        return _build_archive_replay(archive, int(year), int(round_number), str(session_code).upper(),
                                     fetch_file or fetch_archive_file, max(1, min(4, int(workers))))
    except ArchiveUnavailableError:
        raise
    except (AttributeError, ValueError, KeyError, TypeError, OverflowError):
        raise ArchiveUnavailableError('unavailable') from None


def _build_archive_replay(archive, year, round_number, code, fetch_file, workers):
    from archive_source import ArchiveUnavailableError
    rows = _rows(archive['lap_columns'])
    dated, t0, origin, ending = _clock(archive, rows)
    roster = {row['driver']: int(row['dn']) for row in archive['drivers']}
    identities = set()
    for row in rows:
        identity = (row['drv'], _integer(row.get('lap')))
        if (identity in identities or identity[0] not in roster or identity[1] is None
                or identity[1] < 1 or _integer(row.get('dNum')) != roster[identity[0]]):
            raise ValueError('Invalid archive lap identity')
        identities.add(identity)
    warnings, stamps = [], [archive['retrieved_utc']]
    feed_stamps = {'archive_metadata': archive['retrieved_utc']}
    optional = {}
    for filename in ('weather.json', 'rcm.json'):
        try:
            data, stamp = fetch_file(archive, filename)
            optional[filename] = _rows(data)
            stamps.append(stamp)
            feed_stamps[filename] = stamp
        except ArchiveUnavailableError as error:
            if error.reason in ('access_limited', 'unavailable'):
                raise
            optional[filename] = []
            warnings.append(f'{filename} is unavailable in the archive.')
        except (AttributeError, ValueError, KeyError, TypeError):
            optional[filename] = []
            warnings.append(f'{filename} has no usable archive records.')
    stop = threading.Event()
    def load_lap(item):
        row, start, finish = item
        if stop.is_set():
            return None
        path = f'{row["drv"]}/{int(row["lap"])}_tel.json'
        try:
            data, stamp = fetch_file(archive, path)
            return row, _telemetry(archive, year, row, start, finish, data), stamp, None
        except ArchiveUnavailableError as error:
            if error.reason in ('access_limited', 'unavailable'):
                stop.set()
                raise
            return row, [], None, path
        except (AttributeError, ValueError, KeyError, TypeError, OverflowError):
            return row, [], None, path
    locations, cars, missing = defaultdict(list), defaultdict(list), []
    items = iter(dated)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = deque()
        for _ in range(workers):
            item = next(items, None)
            if item is not None:
                pending.append(executor.submit(load_lap, item))
        while pending:
            result = pending.popleft().result()
            if result is not None:
                row, samples, stamp, failure = result
                if failure:
                    missing.append(failure)
                else:
                    number = roster[row['drv']]
                    locations[number].extend(samples)
                    cars[number].extend(samples)
                    stamps.append(stamp)
            item = next(items, None) if not stop.is_set() else None
            if item is not None:
                pending.append(executor.submit(load_lap, item))
    if missing:
        warnings.append(f'{len(missing)} lap telemetry files are missing or invalid; their positions remain unavailable.')
    if len(dated) < len(rows):
        warnings.append(f'{len(rows) - len(dated)} laps have no UTC start time and cannot be placed in the replay.')
    telemetry_stamps = stamps[len(feed_stamps):]
    if telemetry_stamps:
        feed_stamps['telemetry'] = min(telemetry_stamps, key=_timestamp)
    drivers = [{'driver_number': row['dn'], 'name_acronym': row['driver'],
                'full_name': ' '.join(str(row.get(key) or '') for key in ('fn', 'ln')).strip() or row['driver'],
                'team_name': row['team'], 'team_colour': row.get('tc')}
               for row in archive['drivers']]
    laps, checkpoints, reference = [], [], []
    for row, start, finish in dated:
        duration = _number(row.get('time'))
        lap = {'driver_number': roster[row['drv']], 'lap_number': _integer(row['lap']),
               'date_start': _date(start), 'lap_duration': duration,
               'is_pit_out_lap': _number(row.get('pout')) is not None}
        laps.append(lap)
        position = _integer(row.get('pos'))
        if code in ('R', 'S') and position is not None and position > 0:
            checkpoints.append({'driver_number': roster[row['drv']], 'position': position, 'date': _date(finish)})
        if (row.get('del') is False and row.get('iacc') is True and row.get('ff1G') is False
                and _number(row.get('pin')) is None and _number(row.get('pout')) is None
                and str(row.get('status')) == '1' and duration is not None and duration > 0):
            reference.append(lap)
    weather = []
    for row in optional['weather.json']:
        seconds = _number(row.get('wT'))
        if seconds is not None:
            weather.append({'date': _date(t0 + seconds), **{
                target: _number(row.get(source)) for source, target in (('wAT', 'air_temperature'), ('wTT', 'track_temperature'),
                ('wH', 'humidity'), ('wP', 'pressure'), ('wR', 'rainfall'), ('wWD', 'wind_direction'), ('wWS', 'wind_speed'))}})
    controls = []
    for row in optional['rcm.json']:
        try:
            date = _timestamp(row.get('time')).isoformat()
        except (ValueError, TypeError):
            continue
        controls.append({'date': date, 'category': row.get('cat'), 'message': row.get('msg'),
                         'flag': None if row.get('flag') == 'None' else row.get('flag'),
                         'scope': row.get('scope'), 'driver_number': _integer(row.get('dNum'))})
    session = {'date_start': _date(origin), 'date_end': _date(ending), 'year': year, 'round': round_number,
               'code': code, 'scheduled': archive['scheduled'], 'location': archive['scheduled'].get('location', ''),
               'session_name': archive['archive_session']}
    payload = build_browser_replay(session, drivers, laps, locations, positions=checkpoints,
                                   car_data=cars, weather=weather, race_control=controls, warnings=warnings,
                                   pit_available=False)
    payload['lap_timing'] = _lap_timing(archive, rows, dated, origin, ending, code)
    strategy = _strategy(rows, t0, origin)
    for driver, values in payload['cars'].items():
        states = [strategy.at(driver, seconds) for seconds in payload['times']]
        for key in ('compound', 'age', 'stops', 'last_stop_lap', 'in_pit'):
            values[key] = [getattr(state, key) for state in states]
    payload['track'] = _track(reference, locations, origin)
    warnings = [warning for warning in payload['metadata']['warnings']
                if warning != 'A complete lap outline is unavailable; only recorded car positions can be shown.']
    if not payload['track']['x']:
        warnings.append('A complete valid recorded lap outline is unavailable.')
    metadata = payload['metadata']
    metadata.update(provider=PROVIDER, source='TracingInsights public archive / FastF1', archived=True,
                    source_url=archive['source_url'], revision=archive['revision'], archive_revision=archive['revision'],
                    start_utc=_date(origin), end_utc=_date(ending),
                    cached_fallback=bool(archive.get('cached_fallback')),
                    published_utc=archive['published_utc'],
                    session_fingerprint=archive.get('session_fingerprint'),
                    source_retrieved_utc=min(stamps, key=_timestamp), feed_retrieved_utc=feed_stamps,
                    position_definition=('Lap-end checkpoints from recorded timing; not live track order' if code in ('R', 'S')
                                         else 'Session classification is unavailable from this archive.'),
                    gap_definition='Race intervals are unavailable in this archive.',
                    tyre_definition='Recorded lap compound; tyre age counts completed laps and updates on recorded pit exit.',
                    pit_definition='Completed recorded pit-lane visits count on pit exit, including drive-throughs.',
                    drs_definition='Recorded binary DRS is mapped to active or inactive; other values are unavailable.',
                    missing_telemetry_laps=len(missing), placed_laps=len(dated),
                    warnings=list(dict.fromkeys(warnings)))
    for key in ('session_key', 'meeting_key', 'max_interval_age_seconds'):
        metadata.pop(key, None)
    return payload


def _cached_replay(path, year, round_number, code):
    cached = _cached_completed_replay(path, year, round_number, code)
    return cached if cached is not None and cached['metadata'].get('provider') == PROVIDER else None


def _fingerprint(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value) else None


def _save_replay(path, payload):
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        with gzip.open(temporary, 'wt', encoding='utf-8') as output:
            json.dump(payload, output, separators=(',', ':'), allow_nan=False)
        temporary.replace(path)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_archive_replay(year, round_number, session_code, *, refresh=False):
    from archive_source import ArchiveUnavailableError, get_session_fingerprint, load_archive_session
    code = str(session_code).upper()
    path = CACHE / f'{int(year)}_{int(round_number)}_{code}_v{SCHEMA_VERSION}.json.gz'
    try:
        archive = load_archive_session(year, round_number, code, refresh=refresh)
        cached = _cached_replay(path, year, round_number, code)
        current_fingerprint = _fingerprint(archive.get('session_fingerprint'))
        if cached is not None:
            metadata = cached['metadata']
            previous_fingerprint = _fingerprint(metadata.get('session_fingerprint'))
            same_revision = metadata.get('archive_revision') == archive['revision']
            if current_fingerprint and previous_fingerprint is None:
                previous_fingerprint = (current_fingerprint if same_revision else get_session_fingerprint(
                    {**archive, 'revision': metadata.get('archive_revision')}))
                previous_fingerprint = _fingerprint(previous_fingerprint)
                if previous_fingerprint:
                    metadata['session_fingerprint'] = previous_fingerprint
                    _save_replay(path, cached)
            unchanged = (previous_fingerprint == current_fingerprint
                         if previous_fingerprint and current_fingerprint else same_revision)
        else:
            unchanged = False
        if unchanged:
            timing = _enrich_timing(cached, archive, code)
            if cached.get('lap_timing') != timing:
                cached['lap_timing'] = timing
                _save_replay(path, cached)
            if not same_revision:
                cached['metadata']['checked_archive_revision'] = archive['revision']
            if archive.get('cached_fallback'):
                cached['metadata']['cached_fallback'] = True
                cached['metadata']['warnings'] = list(dict.fromkeys([*cached['metadata'].get('warnings', []),
                    'The latest archive version could not be checked. Showing the saved published replay.']))
            return cached
        del cached
        payload = build_archive_replay(archive, year, round_number, code)
    except ArchiveUnavailableError as error:
        cached = _cached_replay(path, year, round_number, code)
        if cached is None:
            raise
        cached['metadata'].update(cached_fallback=True, access_limited=error.reason == 'access_limited',
                                  archive_unavailable_reason=error.reason, access_message=str(error))
        cached['metadata']['warnings'] = list(dict.fromkeys([*cached['metadata'].get('warnings', []),
            'The archive could not be refreshed. Showing a previously saved completed replay.']))
        return cached
    _save_replay(path, payload)
    return payload
