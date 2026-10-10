from types import SimpleNamespace

import pandas as pd
from replay_strategy import StrategyState, StrategyTimeline, replay_time_origin


def source(rows, origin=100):
    for row in rows:
        for column in ('LapStartTime', 'Time', 'PitInTime', 'PitOutTime'):
            if column in row and row[column] is not None:
                row[column] = pd.to_timedelta(float(row[column]), unit='s')
    session = SimpleNamespace(laps=pd.DataFrame(rows), session_start_time=pd.to_timedelta(100., unit='s'),
                              track_status=pd.DataFrame({'Time': pd.to_timedelta([0, 120], unit='s'),
                                                         'Status': ['1', '2']}))
    replay = {'track_statuses': [{'status': '1', 'start_time': -origin},
                                 {'status': '2', 'start_time': 120 - origin}]}
    return session, replay


def lap(number, start, finish, compound='MEDIUM', age=None, pit_in=None, pit_out=None):
    return {'Driver': 'AAA', 'LapNumber': number, 'LapStartTime': start, 'Time': finish,
            'Compound': compound, 'TyreLife': number if age is None else age,
            'PitInTime': pit_in, 'PitOutTime': pit_out}


def test_origin_recovers_exact_engine_shift_and_rejects_inconsistent_sources():
    session, data = source([lap(1, 100, 160)], origin=99.5)
    assert replay_time_origin(session, data) == 99.5
    data['track_statuses'][1]['start_time'] += .1
    assert replay_time_origin(session, data) is None
    assert StrategyTimeline.from_session(session, data).warning


def test_tyre_age_counts_completed_laps_and_does_not_read_future_lap():
    session, data = source([lap(1, 100, 160), lap(2, 160, 220), lap(3, 220, 280, 'HARD', 1, pit_out=235)])
    strategy = StrategyTimeline.from_session(session, data)
    assert strategy.at('AAA', 0).tyre_label() == 'MED 0L'
    assert strategy.at('AAA', 59).age == 0
    assert strategy.at('AAA', 60).age == 1
    assert strategy.at('AAA', 120).tyre_label() == 'MED 2L'
    assert strategy.at('AAA', 134.99).compound == 'MEDIUM'
    assert strategy.at('AAA', 135).tyre_label() == 'HARD 0L'
    assert strategy.at('AAA', 180).tyre_label() == 'HARD 1L'
    assert strategy.at('AAA', 60).tyre_label() == 'MED 1L'


def test_visits_count_on_pit_exit_after_a_recorded_entry_and_rewind():
    session, data = source([lap(1, 100, 160, pit_in=155),
                            lap(2, 160, 220, 'HARD', 1, pit_in=215, pit_out=170),
                            lap(3, 220, 280, 'SOFT', 1, pit_out=230)])
    strategy = StrategyTimeline.from_session(session, data)
    assert strategy.at('AAA', 55).in_pit
    assert strategy.at('AAA', 55).stops == 0
    assert strategy.at('AAA', 69.99).stops == 0
    assert strategy.at('AAA', 70).stops_label() == '1 · L1'
    assert not strategy.at('AAA', 70).in_pit
    assert strategy.at('AAA', 115).in_pit
    assert strategy.at('AAA', 130).stops_label() == '2 · L2'
    assert strategy.at('AAA', 30).stops_label() == '0'


def test_initial_pit_exit_does_not_count_as_a_completed_stop():
    session, data = source([lap(1, 100, 160, pit_out=110), lap(2, 160, 220)])
    strategy = StrategyTimeline.from_session(session, data)
    assert strategy.at('AAA', 0).in_pit
    assert strategy.at('AAA', 10).stops == 0
    assert not strategy.at('AAA', 10).in_pit
    assert strategy.at('AAA', 9.99).compound is None
    assert strategy.at('AAA', 10).compound == 'MEDIUM'


def test_unpaired_later_exit_shows_unknown_only_after_missing_visit_occurs():
    session, data = source([lap(1, 100, 160), lap(2, 160, 220, pit_out=170)])
    strategy = StrategyTimeline.from_session(session, data)
    assert strategy.at('AAA', 20).stops == 0
    assert strategy.at('AAA', 70).stops is None
    assert strategy.at('AAA', 70).in_pit is None
    assert strategy.at('AAA', 20).stops == 0


def test_pre_start_pit_entry_is_not_counted_and_incomplete_visit_stays_pending():
    session, data = source([lap(1, 100, 160, pit_in=90, pit_out=110),
                            lap(2, 160, 220, pit_in=215)])
    strategy = StrategyTimeline.from_session(session, data)
    assert strategy.at('AAA', 20).stops == 0
    assert strategy.at('AAA', 120).stops == 0
    assert strategy.at('AAA', 120).in_pit


def test_same_compound_pit_visit_counts_without_claiming_a_tyre_change():
    session, data = source([lap(1, 100, 160, 'INTERMEDIATE', 1, pit_in=155),
                            lap(2, 160, 220, 'INTERMEDIATE', 2, pit_out=170)])
    state = StrategyTimeline.from_session(session, data).at('AAA', 70)
    assert state.stops == 1 and state.compound == 'INTERMEDIATE'
    assert state.tyre_label() == 'INTER 1L'


def test_unaligned_clock_or_missing_compound_has_visible_missingness():
    session, data = source([lap(1, 100, 160, 'UNKNOWN', age=float('nan'))])
    state = StrategyTimeline.from_session(session, data).at('AAA', 20)
    assert state.tyre_label() == '—' and state.age is None
    data['track_statuses'] = []
    state = StrategyTimeline.from_session(session, data).at('AAA', 20)
    assert state.tyre_label() == '—' and state.stops_label() == '—'
    assert state.in_pit is None


def test_missing_pit_columns_or_missing_start_lap_do_not_show_false_zero():
    session, data = source([lap(2, 160, 220)])
    state = StrategyTimeline.from_session(session, data).at('AAA', 70)
    assert state.stops is None and state.in_pit is None
    session, data = source([lap(1, 100, 160)])
    session.laps = session.laps.drop(columns=['PitInTime'])
    assert StrategyTimeline.from_session(session, data).at('AAA', 20).stops is None


def test_exact_origin_handles_filtered_replay_and_unknown_driver():
    session, data = source([lap(1, 100, 160, 'SOFT', 5)], origin=99.5)
    strategy = StrategyTimeline.from_session(session, data)
    assert strategy.at('AAA', .5).tyre_label() == 'SOFT 4L'
    assert strategy.at('AAA', 60.5).age == 5
    assert strategy.at('MISSING', 70) == StrategyState()
    assert strategy.at('AAA', float('nan')) == StrategyState()
