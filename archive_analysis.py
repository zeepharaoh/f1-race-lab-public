"""Session analysis from the public TracingInsights archive."""
from datetime import datetime, timezone
import math

import pandas as pd

from weekend import (
    RESULT_COLUMNS, SESSION_LABELS, SessionUnavailableError,
    canonical_team, normalize_session_code,
)


def _load_archive_session(*args, **kwargs):
    from archive_source import load_archive_session
    return load_archive_session(*args, **kwargs)


def _missing(value):
    return value is None or isinstance(value, str) and value.strip().lower() in {
        '', 'none', 'null', 'nan', 'nat',
    }


def _number(value):
    if isinstance(value, bool) or _missing(value):
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _number_id(value):
    number = _number(value)
    return str(int(number)) if pd.notna(number) and number > 0 and number.is_integer() else ''


def _boolean(value):
    return value if isinstance(value, bool) else pd.NA


def _text(value):
    return '' if _missing(value) else str(value).strip()


def _pit_flag(value, present):
    if not present:
        return pd.NA
    if _missing(value):
        return False
    number = _number(value)
    return bool(number >= 0) if pd.notna(number) else pd.NA


def _timedelta(value, *, positive=False):
    number = _number(value)
    if pd.isna(number) or number < 0 or positive and number == 0:
        return pd.NaT
    return pd.to_timedelta(number, unit='s')


def _normalise_laps(columns, driver_rows):
    if not isinstance(columns, dict) or not columns:
        raise SessionUnavailableError('Archive lap timing is not published yet')
    lengths = {len(values) for values in columns.values() if isinstance(values, list)}
    if len(lengths) != 1 or any(not isinstance(values, list) for values in columns.values()):
        raise ValueError('Archive lap columns have inconsistent lengths')
    count = next(iter(lengths))
    if not count:
        raise SessionUnavailableError('Archive lap timing is not published yet')
    if not {'drv', 'dNum', 'lap', 'time'}.issubset(columns):
        raise ValueError('Archive lap identity columns are missing')
    drivers, codes = {}, set()
    for source in driver_rows:
        if not isinstance(source, dict):
            raise ValueError('Invalid archive driver record')
        number, code = _number_id(source.get('dn')), _text(source.get('driver')).upper()
        if not number or not code or number in drivers or code in codes:
            raise ValueError('Invalid or duplicate archive driver')
        drivers[number] = source
        codes.add(code)
    records, seen = [], set()
    for index in range(count):
        row = {key: values[index] for key, values in columns.items()}
        number, code = _number_id(row.get('dNum')), _text(row.get('drv')).upper()
        lap = _number(row.get('lap'))
        if not number or not code or pd.isna(lap) or lap <= 0 or not lap.is_integer():
            raise ValueError('Invalid archive lap identity')
        identity = (number, int(lap))
        if identity in seen:
            raise ValueError('Duplicate archive driver lap')
        seen.add(identity)
        driver = drivers.get(number, {})
        if driver and _text(driver.get('driver')).upper() != code:
            raise ValueError('Archive driver and lap identities disagree')
        duration = _number(row.get('time'))
        duration = duration if duration > 0 else math.nan
        sectors = [_number(row.get(f's{part}')) for part in (1, 2, 3)]
        consistent = (pd.notna(duration) and all(pd.notna(value) and value > 0 for value in sectors)
                      and abs(sum(sectors) - duration) <= .05)
        start = pd.to_datetime(row.get('lSD') if not _missing(row.get('lSD')) else None,
                               utc=True, errors='coerce')
        records.append({
            'DriverNumber': number, 'Driver': code,
            'Team': canonical_team(_text(driver.get('team')) or _text(row.get('team'))),
            'LapNumber': int(lap), 'LapTime': _timedelta(duration, positive=True),
            'lap_seconds': duration, 'LapStartTime': _timedelta(row.get('lST')),
            'Time': _timedelta(row.get('sesT')), 'date_start': start,
            'LapStartDate': start, 'Compound': _text(row.get('compound')) or None,
            'TyreLife': _number(row.get('life')), 'Stint': _number(row.get('stint')),
            'Position': _number(row.get('pos')), 'FreshTyre': _boolean(row.get('fresh')),
            'IsAccurate': _boolean(row.get('iacc')), 'Deleted': _boolean(row.get('del')),
            'DeletedReason': _text(row.get('delR')), 'FastF1Generated': _boolean(row.get('ff1G')),
            'PitInTime': _timedelta(row.get('pin')), 'PitOutTime': _timedelta(row.get('pout')),
            'IsPitInLap': _pit_flag(row.get('pin'), 'pin' in columns),
            'IsPitOutLap': _pit_flag(row.get('pout'), 'pout' in columns),
            'TrackStatus': _text(row.get('status')), 'SectorTimesConsistent': bool(consistent),
            'QualifyingSegment': _text(row.get('qs')).upper(),
            **{f'Sector{part}Time': _timedelta(value, positive=True)
               for part, value in enumerate(sectors, 1)},
        })
    laps = pd.DataFrame(records)
    for name in ('LapTime', 'LapStartTime', 'Time', 'PitInTime', 'PitOutTime',
                 'Sector1Time', 'Sector2Time', 'Sector3Time'):
        laps[name] = pd.Series(laps[name].tolist(), dtype='timedelta64[ns]')
    for name in ('IsAccurate', 'Deleted', 'FastF1Generated', 'FreshTyre', 'IsPitInLap', 'IsPitOutLap'):
        laps[name] = laps[name].astype('boolean')
    return laps, drivers


def _valid_laps(laps):
    return (laps.lap_seconds.gt(0) & laps.Deleted.eq(False).fillna(False)
            & laps.FastF1Generated.eq(False).fillna(False)
            & laps.IsPitInLap.eq(False).fillna(False)
            & laps.IsPitOutLap.eq(False).fillna(False))


def _results(laps, drivers, code):
    valid = laps.loc[_valid_laps(laps)]
    participants = set(drivers) | set(laps.DriverNumber)
    records = []
    for number in sorted(participants, key=int):
        own = laps.loc[laps.DriverNumber.eq(number)]
        timed = valid.loc[valid.DriverNumber.eq(number)]
        source = drivers.get(number, {})
        fallback = own.iloc[0] if not own.empty else {}
        driver_code = _text(source.get('driver')) or fallback.get('Driver', number)
        name = ' '.join(part for part in (_text(source.get('fn')), _text(source.get('ln'))) if part)
        segments = {}
        for part in (1, 2, 3):
            selected = timed.loc[timed.QualifyingSegment.isin((f'Q{part}', f'SQ{part}'))]
            segments[f'Q{part}_seconds'] = selected.lap_seconds.min() if code in ('Q', 'SQ') else math.nan
        records.append({
            'position': math.nan, 'driver_number': number, 'code': driver_code,
            'driver': name or driver_code,
            'team': canonical_team(_text(source.get('team')) or fallback.get('Team', '')),
            'best_lap_seconds': timed.lap_seconds.min(), 'gap_seconds': math.nan,
            'lap_count': int(own.lap_seconds.gt(0).sum()), 'timed_lap_count': len(timed),
            'points': math.nan, 'grid': math.nan, 'status': '', **segments,
        })
    result = pd.DataFrame(records, columns=RESULT_COLUMNS)
    if not result.empty:
        result['gap_seconds'] = result.best_lap_seconds - result.best_lap_seconds.min()
        if code.startswith('FP'):
            result['position'] = result.best_lap_seconds.rank(method='min', na_option='keep')
        result = result.sort_values(['position', 'best_lap_seconds', 'driver_number'],
                                    na_position='last').reset_index(drop=True)
    return result


def _build_analysis(bundle, year, round_number, session_code, *, refresh=False):
    code = normalize_session_code(session_code)
    scheduled = bundle['scheduled']
    start = pd.to_datetime(scheduled.get('session_utc'), utc=True, errors='coerce')
    if pd.isna(start) or start > pd.Timestamp.now(tz='UTC'):
        raise SessionUnavailableError('Archive session has not reached its scheduled start')
    if scheduled.get('code', code) != code or int(scheduled.get('round', round_number)) != int(round_number):
        raise ValueError('Archive session identity does not match the selection')
    laps, drivers = _normalise_laps(bundle['lap_columns'], bundle['drivers'])
    results = _results(laps, drivers, code)
    keep = (_valid_laps(laps) & laps.IsAccurate.eq(True).fillna(False)
            & laps.TrackStatus.eq('1') & laps.SectorTimesConsistent
            & laps.LapStartTime.notna() & laps.Time.notna() & laps.date_start.notna())
    loaded = datetime.now(timezone.utc).isoformat()
    note = ('Lap comparisons use published FastF1 accuracy, deletion, generated lap and track status flags, '
            'sector consistency and pit entry or exit times. Unknown flags are excluded from clean pace. '
            'The archive contains lap timing, not an official session classification. '
            'Points, grid positions and final results are unavailable from this source.')
    if code in ('Q', 'SQ'):
        note += ' Qualifying segment times are shown only where the archive supplies explicit segment labels.'
    return {
        'results': results, 'laps': laps, 'clean_laps': laps.loc[keep].copy(),
        'metadata': {
            'event': scheduled['race'], 'location': scheduled.get('location', ''),
            'session_code': code, 'session': SESSION_LABELS[code], 'year': int(year),
            'round': int(round_number), 'session_utc': start.isoformat(),
            'source': 'TracingInsights / FastF1 archive', 'source_url': bundle['source_url'],
            'loaded_utc': loaded, 'retrieved_utc': bundle['retrieved_utc'],
            'source_retrieved_utc': bundle['retrieved_utc'], 'published_utc': bundle['published_utc'],
            'archive_revision': bundle['revision'], 'is_live': False, 'archived': True,
            'cached_fallback': bool(bundle.get('cached_fallback', False)),
            'refresh_requested': bool(refresh), 'timing_status': 'Published session archive',
            'completion_status': 'Published archive; official finalisation not supplied',
            'classification_available': bool(code.startswith('FP')),
            'classification_kind': ('Best valid lap order' if code.startswith('FP')
                                    else 'Classification unavailable in lap archive'),
            'has_results': bool(results.position.notna().any()),
            'deleted_laps_checked': bool(laps.Deleted.notna().all()), 'quality_note': note,
        },
    }


def load_archive_analysis(year, round_number, session_code, *, refresh=False):
    """Normalise published laps without inferring official classification."""
    code = normalize_session_code(session_code)
    bundle = _load_archive_session(year, round_number, code, refresh=refresh)
    try:
        return _build_analysis(bundle, year, round_number, code, refresh=refresh)
    except (ValueError, TypeError, KeyError, OverflowError):
        from archive_source import ArchiveUnavailableError
        raise ArchiveUnavailableError('unavailable') from None
