# F1 Race Lab

A Python dashboard for the full 2026 Formula 1 season and each race weekend, with championship analysis, practice and qualifying timing, Sprint and Grand Prix results, and animated replays. Explore the full grid, compare the current top three constructors, or select individual teams and drivers.

## Installation

Requirements: Python 3.12 or later, Git, and internet access. The installation script and desktop launcher support macOS. Animated replays require a desktop graphics session.

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

## Race-weekend workflow

1. Check the weekend schedule to see which sessions are planned.
2. After a session finishes, click **Refresh now** to check for newly published timing and results.
3. Open **Weekend sessions**, select a **Weekend** and **Session**, then click **Load session data**.
4. Use **Lap analysis** to compare drivers and tyres in that session. Use **Grand Prix results**, **Season overview** and **Teammates** for race and championship analysis.
5. Open **Animated replay**, select the weekend and session, then choose the drivers and playback speed.

The session selector follows the selected weekend's schedule. A regular weekend includes FP1, FP2, FP3, qualifying and the Grand Prix. A Sprint weekend includes FP1, Sprint qualifying, the Sprint, qualifying and the Grand Prix. Use the displayed schedule for session dates and times.

The dashboard checks the schedule, championship data and loaded sessions every **15 minutes while it is open**. **Refresh now** checks the schedule and loaded session data against the source again. Session analysis is available after final timing is published; publication can take time after a session finishes. The app does not provide a live timing feed or make background checks while closed.

If a championship refresh fails, the dashboard can display its last complete saved season snapshot. Loaded session timing remains visible if its refresh fails. Warnings and the original check or load times identify stale data. Weekend timing can still be loaded while championship results are unavailable. Fresh results and uncached telemetry require an internet connection.

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

**Comparison scope** filters the analysis by **All teams**, **Top three constructors** or **Choose teams**. Replay driver selection is independent of this filter.

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

In **Lap analysis**, select the weekend and session, click **Load lap analysis**, then choose **Drivers on lap charts** and **Tyre compounds**. Each median includes a count of the laps used. You can analyse practice, qualifying, Sprint qualifying, Sprint and Grand Prix laps when their timing is available.

Compare similar tyres and consider tyre age, fuel, traffic and weather. Median lap time describes the selected laps; it does not isolate car performance.

### Animated replay

In **Animated replay**, select a weekend and session, then choose **All drivers**, **Top three constructors** or **Choose drivers**. For **Choose drivers**, click **Load session drivers** first, then choose from **Replay drivers**. The roster includes the session's actual participants, including practice substitutes. Click **Open animated replay** when ready.

**Sprint and Grand Prix replays** show cars moving around the track, a lap counter, race time, weather and a leaderboard. Select **All drivers** to show the full field. Set their initial **Playback speed** in the dashboard or with `--speed`.

Click a driver on the replay's leaderboard to show a telemetry card with speed, gear, DRS status, throttle, braking and estimated gaps ahead and behind. Hold **Shift** and click other drivers to compare their cards. Shift-click a selected driver to remove their card; a regular click switches to one driver. Cards start hidden until a driver is selected. Enlarge the replay window to fit more cards.

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
| **R** | Restart |
| **L** | Toggle driver labels |
| **H** | Show keyboard help |
| **Escape** | Close the replay window |

Launch from the desktop launcher or a desktop terminal to use graphical replays. A headless server can display the dashboard but cannot open a replay window.

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
| Lap chart is empty | Click **Load lap analysis** and select drivers and tyres with available clean laps. |

Dashboard-launched replay logs are saved to `outputs/replay.log`.

## Project structure

| File or folder | Purpose |
| --- | --- |
| `Open F1 Race Lab.command` | macOS desktop launcher |
| `launch.py` | Dashboard server and browser launcher |
| `dashboard.py` | Dashboard views and controls |
| `data.py` | Downloads, snapshots, calculations and lap filtering |
| `weekend.py` | Weekend schedules, session timing and classifications |
| `main.py` | Animated replay launcher |
| `replay_insights.py` | Replay lap charts and telemetry tools |
| `season_analysis.py` | Static season charts |
| `setup.sh` | Environment and dependency installation |
| `requirements.txt` | Dashboard dependencies |
| `requirements-dev.txt` | Development dependencies |
| `cache/` and `computed_data/` | Downloaded and processed data |
| `outputs/` | Charts, tables and replay logs |
| `tests/` | Calculation and filtering checks |

Environments, caches, logs and generated outputs are excluded from version control.

## Data sources and interpretation

Season results and standings come from the [Jolpica F1 API](https://api.jolpi.ca/ergast/f1/2026/). Weekend schedules, session timing, available classifications and position data come through [FastF1](https://docs.fastf1.dev/). Session classifications may be derived from timing data and can differ from subsequently amended official results.

The replay uses [IAmTomShaw/f1-race-replay](https://github.com/IAmTomShaw/f1-race-replay), pinned for fresh installations at commit `efdb8a31900d64e3f269d66601ab9726b7dc5923`. Its README declares an MIT licence. Engine source and assets are downloaded during setup and excluded from this repository.

- Championship points include Grands Prix and Sprints. Driver points include every team represented during the season.
- Grid-to-finish gain is starting grid position minus classified finishing position. Pit-lane starts have no gain value.
- Classified finishing positions can include retirements and do not measure pace directly.
- Clean laps require a recorded time, accurate and undeleted status, green track conditions throughout, and no pit entry or exit.
- Lap times are not adjusted for fuel, tyre age, traffic, weather or strategy.
- Replay car positions are interpolated. Leaderboard order is approximate, especially on lap 1, and Safety Car position is simulated.
- Telemetry-card gaps are estimates from car positions and a fixed reference speed. Detected DRS zones come from one example lap and may be absent when its telemetry has no active DRS samples.
- Practice and qualifying replays compare fastest laps. Drivers' laps may have been recorded at different times and in different conditions.
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
