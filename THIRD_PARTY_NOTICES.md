# Third-party notices

The root [MIT License](LICENSE) applies to this repository's original code and documentation. It does not replace the licences, copyright notices or terms of third-party software, data or assets.

## Replay engine

Native desktop replays use [f1-race-replay](https://github.com/IAmTomShaw/f1-race-replay) by Tom Shaw and contributors. Fresh installations download revision `efdb8a31900d64e3f269d66601ab9726b7dc5923`; setup can also use an existing sibling checkout. The browser replay is implemented separately in this repository and does not load the desktop engine or its assets.

The [README at the pinned revision](https://github.com/IAmTomShaw/f1-race-replay/blob/efdb8a31900d64e3f269d66601ab9726b7dc5923/README.md#-license) declares:

> This project is licensed under the MIT License.

That revision has no separate licence file. This notice records the upstream declaration and attribution; it does not assign ownership of upstream work to this project's author. Keep the upstream README and any supplied notices with the engine when copying or redistributing it.

The engine is installed separately. Its source, assets and Python environment are excluded from this repository. Assets copied locally for playback retain their owners' rights.

## Python dependencies

FastF1, Streamlit, Plotly, pandas, Matplotlib, Requests and the replay engine's dependencies retain their own licences. Their installed distributions supply the applicable licence texts and copyright notices. Keep those notices when redistributing dependencies.

## TracingInsights session archive

Hosted timing and browser replay primarily read the public [TracingInsights 2026 archive](https://github.com/TracingInsights/2026), created by TracingInsights from FastF1 and other sources. The provider publishes the archive under the [Apache License 2.0](licenses/TracingInsights-APACHE-2.0.txt). Keep this attribution, the upstream notices and the licence copy with redistributed archive data. This repository does not copy the upstream processing scripts, images or audio.

The application normalizes timing fields, filters clean laps, samples telemetry for browser playback and derives a replay timeline. Those transformations are this project's implementation. The original archive remains attributed to TracingInsights. These notices do not grant rights beyond those held by the data publisher or remove the underlying rights of Formula 1 and other owners.

## Data and trademarks

Championship results and standings are obtained through the [Jolpica F1 API](https://github.com/jolpica/jolpica-f1). Local session timing and telemetry are obtained through [FastF1](https://docs.fastf1.dev/). Hosted session timing and browser replay use the TracingInsights archive first, with backup data through [OpenF1](https://openf1.org/docs/), an independent community project intended for education, research and noncommercial fan use. These software sources do not transfer ownership of the underlying Formula 1 data.

Formula 1, team and driver names, related trademarks, circuit information and third-party assets belong to their respective owners. They are not covered by this repository's MIT licence. See the [README disclaimer](README.md#disclaimer) and the [Formula 1 guidelines](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt).
