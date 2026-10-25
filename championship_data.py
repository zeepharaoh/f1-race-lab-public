"""Championship points before a replay session."""
import math

import requests

from data import ROOT, fetch
from weekend import canonical_team


def _source(year, endpoint):
    payload = fetch(year, endpoint)
    return payload, bool(payload.get('cached_fallback'))


def _points(value):
    points = float(value)
    if not math.isfinite(points) or points < 0:
        raise ValueError('Invalid championship points')
    return points


def _standings(payload, year, round_number, key, name):
    rows = []
    for page in payload['pages']:
        for block in page['StandingsTable']['StandingsLists']:
            if int(block['season']) != year or int(block['round']) != round_number:
                raise ValueError('Championship snapshot has different round coverage')
            rows.extend(block[key])
    if not rows:
        raise ValueError('Championship snapshot is not published')
    result = {}
    for row in sorted(rows, key=lambda item: int(item['position'])):
        identity = name(row)
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('Championship identity is missing')
        if identity in result:
            raise ValueError('Duplicate championship entry')
        result[identity] = _points(row['points'])
    return result


def _sprint_results(payload, year, round_number):
    rows = []
    for page in payload['pages']:
        for race in page['RaceTable']['Races']:
            if int(race['season']) != year or int(race['round']) != round_number:
                raise ValueError('Sprint results have different round coverage')
            rows.extend(race['SprintResults'])
    if not rows:
        raise ValueError('Sprint points are not published')
    seen = set()
    for row in rows:
        code = row['Driver']['code']
        name = row['Constructor']['name']
        if not isinstance(code, str) or not code.strip():
            raise ValueError('Sprint driver is missing')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Sprint constructor is missing')
        if code in seen:
            raise ValueError('Duplicate Sprint result')
        seen.add(code)
        _points(row['points'])
    return rows


def load_championship_snapshot(session, round_number, session_code, year=2026):
    """Use a historical cutoff, independent of replay driver filters."""
    label = f'Before {"Sprint" if session_code == "S" else "Race"}'
    snapshot = {'drivers': {}, 'constructors': {}, 'label': label, 'warning': ''}
    teams = {canonical_team(name): name for name in session.results['TeamName'].dropna().unique()}
    team_name = lambda name: teams.get(canonical_team(name), canonical_team(name))
    stale = False
    try:
        if round_number == 1:
            drivers, constructors = {}, {}
            label += ' · season start'
        else:
            previous = round_number - 1
            dpayload, dstale = _source(year, f'{previous}/driverStandings')
            cpayload, cstale = _source(year, f'{previous}/constructorStandings')
            stale = dstale or cstale
            drivers = _standings(dpayload, year, previous, 'DriverStandings',
                                 lambda row: row['Driver']['code'])
            constructors = _standings(cpayload, year, previous, 'ConstructorStandings',
                                      lambda row: team_name(row['Constructor']['name']))
            label += f' · through round {previous}'
        for code in session.results['Abbreviation'].dropna():
            drivers.setdefault(code, 0.0)
        for name in teams.values():
            constructors.setdefault(name, 0.0)
        sprint_weekend = 'sprint' in str(session.event.get('EventFormat', '')).lower()
        sprint_weekend |= any(session.event.get(f'Session{n}') == 'Sprint' for n in range(1, 6))
        if session_code == 'R' and sprint_weekend:
            try:
                payload, sstale = _source(year, f'{round_number}/sprint')
                sprint = _sprint_results(payload, year, round_number)
                next_drivers, next_constructors = dict(drivers), dict(constructors)
                for row in sprint:
                    code = row['Driver']['code']
                    name = team_name(row['Constructor']['name'])
                    points = _points(row['points'])
                    next_drivers[code] = next_drivers.get(code, 0.0) + points
                    next_constructors[name] = next_constructors.get(name, 0.0) + points
                drivers = dict(sorted(next_drivers.items(), key=lambda item: -item[1]))
                constructors = dict(sorted(next_constructors.items(), key=lambda item: -item[1]))
                label = f'Before Race · round {round_number} Sprint included'
                stale |= sstale
            except (requests.RequestException, ValueError, KeyError, TypeError, OSError):
                snapshot['warning'] = 'Same-weekend Sprint points unavailable; earlier totals shown.'
                label = f'Through round {round_number - 1} · Sprint pending'
        if stale:
            snapshot['warning'] = (snapshot['warning'] + ' Saved snapshot; refresh unavailable.').strip()
        snapshot.update(drivers=drivers, constructors=constructors, label=label)
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError):
        snapshot['warning'] = 'Standings unavailable. Check the connection and reopen the replay.'
    return snapshot
