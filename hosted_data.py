"""Choose published sources for hosted session views."""
import requests

from weekend import SessionUnavailableError


class HostedDataUnavailable(SessionUnavailableError):
    pass


def _load(primary, backup, year, round_number, session_code, refresh):
    from archive_source import ArchiveUnavailableError
    from openf1_data import OpenF1AccessError
    try:
        return primary(year, round_number, session_code, refresh=refresh)
    except ArchiveUnavailableError as archive_error:
        reason = archive_error.reason
    try:
        result = backup(year, round_number, session_code, refresh=refresh)
    except OpenF1AccessError as error:
        archive_message = ('The free archive has not published this session yet.' if reason == 'not_published'
                           else 'The free session archive is temporarily unavailable.')
        raise HostedDataUnavailable(f'{archive_message} {error}') from None
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise HostedDataUnavailable('Neither source has usable recorded data for this session right now. '
                                    'Try Refresh now after the session archive is published.') from None
    result['metadata'] = {**result['metadata'], 'fallback_from': 'TracingInsights',
                          'fallback_reason': reason}
    return result


def load_hosted_analysis(year, round_number, session_code, *, refresh=False):
    from archive_analysis import load_archive_analysis
    from openf1_data import load_openf1_analysis
    return _load(load_archive_analysis, load_openf1_analysis, year, round_number, session_code, refresh)


def load_hosted_replay(year, round_number, session_code, *, refresh=False):
    from archive_replay import load_archive_replay
    from web_replay import load_browser_replay
    return _load(load_archive_replay, load_browser_replay, year, round_number, session_code, refresh)


def published_classification(analysis, season):
    metadata = analysis['metadata']
    results = analysis['results']
    if not metadata.get('archived') or metadata.get('classification_available', True) or season is None:
        return results, False
    code = metadata['session_code']
    if code in ('R', 'S'):
        official = season['results']
        kind = 'Grand Prix' if code == 'R' else 'Sprint'
        official = official[official['round'].eq(metadata['round']) & official['kind'].eq(kind)]
        fields = ('position', 'points', 'grid', 'status')
    elif code == 'Q':
        official = season['qualifying']
        official = official[official['round'].eq(metadata['round'])]
        fields = ('position',)
    else:
        return results, False
    if official.empty or official.code.duplicated().any():
        return results, False
    official = official.set_index('code')
    joined = results.copy()
    matched = joined.code.isin(official.index)
    for field in fields:
        joined.loc[matched, field] = joined.loc[matched, 'code'].map(official[field])
    return joined.sort_values('position', na_position='last'), bool(matched.any())
