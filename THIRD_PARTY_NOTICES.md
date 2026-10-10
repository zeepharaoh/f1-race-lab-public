# Third-party notices

The root [MIT License](LICENSE) applies to this repository's original code and documentation. It does not replace the licences, copyright notices or terms of third-party software, data or assets.

## Replay engine

Animated replays use [f1-race-replay](https://github.com/IAmTomShaw/f1-race-replay) by Tom Shaw and contributors. Fresh installations download revision `efdb8a31900d64e3f269d66601ab9726b7dc5923`; setup can also use an existing sibling checkout.

The [README at the pinned revision](https://github.com/IAmTomShaw/f1-race-replay/blob/efdb8a31900d64e3f269d66601ab9726b7dc5923/README.md#-license) declares:

> This project is licensed under the MIT License.

That revision has no separate licence file. This notice records the upstream declaration and attribution; it does not assign ownership of upstream work to this project's author. Keep the upstream README and any supplied notices with the engine when copying or redistributing it.

The engine is installed separately. Its source, assets and Python environment are excluded from this repository. Assets copied locally for playback retain their owners' rights.

## Python dependencies

FastF1, Streamlit, Plotly, pandas, Matplotlib, Requests and the replay engine's dependencies retain their own licences. Their installed distributions supply the applicable licence texts and copyright notices. Keep those notices when redistributing dependencies.

## Data and trademarks

Championship results and standings are obtained through the [Jolpica F1 API](https://github.com/jolpica/jolpica-f1). Session timing and telemetry are obtained through [FastF1](https://docs.fastf1.dev/). These software sources do not transfer ownership of the underlying Formula 1 data.

Formula 1, team and driver names, related trademarks, circuit information and third-party assets belong to their respective owners. They are not covered by this repository's MIT licence. See the [README disclaimer](README.md#disclaimer) and the [Formula 1 guidelines](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt).
