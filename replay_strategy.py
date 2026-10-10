"""Tyres and completed pit visits at the recorded replay time."""
from bisect import bisect_right
from dataclasses import dataclass
import math

import pandas as pd


COMPOUNDS = {'SOFT', 'MEDIUM', 'HARD', 'INTERMEDIATE', 'WET'}


@dataclass(frozen=True)
class StrategyState:
    compound: str | None = None
    age: int | None = None
    stops: int | None = None
    last_stop_lap: int | None = None
    in_pit: bool | None = None

    def tyre_label(self):
        if self.compound is None:
            return '—'
        name = {'MEDIUM': 'MED', 'INTERMEDIATE': 'INTER'}.get(self.compound, self.compound)
        return f'{name} {self.age}L' if self.age is not None else name

    def stops_label(self):
        if self.stops is None:
            return '—'
        return f'{self.stops} · L{self.last_stop_lap}' if self.last_stop_lap is not None else str(self.stops)


def _seconds(value):
    if value is None or pd.isna(value):
        return None
    try:
        seconds = value.total_seconds() if hasattr(value, 'total_seconds') else float(value)
        return float(seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value):
    number = _seconds(value)
    return int(number) if number is not None and number >= 0 else None


def replay_time_origin(session, replay_data):
    """Recover the engine's SessionTime offset from the unchanged status events."""
    source = getattr(session, 'track_status', None)
    shifted = replay_data.get('track_statuses', [])
    if source is None or source.empty or not shifted or len(source) != len(shifted):
        return None
    offsets = []
    for recorded, replay in zip(source.to_dict('records'), shifted):
        absolute, relative = _seconds(recorded.get('Time')), _seconds(replay.get('start_time'))
        if (absolute is None or relative is None
                or str(recorded.get('Status')) != str(replay.get('status'))):
            return None
        offsets.append(absolute - relative)
    origin = offsets[0]
    return origin if all(abs(offset - origin) < 1e-5 for offset in offsets) else None


class StrategyTimeline:
    def __init__(self, origin=None, drivers=None):
        self.origin = origin
        self.drivers = drivers or {}
        self.warning = 'Tyre and pit timing unavailable: replay clock could not be aligned.' if origin is None else None

    @classmethod
    def from_session(cls, session, replay_data):
        origin = replay_time_origin(session, replay_data)
        laps = getattr(session, 'laps', None)
        if origin is None:
            return cls()
        if laps is None or laps.empty or 'Driver' not in laps:
            timeline = cls(origin)
            timeline.warning = 'Tyre and pit timing unavailable: lap records are missing.'
            return timeline
        race_start = _seconds(getattr(session, 'session_start_time', None))
        if race_start is None:
            race_start = origin
        drivers = {}
        for code, rows in laps.groupby('Driver', sort=False):
            tyre_events, visits, occupancy, coverage_breaks = [], [], [], []
            entry_events, exit_events = {}, {}
            for row in rows.to_dict('records'):
                start, finish = _seconds(row.get('LapStartTime')), _seconds(row.get('Time'))
                pit_in, pit_out = _seconds(row.get('PitInTime')), _seconds(row.get('PitOutTime'))
                lap = _integer(row.get('LapNumber'))
                compound = str(row.get('Compound', '')).upper()
                if compound not in COMPOUNDS:
                    compound = None
                age = _integer(row.get('TyreLife'))
                effective = pit_out if pit_out is not None else start
                if effective is not None:
                    tyre_events.append((effective, 1, compound, max(age - 1, 0) if age is not None else None))
                if finish is not None and effective is not None and finish >= effective:
                    tyre_events.append((finish, 0, compound, age))
                if pit_in is not None:
                    entry_events.setdefault(pit_in, lap)
                if pit_out is not None:
                    exit_events.setdefault(pit_out, lap)

            events = sorted([(time, 1, lap) for time, lap in entry_events.items()]
                            + [(time, 0, lap) for time, lap in exit_events.items()])
            pending = None
            for time, event, lap in events:
                if event == 1:
                    if pending is None:
                        pending = (time, lap)
                    occupancy.append((time, True))
                else:
                    if pending is not None and time >= pending[0]:
                        if pending[0] >= race_start:
                            visits.append((time, pending[1]))
                        pending = None
                    elif lap == 1 and time > race_start:
                        occupancy.append((race_start, True))
                    elif time >= race_start:
                        coverage_breaks.append(time - origin)
                    occupancy.append((time, False))

            tyre_events.sort(key=lambda item: (item[0], item[1]))
            occupancy.sort(key=lambda item: item[0])
            pit_coverage = {'PitInTime', 'PitOutTime', 'LapNumber'}.issubset(rows.columns)
            first_lap = rows['LapNumber'].min() if 'LapNumber' in rows else math.nan
            pit_coverage = bool(pit_coverage and pd.notna(first_lap) and first_lap == 1)
            drivers[str(code)] = {
                'tyres': [(time - origin, compound, age) for time, _, compound, age in tyre_events],
                'visits': [(time - origin, lap) for time, lap in visits],
                'occupancy': [(time - origin, state) for time, state in occupancy],
                'pit_coverage': pit_coverage,
                'coverage_breaks': coverage_breaks,
            }
        return cls(origin, drivers)

    def at(self, code, replay_seconds):
        data = self.drivers.get(code)
        time = _seconds(replay_seconds)
        if self.origin is None or data is None or time is None:
            return StrategyState()
        tyres = data['tyres']
        index = bisect_right([event[0] for event in tyres], time) - 1
        compound, age = tyres[index][1:] if index >= 0 else (None, None)
        visits = data['visits']
        completed = bisect_right([visit[0] for visit in visits], time)
        covered = data['pit_coverage'] and not bisect_right(data['coverage_breaks'], time)
        stops = completed if covered else None
        last_lap = visits[completed - 1][1] if completed and covered else None
        occupancy = data['occupancy']
        pit_index = bisect_right([event[0] for event in occupancy], time) - 1
        in_pit = occupancy[pit_index][1] if pit_index >= 0 else False
        return StrategyState(compound, age, stops, last_lap, in_pit if covered else None)
