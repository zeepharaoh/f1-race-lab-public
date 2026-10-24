"""F1 dashboard, session analysis and replay controls."""
from pathlib import Path
import subprocess
import sys
import pandas as pd
import plotly.express as px
import streamlit as st
from data import ROOT, COLORS, load_season, cumulative_points
from weekend import load_weekend_schedule, eligible_sessions, load_session_analysis

HOSTED = bool(globals().get('HOSTED', False))

st.set_page_config(page_title='F1 Race Lab', page_icon='🏁', layout='wide')
st.markdown('''<style>
.block-container {padding-top:2rem; max-width:1550px;}
h1 {letter-spacing:-1.5px;} [data-testid="stMetric"] {background:#151c29;padding:18px;border-radius:12px;border:1px solid #273145;}
[data-testid="stSidebar"] {border-right:1px solid #273145;}
</style>''', unsafe_allow_html=True)

@st.cache_data(ttl=900, show_spinner='Checking published 2026 results…')
def season_data():
    return load_season(2026, refresh=True)

@st.cache_data(ttl=900, show_spinner='Checking the weekend schedule…')
def weekend_schedule():
    return load_weekend_schedule(2026, refresh=True)


@st.cache_data(ttl=900, max_entries=12, show_spinner='Loading session timing…')
def session_analysis(round_number, session_code):
    if HOSTED:
        from openf1_data import load_openf1_analysis
        return load_openf1_analysis(2026, round_number, session_code, refresh=True)
    return load_session_analysis(2026, round_number, session_code, refresh=True)


@st.cache_data(ttl=3600, max_entries=3, show_spinner='Preparing recorded positions and telemetry. First loads can take a few minutes…')
def browser_replay(round_number, session_code, refresh=False):
    from web_replay import load_browser_replay
    return load_browser_replay(2026, round_number, session_code, refresh=refresh)


@st.cache_resource
def hosted_refresh_gate():
    from hosting import RefreshGate
    return RefreshGate(interval=60)


def chart(fig, key):
    fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(0,0,0,0)', font=dict(size=12),
                      margin=dict(l=5, r=5, t=35, b=5), legend_title_text='',
                      legend=dict(orientation='h', y=-0.25, x=0))
    if key in ('constructor-points','driver-points','race-points','race-gains','session-points','session-practice-gap','session-practice-laps'):
        fig.update_layout(showlegend=False)
    fig.update_xaxes(title_text={'round':'Race round','code':'Driver','points':'Points','positions_gained':'Positions gained'}.get(fig.layout.xaxis.title.text, fig.layout.xaxis.title.text))
    fig.update_yaxes(title_text={'team':'Constructor','driver':'Driver','code':'Driver','points':'Points'}.get(fig.layout.yaxis.title.text, fig.layout.yaxis.title.text))
    st.plotly_chart(fig, width='stretch', key=key)


def export(frame, name):
    st.download_button('Download this data', frame.to_csv(index=False), f'{name}.csv', 'text/csv', key=f'download-{name}')


def choose_session(schedule, key):
    """Select a scheduled weekend session."""
    weekends = schedule[['round', 'race']].drop_duplicates().sort_values('round')
    labels = dict(zip(weekends['round'], weekends['race']))
    rounds = weekends['round'].tolist()
    started = eligible_sessions(schedule)
    latest_round = int(started.iloc[-1]['round']) if not started.empty else rounds[0]
    selected_round = st.selectbox('Weekend', rounds, index=rounds.index(latest_round),
        format_func=lambda value: f'{value} · {labels[value]}', key=f'{key}-round')
    sessions = schedule[schedule['round'].eq(selected_round)].sort_values('session_utc')
    codes = sessions['code'].tolist()
    past = eligible_sessions(sessions)
    default_code = past.iloc[-1]['code'] if not past.empty else codes[0]
    names = dict(zip(sessions['code'], sessions['session']))
    code = st.selectbox('Session', codes, index=codes.index(default_code),
        format_func=lambda value: names[value], key=f'{key}-session')
    row = sessions[sessions['code'].eq(code)].iloc[0]
    has_started = not eligible_sessions(sessions[sessions['code'].eq(code)]).empty
    start_label = row['session_utc'].strftime('%a %d %b %Y, %H:%M UTC') if pd.notna(row['session_utc']) else 'Start time not published'
    st.caption(f'{row["session"]} · {start_label} · {row["location"]}')
    if not has_started:
        st.info('This session is scheduled. Data loading opens after its scheduled start; published timing is available after the session finishes.')
    return int(selected_round), code, has_started


def source_access_notice(metadata):
    if metadata.get('access_limited') and metadata.get('cached_fallback'):
        message = metadata.get('access_message') or 'OpenF1 access is temporarily unavailable.'
        st.warning(f'{message} Showing saved data for this session.')


def source_caption(metadata, default='FastF1 session data'):
    source_access_notice(metadata)
    retrieved = metadata.get('source_retrieved_utc') or metadata.get('retrieved_utc')
    if retrieved:
        stamp = retrieved[:16].replace('T', ' ')
        st.caption(f'{metadata.get("source", default)} · source retrieved {stamp} UTC')


def session_results(analysis, teams, scope, season=None):
    results = analysis['results']
    metadata = analysis['metadata']
    official_points = False
    if season is not None and metadata['session_code'] in ('R', 'S') and results.points.isna().any():
        kind = 'Grand Prix' if metadata['session_code'] == 'R' else 'Sprint'
        official = season['results']
        official = official[official['round'].eq(metadata['round']) & official['kind'].eq(kind)]
        if not official.empty and not official.code.duplicated().any():
            results = results.copy()
            results['points'] = results.points.fillna(results.code.map(official.set_index('code').points))
            results['grid'] = results.grid.fillna(results.code.map(official.set_index('code').grid))
            official_points = results.points.notna().any()
    view = results if scope == 'All teams' else results[results.team.isin(teams)]
    code = metadata['session_code']
    if view.empty:
        st.info('No session drivers match the comparison scope.')
        return
    source_caption(metadata)
    if metadata.get('quality_note'):
        st.caption(metadata['quality_note'])
    if official_points:
        st.caption('Points and missing grid positions are matched to published Jolpica race results. OpenF1 supplies the session classification and lap timing.')
    if not metadata.get('deleted_laps_checked', True):
        st.warning('Some lap deletion statuses are unavailable. Unconfirmed laps are excluded from fastest-lap and pace comparisons.')
    if code in ('R', 'S'):
        st.caption('Published session classification. Timing results may differ from subsequently amended official standings.')
        points = view.dropna(subset=['points'])
        if not points.empty:
            chart(px.bar(points.sort_values('position'), x='code', y='points', color='team',
                color_discrete_map=COLORS, title='Session points'), 'session-points')
        columns = ['position', 'driver', 'team', 'grid', 'status', 'points', 'lap_count']
    elif code in ('Q', 'SQ'):
        segments = ['Q1_seconds', 'Q2_seconds', 'Q3_seconds']
        times = view.melt(id_vars=['code', 'team'], value_vars=segments,
            var_name='segment', value_name='lap_seconds').dropna(subset=['lap_seconds'])
        times['segment'] = times.segment.str.replace('_seconds', '', regex=False)
        if code == 'SQ':
            times['segment'] = 'S' + times.segment
        if not times.empty:
            chart(px.bar(times, x='code', y='lap_seconds', color='segment', barmode='group',
                labels={'lap_seconds': 'Segment best lap (s)'}, title='Qualifying segment times'), 'session-qualifying')
        st.caption('Classification follows qualifying stages. The fastest lap across all stages does not determine the final order. Missing times indicate no recorded time in that stage.')
        columns = ['position', 'driver', 'team', *segments, 'best_lap_seconds', 'lap_count']
    else:
        c1, c2 = st.columns(2)
        timed = view.dropna(subset=['best_lap_seconds']).sort_values('best_lap_seconds')
        with c1:
            if not timed.empty:
                chart(px.bar(timed, x='code', y='gap_seconds', color='team', color_discrete_map=COLORS,
                    labels={'gap_seconds': 'Gap to session best lap (s)'}, hover_data={'gap_seconds': ':.3f', 'best_lap_seconds': ':.3f'}, title='Practice best laps'), 'session-practice-gap')
        with c2:
            chart(px.bar(view.sort_values('lap_count', ascending=False), x='code', y='lap_count', color='team',
                color_discrete_map=COLORS, labels={'lap_count': 'Timed laps'}, title='Practice running'), 'session-practice-laps')
        st.caption('Practice order is derived from valid best lap times. Fuel loads, tyre choice and run plans differ between drivers.')
        columns = ['position', 'driver', 'team', 'best_lap_seconds', 'gap_seconds', 'lap_count', 'timed_lap_count']
    stage_prefix = 'SQ' if code == 'SQ' else 'Q'
    column_config = {
        'position': st.column_config.NumberColumn('Position', format='%d'),
        'driver': 'Driver', 'team': 'Constructor', 'status': 'Status',
        'grid': st.column_config.NumberColumn('Grid', format='%d'),
        'points': st.column_config.NumberColumn('Points', format='%g'),
        'best_lap_seconds': st.column_config.NumberColumn('Best lap (s)', format='%.3f'),
        'gap_seconds': st.column_config.NumberColumn('Gap (s)', format='%.3f'),
        'lap_count': st.column_config.NumberColumn('Laps' if code in ('R', 'S') else 'Timed laps', format='%d'),
        'timed_lap_count': st.column_config.NumberColumn('Valid timed laps', format='%d'),
        **{f'Q{stage}_seconds': st.column_config.NumberColumn(f'{stage_prefix}{stage} (s)', format='%.3f') for stage in range(1, 4)},
    }
    st.dataframe(view[[col for col in columns if col in view]], column_config=column_config, hide_index=True, width='stretch')
    export(view, f'round_{metadata["round"]}_{code}_session')


@st.fragment(run_every='15m')
def app():
    st.title('F1 Race Lab')
    try:
        season = season_data()
    except Exception as error:
        season = None
        st.warning(f'Championship results are unavailable: {error}. Weekend session timing can still be loaded.')
    if season and season.get('refresh_error'):
        st.warning('Refresh unavailable. Showing the last complete saved snapshot; its check time is shown below. Try Refresh now when your connection returns.')
    if season is not None:
        gp, results, qualifying = season['gp'], season['results'], season['qualifying']
    else:
        gp = pd.DataFrame(columns=['round', 'race', 'team', 'driver_id', 'position', 'points'])
        results, qualifying = gp.copy(), gp.copy()
    schedule_error = None
    try:
        schedule = weekend_schedule()
    except Exception as error:
        schedule = pd.DataFrame()
        schedule_error = str(error)
    top_teams = season['teams'].head(3).team.tolist() if season is not None else []
    with st.sidebar:
        st.subheader('2026 season')
        st.caption('Checks for published results every 15 minutes while open. Reopening also checks freshness.')
        if st.button('Refresh now', width='stretch'):
            wait = hosted_refresh_gate().request() if HOSTED else 0
            if wait:
                st.info(f'A refresh was requested recently. Try again in {wait} seconds.')
            else:
                season_data.clear()
                weekend_schedule.clear()
                session_analysis.clear()
                browser_replay.clear()
                st.session_state.pop('browser_replay', None)
                st.session_state['replay_refresh_requested'] = True
                st.session_state.pop('replay_drivers', None)
                st.rerun()
        scope_options = ['All teams', 'Top three constructors', 'Choose teams'] if season is not None else ['All teams', 'Choose teams']
        scope = st.radio('Comparison scope', scope_options)
        teams = sorted(results.team.unique()) if season is not None else sorted(COLORS)
        if scope == 'Top three constructors':
            teams = top_teams
        elif scope == 'Choose teams':
            teams = st.multiselect('Teams', teams, default=top_teams)
        if season is not None:
            st.caption(f'Published through round {season["round"]}. Grand Prix + Sprint points.')
            future = season['calendar'][season['calendar']['round'].gt(season['round'])]
            if not future.empty:
                next_race = future.iloc[0]
                st.markdown(f'**Next weekend** · {next_race.race}')
                st.caption(f'{next_race.date} · {next_race.circuit}')
            else:
                st.success('All scheduled season rounds have results.')
        if not schedule.empty:
            upcoming = schedule[schedule.session_utc.gt(pd.Timestamp.now(tz='UTC'))].sort_values('session_utc')
            if not upcoming.empty:
                next_session = upcoming.iloc[0]
                st.markdown(f'**Next session** · {next_session["session"]}')
                st.caption(f'{next_session["race"]} · {next_session["session_utc"].strftime("%a %d %b, %H:%M UTC")}')
        with st.expander('2026 calendar'):
            if season is not None:
                st.dataframe(season['calendar'], hide_index=True, width='stretch')
            elif not schedule.empty:
                st.dataframe(schedule[['round', 'race', 'location']].drop_duplicates(), hide_index=True, width='stretch')
        st.divider()
        st.markdown('[Official standings](https://www.formula1.com/en/results/2026/team)')
        st.markdown('[Results API](https://api.jolpi.ca/ergast/f1/2026/)')
    selected = results[results.team.isin(teams)]
    selected_gp = gp[gp.team.isin(teams)]
    if season is not None and selected.empty:
        st.info('Choose at least one team.')
        return
    if season is not None:
        source_time = season['sources']['results']['retrieved_utc']
        st.caption(f'2026 • {gp["round"].nunique()} completed Grands Prix • checked {source_time[:16].replace("T", " ")} UTC')
        checks = season['reconciliation']
        if checks.difference.abs().gt(0.001).any() or season['driver_checks'].difference.abs().gt(0.001).any():
            st.warning('Some points do not reconcile with official standings. Inspect Sources before drawing conclusions.')
        latest_race = gp[gp['round'].eq(season['round'])]
        winner = latest_race.loc[latest_race.position.eq(1)].iloc[0]
        st.markdown(f'### Latest result · {winner.race}')
        st.caption(f'{winner.date} · Winner: {winner.driver} · {winner.team}')
        cards = st.columns(4)
        cards[0].metric('Constructors in view', len(teams))
        cards[1].metric('Drivers with results', selected.driver_id.nunique())
        cards[2].metric('Championship points', f'{selected.points.sum():,.0f}')
        cards[3].metric('Grand Prix podiums', int(selected_gp.position.le(3).sum()))
    sessions_tab, races, overview, teammates, pace, replay, sources = st.tabs(
        ['Weekend sessions', 'Grand Prix results', 'Season overview', 'Teammates', 'Lap analysis', 'Animated replay', 'Sources'],
        default='Season overview' if HOSTED else None)
    with sessions_tab:
        st.subheader('Practice, qualifying and race sessions')
        if schedule.empty:
            st.warning(f'The weekend schedule could not be loaded: {schedule_error}')
        else:
            session_round, session_code, started = choose_session(schedule, 'weekend')
            if st.button('Load session data', disabled=not started, type='primary'):
                try:
                    analysis = session_analysis(session_round, session_code)
                    st.session_state['loaded_session'] = ((session_round, session_code), analysis)
                except Exception as error:
                    st.warning(f'Session data is unavailable: {error}')
            loaded_session = st.session_state.get('loaded_session')
            if loaded_session and loaded_session[0] == (session_round, session_code):
                try:
                    current = session_analysis(session_round, session_code)
                    loaded_session = ((session_round, session_code), current)
                    st.session_state['loaded_session'] = loaded_session
                except Exception:
                    st.warning('Session refresh is unavailable. Showing the last loaded timing and its original load time.')
                session_results(loaded_session[1], teams, scope, season)
            else:
                st.caption('Load the session to inspect driver times, classifications and laps. First downloads can take several minutes.')
            with st.expander('Weekend timetable'):
                timetable = schedule[schedule['round'].eq(session_round)][['session', 'session_utc']].copy()
                timetable['session_utc'] = timetable.session_utc.dt.strftime('%a %d %b, %H:%M UTC')
                st.dataframe(timetable.rename(columns={'session': 'Session', 'session_utc': 'Scheduled start'}), hide_index=True, width='stretch')
    with overview:
        if season is None:
            st.info('Championship results are unavailable. Use Weekend sessions for session timing.')
        else:
            c1, c2 = st.columns([1, 1])
            constructors = season['teams'][season['teams'].team.isin(teams)]
            with c1:
                st.subheader('Constructor championship')
                chart(px.bar(constructors, x='team', y='points', color='team', color_discrete_map=COLORS), 'constructor-points')
            with c2:
                st.subheader('How the championship evolved')
                cumulative = cumulative_points(results, 'team')
                chart(px.line(cumulative[cumulative.team.isin(teams)], x='round', y='points', color='team', markers=True, color_discrete_map=COLORS), 'cumulative-points')
            st.subheader('Driver championship')
            ids = selected.driver_id.unique()
            drivers = season['drivers'][season['drivers'].driver_id.isin(ids)]
            chart(px.bar(drivers.sort_values('points'), x='points', y='driver', color='team', orientation='h', color_discrete_map=COLORS, height=max(420, len(drivers)*24)), 'driver-points')
            st.caption('Driver championship points include all their teams. Constructor totals are points earned for that team.')
            st.dataframe(drivers[['rank', 'driver', 'team', 'points', 'wins']], hide_index=True, width='stretch')
            export(drivers, 'driver_standings')
    races_list = gp[['round', 'race']].drop_duplicates().sort_values('round')
    race_names = dict(zip(races_list['round'], races_list.race))
    with races:
        if season is None:
            st.info('Championship results are unavailable. Use Weekend sessions for session timing.')
        else:
            st.subheader('Race-by-race performance')
            round_number = st.selectbox('Grand Prix', races_list['round'].tolist(), index=len(races_list)-1, format_func=lambda n: f'{n} · {race_names[n]}', key='results-round')
            race = selected_gp[selected_gp['round'].eq(round_number)].copy()
            race['positions_gained'] = race.grid - race.position
            race.loc[race.grid.eq(0), 'positions_gained'] = float('nan')
            c1, c2 = st.columns(2)
            with c1:
                chart(px.bar(race.sort_values('position'), x='code', y='points', color='team', color_discrete_map=COLORS, title='Grand Prix points'), 'race-points')
            with c2:
                chart(px.bar(race.sort_values('positions_gained'), x='positions_gained', y='code', color='team', orientation='h', color_discrete_map=COLORS, title='Grid to classified finish', height=600), 'race-gains')
            st.caption('Grid − finishing position. Pit-lane starts have no gain value. Classified finish includes retirements.')
            q = qualifying[qualifying['round'].eq(round_number)][['driver_id', 'position', 'Q1', 'Q2', 'Q3']].rename(columns={'position': 'qualifying_position'})
            detail = race.drop(columns=['Q1','Q2','Q3']).merge(q, on='driver_id', how='left')
            st.dataframe(detail[['driver','team','grid','position','status','points','qualifying_position','Q1','Q2','Q3']], hide_index=True, width='stretch')
            export(detail, f'round_{round_number}_results')
    with teammates:
        if season is None:
            st.info('Championship results are unavailable. Use Weekend sessions for session timing.')
        else:
            st.subheader('Driver contribution within each team')
            contributions = selected.groupby(['team', 'driver'], as_index=False).points.sum()
            contributions['share_pct'] = contributions.points / contributions.groupby('team').points.transform('sum').replace(0, float('nan')) * 100
            chart(px.bar(contributions, x='team', y='points', color='driver', title='Points earned for each constructor'), 'contribution')
            st.dataframe(contributions.rename(columns={'share_pct':'Share of team points (%)'}), hide_index=True, width='stretch')
            team = st.selectbox('Inspect teammates', teams, key='teammate-team')
            tgp = gp[gp.team.eq(team)]
            comparisons = []
            for r, rows in tgp.groupby('round'):
                if len(rows) != 2:
                    continue
                rows = rows.sort_values('driver')
                first, second = rows.iloc[0], rows.iloc[1]
                comparisons.append({'race': first.race, 'driver_a': first.driver, 'finish_a': first.position,
                                    'driver_b': second.driver, 'finish_b': second.position,
                                    'higher_classified_finish': first.driver if first.position < second.position else second.driver})
            head_to_head = pd.DataFrame(comparisons)
            st.caption('Higher classified finish is a results comparison. Reliability, penalties and strategy can affect it.')
            if not head_to_head.empty:
                st.dataframe(head_to_head, hide_index=True, width='stretch')
                export(head_to_head, 'teammate_comparison')
    with pace:
        st.subheader('Lap timing and tyre stints')
        st.caption('Clean timed laps excluding pit laps, reported deletions and caution periods where source flags are available. Medians reflect fuel, tyres and traffic; they are not adjusted car performance.')
        if schedule.empty:
            st.warning('The weekend schedule is unavailable. Use Refresh now to retry.')
        else:
            lap_round, lap_code, lap_started = choose_session(schedule, 'pace')
            if st.button('Load lap analysis', disabled=not lap_started):
                try:
                    analysis = session_analysis(lap_round, lap_code)
                    st.session_state['lap_analysis'] = ((lap_round, lap_code), analysis)
                except Exception as error:
                    st.warning(f'Lap timing could not be loaded: {error}')
            loaded = st.session_state.get('lap_analysis')
            if loaded and loaded[0] == (lap_round, lap_code):
                try:
                    current = session_analysis(lap_round, lap_code)
                    loaded = ((lap_round, lap_code), current)
                    st.session_state['lap_analysis'] = loaded
                except Exception:
                    st.warning('Lap refresh is unavailable. Showing the last loaded timing.')
                metadata = loaded[1]['metadata']
                source_caption(metadata, 'FastF1 timing')
                if metadata.get('quality_note'):
                    st.caption(metadata['quality_note'])
                laps = loaded[1]['clean_laps']
                if scope != 'All teams':
                    laps = laps[laps.Team.isin(teams)]
                if laps.empty:
                    st.info('No clean laps are available for this session and comparison scope.')
                else:
                    available = sorted(laps.Driver.dropna().unique())
                    codes = st.multiselect('Drivers on lap charts', available, default=available[:4])
                    tyre_options = sorted(laps.Compound.dropna().unique())
                    compounds = st.multiselect('Tyre compounds', tyre_options, default=tyre_options)
                    view = laps[laps.Driver.isin(codes) & laps.Compound.isin(compounds)]
                    if not view.empty:
                        chart(px.line(view.sort_values('LapNumber'), x='LapNumber', y='lap_seconds', color='Driver', markers=True,
                            labels={'lap_seconds': 'Lap time (s)'}, hover_data=['Compound', 'TyreLife', 'Stint']), 'laps')
                        summary = view.groupby(['Driver', 'Compound'], as_index=False).agg(
                            median_lap_seconds=('lap_seconds', 'median'), included_laps=('lap_seconds', 'size'))
                        chart(px.bar(summary, x='Driver', y='median_lap_seconds', color='Compound', barmode='group',
                            hover_data=['included_laps'], labels={'median_lap_seconds': 'Median lap time (s)'}), 'median-pace')
                        st.dataframe(summary, hide_index=True, width='stretch')
                        export(view, f'round_{lap_round}_{lap_code}_clean_laps')
                    else:
                        st.info('No laps match the selected drivers and tyres.')
    with replay:
        st.subheader('Animated session replay')
        if HOSTED:
            if schedule.empty:
                st.warning('The weekend schedule is unavailable. Use Refresh now to retry.')
            else:
                replay_round, replay_code, replay_started = choose_session(schedule, 'browser-replay')
                st.caption('Watch the recorded session in your browser. The first load downloads car positions and telemetry; later viewers reuse the prepared data.')
                replay_key = (replay_round, replay_code)
                if st.button('Load browser replay', disabled=not replay_started, type='primary'):
                    try:
                        refresh_replay = st.session_state.pop('replay_refresh_requested', False)
                        payload = browser_replay(*replay_key, refresh=refresh_replay)
                        st.session_state['browser_replay'] = (replay_key, payload)
                    except Exception as error:
                        st.warning(f'Replay could not be loaded: {error}')
                loaded_replay = st.session_state.get('browser_replay')
                if loaded_replay and loaded_replay[0] == replay_key:
                    from replay_player import replay_html
                    payload = loaded_replay[1]
                    shown_teams = None if scope == 'All teams' else teams
                    visible_drivers = [driver for driver in payload['drivers']
                                       if shown_teams is None or driver['team'] in shown_teams]
                    if visible_drivers:
                        source_caption(payload['metadata'], 'OpenF1')
                        st.iframe(replay_html(payload, shown_teams), height='content', alt='Animated session replay')
                        st.caption('Recorded car positions and telemetry. Playback follows the saved session timeline.')
                        if payload.get('unavailable'):
                            st.caption('Position data is unavailable for: ' + ', '.join(payload['unavailable']))
                    else:
                        st.info('No replay drivers match the comparison scope.')
        elif schedule.empty:
            st.warning('The weekend schedule is unavailable. Use Refresh now to retry.')
        else:
            replay_round, replay_code, replay_started = choose_session(schedule, 'replay')
            if replay_code in ('R', 'S'):
                st.caption('Sprint and Grand Prix replays follow the recorded race timeline. Positions are interpolated, leaderboard order is approximate, and Safety Car position is simulated.')
                st.caption('The track replay opens in a desktop window. Click a leaderboard driver for its telemetry card; hold Shift while clicking to compare multiple drivers.')
                st.caption('The leaderboard shows tyres and tyre age. Press P or click Stops for pit-stop counts and last stop laps. G switches gap mode; C and A show the pre-session driver and constructor championships.')
                st.caption('A lap-time chart opens alongside the replay. Use the Insights menu for sector times, driver telemetry and tyre strategy. These views follow replay playback.')
            else:
                st.caption('Practice and qualifying replays compare individual fastest laps. Select a driver in the replay window; qualifying also offers Q1, Q2 and Q3 segments.')
            replay_options = ['All drivers', 'Top three constructors', 'Choose drivers'] if season is not None else ['All drivers', 'Choose drivers']
            replay_scope = st.radio('Drivers to show', replay_options, horizontal=True)
            picked = []
            if replay_scope == 'Choose drivers':
                if st.button('Load session drivers', disabled=not replay_started):
                    try:
                        analysis = session_analysis(replay_round, replay_code)
                        st.session_state['replay_drivers'] = ((replay_round, replay_code), analysis['results'])
                    except Exception as error:
                        st.warning(f'Drivers could not be loaded: {error}')
                roster = st.session_state.get('replay_drivers')
                if roster and roster[0] == (replay_round, replay_code):
                    options = roster[1].code.dropna().tolist()
                    picked = st.multiselect('Replay drivers', options, default=options)
                else:
                    st.caption('Load session drivers to include the actual participants, including practice substitutes.')
            speed = st.select_slider('Playback speed', [0.5, 1.0, 2.0, 4.0, 8.0, 16.0], value=4.0, disabled=replay_code not in ('R', 'S'))
            if replay_code not in ('R', 'S'):
                st.caption('Fastest-lap playback speed is controlled inside the replay window.')
            command = [sys.executable, str(ROOT / 'main.py'), '--round', str(replay_round),
                '--session', replay_code, '--speed', str(speed)]
            if replay_scope == 'Top three constructors':
                command += ['--top-three']
            elif replay_scope == 'Choose drivers':
                command += ['--drivers', *picked]
            existing = st.session_state.get('replay_process')
            active = existing is not None and existing.poll() is None
            disabled = active or not replay_started or (replay_scope == 'Choose drivers' and not picked)
            if st.button('Open animated replay', disabled=disabled, type='primary'):
                logs = ROOT / 'outputs' / 'replay.log'
                logs.parent.mkdir(exist_ok=True)
                with logs.open('w') as log:
                    st.session_state['replay_process'] = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                st.success('Replay is loading in a separate desktop window. The first download can take a few minutes.')
            if active:
                st.info('A replay is running. Close its window before opening another.')
            if existing is not None and existing.poll() not in (None, 0):
                st.error('Replay exited with an error. Check the replay log. Start the app from a desktop terminal or launcher for graphics access.')
            with st.expander('Replay log'):
                log = ROOT / 'outputs' / 'replay.log'
                if log.exists():
                    st.code(log.read_text()[-6000:])
            st.code(f'python main.py --round {replay_round} --session {replay_code}')
            st.caption('Space: pause • ← / →: seek • ↑ / ↓: playback speed • click a driver: telemetry')
    with sources:
        st.subheader('Sources and validation')
        st.write('Championship results: Jolpica F1 API. Weekend schedule and local session analysis: FastF1. Online session analysis and browser replay: OpenF1 historical data. Refresh checks run every 15 minutes while the app is open. Completed sessions appear after the source publishes them.')
        st.markdown('[FastF1 session data and schedule](https://docs.fastf1.dev/)')
        st.markdown('[OpenF1 timing, positions and telemetry](https://openf1.org/docs/)')
        st.caption('OpenF1 can restrict all unauthenticated requests during a live session, including older sessions. Saved completed session data remains available when present. New downloads can resume after the restriction ends.')
        st.caption('Session timing and classifications may be derived by FastF1. Championship point reconciliation below uses Grand Prix and Sprint results from Jolpica.')
        if season is not None:
            st.dataframe(season['reconciliation'], hide_index=True, width='stretch')
            st.dataframe(season['driver_checks'], hide_index=True, width='stretch')
            st.caption('Differences can reflect penalties, amendments or partial publication. Non-zero gaps remain visible.')
            for name, source in season['sources'].items():
                st.markdown(f'**{name}** · [source]({source["url"]}) · {source["retrieved_utc"]}')
            export(results, 'all_race_and_sprint_results')

app()
st.caption('Unofficial fan project for educational and noncommercial analysis. This application is not affiliated with or endorsed by the Formula 1 companies. Formula 1 and related trademarks belong to their respective owners.')
