"""Compare the current top three 2026 constructors and their drivers."""
from pathlib import Path
from datetime import datetime, timezone
import json
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
YEAR = 2026
BASE = f'https://api.jolpi.ca/ergast/f1/{YEAR}'
COLORS = {'mercedes': '#00a99d', 'ferrari': '#d62728', 'mclaren': '#ff8700'}


def fetch(endpoint, output):
    pages, offset = [], 0
    while True:
        response = requests.get(f'{BASE}/{endpoint}/', params={'limit': 100, 'offset': offset}, timeout=45)
        response.raise_for_status()
        data = response.json()['MRData']
        pages.append(data)
        offset += int(data['limit'])
        if offset >= int(data['total']):
            break
    (output / f'{endpoint}_source.json').write_text(json.dumps(pages, indent=2))
    return pages


def result_rows(pages, key, kind, cutoff):
    rows = []
    for page in pages:
        for race in page['RaceTable']['Races']:
            if int(race['round']) > cutoff:
                continue
            for result in race[key]:
                driver = result['Driver']
                rows.append({'round': int(race['round']), 'race': race['raceName'],
                             'date': race['date'], 'kind': kind,
                             'team_id': result['Constructor']['constructorId'],
                             'driver_id': driver['driverId'],
                             'driver': driver['givenName'] + ' ' + driver['familyName'],
                             'points': float(result['points']), 'finish': int(result['position']),
                             'grid': int(result['grid']), 'status': result['status']})
    return rows


def main():
    output = ROOT / 'outputs' / '2026'
    output.mkdir(parents=True, exist_ok=True)
    standings = fetch('constructorStandings', output)[0]['StandingsTable']['StandingsLists']
    if not standings:
        raise RuntimeError('No 2026 standings available. No older season will be substituted.')
    snapshot = standings[0]
    cutoff = int(snapshot['round'])
    top = sorted(snapshot['ConstructorStandings'], key=lambda x: int(x['position']))[:3]
    teams = {t['Constructor']['constructorId']: t['Constructor']['name'] for t in top}
    rows = result_rows(fetch('results', output), 'Results', 'Grand Prix', cutoff)
    rows += result_rows(fetch('sprint', output), 'SprintResults', 'Sprint', cutoff)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError('No 2026 results available.')
    frame = frame[frame['team_id'].isin(teams)].copy()
    frame.to_csv(output / 'race_and_sprint_results.csv', index=False)
    team_summary = pd.DataFrame([{'position': int(t['position']), 'team': t['Constructor']['name'],
        'points': float(t['points']), 'GP_wins': int(t['wins'])} for t in top])
    checks = []
    for t in top:
        team_id = t['Constructor']['constructorId']
        actual = frame.loc[frame.team_id == team_id, 'points'].sum()
        expected = float(t['points'])
        checks.append({'team': teams[team_id], 'results_points': actual,
                       'standings_points': expected, 'difference': actual - expected})
    reconciliation = pd.DataFrame(checks)
    reconciliation.to_csv(output / 'points_reconciliation.csv', index=False)
    driver_summary = frame.groupby(['team_id', 'driver_id', 'driver'], as_index=False).agg(points=('points', 'sum'))
    gp = frame[frame.kind == 'Grand Prix']
    gp_stats = gp.groupby('driver_id').agg(GP_starts=('finish', 'size'),
        GP_wins=('finish', lambda x: (x == 1).sum()), GP_podiums=('finish', lambda x: (x <= 3).sum()),
        average_GP_finish=('finish', 'mean'))
    driver_summary = driver_summary.join(gp_stats, on='driver_id')
    driver_summary.to_csv(output / 'driver_summary.csv', index=False)
    team_summary.to_csv(output / 'constructor_summary.csv', index=False)
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    axes[0, 0].bar(team_summary.team, team_summary.points, color=[COLORS.get(t, '#777777') for t in teams])
    axes[0, 0].set_title('Constructor championship points')
    driver_summary = driver_summary.sort_values('points', ascending=False)
    axes[0, 1].barh(driver_summary.driver, driver_summary.points,
                    color=[COLORS.get(t, '#777777') for t in driver_summary.team_id])
    axes[0, 1].invert_yaxis()
    axes[0, 1].set_title('Driver points earned for the selected teams')
    rounds = range(1, cutoff + 1)
    for team_id, name in teams.items():
        totals = frame[frame.team_id == team_id].groupby('round').points.sum().reindex(rounds, fill_value=0).cumsum()
        axes[1, 0].plot(totals.index, totals, marker='.', label=name, color=COLORS.get(team_id))
    axes[1, 0].set_title('Cumulative points: Grand Prix + Sprint')
    axes[1, 0].set_xlabel('Round')
    axes[1, 0].legend()
    for index, (driver_id, laps) in enumerate(gp.groupby('driver_id')):
        axes[1, 1].plot(laps.sort_values('round')['round'], laps.sort_values('round')['finish'],
                       marker='.', label=laps.driver.iloc[0], alpha=0.8)
    axes[1, 1].invert_yaxis()
    axes[1, 1].set_title('Grand Prix classified finishing positions')
    axes[1, 1].set_xlabel('Round')
    axes[1, 1].legend(fontsize=7)
    for ax in axes.flat:
        ax.grid(axis='y', alpha=0.2)
    fig.suptitle(f'2026 F1: current top three constructors | through round {cutoff}')
    fig.tight_layout()
    fig.savefig(output / 'season_comparison.png', dpi=160)
    plt.close(fig)
    stamp = datetime.now(timezone.utc).isoformat()
    note = f'''2026 F1 comparison through round {cutoff}\nRetrieved: {stamp}\nSource: {BASE}\n\n{team_summary.to_string(index=False)}\n\n{driver_summary.to_string(index=False)}\n\nPoints reconciliation:\n{reconciliation.to_string(index=False)}\n\nPoints include Grands Prix and Sprints. Finishing positions include classified retirements; they do not measure clean race pace. The top three are selected at the standings snapshot, not separately at each round. All drivers who scored results for those constructors are retained.\n'''
    (output / 'analysis.txt').write_text(note)
    print(note)
    print(f'Chart: {output / "season_comparison.png"}')
    if reconciliation['difference'].abs().gt(0.001).any():
        print('WARNING: points differ from championship standings; inspect reconciliation before drawing conclusions.')


if __name__ == '__main__':
    main()
