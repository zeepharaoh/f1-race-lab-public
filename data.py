"""F1 results, caches and lap analysis."""
from pathlib import Path
from datetime import datetime, timezone
import json
import time
import requests
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = 'https://api.jolpi.ca/ergast/f1'
COLORS = {'Mercedes': '#27d7c5', 'Ferrari': '#ff4b5b', 'McLaren': '#ff9d2e',
          'Red Bull': '#6598ff', 'RB F1 Team': '#a59aff', 'Alpine F1 Team': '#ed87ca',
          'Haas F1 Team': '#d4d8e1', 'Audi': '#92dd51', 'Williams': '#64c9ff',
          'Aston Martin': '#36aa8d', 'Cadillac F1 Team': '#b6aa91'}


def fetch(year, endpoint, refresh=False):
    from jolpica_client import fetch_payload
    return fetch_payload(ROOT, BASE, year, endpoint, refresh=refresh)

def flatten(payload, key, kind, cutoff):
    rows = []
    for page in payload['pages']:
        for race in page['RaceTable']['Races']:
            if int(race['round']) > cutoff:
                continue
            for result in race[key]:
                driver = result['Driver']
                row = {'round': int(race['round']), 'race': race['raceName'], 'date': race['date'],
                       'kind': kind, 'team': result['Constructor']['name'],
                       'team_id': result['Constructor']['constructorId'],
                       'driver_id': driver['driverId'], 'code': driver.get('code', driver['familyName']),
                       'driver': driver['givenName'] + ' ' + driver['familyName'],
                       'position': int(result['position']), 'points': float(result.get('points', 0)),
                       'grid': int(result.get('grid', 0)), 'status': result.get('status', '')}
                for segment in ('Q1', 'Q2', 'Q3'):
                    row[segment] = result.get(segment)
                rows.append(row)
    columns = ['round', 'race', 'date', 'kind', 'team', 'team_id', 'driver_id', 'code', 'driver', 'position', 'points', 'grid', 'status', 'Q1', 'Q2', 'Q3']
    frame = pd.DataFrame(rows, columns=columns)
    if frame.duplicated(['round', 'kind', 'driver_id']).any():
        raise ValueError('Duplicate driver results found; refusing to double count')
    return frame


def _season_frames(sources, year):
    lists = sources['constructorStandings']['pages'][0]['StandingsTable']['StandingsLists']
    if len(lists) != 1:
        raise ValueError(f'No unambiguous championship standings available for {year}')
    snapshot = lists[0]
    if str(snapshot.get('season', year)) != str(year):
        raise ValueError('Constructor standings have different season coverage')
    cutoff = int(snapshot['round'])
    if cutoff < 1:
        raise ValueError('No completed championship round is available')
    teams = pd.DataFrame([{'rank': int(t['position']), 'team': t['Constructor']['name'],
                          'team_id': t['Constructor']['constructorId'], 'points': float(t['points']),
                          'wins': int(t['wins'])} for t in snapshot['ConstructorStandings']]).sort_values('rank')
    dlists = sources['driverStandings']['pages'][0]['StandingsTable']['StandingsLists']
    if len(dlists) != 1 or int(dlists[0]['round']) != cutoff:
        raise ValueError('Driver and constructor standings have different round coverage')
    if str(dlists[0].get('season', year)) != str(year):
        raise ValueError('Driver standings have different season coverage')
    drivers = pd.DataFrame([{'rank': int(d['position']), 'driver_id': d['Driver']['driverId'],
                            'driver': d['Driver']['givenName'] + ' ' + d['Driver']['familyName'],
                            'code': d['Driver'].get('code', d['Driver']['familyName']),
                            'team': d['Constructors'][-1]['name'],
                            'points': float(d['points']), 'wins': int(d['wins'])}
                           for d in dlists[0]['DriverStandings']]).sort_values('rank')
    if any(not float('-inf') < value < float('inf')
           for frame in (teams, drivers) for field in ('rank', 'points', 'wins') for value in frame[field]):
        raise ValueError('Championship standings have invalid numeric values')
    if teams.team_id.duplicated().any() or drivers.driver_id.duplicated().any():
        raise ValueError('Duplicate championship standings found')
    gp = flatten(sources['results'], 'Results', 'Grand Prix', cutoff)
    sprint = flatten(sources['sprint'], 'SprintResults', 'Sprint', cutoff)
    results = pd.concat([gp, sprint], ignore_index=True)
    if set(gp['round']) != set(range(1, cutoff + 1)):
        raise ValueError('Race results do not cover every standings round')
    actual = results.groupby('team').points.sum()
    reconciliation = teams[['team', 'points']].copy().rename(columns={'points': 'official_points'})
    reconciliation['results_points'] = reconciliation.team.map(actual).fillna(0)
    reconciliation['difference'] = reconciliation.results_points - reconciliation.official_points
    driver_actual = results.groupby('driver_id').points.sum()
    driver_checks = drivers[['driver_id', 'driver', 'points']].copy().rename(columns={'points': 'official_points'})
    driver_checks['results_points'] = driver_checks.driver_id.map(driver_actual).fillna(0)
    driver_checks['difference'] = driver_checks.results_points - driver_checks.official_points
    calendar = pd.DataFrame([{'round':int(r['round']), 'race':r['raceName'], 'date':r['date'], 'circuit':r['Circuit']['circuitName']}
        for page in sources['calendar']['pages'] for r in page['RaceTable']['Races']]).sort_values('round')
    if calendar['round'].duplicated().any() or not set(range(1, cutoff + 1)).issubset(set(calendar['round'])):
        raise ValueError('Calendar does not cover the championship rounds')
    return {'calendar': calendar, 'year': year, 'round': cutoff, 'teams': teams,
            'drivers': drivers, 'gp': gp, 'results': results,
            'reconciliation': reconciliation, 'driver_checks': driver_checks}


def _saved_season_sources(path, year):
    from jolpica_client import validate_payload
    try:
        if path.stat().st_size > 96 * 1024 * 1024:
            return None
        sources = json.loads(path.read_text())
        core = ['constructorStandings', 'driverStandings', 'results', 'sprint', 'calendar']
        saved = {key: validate_payload(sources[key], BASE, year, key) for key in core}
        if 'qualifying' in sources:
            try:
                saved['qualifying'] = validate_payload(sources['qualifying'], BASE, year, 'qualifying')
            except (ValueError, TypeError, KeyError):
                pass
        return saved
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _save_season_sources(path, sources):
    import uuid
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(sources, allow_nan=False))
        temporary.replace(path)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _qualifying_source(year, refresh, saved, cutoff):
    error = None
    try:
        source = fetch(year, 'qualifying', refresh)
        qualifying = flatten(source, 'QualifyingResults', 'Qualifying', cutoff)
        status = 'saved' if source.get('cached_fallback') else 'available'
        error = source.get('refresh_error') if status == 'saved' else None
        return source, qualifying, status, error
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as failure:
        error = str(failure)
    if saved and saved.get('qualifying'):
        try:
            source = saved['qualifying']
            qualifying = flatten(source, 'QualifyingResults', 'Qualifying', cutoff)
            return source, qualifying, 'saved', error
        except (ValueError, KeyError, TypeError, IndexError):
            pass
    empty = flatten({'pages': []}, 'QualifyingResults', 'Qualifying', cutoff)
    return None, empty, 'unavailable', error


def load_season(year=2026, refresh=False):
    bundle = ROOT / 'cache' / 'results' / str(year) / 'last_complete_snapshot.json'
    saved = _saved_season_sources(bundle, year)
    refresh_error = None
    core = ['constructorStandings', 'driverStandings', 'results', 'sprint', 'calendar']
    try:
        sources = {key: fetch(year, key, refresh) for key in core}
        frames = _season_frames(sources, year)
        if any(source.get('cached_fallback') for source in sources.values()):
            refresh_error = 'Some championship sources could not be refreshed. Saved data is shown with its original source times.'
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as error:
        if saved is None:
            raise
        try:
            sources = {key: saved[key] for key in core}
            frames = _season_frames(sources, year)
        except (ValueError, KeyError, TypeError, IndexError):
            raise error from None
        refresh_error = str(error)
    source, qualifying, status, error = _qualifying_source(year, refresh, saved, frames['round'])
    if source is not None:
        sources['qualifying'] = source
    if refresh_error is None:
        _save_season_sources(bundle, sources)
    return {**frames, 'refresh_error': refresh_error, 'qualifying': qualifying, 'sources': sources,
            'qualifying_status': status, 'qualifying_error': error,
            'qualifying_available': not qualifying.empty}


def cumulative_points(results, entity):
    totals = results.groupby(['round', entity]).points.sum().unstack(fill_value=0)
    totals = totals.reindex(range(1, int(results['round'].max()) + 1), fill_value=0).cumsum()
    return totals.reset_index().melt('round', var_name=entity, value_name='points')


def clean_laps(laps):
    """Select accurate green laps outside pit entry and exit."""
    keep = laps['LapTime'].notna() & laps['PitInTime'].isna() & laps['PitOutTime'].isna()
    keep &= laps['TrackStatus'].astype(str).eq('1')
    keep &= laps['IsAccurate'].fillna(False)
    if 'Deleted' in laps:
        keep &= ~laps['Deleted'].astype('boolean').fillna(False)
    result = laps.loc[keep].copy()
    result['lap_seconds'] = result['LapTime'].dt.total_seconds()
    return result


def load_laps(year, round_number, session_code='R'):
    """Load clean laps for a completed session."""
    from weekend import load_session_analysis
    analysis = load_session_analysis(year, round_number, session_code)
    return analysis['clean_laps'], analysis['metadata']
