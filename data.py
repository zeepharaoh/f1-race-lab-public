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
    path = ROOT / 'cache' / 'results' / str(year) / f'{endpoint}.json'
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < 3600:
        return json.loads(path.read_text())
    url = f'{BASE}/{year}/' if endpoint == 'calendar' else f'{BASE}/{year}/{endpoint}/'
    pages, offset = [], 0
    while True:
        response = requests.get(url,
                                params={'limit': 100, 'offset': offset}, timeout=45)
        response.raise_for_status()
        page = response.json()['MRData']
        pages.append(page)
        offset += int(page['limit'])
        if offset >= int(page['total']):
            break
    payload = {'retrieved_utc': datetime.now(timezone.utc).isoformat(), 'url': url, 'pages': pages}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


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


def load_season(year=2026, refresh=False):
    bundle = ROOT / 'cache' / 'results' / str(year) / 'last_complete_snapshot.json'
    refresh_error = None
    try:
        sources = {key: fetch(year, key, refresh) for key in ['constructorStandings', 'driverStandings', 'results', 'sprint', 'qualifying', 'calendar']}
    except requests.RequestException as error:
        if not bundle.exists():
            raise
        sources = json.loads(bundle.read_text())
        refresh_error = str(error)

    lists = sources['constructorStandings']['pages'][0]['StandingsTable']['StandingsLists']
    if not lists:
        raise ValueError(f'No championship standings available for {year}')
    snapshot = lists[0]
    cutoff = int(snapshot['round'])
    teams = pd.DataFrame([{'rank': int(t['position']), 'team': t['Constructor']['name'],
                          'team_id': t['Constructor']['constructorId'], 'points': float(t['points']),
                          'wins': int(t['wins'])} for t in snapshot['ConstructorStandings']]).sort_values('rank')
    dlists = sources['driverStandings']['pages'][0]['StandingsTable']['StandingsLists']
    if not dlists or int(dlists[0]['round']) != cutoff:
        raise ValueError('Driver and constructor standings have different round coverage')
    drivers = pd.DataFrame([{'rank': int(d['position']), 'driver_id': d['Driver']['driverId'],
                            'driver': d['Driver']['givenName'] + ' ' + d['Driver']['familyName'],
                            'code': d['Driver'].get('code', d['Driver']['familyName']),
                            'team': d['Constructors'][-1]['name'],
                            'points': float(d['points']), 'wins': int(d['wins'])}
                           for d in dlists[0]['DriverStandings']]).sort_values('rank')
    gp = flatten(sources['results'], 'Results', 'Grand Prix', cutoff)
    sprint = flatten(sources['sprint'], 'SprintResults', 'Sprint', cutoff)
    qualifying = flatten(sources['qualifying'], 'QualifyingResults', 'Qualifying', cutoff)
    results = pd.concat([gp, sprint], ignore_index=True)
    if gp['round'].nunique() != cutoff:
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
    if refresh_error is None:
        bundle.parent.mkdir(parents=True, exist_ok=True)
        temporary = bundle.with_suffix('.tmp')
        temporary.write_text(json.dumps(sources))
        temporary.replace(bundle)
    return {'refresh_error': refresh_error, 'calendar': calendar, 'year': year, 'round': cutoff, 'teams': teams, 'drivers': drivers, 'gp': gp,
            'results': results, 'qualifying': qualifying, 'sources': sources,
            'reconciliation': reconciliation, 'driver_checks': driver_checks}


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
