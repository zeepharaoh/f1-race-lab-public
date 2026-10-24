import math

import pytest

from replay_timing import build_lap_timing, lap_validity, segment_number


def row(**changes):
    return {'code': 'AAA', 'lap': 1, 'start': 0, 'end': 90, 'time': 90,
            'segment': 'Q1', 'valid': True, **changes}


def test_completed_lap_clock_does_not_reveal_a_future_best():
    timing = build_lap_timing([row(), row(lap=2, start=90, end=180, time=89)], 200, qualifying=True)
    assert not [lap for lap in timing['records'] if lap['end'] <= 89.999]
    assert min(lap['time'] for lap in timing['records'] if lap['end'] <= 90) == 90
    assert min(lap['time'] for lap in timing['records'] if lap['end'] <= 180) == 89


def test_segment_coverage_uses_explicit_labels_and_effective_pit_exit():
    timing = build_lap_timing([row(), row(lap=2, start=90, end=300, time=None, segment='Q2', coverage_start=210),
                               row(lap=3, start=300, end=390, segment='Q2')], 400, qualifying=True)
    assert timing['segments'] == [{'segment': 1, 'start': 0, 'end': 90},
                                  {'segment': 2, 'start': 210, 'end': 390}]
    assert 'not official session boundaries' in timing['note']


def test_unlabelled_qualifying_never_gets_fixed_time_segments():
    timing = build_lap_timing([row(segment=None), row(lap=2, start=1800, end=1890, segment=None)],
                             3600, qualifying=True)
    assert timing['segments'] == []
    assert all(lap['segment'] is None for lap in timing['records'])
    assert 'no qualifying segment labels' in timing['note']


def test_overlapping_coverage_does_not_offer_ambiguous_segment_playback():
    timing = build_lap_timing([row(), row(lap=2, start=60, end=150, segment='Q2')], 200, qualifying=True)
    assert timing['segments'] == []
    assert 'coverage overlaps' in timing['note']
    assert [lap['segment'] for lap in timing['records']] == [1, 2]


@pytest.mark.parametrize('value,expected', [('Q1', 1), ('SQ2', 2), ('q3', 3), (1, 1),
                                          ('Q4', None), (True, None), ('3.5', None), (None, None)])
def test_segment_labels_are_strict(value, expected):
    assert segment_number(value) == expected


@pytest.mark.parametrize('field', ['deleted', 'generated', 'pit_in', 'pit_out'])
def test_explicit_invalid_flags_exclude_a_lap(field):
    flags = dict(deleted=False, generated=False, pit_in=False, pit_out=False, accurate=True)
    assert lap_validity(**flags) is True
    flags[field] = True
    assert lap_validity(**flags) is False
    flags[field] = None
    assert lap_validity(**flags) is None


def test_explicit_inaccurate_lap_is_excluded_but_unknown_accuracy_is_not_invented_false():
    flags = dict(deleted=False, generated=False, pit_in=False, pit_out=False)
    assert lap_validity(**flags, accurate=False) is False
    assert lap_validity(**flags, accurate=None) is True


@pytest.mark.parametrize('changes', [{'end': math.inf}, {'time': math.nan}, {'time': 0}, {'time': None},
                                      {'time': 200}, {'lap': 1.5}, {'lap': True}, {'start': -1},
                                      {'end': 101}, {'code': ''}])
def test_invalid_lap_identity_time_and_clock_are_not_emitted(changes):
    timing = build_lap_timing([row(**changes)], 100)
    assert timing['records'] == []
    assert timing['available'] is False


def test_duplicate_driver_lap_identity_is_ambiguous_and_excluded():
    timing = build_lap_timing([row(), row(time=89)], 100)
    assert timing['records'] == []


def test_validity_remains_explicit_for_display_and_only_true_is_available():
    timing = build_lap_timing([row(valid=None), row(code='BBB', valid=False)], 100)
    assert [lap['valid'] for lap in timing['records']] == [None, False]
    assert timing['available'] is False
