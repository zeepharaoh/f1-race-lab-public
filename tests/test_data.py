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


import copy
import json

import pytest
import requests


@pytest.fixture
def season_sources():
    constructor = {'constructorId': 'mclaren', 'name': 'McLaren'}
    driver = {'driverId': 'norris', 'givenName': 'Lando', 'familyName': 'Norris', 'code': 'NOR'}
    race = {'round': '1', 'raceName': 'Example Grand Prix', 'date': '2026-03-01',
            'Circuit': {'circuitName': 'Example Circuit'}}
    result = {'Driver': driver, 'Constructor': constructor, 'position': '1', 'points': '25',
              'grid': '1', 'status': 'Finished'}
    tables = {
        'constructorStandings': {'StandingsTable': {'StandingsLists': [{'season': '2026', 'round': '1',
            'ConstructorStandings': [{'position': '1', 'points': '25', 'wins': '1', 'Constructor': constructor}]}]}},
        'driverStandings': {'StandingsTable': {'StandingsLists': [{'season': '2026', 'round': '1',
            'DriverStandings': [{'position': '1', 'points': '25', 'wins': '1', 'Driver': driver,
                                 'Constructors': [constructor]}]}]}},
        'results': {'RaceTable': {'Races': [{**race, 'Results': [result]}]}},
        'sprint': {'RaceTable': {'Races': []}},
        'qualifying': {'RaceTable': {'Races': [{**race, 'QualifyingResults': [
            {**result, 'Q1': '1:30.000', 'Q2': '1:29.000', 'Q3': '1:28.000'}]}]}},
        'calendar': {'RaceTable': {'Races': [race]}},
    }
    sources = {}
    for key, value in tables.items():
        total = 0 if key == 'sprint' else 1
        sources[key] = {'retrieved_utc': '2026-03-01T18:00:00+00:00',
                        'url': ('https://api.jolpi.ca/ergast/f1/2026/' if key == 'calendar'
                                else f'https://api.jolpi.ca/ergast/f1/2026/{key}/'),
                        'pages': [{'limit': '100', 'offset': '0', 'total': str(total), **value}]}
    return sources


def season_loader(monkeypatch, tmp_path, sources, *, unavailable=()):
    import data
    monkeypatch.setattr(data, 'ROOT', tmp_path)
    calls = []
    def fetch(year, endpoint, refresh=False):
        calls.append(endpoint)
        if endpoint in unavailable:
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError('429 Too Many Requests', response=response)
        return copy.deepcopy(sources[endpoint])
    monkeypatch.setattr(data, 'fetch', fetch)
    return data, calls


def snapshot_path(root):
    return root / 'cache' / 'results' / '2026' / 'last_complete_snapshot.json'


def test_optional_qualifying_limit_does_not_hide_valid_championship(monkeypatch, tmp_path, season_sources):
    module, calls = season_loader(monkeypatch, tmp_path, season_sources, unavailable={'qualifying'})
    result = module.load_season(refresh=True)
    assert calls == ['constructorStandings', 'driverStandings', 'results', 'sprint', 'calendar', 'qualifying']
    assert result['teams'].points.tolist() == [25.]
    assert result['drivers'].code.tolist() == ['NOR']
    assert result['refresh_error'] is None
    assert result['qualifying_status'] == 'unavailable'
    assert result['qualifying_available'] is False
    assert result['qualifying'].empty
    assert {'Q1', 'Q2', 'Q3', 'driver_id', 'round', 'position'}.issubset(result['qualifying'].columns)
    assert 'qualifying' not in result['sources']
    assert '429' in result['qualifying_error']
    assert snapshot_path(tmp_path).exists()


def test_optional_qualifying_uses_saved_times_without_staling_new_championship(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    module.load_season()
    season_sources['constructorStandings']['retrieved_utc'] = '2026-03-01T19:00:00+00:00'
    module, _ = season_loader(monkeypatch, tmp_path, season_sources, unavailable={'qualifying'})
    result = module.load_season(refresh=True)
    assert result['refresh_error'] is None
    assert result['qualifying_status'] == 'saved'
    assert result['qualifying_available'] is True
    assert result['qualifying'].Q3.tolist() == ['1:28.000']
    assert result['sources']['qualifying']['retrieved_utc'] == '2026-03-01T18:00:00+00:00'
    assert result['sources']['constructorStandings']['retrieved_utc'] == '2026-03-01T19:00:00+00:00'


def test_core_limit_uses_coherent_snapshot_with_original_dates(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    module.load_season()
    before = snapshot_path(tmp_path).read_bytes()
    module, _ = season_loader(monkeypatch, tmp_path, season_sources, unavailable={'driverStandings'})
    result = module.load_season(refresh=True)
    assert '429' in result['refresh_error']
    assert result['round'] == 1
    assert result['sources']['driverStandings']['retrieved_utc'] == '2026-03-01T18:00:00+00:00'
    assert snapshot_path(tmp_path).read_bytes() == before


def test_inconsistent_round_refresh_uses_saved_snapshot_and_does_not_overwrite(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    module.load_season()
    before = snapshot_path(tmp_path).read_bytes()
    season_sources['driverStandings']['pages'][0]['StandingsTable']['StandingsLists'][0]['round'] = '2'
    result = module.load_season(refresh=True)
    assert result['round'] == 1
    assert 'different round coverage' in result['refresh_error']
    assert snapshot_path(tmp_path).read_bytes() == before


def test_missing_results_round_refresh_uses_saved_snapshot(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    module.load_season()
    season_sources['results']['pages'][0]['RaceTable']['Races'] = []
    result = module.load_season(refresh=True)
    assert result['round'] == 1
    assert len(result['gp']) == 1
    assert 'do not cover every standings round' in result['refresh_error']


def test_inconsistent_core_without_snapshot_is_not_saved_as_complete(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    season_sources['driverStandings']['pages'][0]['StandingsTable']['StandingsLists'][0]['round'] = '2'
    with pytest.raises(ValueError, match='different round coverage'):
        module.load_season()
    assert not snapshot_path(tmp_path).exists()


def test_stale_endpoint_fallback_marks_championship_without_rewriting_snapshot(monkeypatch, tmp_path, season_sources):
    season_sources['results'].update(cached_fallback=True, refresh_error='Rate limit')
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    result = module.load_season(refresh=True)
    assert result['refresh_error']
    assert result['sources']['results']['retrieved_utc'] == '2026-03-01T18:00:00+00:00'
    assert not snapshot_path(tmp_path).exists()


def test_malformed_optional_qualifying_does_not_discard_championship(monkeypatch, tmp_path, season_sources):
    season_sources['qualifying']['pages'] = [{'RaceTable': {'Races': [{'round': '1'}]}}]
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    result = module.load_season()
    assert result['round'] == 1 and result['refresh_error'] is None
    assert result['qualifying'].empty and result['qualifying_error']


def test_source_season_mismatch_is_not_saved_as_this_year(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    season_sources['driverStandings']['pages'][0]['StandingsTable']['StandingsLists'][0]['season'] = '2025'
    with pytest.raises(ValueError, match='different season coverage'):
        module.load_season()
    assert not snapshot_path(tmp_path).exists()


def test_saved_snapshot_missing_zero_point_driver_is_not_complete(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    saved = copy.deepcopy(season_sources)
    zero_driver = {'driverId': 'bottas', 'givenName': 'Valtteri', 'familyName': 'Bottas', 'code': 'BOT'}
    zero_result = {**saved['results']['pages'][0]['RaceTable']['Races'][0]['Results'][0],
                   'Driver': zero_driver, 'points': '0', 'position': '2'}
    results = saved['results']['pages'][0]['RaceTable']['Races'][0]['Results']
    results.append(zero_result)
    saved['results']['pages'][0]['total'] = '2'
    standing = saved['driverStandings']['pages'][0]['StandingsTable']['StandingsLists'][0]['DriverStandings']
    standing.append({**standing[0], 'Driver': zero_driver, 'position': '2', 'points': '0', 'wins': '0'})
    saved['driverStandings']['pages'][0]['total'] = '2'
    results.remove(zero_result)
    frames = module._season_frames(saved, 2026)
    assert frames['gp']['round'].nunique() == 1
    assert frames['reconciliation'].difference.eq(0).all()
    bundle = snapshot_path(tmp_path)
    bundle.parent.mkdir(parents=True)
    bundle.write_text(json.dumps(saved))
    before = bundle.read_bytes()
    module, _ = season_loader(monkeypatch, tmp_path, season_sources, unavailable={'results'})
    with pytest.raises(requests.HTTPError, match='429'):
        module.load_season(refresh=True)
    assert bundle.read_bytes() == before


def test_bad_saved_qualifying_is_discarded_without_losing_good_core(monkeypatch, tmp_path, season_sources):
    module, _ = season_loader(monkeypatch, tmp_path, season_sources)
    module.load_season()
    saved = json.loads(snapshot_path(tmp_path).read_text())
    saved['qualifying']['pages'][0]['total'] = '2'
    snapshot_path(tmp_path).write_text(json.dumps(saved))
    module, _ = season_loader(monkeypatch, tmp_path, season_sources,
                             unavailable={'driverStandings', 'qualifying'})
    result = module.load_season(refresh=True)
    assert result['round'] == 1 and result['refresh_error']
    assert result['teams'].points.tolist() == [25.]
    assert result['qualifying_status'] == 'unavailable'
    assert result['qualifying'].empty
    assert 'qualifying' not in result['sources']


@pytest.mark.parametrize('field,value', [('url', 'https://example.invalid/standings'),
                                        ('retrieved_utc', 'not a date')])
def test_saved_snapshot_source_identity_and_date_are_validated(monkeypatch, tmp_path, season_sources, field, value):
    saved = copy.deepcopy(season_sources)
    saved['constructorStandings'][field] = value
    bundle = snapshot_path(tmp_path)
    bundle.parent.mkdir(parents=True)
    bundle.write_text(json.dumps(saved))
    module, _ = season_loader(monkeypatch, tmp_path, season_sources, unavailable={'results'})
    with pytest.raises(requests.HTTPError, match='429'):
        module.load_season(refresh=True)
