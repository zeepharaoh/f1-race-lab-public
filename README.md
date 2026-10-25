# F1 Race Lab

A Python dashboard for the full 2026 Formula 1 season and each race weekend, with championship analysis, practice and qualifying timing, Sprint and Grand Prix results, and animated replays. Explore the full grid, compare the current top three constructors, or select individual teams and drivers.

## Online dashboard

Open [F1 Race Lab online](https://f1racelab.streamlit.app/) in a browser with no installation or account. It opens on **Season overview**.

1. Use **Weekend sessions** to choose a weekend and session, then click **Load session data**.
2. Open **Lap analysis**, select the session and click **Load lap analysis**. Choose drivers and tyre compounds to compare. For qualifying, use **Qualifying part** to select all parts, Q1, Q2 or Q3 when stage labels are available.
3. Open **Animated replay**, select the session and click **Load browser replay**. Playback runs directly in the page. Practice shows best and last valid lap times. Qualifying shows Q1, Q2 and Q3 times; use **Qualifying part** inside the replay to watch one part or return to **All qualifying**. Times follow the replay clock and appear after each lap finishes.
4. Use **Grand Prix results**, **Season overview** and **Teammates** for race and championship comparisons.
5. Click **Refresh now** after a session to check newly published data.

Online timing and replay first use the free [TracingInsights session archive](https://github.com/TracingInsights/2026), published as JSON with no account or API key. Archived sessions remain available while another F1 session is live. Practice, qualifying, Sprint qualifying, Sprint and Grand Prix sessions are supported when the publisher has added them. The publisher typically updates about 30 minutes after a session finishes; this is a target, not a guarantee.

Each load uses one published archive version. **Refresh now** checks for a newer version. Lap analysis retains accuracy and deletion flags, tyres, pit times and sector times. Official classifications are matched from Jolpica where available. Missing classifications and telemetry remain visible as unavailable.

[OpenF1 historical data](https://openf1.org/docs/) is a backup when the archive is missing or unavailable. OpenF1 can restrict all public downloads during a live session, including older races. Saved sessions can still be shown with their original source dates. If neither source has usable data, the app explains this and you can retry after publication. This app plays recorded sessions and does not provide live timing.

Initial replay downloads can take several minutes. Prepared replays are shared between viewers and playback then runs in the browser. Your computer does not need to stay on for colleagues to use the app.

## Host the dashboard

Deploy the public repository with [Streamlit Community Cloud](https://share.streamlit.io/). Sign in and connect the GitHub account that owns the repository, then create an app with these settings:

| Setting | Value |
| --- | --- |
| Repository | `zeepharaoh/f1-race-lab-public` |
| Branch | `main` |
| Main file | `online.py` |
| Python version | `3.12` |
| Visibility | Public |

Choose an available app URL and deploy. Share the resulting address with viewers. Streamlit installs `requirements.txt` automatically. The primary archive needs no credentials or replay engine installation. The hosting account needs repository access; viewers of a public app do not need to install Python or clone the repository.

The hosted server checks for published data every 15 minutes while a visitor has the dashboard open. Manual refresh requests are shared and limited to one per minute. Session caches hold at most 12 analyses and replay caches hold three prepared payloads in memory. Archive downloads use at most four parallel requests. OpenF1 backup requests share a paced queue to respect its request limit. Prepared replays are reused while the selected session files are unchanged, including after manual refresh or an archive update that only adds other sessions. A validated Git tree fingerprint checks the whole selected session, including its telemetry. If that check is unavailable, the app keeps the archive version check. Cached data is temporary, so a server restart can require fresh downloads. A sleeping app can take time to wake. See the [hosting guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy) and [app limits](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app).

### Optional OpenF1 authentication

The app owner can configure a valid OpenF1 access token through the server environment variable `OPENF1_ACCESS_TOKEN`. The server sends it as a Bearer token to the OpenF1 backup. The primary TracingInsights archive does not use it. Keep it in the hosting platform's secret settings or a private environment, never in source files or Git. Viewers do not enter credentials.

Authentication requires access granted by OpenF1. This project does not include a subscription or create one. OpenF1 access tokens expire after one hour; this app does not renew them. An invalid or expired token still produces an access message. See the [OpenF1 authentication guide](https://openf1.org/auth.html). Without a token, historical downloads work when OpenF1 allows public access. See the [OpenF1 access policy](https://openf1.org/).

## Installation

Requirements: Python 3.12 or later, Git, and internet access. The installation script and desktop launcher support macOS. The native replay requires a desktop graphics session; the browser replay does not.

Clone or download the repository, open a terminal in the project folder, then run:

```bash
sh setup.sh
```

Setup creates the Python environments, installs dependencies and prepares the replay engine. It uses `../f1-race-replay` when that sibling project exists; otherwise, it downloads the engine into `.engine/f1-race-replay`.

## Start the application

Double-click **Open F1 Race Lab.command** in the project folder, or run:

```bash
.venv/bin/python launch.py
```

The launcher opens the dashboard at **http://localhost:8501**. Keep its Terminal window running while using the app. If the dashboard is already running, the launcher opens the existing instance.

Press **Control + C** in the Terminal window to stop the server. Closing the browser tab does not stop it. Close any replay window separately.

To run the same browser replay version locally, use `.venv/bin/python -m streamlit run online.py` from the project folder.

## Race-weekend workflow

1. Check the weekend schedule to see which sessions are planned.
2. After a session finishes, click **Refresh now** to check for newly published timing and results.
3. Open **Weekend sessions**, select a **Weekend** and **Session**, then click **Load session data**.
4. Use **Lap analysis** to compare drivers and tyres in that session. Use **Grand Prix results**, **Season overview** and **Teammates** for race and championship analysis.
5. Open **Animated replay**, select the weekend and session, then choose the drivers and playback speed.

The session selector follows the selected weekend's schedule. A regular weekend includes FP1, FP2, FP3, qualifying and the Grand Prix. A Sprint weekend includes FP1, Sprint qualifying, the Sprint, qualifying and the Grand Prix. Use the displayed schedule for session dates and times.

The dashboard checks the schedule, championship data and loaded sessions every **15 minutes while it is open**. **Refresh now** checks the schedule and loaded session data against the source again. Session analysis is available after final timing is published; publication can take time after a session finishes. The app does not provide a live timing feed or make background checks while closed.

Championship endpoint data is cached for 15 minutes and shared between viewers. Requests use a shared paced queue and a local budget of 400 requests per hour, below the published Jolpica allowance. Other applications on a shared hosting address can still cause provider limits. A manual refresh checks each endpoint at most once per minute. When Jolpica limits requests, the app pauses further requests for the provider cooldown and reuses validated saved data with its original source time. Qualifying classifications load separately, so a qualifying failure does not hide championship standings. If championship sources cannot be refreshed or have inconsistent round coverage, the dashboard can display its last complete saved season snapshot. Loaded session timing remains visible if its refresh fails. Warnings and the original check or load times identify stale data. Weekend timing can still be loaded while championship results are unavailable. Fresh results and uncached telemetry require an internet connection.

## Dashboard

| Tab | Analysis |
| --- | --- |
| **Weekend sessions** | Practice timing and workload, qualifying segment times, Sprint and Grand Prix classifications |
| **Grand Prix results** | Grand Prix points, grid-to-finish changes, finishing status and qualifying times |
| **Season overview** | Constructor points, championship progress and driver standings |
| **Teammates** | Contributions to team points and classified finishes by race |
| **Lap analysis** | Session lap-time charts and medians by driver and tyre compound |
| **Animated replay** | Weekend and session selection, drivers and playback controls |
| **Sources** | Source links, retrieval times and championship point reconciliation |

**Comparison scope** filters the analysis by **All teams**, **Top three constructors** or **Choose teams**. It also selects which cars appear on the browser replay map. The timing tower retains the full field so gaps keep their original meaning. Native desktop replay driver selection is independent of this filter.

**Download this data** exports the corresponding table as CSV. Charts also support PNG downloads.

### Weekend sessions

Select **Weekend** and **Session** in **Weekend sessions**, then click **Load session data**. Session codes are:

| Code | Session |
| --- | --- |
| `FP1`, `FP2`, `FP3` | First, second and third practice |
| `Q` | Grand Prix qualifying |
| `SQ` | Sprint qualifying |
| `S` | Sprint |
| `R` | Grand Prix |

Practice views show timing and completed-lap workload. Qualifying views show available segment times. Sprint and Grand Prix views show classifications and points. A missing session or unfinished publication is reported rather than replaced with another session's data.

Practice and qualifying results help compare sessions; they do not add championship points. Championship charts count Grand Prix and Sprint points only.

### Lap analysis

In **Lap analysis**, select the weekend and session, click **Load lap analysis**, then choose **Drivers on lap charts** and **Tyre compounds**. For qualifying, **Qualifying part** filters the charts, driver list and CSV to Q1, Q2 or Q3. Choose **All qualifying** to restore all recorded parts. Each median includes a count of the laps used. You can analyse practice, qualifying, Sprint qualifying, Sprint and Grand Prix laps when their timing is available.

Compare similar tyres and consider tyre age, fuel, traffic and weather. Median lap time describes the selected laps; it does not isolate car performance.

### Animated replay

The online dashboard plays recorded sessions directly in the page. Select the weekend and session, then click **Load browser replay**.

1. Click **Play** or **Pause**. Choose **Speed**, drag the timeline, or jump ten seconds forward and back.
2. Click a driver in the timing tower to show their speed, gear, lap, throttle, brake and DRS status. Select up to three drivers to compare telemetry cards.
3. Use **Gap display** to switch between gaps ahead and behind or the gap to the leader. Race gaps apply to Grand Prix and Sprint sessions.
4. Each row shows tyre compound and age in completed laps. Click **Show pit stops** to see recorded pit visits and the lap of the most recent visit. Click **Show tyres** to switch back.
5. Practice timing shows **Best** and **Last valid** lap times. Qualifying shows **Q1**, **Q2** and **Q3** together. Choose **Qualifying part** to replay one part, or **All qualifying** for the full recording.
6. Use **Driver labels** to reduce map clutter. **Restart** resets the current replay range.

Lap times appear only after the recorded lap finishes. The replay timing order is calculated from valid recorded laps, rather than an official classification. Deleted laps and pit laps are excluded. Qualifying ranges use explicitly labelled recorded coverage; missing labels or times remain unavailable. Switching parts reuses the prepared replay and does not download telemetry again.

All session types follow their recorded chronological timeline in the browser, including practice and qualifying. The source and archive publication date appear above the replay. A full race can require more than a thousand small lap files on its first load; requests are limited to four at once and recordings are reduced before playback to keep memory bounded.

With TracingInsights, the timing tower uses the last recorded lap checkpoint position. Practice and qualifying order may be unavailable. Continuous race gaps are unavailable and show **N/A**. The OpenF1 backup can provide more frequent session positions and race intervals when accessible. Source definitions appear inside the player.

Archived coordinates come from FastF1 lap telemetry and already include interpolation. The player fills only short recorded intervals; missing lap recordings leave cars hidden during those spans. It never repeats a different lap to replace one that is missing. Tyres and completed pit visits follow recorded lap, pit entry and pit exit times. A pit visit can include a drive through and does not prove stationary service or a tyre change. Missing telemetry shows **N/A**.

### Desktop replay

In **Animated replay**, select a weekend and session, then choose **All drivers**, **Top three constructors** or **Choose drivers**. For **Choose drivers**, click **Load session drivers** first, then choose from **Replay drivers**. The roster includes the session's actual participants, including practice substitutes. Click **Open animated replay** when ready.

**Sprint and Grand Prix replays** show cars moving around the track, a lap counter, race time, weather and a leaderboard. Select **All drivers** to show the full field. Set their initial **Playback speed** in the dashboard or with `--speed`.

Click a driver on the replay's leaderboard to show a telemetry card with speed, gear, DRS status, throttle, braking and estimated gaps ahead and behind. Hold **Shift** and click other drivers to compare their cards. Shift-click a selected driver to remove their card; a regular click switches to one driver. Cards start hidden until a driver is selected. Enlarge the replay window to fit more cards.

The leaderboard shows gaps **Ahead** and **Behind** each driver. Press **G** to switch to **Leader** gaps, or use the **I / L** buttons above the leaderboard. These gaps use recorded car crossings; unavailable timing is shown as a dash, and lapped cars show a lap deficit. They refer to the full field even when only some drivers are selected for replay.

Every row shows the current **Tyre** compound and its recorded age in completed laps. **SOFT / MED / HARD / INTER / WET** identify the five compounds. Press **P** or click **Stops** to show each driver's completed pit-visit count and last pit-entry lap. Tyres remain visible. Stops count on pit exit; entering the pits shows **PIT** beside the driver. The display follows pause, rewind and restart. Missing timing appears as a dash. Rows compact in shorter windows; enlarge the window for easier reading.

Press **C** for the driver championship or **A** for the constructor championship. These tables show points **before the selected session**, including drivers outside the replay selection. A Grand Prix includes that weekend's published Sprint points. The tables keep their historical cutoff while playback runs; they do not project championship points from race positions. Missing data and saved snapshots are identified in the table. After adding Sprint points, ties keep the previous published order.

A **Lap Time & Gap Evolution** chart and **F1 Insights** menu open alongside race and Sprint replays. Use the menu for **Sector Times**, **Driver Live Telemetry**, **Live Tyre Strategy** and race-control messages. The views follow replay playback, including pause and seek; they are recorded data, not a live race feed. They use the drivers selected for the replay. The Insights menu and its charts close when the replay closes.

**Practice, qualifying and Sprint qualifying replays** compare each selected driver's fastest lap on the track. They do not animate the entire session in chronological order. Click a driver in the replay window to load their lap. Qualifying also offers segment selection when the corresponding data is available. Change fastest-lap playback speed inside the replay window; the dashboard speed selector and `--speed` apply to Sprint and Grand Prix timelines only.

The replay opens in a separate desktop window. Initial telemetry downloads and processing can take several minutes. Subsequent launches use local caches. Close the current replay window before opening another.

Sprint and Grand Prix controls:

| Control | Action |
| --- | --- |
| **Space** | Pause or resume |
| **Hold Left / Right arrows** | Rewind or move forward |
| **Up / Down arrows** | Change playback speed |
| **Click a leaderboard driver** | Show that driver's telemetry card |
| **Shift + click a leaderboard driver** | Add or remove a comparison card |
| **G** | Switch between ahead/behind gaps and gap to the leader |
| **P** or **Stops button** | Show or hide completed pit stops and last stop lap |
| **C** | Toggle the driver championship table |
| **A** | Toggle the constructor championship table |
| **R** | Restart |
| **L** | Toggle driver labels |
| **H** | Show keyboard help |
| **Escape** | Close the replay window |

Launch from the desktop launcher or a desktop terminal to use native graphical replays. The online browser replay works on a headless server.

## Editors

Open the repository folder in either editor:

- **PyCharm:** select `.venv/bin/python` as the project interpreter. Run `launch.py` for the dashboard or `main.py` for the replay.
- **VS Code:** use **Python: Select Interpreter** to select `.venv/bin/python`, then run the same files or use the integrated terminal.

The application runs locally; an editor is optional.

## Command-line usage

Activate the project environment first:

```bash
source .venv/bin/activate
```

```bash
# Open the dashboard and browser.
python launch.py

# Start the dashboard directly.
python -m streamlit run dashboard.py

# Replay the latest completed Grand Prix with all drivers.
python main.py

# Replay round 1 with the current top three constructors.
python main.py --round 1 --session R --top-three

# Replay round 1 with Norris and Piastri at 4x speed.
python main.py --round 1 --session R --drivers NOR PIA --speed 4

# Compare fastest practice laps from round 1.
python main.py --round 1 --session FP2

# Compare qualifying laps from round 1.
python main.py --round 1 --session Q

# Prepare replay data without opening a window.
python main.py --round 1 --session Q --prepare-only

# Generate static season charts.
python season_analysis.py
```

For Sprint weekends, replace `ROUND_NUMBER` below with the round shown in the weekend schedule:

```bash
# Compare Sprint qualifying laps.
python main.py --round ROUND_NUMBER --session SQ

# Replay the Sprint race timeline.
python main.py --round ROUND_NUMBER --session S
```

Replay supports published 2026 sessions. `--session` accepts `FP1`, `FP2`, `FP3`, `Q`, `SQ`, `S` and `R`; its default is `R`. Only sessions scheduled for the chosen weekend can be loaded. **Top three** refers to the latest constructor standings, including when an earlier weekend is selected.

## Troubleshooting

| Issue | Resolution |
| --- | --- |
| Missing Python module | Run `sh setup.sh`, then launch with `.venv/bin/python launch.py`. |
| Browser does not open | Open http://localhost:8501 after the server starts. |
| Port 8501 is in use | Open the existing dashboard, or stop its server with Control + C before restarting. |
| Latest session is missing | Click **Refresh now** and check the weekend schedule. Final timing must be published by the source before analysis is available. |
| `SQ` or `S` is unavailable | Select a Sprint weekend. Regular weekends do not have these sessions. |
| Replay takes several minutes | Allow the initial telemetry processing to finish. Inspect **Replay log** for progress. |
| `Replay environment missing` | Check the engine folder, then run `sh setup.sh`. |
| Replay cannot find a display | Start the app from a desktop terminal or launcher. |
| Driver cards are missing | Click a driver on the replay leaderboard. Hold Shift and click others to compare; enlarge the window for more cards. |
| Direct FastF1 timing returns 403 | Run the browser version locally with `.venv/bin/python -m streamlit run online.py` to use the free published archive. |
| Championship refresh is unavailable | The app retains validated saved standings when available. Qualifying classifications can be unavailable independently. Wait for the provider cooldown, then try **Refresh now**. |
| Session archive is unavailable | New sessions must be published first. Click **Refresh now** later. The app tries the OpenF1 backup and can retain a saved replay. |
| OpenF1 backup returns 401 | The backup is restricted during a live session. The primary archive does not share this restriction. If the archive has not published the selected session, retry after publication. |
| Lap chart is empty | Click **Load lap analysis** and select drivers and tyres with available clean laps. |

Dashboard-launched replay logs are saved to `outputs/replay.log`.

## Project structure

| File or folder | Purpose |
| --- | --- |
| `Open F1 Race Lab.command` | macOS desktop launcher |
| `launch.py` | Dashboard server and browser launcher |
| `dashboard.py` | Dashboard views and controls |
| `online.py` | Hosted dashboard entrypoint |
| `hosting.py` | Shared manual refresh limit |
| `hosted_data.py` | Primary archive selection, OpenF1 backup and official classification matching |
| `archive_source.py` | Public archive version lookup, bounded downloads and metadata cache |
| `archive_analysis.py` | Published lap normalization and clean pace comparisons |
| `archive_replay.py` | Recorded lap telemetry, browser replay and prepared cache |
| `replay_timing.py` | Completed practice laps and labelled qualifying times |
| `openf1_data.py` | Backup session timing, source cache and lap normalization |
| `web_replay.py` | Recorded positions, timing and telemetry for browser playback |
| `replay_player.py`, `replay_player.html`, `replay_player.js` | Browser replay player and controls |
| `jolpica_client.py` | Shared results caching, request pacing and provider cooldown |
| `data.py` | Downloads, snapshots, calculations and lap filtering |
| `weekend.py` | Weekend schedules, session timing and classifications |
| `main.py` | Animated replay launcher |
| `replay_insights.py` | Replay lap charts and telemetry tools |
| `replay_overlays.py` | Leaderboard gaps, tyres and pit-stop controls |
| `replay_strategy.py` | Recorded tyre ages and completed pit visits |
| `championship_data.py` | Championship points before each replay session |
| `championship_overlays.py` | Driver and constructor championship tables |
| `season_analysis.py` | Static season charts |
| `setup.sh` | Environment and dependency installation |
| `requirements.txt` | Dashboard dependencies |
| `requirements-dev.txt` | Development dependencies |
| `cache/` and `computed_data/` | Downloaded and processed data |
| `outputs/` | Charts, tables and replay logs |
| `tests/` | Calculation and filtering checks |

Environments, caches, logs and generated outputs are excluded from version control.

## Data sources and interpretation

Season results and standings come from the [Jolpica F1 API](https://api.jolpi.ca/ergast/f1/2026/). Weekend schedules and local session analysis come through [FastF1](https://docs.fastf1.dev/). Online session analysis and browser replay primarily use the [TracingInsights public archive](https://github.com/TracingInsights/2026), processed from FastF1 and other timing sources. [OpenF1](https://openf1.org/docs/) supplies a backup. Archive versions, publication times and retrieval times are retained. The archive is distributed under Apache 2.0; see its [licence copy](licenses/TracingInsights-APACHE-2.0.txt) and [third party notices](THIRD_PARTY_NOTICES.md). Session classifications can differ from subsequently amended official results.

The replay uses [f1-race-replay by Tom Shaw and contributors](https://github.com/IAmTomShaw/f1-race-replay), pinned for fresh installations at commit `efdb8a31900d64e3f269d66601ab9726b7dc5923`. Its README declares an MIT licence. Engine source and assets are downloaded during setup and excluded from this repository. See [Third-party notices](THIRD_PARTY_NOTICES.md) for attribution and licence scope.

- Championship points include Grands Prix and Sprints. Driver points include every team represented during the season.
- Grid-to-finish gain is starting grid position minus classified finishing position. Pit-lane starts have no gain value.
- Classified finishing positions can include retirements and do not measure pace directly.
- Local FastF1 clean laps require a recorded time, accurate and undeleted status, green track conditions throughout, and no pit entry or exit. OpenF1 has no equivalent accuracy flag; its separate cleaning rules and source coverage are shown with the online analysis.
- Lap times are not adjusted for fuel, tyre age, traffic, weather or strategy.
- Native replay car positions are interpolated. Leaderboard order is approximate, especially on lap 1, and Safety Car position is simulated. Browser replay uses recorded positions, hides missing position spans, and does not simulate a Safety Car. Archive timing order uses lap checkpoints; the OpenF1 backup uses its position feed.
- Native replay telemetry card gaps are estimates from car positions and a fixed reference speed. Detected DRS zones come from one example lap and may be absent when its telemetry has no active DRS samples.
- Native replay leaderboard gaps are approximate recorded crossing times, distinct from the telemetry-card gap estimates. They are replay measurements rather than an official live timing feed.
- Native replay leaderboard tyres use recorded lap and pit exit timing; age means completed laps on that set, including prior use. Stop counts require a recorded pit entry followed by an exit. Initial pit-lane departures and red-flag tyre changes do not add stops. Timing alone cannot distinguish a drive-through from a serviced stop, so counts represent completed pit visits. Stop lap is the pit-entry lap.
- Native practice and qualifying replays compare fastest laps. Drivers' laps may have been recorded at different times and in different conditions.
- Point reconciliation checks accounting totals; it does not validate interpolated race positions.

When circuit rotation metadata is unavailable, the replay uses the recorded track coordinates.

## Development

```bash
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

GitHub Actions runs the Python checks on pushes and pull requests.

## License

This project's original source code is licensed under the [MIT License](LICENSE). Third-party software, data, assets and trademarks retain their respective licences and rights. See [Third-party notices](THIRD_PARTY_NOTICES.md).

## Disclaimer

No copyright infringement intended. This is an unofficial fan project for educational and non-commercial purposes. It is not affiliated with, approved by or endorsed by the Formula 1 companies, the FIA, teams or drivers.

Formula 1 and related trademarks are the property of their respective owners. Data is sourced from publicly accessible APIs through TracingInsights, FastF1, OpenF1 and Jolpica and is used here for educational and noncommercial analysis.

The MIT licence covers this project's code; it does not grant rights to third-party timing data, images, assets or trademarks. Public API access does not remove the rights of the data owners. Refer to the [Formula 1 guidelines](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt) and each source's terms.
