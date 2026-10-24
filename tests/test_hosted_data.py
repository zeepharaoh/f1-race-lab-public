"""Hosted source routing and classification provenance."""
import pandas as pd
import pytest

import archive_analysis
import archive_replay
from archive_source import ArchiveUnavailableError
import hosted_data
import openf1_data
import web_replay


@pytest.mark.parametrize('view', ['analysis', 'replay'])
def test_archive_success_does_not_call_backup(monkeypatch, view):
    expected = {'metadata': {'source': 'TracingInsights', 'archive_revision': '2026.297.2'}}
    calls = []
    def primary(*args, **kwargs):
        calls.append((args, kwargs))
        return expected
    def denied(*args, **kwargs):
        raise AssertionError('Backup must not run when the free archive is available')
    if view == 'analysis':
        monkeypatch.setattr(archive_analysis, 'load_archive_analysis', primary)
        monkeypatch.setattr(openf1_data, 'load_openf1_analysis', denied)
        result = hosted_data.load_hosted_analysis(2026, 18, 'FP3', refresh=True)
    else:
        monkeypatch.setattr(archive_replay, 'load_archive_replay', primary)
        monkeypatch.setattr(web_replay, 'load_browser_replay', denied)
        result = hosted_data.load_hosted_replay(2026, 18, 'FP3', refresh=True)
    assert result is expected
    assert calls == [((2026, 18, 'FP3'), {'refresh': True})]


def test_backup_retains_its_source_and_original_time(monkeypatch):
    def missing(*args, **kwargs):
        raise ArchiveUnavailableError('not_published')
    def backup(*args, **kwargs):
        return {'metadata': {'source': 'OpenF1', 'source_retrieved_utc': '2026-10-01T10:00:00Z'}}
    monkeypatch.setattr(archive_analysis, 'load_archive_analysis', missing)
    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', backup)
    result = hosted_data.load_hosted_analysis(2026, 18, 'Q')
    assert result['metadata'] == {'source': 'OpenF1', 'source_retrieved_utc': '2026-10-01T10:00:00Z',
                                  'fallback_from': 'TracingInsights', 'fallback_reason': 'not_published'}


def test_both_source_failures_are_safe_and_distinct(monkeypatch):
    def missing(*args, **kwargs):
        raise ArchiveUnavailableError('not_published')
    def denied(*args, **kwargs):
        raise openf1_data.OpenF1AccessError(reason='live_access_restricted')
    monkeypatch.setattr(archive_analysis, 'load_archive_analysis', missing)
    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', denied)
    with pytest.raises(hosted_data.HostedDataUnavailable) as failure:
        hosted_data.load_hosted_analysis(2026, 18, 'Q')
    assert 'archive has not published' in str(failure.value)
    assert 'OpenF1 temporarily restricts' in str(failure.value)
    assert 'http' not in str(failure.value)


@pytest.mark.parametrize('code,kind', [('R', 'Grand Prix'), ('S', 'Sprint'), ('Q', 'Qualifying')])
def test_only_matching_official_results_fill_archive_classification(code, kind):
    results = pd.DataFrame({'code': ['NOR', 'RES'], 'position': [float('nan')] * 2,
                            'points': [float('nan')] * 2, 'grid': [float('nan')] * 2, 'status': ['', '']})
    analysis = {'metadata': {'archived': True, 'classification_available': False,
                             'session_code': code, 'round': 18}, 'results': results}
    official = pd.DataFrame({'code': ['NOR', 'RES'], 'position': [2., 1.], 'points': [18., 25.],
                             'grid': [3., 1.], 'status': ['Finished', 'Finished'],
                             'round': [18, 17], 'kind': [kind, kind]})
    original = results.copy(deep=True)
    season = {'results': official, 'qualifying': official}
    merged, matched = hosted_data.published_classification(analysis, season)
    assert matched
    assert merged.set_index('code').loc['NOR', 'position'] == 2
    assert pd.isna(merged.set_index('code').loc['RES', 'position'])
    pd.testing.assert_frame_equal(original, results)
    if code in ('R', 'S'):
        assert merged.set_index('code').loc['NOR', 'points'] == 18
    else:
        assert merged.points.isna().all()
    season = {'results': official.assign(round=17), 'qualifying': official.assign(round=17)}
    unchanged, matched = hosted_data.published_classification(analysis, season)
    assert not matched
    pd.testing.assert_frame_equal(original, unchanged)


def test_backup_classification_is_preserved():
    results = pd.DataFrame({'code': ['NOR'], 'position': [1.]})
    analysis = {'metadata': {'session_code': 'R', 'round': 18}, 'results': results}
    unchanged, matched = hosted_data.published_classification(analysis, {})
    assert unchanged is results and not matched


@pytest.mark.parametrize('failure', [ValueError('invalid raw payload'), KeyError('driver'), TypeError('bad type')])
def test_malformed_backup_is_reported_as_source_unavailable(monkeypatch, failure):
    def missing(*args, **kwargs):
        raise ArchiveUnavailableError('unavailable')
    def malformed(*args, **kwargs):
        raise failure
    monkeypatch.setattr(archive_analysis, 'load_archive_analysis', missing)
    monkeypatch.setattr(openf1_data, 'load_openf1_analysis', malformed)
    with pytest.raises(hosted_data.HostedDataUnavailable, match='Neither source') as error:
        hosted_data.load_hosted_analysis(2026, 18, 'Q')
    assert 'raw payload' not in str(error.value)
