"""Championship snapshots for the desktop replay."""
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
import textwrap


@dataclass(frozen=True)
class TableLayout:
    left: float
    top: float
    width: float
    height: float
    columns: int
    rows: int
    row_height: float
    font_size: float
    header_height: float


def table_layout(width, height, count, warning_lines=0, help_right=None,
                 left_margin=340):
    panel_width = min(350.0, max(180.0, width - 32.0))
    left = max(336.0, float(left_margin))
    if help_right is not None:
        left = max(left, help_right + 12.0)
    left = min(left, max(16.0, width - panel_width - 16.0))
    top = max(140.0, height - 130.0)
    bottom = 120.0 if height >= 620 else 56.0
    available = max(84.0, top - bottom)
    header = 76.0 + warning_lines * 14.0
    max_columns = max(1, int(panel_width // 145))
    columns = min(max_columns, max(1, ceil(count * 14 / max(14, available - header))))
    rows = max(1, ceil(count / columns))
    row_height = min(22.0, max(8.0, (available - header) / rows))
    font_size = min(12.0, max(7.0, row_height - 5.0))
    return TableLayout(left, top, panel_width, header + rows * row_height,
                       columns, rows, row_height, font_size, header)


def _points(value):
    return f'{float(value):g}'


class ChampionshipTable:
    def __init__(self, arcade, snapshot, kind, visible=True, driver_colors=None,
                 session=None, **kwargs):
        self.arcade = arcade
        self.snapshot = snapshot
        self.kind = kind
        self.visible = visible
        self.current_lap = 1
        self.cached_lap_standings = dict(snapshot.get(kind) or {})
        self.colors = dict(driver_colors or {})
        if kind == 'constructors' and session is not None:
            source = getattr(session, 'source', session)
            results = getattr(source, 'results', None)
            if results is not None and hasattr(results, 'iterrows'):
                for _, row in results.iterrows():
                    color = str(row.get('TeamColor', '')).lstrip('#')
                    if len(color) == 6:
                        try:
                            self.colors[row.get('TeamName')] = tuple(
                                int(color[index:index + 2], 16) for index in (0, 2, 4))
                        except ValueError:
                            pass

    def update(self, lap):
        self.current_lap = lap

    def draw(self):
        if not self.visible:
            return
        arcade = self.arcade
        window = arcade.get_window()
        popup = getattr(window, 'controls_popup_comp', None)
        help_right = None
        if popup is not None and getattr(popup, 'visible', False):
            center = getattr(popup, 'cx', None)
            center = window.width / 2 if center is None else center
            help_right = center + popup.width / 2
        warning = str(self.snapshot.get('warning') or '')
        warning_lines = textwrap.wrap(warning, width=48)
        layout = table_layout(window.width, window.height, len(self.cached_lap_standings),
                              len(warning_lines), help_right,
                              getattr(window, 'left_ui_margin', 340))
        arcade.draw_rect_filled(
            arcade.XYWH(layout.left + layout.width / 2, layout.top - layout.height / 2,
                        layout.width, layout.height), (15, 20, 30, 248))
        title = 'Driver championship' if self.kind == 'drivers' else 'Constructor championship'
        left, top = layout.left + 14, layout.top - 24
        arcade.draw_text(title, left, top, (245, 247, 250), 15, bold=True)
        label = str(self.snapshot.get('label') or 'Coverage unavailable')
        arcade.draw_text(label, left, top - 20, (170, 184, 201), 10,
                         width=layout.width - 28, multiline=True)
        for index, line in enumerate(warning_lines):
            arcade.draw_text(line, left, top - 39 - index * 14, (255, 199, 89), 9)
        if not self.cached_lap_standings:
            arcade.draw_text('Championship data unavailable', left,
                             layout.top - layout.header_height, (170, 184, 201), 11)
            return
        column_width = (layout.width - 28) / layout.columns
        for index, (entity, points) in enumerate(self.cached_lap_standings.items()):
            column, row = divmod(index, layout.rows)
            x = left + column * column_width
            y = layout.top - layout.header_height - row * layout.row_height
            color = self.colors.get(entity, (170, 184, 201))
            arcade.draw_text(str(index + 1), x, y, (170, 184, 201), layout.font_size)
            arcade.draw_text(str(entity), x + 25, y, color, layout.font_size)
            arcade.draw_text(_points(points), x + column_width - 2, y,
                             (245, 247, 250), layout.font_size, anchor_x='right')


@contextmanager
def install_championship_tables(interface, snapshot):
    driver_type = interface.DriversChampionshipOverlay
    constructor_type = interface.ConstructorsChampionshipOverlay

    class DriversTable(ChampionshipTable):
        def __init__(self, **kwargs):
            super().__init__(interface.arcade, snapshot, 'drivers', **kwargs)

    class ConstructorsTable(ChampionshipTable):
        def __init__(self, **kwargs):
            super().__init__(interface.arcade, snapshot, 'constructors', **kwargs)

    interface.DriversChampionshipOverlay = DriversTable
    interface.ConstructorsChampionshipOverlay = ConstructorsTable
    try:
        yield
    finally:
        interface.DriversChampionshipOverlay = driver_type
        interface.ConstructorsChampionshipOverlay = constructor_type
