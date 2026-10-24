import copy

import pandas as pd
import pytest

import archive_analysis as archive


@pytest.fixture
def session(monkeypatch):
    columns = {
        'drv': ['AAA'] * 7 + ['BBB'], 'dNum': ['1'] * 7 + ['2'],
        'lap': [1, 2, 3, 4, 5, 6, 7, 1], 'time': [90, 85, 89, 88, 92, 93, 94, 91],
        'sesT': [190, 275, 364, 452, 544, 637, 731, 191],
        'lST': [100, 190, 275, 364, 452, 544, 637, 100],
        'lSD': ['2026-10-10T13:00:00.000000000'] * 8,
        's1': [30, 30, 30, 30, 30, 30, 30, 30],
        's2': [30, 30, 30, 30, 30, 30, 30, 30],
        's3': [30, 25, 29, 28, 32, 33, 34, 31],
        'compound': ['MEDIUM'] * 8, 'life': [1, 2, 3, 4, 5, 6, 7, 3],
        'stint': [1] * 8, 'status': ['1', '1', '1', '2', '1', '1', '1', '1'],
        'del': [False, True, False, False, False, False, False, False],
        'ff1G': [False, False, False, False, True, False, False, False],
        'iacc': [True, True, True, True, True, 'None', True, True],
        'pin': ['None', 'None', 350, 'None', 'None', 'None', 'None', 'None'],
        'pout': ['None'] * 8, 'pos': [1] * 7 + [2], 'fresh': [True] * 8,
    }
    bundle = {
        'scheduled': {'round': 9, 'race': 'Example Grand Prix', 'location': 'Example',
                      'session_utc': '2026-10-10T13:00:00Z'},
        'revision': '2026.297.2', 'published_utc': '2026-10-10T18:00:00Z',
        'retrieved_utc': '2026-10-11T12:00:00Z',
        'source_url': 'https://github.com/TracingInsights/2026/tree/2026.297.2/Example/Qualifying',
        'drivers': [{'driver': 'AAA', 'dn': '1', 'team': 'Red Bull Racing', 'fn': 'Driver', 'ln': 'A'},
                    {'driver': 'BBB', 'dn': '2', 'team': 'Ferrari', 'fn': 'Driver', 'ln': 'B'},
                    {'driver': 'CCC', 'dn': '3', 'team': 'Racing Bulls', 'fn': 'Driver', 'ln': 'C'}],
        'lap_columns': columns,
    }
    calls = []
    monkeypatch.setattr(archive, '_load_archive_session',
                        lambda *args, **kwargs: calls.append((args, kwargs)) or bundle)
    return bundle, calls


def test_archive_preserves_normalised_laps_and_source_provenance(session):
    _, calls = session
    analysis = archive.load_archive_analysis(2026, 9, 'Practice 1', refresh=True)
    laps = analysis['laps']
    assert calls == [((2026, 9, 'FP1'), {'refresh': True})]
    assert str(laps.LapTime.dtype) == 'timedelta64[ns]'
    assert str(laps.IsAccurate.dtype) == 'boolean'
    assert laps.Team.iloc[0] == 'Red Bull'
    assert laps.LapStartTime.iloc[0].total_seconds() == 100
    assert laps.Time.iloc[0].total_seconds() == 190
    assert laps.date_start.iloc[0] == pd.Timestamp('2026-10-10T13:00:00Z')
    assert laps.TyreLife.iloc[-1] == 3
    metadata = analysis['metadata']
    assert metadata['archive_revision'] == '2026.297.2'
    assert metadata['source_retrieved_utc'] == '2026-10-11T12:00:00Z'
    assert metadata['published_utc'] == '2026-10-10T18:00:00Z'
    assert metadata['retrieved_utc'] != metadata['loaded_utc']
    assert metadata['is_live'] is False and metadata['archived'] is True
    assert 'is_complete' not in metadata
    assert metadata['source'] == 'TracingInsights / FastF1 archive'
    assert metadata['cached_fallback'] is False


def test_saved_release_fallback_is_explicit_without_claiming_access_restriction(session):
    bundle, _ = session
    bundle['cached_fallback'] = True
    metadata = archive.load_archive_analysis(2026, 9, 'FP1')['metadata']
    assert metadata['cached_fallback'] is True
    assert metadata['archive_revision'] == bundle['revision']
    assert metadata['source_retrieved_utc'] == bundle['retrieved_utc']
    assert 'access_limited' not in metadata


def test_clean_pace_excludes_deleted_pit_yellow_generated_and_unknown_accuracy(session):
    analysis = archive.load_archive_analysis(2026, 9, 'Q')
    assert list(zip(analysis['clean_laps'].Driver, analysis['clean_laps'].LapNumber)) == [
        ('AAA', 1), ('AAA', 7), ('BBB', 1),
    ]
    assert pd.isna(analysis['laps'].IsAccurate.iloc[5])
    assert analysis['laps'].IsPitInLap.iloc[2]


def test_practice_order_is_fastest_valid_lap_without_official_points(session):
    result = archive.load_archive_analysis(2026, 9, 'FP1')['results'].set_index('code')
    assert result.loc['AAA', 'best_lap_seconds'] == 88
    assert result.loc['AAA', 'position'] == 1
    assert result.loc['BBB', 'position'] == 2
    assert result.loc['BBB', 'gap_seconds'] == 3
    assert pd.isna(result.loc['CCC', 'position'])
    assert result.loc['CCC', 'lap_count'] == 0
    assert result.loc['CCC', 'team'] == 'RB F1 Team'
    assert result.points.isna().all() and result.grid.isna().all()


@pytest.mark.parametrize('code', ['Q', 'SQ', 'S', 'R'])
def test_lap_order_does_not_become_official_classification(session, code):
    analysis = archive.load_archive_analysis(2026, 9, code)
    result = analysis['results']
    assert result.position.isna().all()
    assert result.points.isna().all() and result.grid.isna().all()
    assert result.status.eq('').all()
    assert not analysis['metadata']['has_results']
    assert not analysis['metadata']['classification_available']
    assert analysis['metadata']['classification_kind'] == 'Classification unavailable in lap archive'
    assert result[['Q1_seconds', 'Q2_seconds', 'Q3_seconds']].isna().all().all()


@pytest.mark.parametrize('code,prefix', [('Q', 'Q'), ('SQ', 'SQ')])
def test_qualifying_segment_times_use_explicit_labels_and_ignore_deleted_laps(session, code, prefix):
    bundle, _ = session
    bundle['lap_columns']['qs'] = [f'{prefix}1', f'{prefix}1', f'{prefix}2', f'{prefix}2',
                                    f'{prefix}3', f'{prefix}3', f'{prefix}3', f'{prefix}1']
    result = archive.load_archive_analysis(2026, 9, code)['results'].set_index('code')
    assert result.loc['AAA', 'Q1_seconds'] == 90
    assert result.loc['AAA', 'Q2_seconds'] == 88
    assert result.loc['AAA', 'Q3_seconds'] == 93
    assert pd.isna(result.loc['BBB', 'Q2_seconds'])
    assert result.position.isna().all()


@pytest.mark.parametrize('field', ['iacc', 'del', 'ff1G'])
def test_unknown_boolean_is_not_assumed_safe(session, field):
    bundle, _ = session
    bundle['lap_columns'][field][0] = 'false'
    analysis = archive.load_archive_analysis(2026, 9, 'FP1')
    assert 1 not in analysis['clean_laps'].loc[lambda df: df.Driver.eq('AAA'), 'LapNumber'].tolist()
    if field == 'del':
        assert not analysis['metadata']['deleted_laps_checked']


@pytest.mark.parametrize('field', ['pin', 'pout'])
def test_missing_or_malformed_pit_flags_prevent_clean_claims(session, field):
    bundle, _ = session
    bundle['lap_columns'][field][0] = 'bad'
    analysis = archive.load_archive_analysis(2026, 9, 'FP1')
    assert analysis['clean_laps'].Driver.tolist().count('AAA') == 1
    bundle['lap_columns'].pop(field)
    assert archive.load_archive_analysis(2026, 9, 'FP1')['clean_laps'].empty


def test_inconsistent_sectors_or_missing_clock_remain_visible_outside_clean_pace(session):
    bundle, _ = session
    bundle['lap_columns']['s3'][0] = 'None'
    bundle['lap_columns']['lST'][6] = 'None'
    analysis = archive.load_archive_analysis(2026, 9, 'FP1')
    assert len(analysis['laps']) == 8
    assert analysis['clean_laps'].Driver.tolist() == ['BBB']
    assert pd.isna(analysis['laps'].iloc[0].Sector3Time)


def test_missing_dates_are_not_replaced_with_scheduled_start(session):
    bundle, _ = session
    bundle['lap_columns']['lSD'][0] = 'None'
    analysis = archive.load_archive_analysis(2026, 9, 'FP1')
    assert pd.isna(analysis['laps'].iloc[0].date_start)
    assert analysis['clean_laps'].Driver.tolist().count('AAA') == 1


@pytest.mark.parametrize('mutation,match', [
    (lambda c: c['time'].pop(), 'inconsistent lengths'),
    (lambda c: c.update(extra='bad'), 'inconsistent lengths'),
    (lambda c: c.pop('drv'), 'identity columns'),
    (lambda c: c['lap'].__setitem__(1, 1), 'Duplicate archive driver lap'),
    (lambda c: c['lap'].__setitem__(1, 1.5), 'Invalid archive lap identity'),
    (lambda c: c['drv'].__setitem__(0, 'BBB'), 'identities disagree'),
])
def test_invalid_columns_and_duplicate_laps_are_rejected(session, mutation, match):
    bundle, _ = session
    mutation(bundle['lap_columns'])
    with pytest.raises(ValueError, match=match):
        archive._normalise_laps(bundle['lap_columns'], bundle['drivers'])


def test_duplicate_driver_metadata_is_rejected(session):
    bundle, _ = session
    bundle['drivers'].append(copy.deepcopy(bundle['drivers'][0]))
    with pytest.raises(ValueError, match='duplicate archive driver'):
        archive._normalise_laps(bundle['lap_columns'], bundle['drivers'])


def test_future_or_wrong_session_identity_is_rejected(session):
    bundle, _ = session
    bundle['scheduled']['session_utc'] = '2099-01-01T00:00:00Z'
    with pytest.raises(archive.SessionUnavailableError, match='scheduled start'):
        archive._build_analysis(bundle, 2026, 9, 'FP1')
    bundle['scheduled']['session_utc'] = '2026-10-10T13:00:00Z'
    bundle['scheduled']['round'] = 8
    with pytest.raises(ValueError, match='identity does not match'):
        archive._build_analysis(bundle, 2026, 9, 'FP1')


def test_malformed_source_is_a_safe_typed_failure_for_backup_provider(session):
    from archive_source import ArchiveUnavailableError
    bundle, _ = session
    bundle['lap_columns']['time'].pop()
    with pytest.raises(ArchiveUnavailableError) as failure:
        archive.load_archive_analysis(2026, 9, 'FP1')
    assert failure.value.reason == 'unavailable'
    assert 'inconsistent lengths' not in str(failure.value)


def test_nonpositive_and_missing_lap_durations_are_not_zero_second_bests(session):
    bundle, _ = session
    bundle['lap_columns']['time'][0] = 0
    bundle['lap_columns']['time'][-1] = 'None'
    analysis = archive.load_archive_analysis(2026, 9, 'FP1')
    assert pd.isna(analysis['laps'].iloc[0].LapTime)
    assert analysis['results'].set_index('code').loc['BBB', 'timed_lap_count'] == 0
    assert pd.isna(analysis['results'].set_index('code').loc['BBB', 'position'])
