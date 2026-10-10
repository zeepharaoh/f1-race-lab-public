from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import championship_data as standings


def session(sprint=False):
    return SimpleNamespace(results=pd.DataFrame({
        'Abbreviation': ['AAA', 'NEW'], 'TeamName': ['Red Bull Racing', 'Mercedes']}),
        event={'EventFormat': 'sprint_qualifying' if sprint else 'conventional'})


def driver_payload(round_number=3):
    return {'pages': [{'StandingsTable': {'StandingsLists': [{
        'season': '2026', 'round': str(round_number), 'DriverStandings': [
            {'position': '2', 'Driver': {'code': 'AAA'}, 'points': '20'},
            {'position': '1', 'Driver': {'code': 'ABS'}, 'points': '30'}]}]}}]}


def constructor_payload():
    return {'pages': [{'StandingsTable': {'StandingsLists': [{
        'season': '2026', 'round': '3', 'ConstructorStandings': [
            {'position': '1', 'Constructor': {'name': 'Red Bull'}, 'points': '50'},
            {'position': '2', 'Constructor': {'name': 'Mercedes'}, 'points': '40'}]}]}}]}


def sprint_payload():
    return {'pages': [{'RaceTable': {'Races': [{
        'season': '2026', 'round': '4', 'SprintResults': [
            {'Driver': {'code': 'AAA'}, 'Constructor': {'name': 'Red Bull'}, 'points': '8'},
            {'Driver': {'code': 'NEW'}, 'Constructor': {'name': 'Mercedes'}, 'points': '7'}]}]}}]}


def sources(monkeypatch, sprint=None):
    calls = []
    payloads = {'3/driverStandings': driver_payload(), '3/constructorStandings': constructor_payload(),
                '4/sprint': sprint_payload() if sprint is None else sprint}
    monkeypatch.setattr(standings, 'fetch', lambda year, endpoint: (calls.append(endpoint), payloads[endpoint])[1])
    return calls


def test_sprint_uses_previous_round_and_keeps_absent_drivers(monkeypatch):
    calls = sources(monkeypatch)
    snapshot = standings.load_championship_snapshot(session(True), 4, 'S')
    assert calls == ['3/driverStandings', '3/constructorStandings']
    assert snapshot['drivers'] == {'ABS': 30, 'AAA': 20, 'NEW': 0}
    assert list(snapshot['drivers']) == ['ABS', 'AAA', 'NEW']
    assert snapshot['constructors'] == {'Red Bull Racing': 50, 'Mercedes': 40}
    assert not snapshot['warning']


def test_gp_adds_same_weekend_sprint_once_with_source_points(monkeypatch):
    sources(monkeypatch)
    snapshot = standings.load_championship_snapshot(session(True), 4, 'R')
    assert snapshot['drivers'] == {'ABS': 30, 'AAA': 28, 'NEW': 7}
    assert snapshot['constructors'] == {'Red Bull Racing': 58, 'Mercedes': 47}
    assert 'Sprint included' in snapshot['label']


def test_regular_gp_does_not_request_sprint(monkeypatch):
    calls = sources(monkeypatch)
    standings.load_championship_snapshot(session(), 4, 'R')
    assert '4/sprint' not in calls


def test_round_one_starts_at_zero_without_fetching_latest(monkeypatch):
    monkeypatch.setattr(standings, 'fetch', lambda *args: pytest.fail('No prior season points'))
    snapshot = standings.load_championship_snapshot(session(), 1, 'R')
    assert snapshot['drivers'] == {'AAA': 0, 'NEW': 0}
    assert snapshot['constructors'] == {'Red Bull Racing': 0, 'Mercedes': 0}


def test_pending_sprint_keeps_prior_points_and_visible_warning(monkeypatch):
    sources(monkeypatch, {'pages': [{'RaceTable': {'Races': []}}]})
    snapshot = standings.load_championship_snapshot(session(True), 4, 'R')
    assert snapshot['drivers']['AAA'] == 20
    assert 'Sprint points unavailable' in snapshot['warning']
    assert 'Sprint pending' in snapshot['label']


def test_future_round_snapshot_is_rejected(monkeypatch):
    sources(monkeypatch)
    monkeypatch.setattr(standings, '_source', lambda *args: (driver_payload(5), False))
    snapshot = standings.load_championship_snapshot(session(), 4, 'R')
    assert not snapshot['drivers'] and not snapshot['constructors']
    assert 'unavailable' in snapshot['warning']


def test_malformed_sprint_never_leaves_partially_added_points(monkeypatch):
    payload = sprint_payload()
    payload['pages'][0]['RaceTable']['Races'][0]['SprintResults'][1]['Constructor']['name'] = ['invalid', 'team']
    sources(monkeypatch, payload)
    snapshot = standings.load_championship_snapshot(session(True), 4, 'R')
    assert snapshot['drivers']['AAA'] == 20
    assert snapshot['constructors']['Red Bull Racing'] == 50
    assert 'Sprint points unavailable' in snapshot['warning']


def test_exact_saved_snapshot_fallback_is_labelled(monkeypatch, tmp_path):
    import json
    sources(monkeypatch)
    cache = tmp_path / 'cache' / 'results' / '2026' / '3'
    cache.mkdir(parents=True)
    (cache / 'driverStandings.json').write_text(json.dumps(driver_payload()))
    (cache / 'constructorStandings.json').write_text(json.dumps(constructor_payload()))
    monkeypatch.setattr(standings, 'ROOT', tmp_path)
    def unavailable(*args):
        raise requests.ConnectionError('Offline')
    monkeypatch.setattr(standings, 'fetch', unavailable)
    snapshot = standings.load_championship_snapshot(session(), 4, 'R')
    assert snapshot['drivers']['AAA'] == 20
    assert 'Saved snapshot' in snapshot['warning']


def test_duplicate_or_invalid_points_are_rejected():
    payload = driver_payload()
    rows = payload['pages'][0]['StandingsTable']['StandingsLists'][0]['DriverStandings']
    rows.append(rows[0])
    with pytest.raises(ValueError, match='Duplicate'):
        standings._standings(payload, 2026, 3, 'DriverStandings', lambda row: row['Driver']['code'])
    with pytest.raises(ValueError, match='Invalid'):
        standings._points('nan')
