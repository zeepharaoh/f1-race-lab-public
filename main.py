"""F1 session replays and driver filters."""
from pathlib import Path
import argparse
import math
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT.parent / 'f1-race-replay'
if not ENGINE.exists():
    ENGINE = ROOT / '.engine' / 'f1-race-replay'
SESSION_CODES = ('FP1', 'FP2', 'FP3', 'Q', 'SQ', 'S', 'R')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', type=int, help='2026 event round; default latest session of the selected type')
    parser.add_argument('--session', type=str.upper, choices=SESSION_CODES, default='R', help='Session type; default R')
    parser.add_argument('--speed', type=float, default=4.0,
                        help='Initial race/Sprint speed; fastest-lap replays use window controls')
    parser.add_argument('--prepare-only', action='store_true', help='Download and verify without opening the window')
    parser.add_argument('--top-three', action='store_true', help='Show current top three constructors')
    parser.add_argument('--drivers', nargs='+', help='Driver abbreviations, e.g. NOR PIA')
    return parser


def select_drivers(results, team_names=None, drivers=None):
    selected = results.dropna(subset=['Abbreviation']).copy()
    if team_names is not None:
        from weekend import canonical_team
        normalized_teams = {canonical_team(name) for name in team_names}
        selected = selected[selected['TeamName'].map(canonical_team).isin(normalized_teams)]
    if drivers:
        requested = {code.upper() for code in drivers}
        selected = selected[selected['Abbreviation'].isin(requested)]
        missing = requested - set(selected['Abbreviation'])
        if missing:
            raise ValueError(f'Drivers unavailable in the selected session or constructor scope: {sorted(missing)}')
    if selected.empty:
        raise RuntimeError('No drivers available in the selected session or constructor scope')
    return selected


def filter_replay_data(data, session_code, codes):
    """Filter replay data without changing the cache."""
    filtered = dict(data)
    if session_code in ('R', 'S'):
        filtered['full_field_frames'] = data.get('frames', [])
        frames = []
        available = set()
        for frame in data.get('frames', []):
            selected_frame = dict(frame)
            selected_frame['drivers'] = {code: car for code, car in frame.get('drivers', {}).items() if code in codes}
            available.update(selected_frame['drivers'])
            frames.append(selected_frame)
        if not frames or not available:
            raise RuntimeError('No position telemetry available for the selected drivers')
        filtered['frames'] = frames
        filtered['driver_colors'] = {code: color for code, color in data.get('driver_colors', {}).items() if code in codes}
        return filtered, available, len(frames)

    segments = ('best_lap',) if session_code.startswith('FP') else ('Q1', 'Q2', 'Q3')
    telemetry = {}
    for code, block in data.get('telemetry', {}).items():
        if code not in codes:
            continue
        if any(block.get(segment, {}).get('frames') for segment in segments):
            telemetry[code] = block
    results = [row for row in data.get('results', []) if row.get('code') in telemetry]
    available = {row['code'] for row in results}
    if not available:
        raise RuntimeError('No fastest-lap position telemetry available for the selected drivers')
    filtered['results'] = results
    filtered['telemetry'] = {code: block for code, block in telemetry.items() if code in available}
    frame_count = sum(len(block.get(segment, {}).get('frames', []))
                      for block in filtered['telemetry'].values() for segment in segments)
    return filtered, available, frame_count


class SessionView:
    """Use session metadata with filtered driver data."""
    def __init__(self, session, laps, results=None):
        self.source = session
        self.laps = laps
        if results is not None:
            self.results = results
            self.drivers = results['DriverNumber'].tolist()

    def __getattr__(self, name):
        return getattr(self.source, name)


def practice_replay_data(session, session_code, engine_data):
    import pandas as pd
    from weekend import valid_timed_laps
    view = SessionView(session, valid_timed_laps(session.laps))
    data = engine_data.get_practice_telemetry(view, session_type=session_code)
    rows = []
    for row in data.get('results', []):
        best_lap = data.get('telemetry', {}).get(row['code'], {}).get('best_lap', {})
        if not best_lap.get('frames'):
            continue
        try:
            lap_seconds = pd.to_timedelta([best_lap.get('lap_time')], errors='coerce').total_seconds()[0]
        except (TypeError, ValueError):
            continue
        if not math.isfinite(lap_seconds) or lap_seconds <= 0:
            continue
        result = dict(row)
        result['time'] = lap_seconds
        result['full_name'] = result.get('full_name', result.get('driver_name', result['code']))
        rows.append(result)
    rows.sort(key=lambda row: (row['time'], row['code']))
    for position, row in enumerate(rows, 1):
        row['position'] = position
    return {**data, 'results': rows}


def prepare_replay(session, session_code, selected, engine_data):
    if session_code in ('R', 'S'):
        data = engine_data.get_race_telemetry(session, session_type=session_code)
    elif session_code in ('Q', 'SQ'):
        data = engine_data.get_quali_telemetry(session, session_type=session_code)
    else:
        data = practice_replay_data(session, session_code, engine_data)
    return filter_replay_data(data, session_code, set(selected['Abbreviation']))


def current_constructors():
    import requests
    response = requests.get('https://api.jolpi.ca/ergast/f1/2026/constructorStandings/', timeout=30)
    response.raise_for_status()
    lists = response.json()['MRData']['StandingsTable']['StandingsLists']
    if not lists:
        raise RuntimeError('No 2026 championship standings available')
    standings = lists[0]
    top = sorted(standings['ConstructorStandings'], key=lambda team: int(team['position']))[:3]
    return int(standings['round']), [team['Constructor']['name'] for team in top]


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not math.isfinite(args.speed) or args.speed <= 0:
        raise ValueError('Playback speed must be greater than zero')
    # Use the replay environment for graphics.
    engine_python = ENGINE / '.venv' / 'bin' / 'python'
    if Path(sys.prefix).resolve() != (ENGINE / '.venv').resolve():
        if not engine_python.exists():
            raise RuntimeError('Replay environment missing. Run sh setup.sh to install it.')
        arguments = sys.argv[1:] if argv is None else argv
        os.execv(str(engine_python), [str(engine_python), str(ROOT / 'main.py'), *arguments])
    import shutil
    for asset in ('images', 'resources'):
        if not (ROOT / asset).exists():
            shutil.copytree(ENGINE / asset, ROOT / asset)
    sys.path.insert(0, str(ENGINE))
    os.chdir(ROOT)
    os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'cache' / 'matplotlib'))
    import fastf1
    from src import f1_data as engine_data
    from weekend import resolve_session, load_completed_session

    cache = ROOT / 'cache'
    cache.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(cache))
    engine_data.get_settings().cache_location = str(cache)
    team_names = None
    round_number = args.round
    if args.top_three or (args.session == 'R' and round_number is None):
        latest, top_teams = current_constructors()
        if args.top_three:
            team_names = top_teams
            print(f'Current top three: {", ".join(team_names)}', flush=True)
        if args.session == 'R' and round_number is None:
            round_number = latest
    event = resolve_session(round_number, args.session, year=2026)
    round_number = int(event['round'])
    print(f'2026 round {round_number} | {event["race"]} | {event["session"]}', flush=True)
    session = fastf1.get_session(2026, round_number, args.session)
    load_completed_session(session, telemetry=True, weather=True, messages=True)
    selected = select_drivers(session.results, team_names, args.drivers)
    requested_codes = set(selected['Abbreviation'])
    data, codes, frame_count = prepare_replay(session, args.session, selected, engine_data)
    unavailable = requested_codes - codes
    if unavailable:
        print(f'Position telemetry unavailable for: {", ".join(sorted(unavailable))}', flush=True)
    selected = selected[selected['Abbreviation'].isin(codes)]
    print(f'Prepared {frame_count} frames for {len(codes)} drivers: {", ".join(sorted(codes))}', flush=True)
    if args.prepare_only:
        return
    title = f'F1 Race Lab | 2026 {session.event["EventName"]} | {event["session"]} | {len(codes)} drivers'
    if args.session in ('Q', 'SQ'):
        from src.interfaces.qualifying import run_qualifying_replay
        print('Click a driver and select a segment to replay their fastest lap.', flush=True)
        run_qualifying_replay(session=session, data=data, title=f'{title} | Fastest laps')
    elif args.session.startswith('FP'):
        from src.interfaces.practice import run_practice_replay
        print('Click a driver to replay their fastest lap. Use the window controls to compare drivers.', flush=True)
        run_practice_replay(session=session, data=data, title=f'{title} | Fastest laps')
    else:
        render_race(session, selected, data, title, args.speed, round_number, args.session, engine_data)


def render_race(session, selected, data, title, speed, round_number, session_code, engine_data):
    import requests
    from championship_data import load_championship_snapshot
    from championship_overlays import install_championship_tables
    from replay_overlays import install_replay_overlays
    from src.interfaces import race_replay as interface
    from src.run_session import run_arcade_replay
    lap = session.laps.pick_fastest()
    if lap is None:
        raise RuntimeError('No lap available for track outline')
    example = lap.get_telemetry()
    try:
        rotation = engine_data.get_circuit_rotation(session)
    except (AttributeError, ValueError, requests.RequestException):
        rotation = 0.0
        print('Circuit rotation unavailable; using recorded track coordinates.', flush=True)
    view = SessionView(session, session.laps[session.laps.Driver.isin(selected['Abbreviation'])], selected)
    championship = load_championship_snapshot(session, round_number, session_code)
    print(f'Championship: {championship["label"]}', flush=True)
    if championship['warning']:
        print(championship['warning'], flush=True)
    insights = None
    server_type = interface.TelemetryStreamServer
    stream = server_type(port=0)
    start_stream = stream.start

    def start_insights_stream():
        nonlocal insights
        start_stream()
        port = stream.server_socket.getsockname()[1]
        try:
            insights = subprocess.Popen([sys.executable, str(ROOT / 'replay_insights.py'),
                                         '--port', str(port)], cwd=ENGINE)
        except OSError as error:
            print(f'Insights window could not start: {error}', flush=True)

    stream.start = start_insights_stream
    interface.TelemetryStreamServer = lambda: stream
    try:
        with install_championship_tables(interface, championship), install_replay_overlays(
                interface, data.get('full_field_frames', data['frames'])):
            run_arcade_replay(
                frames=data['frames'], track_statuses=data['track_statuses'], example_lap=example,
                drivers=selected['DriverNumber'].tolist(), title=title,
                playback_speed=speed, driver_colors=data['driver_colors'],
                circuit_rotation=rotation, total_laps=data['total_laps'],
                session=view, enable_telemetry=True,
                current_driver_standings=championship['drivers'],
                current_constructors_standings=championship['constructors'],
                race_control_messages=data.get('race_control_messages', []),
                session_info={'event_name': session.event['EventName'], 'year': 2026,
                              'round': round_number, 'country': session.event['Country'],
                              'circuit_name': session.event['Location'],
                              'date': str(session.date.date()), 'session': session_code,
                              'total_laps': data['total_laps'],
                              'circuit_length_m': float(example['Distance'].max())})
    finally:
        interface.TelemetryStreamServer = server_type
        stream.stop()
        if insights is not None and insights.poll() is None:
            insights.terminate()
            try:
                insights.wait(timeout=5)
            except subprocess.TimeoutExpired:
                insights.kill()
                insights.wait()


if __name__ == '__main__':
    main()
