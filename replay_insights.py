"""Lap charts and telemetry tools alongside the replay."""
import sys
import argparse
from functools import partial

from main import ENGINE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ENGINE))
    from src.services import stream
    stream.TelemetryStreamClient = partial(stream.TelemetryStreamClient, port=args.port)
    from PySide6.QtWidgets import QApplication
    from src.gui.insights_menu import InsightsMenu

    class ReplayInsightsMenu(InsightsMenu):
        def launch_telemetry_viewer(self):
            from src.insights.telemetry_stream_viewer import TelemetryStreamViewer
            window = TelemetryStreamViewer()
            window.show()
            self.opened_windows.append(window)

    app = QApplication(sys.argv)
    app.setApplicationName('F1 Race Lab Insights')
    menu = ReplayInsightsMenu()
    menu.show()
    menu.launch_lap_time_chart()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
