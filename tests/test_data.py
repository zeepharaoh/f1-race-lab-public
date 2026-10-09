import pandas as pd
from data import cumulative_points, clean_laps, flatten


def test_cumulative_includes_sprints_and_missing_rounds():
    results = pd.DataFrame({'round':[1,1,3,3], 'team':['A','A','A','B'], 'points':[25,8,18,10]})
    out = cumulative_points(results, 'team').pivot(index='round', columns='team', values='points')
    assert out.loc[1,'A'] == 33
    assert out.loc[2,'A'] == 33
    assert out.loc[3,'A'] == 51
    assert out.loc[3,'B'] == 10


def test_clean_laps_excludes_neutralised_pit_deleted_and_inaccurate():
    td = pd.to_timedelta
    laps = pd.DataFrame({'LapTime':td([90,91,92,93,94], unit='s'),
        'PitInTime':td([None,None,1,None,None],unit='s'), 'PitOutTime':td([None]*5,unit='s'),
        'TrackStatus':['1','14','1','1','1'], 'IsAccurate':[True,True,True,False,True],
        'Deleted':[False,False,False,False,True]})
    out = clean_laps(laps)
    assert list(out.lap_seconds) == [90.0]


def test_empty_sprint_results_preserves_schema():
    out = flatten({'pages':[{'RaceTable':{'Races':[]}}]}, 'SprintResults','Sprint',16)
    assert out.empty
    assert 'points' in out
