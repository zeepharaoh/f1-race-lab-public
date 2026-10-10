"""Recorded-position gap estimates for the desktop replay."""
from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class Gap:
    seconds: float | None = None
    laps: int = 0

    def label(self):
        if self.laps:
            return f'+{self.laps} L'
        if self.seconds is None:
            return '—'
        return f'+{self.seconds:.1f}s'


class GapTimeline:
    """Compare the times cars crossed the same unwrapped circuit position."""
    def __init__(self, times, progress, circuit_length, active=None):
        self.times = np.asarray(times, dtype=float)
        self.progress = {code: np.asarray(values, dtype=float) for code, values in progress.items()}
        self.circuit_length = float(circuit_length)
        self.active = {code: np.asarray((active or {}).get(code, np.ones(len(self.times), dtype=bool)))
                       for code in self.progress}
        self.segments = {}
        for code, values in self.progress.items():
            if len(values) != len(self.times):
                raise ValueError('Progress and replay times must have equal lengths')
            valid = np.isfinite(values) & np.isfinite(self.times)
            increments = np.diff(values)
            elapsed = np.diff(self.times)
            continuous = (valid[:-1] & valid[1:] & (increments > 0)
                          & (increments < self.circuit_length / 4)
                          & (elapsed > 0) & (elapsed <= 5))
            starts = np.flatnonzero(continuous & np.r_[True, ~continuous[:-1]])
            ends = np.flatnonzero(continuous & np.r_[~continuous[1:], True]) + 1
            self.segments[code] = [(int(start), int(end)) for start, end in zip(starts, ends)]

    @classmethod
    def from_window(cls, window, frames):
        times = [frame.get('t', math.nan) for frame in frames]
        codes = set().union(*(frame.get('drivers', {}) for frame in frames))
        progress, active = {}, {}
        length = window._ref_total_length
        for code in codes:
            positions = [frame.get('drivers', {}).get(code, {}) for frame in frames]
            points = np.asarray([(pos.get('x', math.nan), pos.get('y', math.nan)) for pos in positions])
            valid = np.isfinite(points).all(axis=1)
            projected = np.full(len(frames), math.nan)
            if length > 0 and valid.any():
                _, nearest = window.track_tree.query(points[valid])
                nearest = np.asarray(nearest, dtype=int)
                following = np.minimum(nearest + 1, len(window._ref_xs) - 1)
                origin = np.column_stack((window._ref_xs[nearest], window._ref_ys[nearest]))
                vector = np.column_stack((window._ref_xs[following], window._ref_ys[following])) - origin
                squared = np.einsum('ij,ij->i', vector, vector)
                fraction = np.divide(np.einsum('ij,ij->i', points[valid] - origin, vector), squared,
                                     out=np.zeros(len(nearest)), where=squared > 0)
                projected[valid] = window._ref_cumdist[nearest] + np.clip(fraction, 0, 1) * np.sqrt(squared)
            laps = np.asarray([_finite_lap(pos.get('lap')) for pos in positions])
            progress[code] = (laps - 1) * length + projected
            active[code] = np.asarray([bool(pos) and pos.get('rel_dist') != 1 for pos in positions])
        return cls(times, progress, length, active)

    def order(self, frame_index):
        return sorted((code for code, values in self.progress.items() if np.isfinite(values[frame_index])),
                      key=lambda code: (-self.progress[code][frame_index], code))

    def crossed_at(self, code, target, before):
        values = self.progress.get(code)
        if values is None or not math.isfinite(target):
            return None
        for start, end in reversed(self.segments[code]):
            end = min(end, before)
            if end <= start or not values[start] <= target <= values[end]:
                continue
            offset = bisect_right(values, target, start, end + 1)
            if offset > end:
                return float(self.times[end])
            lower = offset - 1
            fraction = (target - values[lower]) / (values[offset] - values[lower])
            return float(self.times[lower] + fraction * (self.times[offset] - self.times[lower]))
        return None

    def between(self, ahead, behind, frame_index):
        if ahead is None or behind is None or self.circuit_length <= 0:
            return Gap()
        for code in (ahead, behind):
            if code not in self.progress or not self.active[code][frame_index]:
                return Gap()
        ahead_progress = self.progress[ahead][frame_index]
        behind_progress = self.progress[behind][frame_index]
        difference = ahead_progress - behind_progress
        if not math.isfinite(difference) or difference < 0:
            return Gap()
        laps = int(difference // self.circuit_length)
        if laps:
            return Gap(laps=laps)
        if difference == 0:
            return Gap(seconds=0.0)
        crossing = self.crossed_at(ahead, behind_progress, frame_index)
        if crossing is None:
            return Gap()
        seconds = self.times[frame_index] - crossing
        return Gap(seconds=float(seconds)) if seconds >= 0 else Gap()

    def gaps(self, frame_index):
        order = self.order(frame_index)
        return {code: {'ahead': self.between(order[index - 1] if index else None, code, frame_index),
                       'behind': self.between(code, order[index + 1] if index + 1 < len(order) else None, frame_index),
                       'leader': self.between(order[0], code, frame_index) if index else Gap()}
                for index, code in enumerate(order)}


def _finite_lap(value):
    try:
        return max(1, int(value))
    except (ValueError, TypeError, OverflowError):
        return math.nan


@dataclass(frozen=True)
class LeaderboardLayout:
    title_y: float
    row_height: float
    driver_font: float
    data_font: float


def leaderboard_layout(height, count):
    title_y = height - 100
    row_height = min(25.0, max(8.0, (title_y - 56 - 160) / max(1, count)))
    font_limit = (row_height - 4) * .75
    return LeaderboardLayout(title_y, row_height, max(4.0, min(14.0, font_limit)),
                             max(4.0, min(12.0, font_limit)))


@contextmanager
def install_replay_overlays(interface, full_field_frames, strategy=None):
    """Add recorded gaps, tyres and pit visits to the native leaderboard."""
    import arcade

    original_board = interface.LeaderboardComponent
    window_type = interface.F1RaceReplayWindow
    original_init = window_type.__init__
    original_key = window_type.on_key_press

    class GapLeaderboard(original_board):
        def __init__(self, *args, **kwargs):
            kwargs['width'] = 520
            super().__init__(*args, **kwargs)
            self.show_neighbor_gaps = True
            self.stops_toggle_rect = None
            self._texts = {}

        def _label(self, key, text, x, y, color, size=12, align='left', bold=False):
            label = self._texts.get(key)
            if label is None:
                label = arcade.Text(text, x, y, color, size, anchor_x=align,
                                    anchor_y='top', bold=bold)
                self._texts[key] = label
            label.text, label.x, label.y, label.color = text, x, y, color
            if label.font_size != size:
                label.font_size = size
            label.draw()

        def _calculate_gaps(self):
            self.computed_gaps = {}
            self.computed_neighbor_gaps = {}

        def draw(self, window):
            if not self.visible or not self.entries:
                self.rects = []
                self.stops_toggle_rect = None
                return
            self.selected = getattr(window, 'selected_drivers', [])
            self.show_gaps = getattr(window, 'leaderboard_show_gaps', self.show_gaps)
            self.show_neighbor_gaps = getattr(window, 'leaderboard_show_neighbor_gaps', self.show_neighbor_gaps)
            if self.show_neighbor_gaps:
                self.show_gaps = False
            show_stops = getattr(window, 'leaderboard_show_pit_stops', False)
            timeline = window._gap_timeline
            index = max(0, min(int(window.frame_index), len(timeline.times) - 1))
            gaps = timeline.gaps(index)
            layout = leaderboard_layout(window.height, len(self.entries))
            title_y, self.row_height = layout.title_y, layout.row_height
            entries = self.entries
            panel_height = 105 + len(entries) * self.row_height
            arcade.draw_rect_filled(arcade.XYWH(self.x + self.width / 2, title_y + 12 - panel_height / 2,
                                               self.width + 16, panel_height), (13, 18, 25, 235))
            self._label('title', 'Leaderboard', self.x + 8, title_y, arcade.color.WHITE, 20, bold=True)
            self._label('gap_hint', 'G: gap mode', self.x + 180, title_y - 5, arcade.color.LIGHT_GRAY, 10)
            self.stops_toggle_rect = (self.x + 344, title_y - 24, self.x + 448, title_y + 2)
            left, bottom, right, top = self.stops_toggle_rect
            arcade.draw_rect_filled(arcade.XYWH((left + right) / 2, (bottom + top) / 2,
                                               right - left, top - bottom),
                                    (44, 110, 83) if show_stops else (42, 51, 66))
            self._label('stops_button', f'P: Stops {"ON" if show_stops else "OFF"}',
                        (left + right) / 2, top - 6, arcade.color.WHITE, 10, 'center', True)
            for name, active, center in [('neighbor', self.show_neighbor_gaps, self.x + self.width - 40),
                                          ('gap', self.show_gaps, self.x + self.width - 10)]:
                y = title_y - 12
                setattr(self, f'{name}_toggle_rect', (center - 10, y - 10, center + 10, y + 10))
                arcade.draw_circle_filled(center, y, 10, (44, 110, 83) if active else (42, 51, 66))
                self._label(name + '_button', 'I' if name == 'neighbor' else 'L',
                            center, y + 7, arcade.color.WHITE, 11, 'center', True)
            columns = []
            if self.show_neighbor_gaps:
                columns = [('ahead', self.x + 207), ('behind', self.x + 287)]
            elif self.show_gaps:
                columns = [('leader', self.x + 287)]
            heading_y = title_y - 36
            self._label('driver_heading', 'DRIVER', self.x + 8, heading_y, arcade.color.LIGHT_GRAY, 9)
            for name, right in columns:
                self._label(name + '_heading', name.title(), right, heading_y,
                            arcade.color.LIGHT_GRAY, 10, 'right')
            self._label('tyre_heading', 'TYRE · AGE', self.x + 388, heading_y,
                        arcade.color.LIGHT_GRAY, 9, 'right')
            if show_stops:
                self._label('stops_heading', 'STOPS · LAST LAP', self.x + 500, heading_y,
                            arcade.color.LIGHT_GRAY, 9, 'right')
            self.rects = []
            for rank, (code, color, pos, _) in enumerate(entries, 1):
                top = title_y - 56 - (rank - 1) * self.row_height
                bottom = top - self.row_height
                self.rects.append((code, self.x, bottom, self.x + self.width, top))
                selected = code in self.selected
                if selected or rank % 2 == 0:
                    arcade.draw_rect_filled(arcade.XYWH(self.x + self.width / 2, (top + bottom) / 2,
                                                       self.width, self.row_height),
                                            arcade.color.LIGHT_GRAY if selected else (24, 31, 42, 180))
                text_color = arcade.color.BLACK if selected else arcade.color.LIGHT_GRAY
                self._label((code, 'driver'), f'{rank:>2}. {code}', self.x + 8, top - 3,
                            arcade.color.BLACK if selected else color, layout.driver_font, bold=True)
                state = strategy.at(code, float(timeline.times[index])) if strategy else None
                in_pit = state.in_pit if state is not None and state.in_pit is not None else pos.get('in_pit', False)
                status = 'OUT' if pos.get('rel_dist') == 1 else 'PIT' if in_pit else ''
                if status:
                    self._label((code, 'status'), status, self.x + 98, top - 4,
                                (190, 50, 65) if status == 'OUT' else text_color,
                                min(11, layout.data_font))
                for name, right in columns:
                    gap = gaps.get(code, {}).get(name, Gap())
                    self._label((code, name), gap.label(), right, top - 4, text_color, layout.data_font, 'right')
                tyre_text = state.tyre_label() if state else '—'
                compound = state.compound if state else None
                tyre_color = {'SOFT': (255, 95, 105), 'MEDIUM': (255, 215, 70), 'HARD': (225, 233, 242),
                              'INTERMEDIATE': (70, 220, 155), 'WET': (90, 165, 255)}.get(compound, text_color)
                self._label((code, 'tyre'), tyre_text, self.x + 388, top - 4,
                            arcade.color.BLACK if selected else tyre_color, layout.data_font, 'right', True)
                if show_stops:
                    self._label((code, 'stops'), state.stops_label() if state else '—',
                                self.x + 500, top - 4, text_color, layout.data_font, 'right')
                if pos.get('drs', 0) >= 10:
                    arcade.draw_circle_filled(self.x + self.width - 5, top - self.row_height / 2,
                                              min(3, self.row_height / 4), (70, 220, 155))
            footer = title_y - 56 - len(entries) * self.row_height - 9
            self._label('footer', '~ Gap estimates · tyre age in laps · stops count on pit exit',
                        self.x + 8, footer, arcade.color.LIGHT_GRAY, 9)
            if strategy is not None and strategy.warning:
                self._label('strategy_warning', strategy.warning, self.x + 8, footer - 16,
                            (255, 215, 70), 9)
            elif _finite_lap(entries[0][2].get('lap')) == 1:
                self._label('lap_warning', 'Order may be inaccurate during lap 1', self.x + 8, footer - 16,
                            (255, 215, 70), 9)

        def on_mouse_press(self, window, x, y, button, modifiers):
            if self.visible and self.stops_toggle_rect is not None:
                left, bottom, right, top = self.stops_toggle_rect
                if left <= x <= right and bottom <= y <= top:
                    window.leaderboard_show_pit_stops = not getattr(window, 'leaderboard_show_pit_stops', False)
                    return True
            return super().on_mouse_press(window, x, y, button, modifiers)

    def initialize(window, *args, **kwargs):
        kwargs['right_ui_margin'] = max(kwargs.get('right_ui_margin', 260), 548)
        original_init(window, *args, **kwargs)
        window._gap_timeline = GapTimeline.from_window(window, full_field_frames or window.frames)
        window.leaderboard_show_neighbor_gaps = True
        window.leaderboard_show_gaps = False
        window.leaderboard_show_pit_stops = False
        popup = getattr(window, 'controls_popup_comp', None)
        if popup is not None:
            popup.set_lines([*popup._default_lines(), ('G', 'Switch ahead/behind and leader gaps'),
                             ('P', 'Show pit-stop count and last stop lap')])
            popup.set_size(popup.width, max(popup.height, 345))

    def key_press(window, symbol, modifiers):
        if symbol == arcade.key.G:
            window.leaderboard_show_gaps = not getattr(window, 'leaderboard_show_gaps', False)
            window.leaderboard_show_neighbor_gaps = not window.leaderboard_show_gaps
            return
        if symbol == arcade.key.P:
            window.leaderboard_show_pit_stops = not getattr(window, 'leaderboard_show_pit_stops', False)
            return
        return original_key(window, symbol, modifiers)

    interface.LeaderboardComponent = GapLeaderboard
    window_type.__init__ = initialize
    window_type.on_key_press = key_press
    try:
        yield
    finally:
        interface.LeaderboardComponent = original_board
        window_type.__init__ = original_init
        window_type.on_key_press = original_key
