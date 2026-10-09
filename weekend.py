"""F1 weekend schedules and session analysis."""
from contextlib import nullcontext
from datetime import datetime, timezone

import pandas as pd
import fastf1
from fastf1.exceptions import DataNotLoadedError, NoLapDataError

from data import ROOT, clean_laps

SESSION_LABELS = {
    'FP1': 'Practice 1', 'FP2': 'Practice 2', 'FP3': 'Practice 3',
    'Q': 'Qualifying', 'SQ': 'Sprint Qualifying', 'S': 'Sprint', 'R': 'Race',
}
SCHEDULE_COLUMNS = ['round', 'race', 'location', 'code', 'session', 'session_utc']
RESULT_COLUMNS = [
    'position', 'driver_number', 'code', 'driver', 'team', 'best_lap_seconds',
    'gap_seconds', 'lap_count', 'timed_lap_count', 'points', 'grid', 'status',
    'Q1_seconds', 'Q2_seconds', 'Q3_seconds',
]
TEAM_ALIASES = {
    'Red Bull Racing': 'Red Bull', 'Racing Bulls': 'RB F1 Team',
    'Alpine': 'Alpine F1 Team', 'Haas': 'Haas F1 Team',
    'Cadillac': 'Cadillac F1 Team',
}


class SessionUnavailableError(ValueError):
    """Session timing is not available yet."""


def normalize_session_code(value):
    """Accept a session code or name."""
    text = str(value).strip()
    if text.upper() in SESSION_LABELS:
        return text.upper()
    for code, label in SESSION_LABELS.items():
        if text.casefold() == label.casefold():
            return code
    if text.casefold() in ('ss', 'sprint shootout'):
        return 'SQ'
    raise ValueError(f'Unsupported session: {value}')


def canonical_team(value):
    """Match team names across sources."""
    if pd.isna(value):
        return ''
    name = str(value)
    return TEAM_ALIASES.get(name, name)


def _enable_cache():
    cache = ROOT / 'cache'
    cache.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(cache))


def load_weekend_schedule(year=2026, *, refresh=False):
    """Use FastF1 UTC times; fallback dates may be estimates."""
    _enable_cache()
    with fastf1.Cache.disabled() if refresh else nullcontext():
        schedule = fastf1.get_event_schedule(year, include_testing=False, backend='fastf1')
    rows = []
    for _, event in schedule.iterrows():
        if int(event['RoundNumber']) <= 0:
            continue
        for number in range(1, 6):
            label = event.get(f'Session{number}')
            if pd.isna(label) or not label:
                continue
            try:
                code = normalize_session_code(label)
            except ValueError:
                continue
            start = pd.to_datetime(event.get(f'Session{number}DateUtc'), utc=True)
            rows.append({
                'round': int(event['RoundNumber']), 'race': event['EventName'],
                'location': event['Location'], 'code': code,
                'session': SESSION_LABELS[code], 'session_utc': start,
            })
    result = pd.DataFrame(rows, columns=SCHEDULE_COLUMNS)
    result['session_utc'] = pd.to_datetime(result['session_utc'], utc=True)
    if result.duplicated(['round', 'code']).any():
        raise ValueError('Duplicate sessions in the weekend schedule')
    return result.sort_values(['round', 'session_utc'], na_position='last').reset_index(drop=True)


def eligible_sessions(schedule, now=None):
    """Select sessions past their scheduled start."""
    current = pd.Timestamp(now if now is not None else datetime.now(timezone.utc))
    current = current.tz_localize('UTC') if current.tzinfo is None else current.tz_convert('UTC')
    starts = pd.to_datetime(schedule['session_utc'], utc=True)
    return schedule.loc[starts.notna() & starts.le(current)].copy()


def resolve_session(round_number=None, session_code='R', year=2026, now=None, *, refresh=False):
    """Find a scheduled session that has started."""
    code = normalize_session_code(session_code)
    schedule = load_weekend_schedule(year, refresh=refresh)
    matches = schedule.loc[schedule['code'].eq(code)]
    if round_number is not None:
        matches = matches.loc[matches['round'].eq(int(round_number))]
    if matches.empty:
        raise SessionUnavailableError(f'{SESSION_LABELS[code]} is not scheduled for this event')
    started = eligible_sessions(matches, now)
    if started.empty:
        raise SessionUnavailableError(f'{SESSION_LABELS[code]} has not reached its scheduled start')
    return started.sort_values('session_utc').iloc[-1].to_dict()


def require_session_complete(session):
    """Check completion; Q/SQ need Finalised or Ends."""
    try:
        statuses = session.session_status
    except (AttributeError, DataNotLoadedError):
        statuses = None
    label = getattr(session, 'name', 'Session')
    if statuses is None or statuses.empty or 'Status' not in statuses:
        raise SessionUnavailableError(f'{label} completion status is not available yet')
    available = statuses['Status'].dropna().astype(str)
    last_status = available.iloc[-1] if not available.empty else ''
    terminal = {'Finalised', 'Ends'} if label in ('Qualifying', 'Sprint Qualifying', 'Sprint Shootout') else {'Finished', 'Finalised', 'Ends'}
    if last_status not in terminal:
        raise SessionUnavailableError(f'{label} is not complete in the timing feed yet')
    return last_status


def load_completed_session(session, *, telemetry=False, weather=False, messages=True, refresh=False):
    """Load completed timing; bypass the cache on refresh or retry."""
    options = {'telemetry': telemetry, 'weather': weather, 'messages': messages}

    def fresh_load():
        with fastf1.Cache.disabled():
            try:
                session.load(**options)
            except (NoLapDataError, DataNotLoadedError, SystemExit):
                raise SessionUnavailableError(f'{session.name} timing data is not published yet') from None
        return require_session_complete(session)

    if refresh:
        return fresh_load()
    try:
        session.load(**options)
        return require_session_complete(session)
    except (SessionUnavailableError, NoLapDataError, DataNotLoadedError, SystemExit):
        return fresh_load()


def valid_timed_laps(laps):
    """Select undeleted timed laps outside pit entry and exit."""
    if laps.empty:
        result = laps.copy()
        result['lap_seconds'] = pd.Series(dtype=float)
        return result
    keep = laps['LapTime'].notna() & laps['LapTime'].gt(pd.Timedelta(0))
    keep &= laps['PitInTime'].isna() & laps['PitOutTime'].isna()
    if 'Deleted' in laps:
        keep &= laps['Deleted'].astype('boolean').eq(False).fillna(False)
    else:
        keep &= False
    result = laps.loc[keep].copy()
    result['lap_seconds'] = result['LapTime'].dt.total_seconds()
    return result


def _text(row, key, default=''):
    value = row.get(key, default)
    return default if pd.isna(value) else str(value)


def _number(row, key):
    return pd.to_numeric(pd.Series([row.get(key)]), errors='coerce').iloc[0]


def _seconds(value):
    return pd.to_timedelta(value).total_seconds() if pd.notna(value) else float('nan')


def session_results(session, laps, code):
    """Build session results and best lap times."""
    source = pd.DataFrame(session.results).copy()
    if 'DriverNumber' not in source:
        source['DriverNumber'] = source.index.astype(str)
    source['DriverNumber'] = source['DriverNumber'].astype(str)
    if source['DriverNumber'].duplicated().any():
        raise ValueError('Duplicate driver numbers in session results')
    eligible = valid_timed_laps(laps)
    bests = eligible.groupby('DriverNumber')['lap_seconds'].min() if not eligible.empty else pd.Series(dtype=float)
    valid_counts = eligible.groupby('DriverNumber').size() if not eligible.empty else pd.Series(dtype=int)
    completed_counts = laps.loc[laps['LapTime'].notna()].groupby('DriverNumber').size() if not laps.empty else pd.Series(dtype=int)
    participants = set(source['DriverNumber']) | (set(laps['DriverNumber']) if not laps.empty else set())
    source = source.set_index('DriverNumber', drop=False)
    rows = []
    for number in sorted(participants):
        row = source.loc[number] if number in source.index else pd.Series(dtype=object)
        driver_laps = laps.loc[laps['DriverNumber'].eq(number)] if not laps.empty else laps
        fallback = driver_laps.iloc[0] if not driver_laps.empty else pd.Series(dtype=object)
        position = _number(row, 'Position') if code in ('R', 'S', 'Q', 'SQ') else float('nan')
        lap_count = _number(row, 'Laps') if code in ('R', 'S') else float('nan')
        rows.append({
            'position': position if position > 0 else float('nan'),
            'driver_number': number,
            'code': _text(row, 'Abbreviation') or _text(fallback, 'Driver', number),
            'driver': _text(row, 'FullName') or _text(fallback, 'Driver', number),
            'team': canonical_team(_text(row, 'TeamName') or _text(fallback, 'Team')),
            'best_lap_seconds': bests.get(number, float('nan')),
            'gap_seconds': float('nan'),
            'lap_count': int(lap_count) if pd.notna(lap_count) else int(completed_counts.get(number, 0)),
            'timed_lap_count': int(valid_counts.get(number, 0)),
            'points': _number(row, 'Points') if code in ('R', 'S') else float('nan'),
            'grid': _number(row, 'GridPosition') if code in ('R', 'S') else float('nan'),
            'status': _text(row, 'Status') if code in ('R', 'S') else '',
            **{f'{segment}_seconds': _seconds(row.get(segment)) if code in ('Q', 'SQ') else float('nan')
               for segment in ('Q1', 'Q2', 'Q3')},
        })
    results = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    if not results.empty:
        results['gap_seconds'] = results['best_lap_seconds'] - results['best_lap_seconds'].min()
        if code.startswith('FP'):
            results['position'] = results['best_lap_seconds'].rank(method='min', na_option='keep')
        results = results.sort_values(['position', 'best_lap_seconds', 'driver_number'], na_position='last').reset_index(drop=True)
    return results


def load_session_analysis(year, round_number, session_code, *, refresh=False):
    """Load results and laps for a completed session."""
    code = normalize_session_code(session_code)
    scheduled = resolve_session(round_number, code, year, refresh=refresh)
    _enable_cache()
    with fastf1.Cache.disabled() if refresh else nullcontext():
        session = fastf1.get_session(year, int(round_number), code, backend='fastf1')
    # Messages include lap deletions.
    timing_status = load_completed_session(session, refresh=refresh)
    try:
        laps = pd.DataFrame(session.laps).copy()
    except (AttributeError, DataNotLoadedError):
        raise SessionUnavailableError(f'{SESSION_LABELS[code]} lap timing is not published yet') from None
    if laps.empty:
        raise SessionUnavailableError(f'{SESSION_LABELS[code]} lap timing is not published yet')
    laps['DriverNumber'] = laps['DriverNumber'].astype(str)
    if 'Team' in laps:
        laps['Team'] = laps['Team'].map(canonical_team)
    laps['lap_seconds'] = laps['LapTime'].dt.total_seconds()
    results = session_results(session, laps, code)
    checked = 'Deleted' in laps and laps['Deleted'].notna().all()
    kind = 'Best valid lap order' if code.startswith('FP') else 'Session classification'
    pace_laps = clean_laps(laps)
    if 'Deleted' in pace_laps:
        pace_laps = pace_laps.loc[pace_laps['Deleted'].astype('boolean').eq(False).fillna(False)].copy()
    else:
        pace_laps = pace_laps.iloc[:0].copy()
    loaded_utc = datetime.now(timezone.utc).isoformat()
    return {
        'results': results, 'laps': laps, 'clean_laps': pace_laps,
        'metadata': {
            'event': session.event['EventName'], 'location': session.event['Location'],
            'session_code': code, 'session': SESSION_LABELS[code], 'year': int(year),
            'round': int(round_number), 'session_utc': scheduled['session_utc'].isoformat(),
            'source': 'FastF1 / F1 timing and Jolpica results',
            'source_url': 'https://docs.fastf1.dev/',
            'loaded_utc': loaded_utc, 'retrieved_utc': loaded_utc,
            'refresh_requested': bool(refresh),
            'timing_status': timing_status, 'is_complete': True,
            'classification_kind': kind, 'has_results': bool(results['position'].notna().any()),
            'deleted_laps_checked': bool(checked),
        },
    }
