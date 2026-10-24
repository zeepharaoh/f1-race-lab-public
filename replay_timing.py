"""Completed lap timing on the browser replay clock."""
from collections import Counter
from datetime import datetime, timezone
import math
import re


TIMING_VERSION = 1


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def segment_number(value):
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r'(?:SQ|Q)?([123])', str(value).strip().upper())
    return int(match[1]) if match else None


def lap_validity(*, deleted=None, generated=None, pit_in=None, pit_out=None, accurate=None):
    flags = (deleted, generated, pit_in, pit_out)
    if any(value is True for value in flags) or accurate is False:
        return False
    return True if all(value is False for value in flags) else None


def _identity(row):
    code, lap = row.get('code'), number(row.get('lap'))
    if not isinstance(code, str) or not code or lap is None or not lap.is_integer() or lap < 1:
        return None
    return code, int(lap)


def _bounds(row, duration):
    start, end = number(row.get('start')), number(row.get('end'))
    if start is None or end is None or not 0 <= start < end <= duration + .002:
        return None
    return round(start, 3), min(round(end, 3), round(duration, 3))


def build_lap_timing(rows, duration, *, qualifying=False, note=''):
    """Keep completion times; the player selects only laps already completed."""
    duration = number(duration)
    if duration is None or duration <= 0:
        return unavailable_timing()
    rows = list(rows)
    identities = Counter(identity for row in rows if (identity := _identity(row)) is not None)
    records, spans = [], {}
    for row in rows:
        identity, bounds = _identity(row), _bounds(row, duration)
        if identity is None or identities[identity] != 1 or bounds is None:
            continue
        code, lap = identity
        start, end = bounds
        segment = segment_number(row.get('segment')) if qualifying else None
        if segment is not None:
            effective_start = number(row.get('coverage_start'))
            effective_start = start if effective_start is None else max(start, effective_start)
            if effective_start < end:
                previous = spans.get(segment, (effective_start, end))
                spans[segment] = (min(previous[0], effective_start), max(previous[1], end))
        lap_time = number(row.get('time'))
        if lap_time is None or lap_time <= 0 or lap_time > end - start + .05:
            continue
        validity = row.get('valid')
        records.append({'code': code, 'lap': int(lap), 'end': end,
                        'time': round(lap_time, 3), 'segment': segment,
                        'valid': validity if isinstance(validity, bool) else None})
    segments = [{'segment': part, 'start': round(start, 3), 'end': round(end, 3)}
                for part, (start, end) in sorted(spans.items())]
    if any(current['start'] < previous['end'] for previous, current in zip(segments, segments[1:])):
        segments = []
        note = (note + ' Segment playback is unavailable because labelled coverage overlaps.').strip()
    if qualifying:
        note = (note + ' Segment playback covers recorded labelled laps, not official session boundaries.').strip()
        if not spans:
            note = (note + ' This source supplies no qualifying segment labels.').strip()
    note = (note + ' Only completed valid laps count; this is lap time order, not an official classification.').strip()
    return {'version': TIMING_VERSION, 'records': sorted(records, key=lambda row: (row['end'], row['code'], row['lap'])),
            'segments': segments, 'available': any(row['valid'] is True for row in records), 'note': note}


def unavailable_timing(note='Lap validity or completion timing is unavailable from this source.'):
    return {'version': TIMING_VERSION, 'records': [], 'segments': [], 'available': False, 'note': note}


def _epoch(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except ValueError:
        return None


def openf1_lap_timing(laps, drivers, origin, duration, code):
    roster = {number(driver.get('number', driver.get('driver_number'))):
              str(driver.get('code', driver.get('name_acronym')) or driver.get('number', driver.get('driver_number')))
              for driver in drivers}
    rows = []
    for lap in laps:
        driver = roster.get(number(lap.get('driver_number')))
        start, lap_time = _epoch(lap.get('date_start')), number(lap.get('lap_duration'))
        end = _epoch(lap.get('date_end'))
        if end is None and start is not None and lap_time is not None:
            end = start + lap_time
        if driver is None or start is None or end is None:
            continue
        validity = lap_validity(deleted=lap.get('deleted'), generated=lap.get('fastf1_generated'),
                                pit_in=lap.get('is_pit_in_lap'), pit_out=lap.get('is_pit_out_lap'),
                                accurate=lap.get('is_accurate'))
        rows.append({'code': driver, 'lap': lap.get('lap_number'), 'start': start - origin,
                     'end': end - origin, 'time': lap_time, 'segment': lap.get('qualifying_segment'),
                     'valid': validity})
    return build_lap_timing(rows, duration, qualifying=code in ('Q', 'SQ'),
                           note='Unknown deletion, generated lap or pit flags are excluded from valid lap timing.')
