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


@contextmanager
def install_replay_overlays(interface, full_field_frames):
    """Keep the native replay controls and add full-field gap columns."""
    import arcade

    original_board = interface.LeaderboardComponent
    window_type = interface.F1RaceReplayWindow
    original_init = window_type.__init__
    original_key = window_type.on_key_press

    class SilentText:
        def draw(self):
            pass

    class GapLeaderboard(original_board):
        def __init__(self, *args, **kwargs):
            kwargs['width'] = 400
            super().__init__(*args, **kwargs)
            self.show_neighbor_gaps = True
            self._silent_gap = SilentText()

        def _calculate_gaps(self):
            self.computed_gaps = {}
            self.computed_neighbor_gaps = {}

        def draw(self, window):
            if not self.visible or not self.entries:
                self.rects = []
                return
            text = self._gap_text
            self._gap_text = self._silent_gap
            try:
                super().draw(window)
            finally:
                self._gap_text = text
            timeline = window._gap_timeline
            index = min(int(window.frame_index), len(timeline.times) - 1)
            gaps = timeline.gaps(index)
            title_y = window.height - 40
            arcade.Text('~ estimated · G: mode', self.x + 180, title_y - 4,
                        arcade.color.LIGHT_GRAY, 10, anchor_y='top').draw()
            if not (self.show_neighbor_gaps or self.show_gaps):
                return
            columns = [('ahead', self.x + self.width - 155), ('behind', self.x + self.width - 50)]
            if self.show_gaps:
                columns = [('leader', self.x + self.width - 50)]
            for name, right in columns:
                arcade.Text(name.title(), right, title_y - 20, arcade.color.LIGHT_GRAY,
                            9, anchor_x='right', anchor_y='top').draw()
            for code, _, _, _, top in self.rects:
                for name, right in columns:
                    gap = gaps.get(code, {}).get(name, Gap())
                    arcade.Text(gap.label(), right, top,
                                arcade.color.BLACK if code in self.selected else arcade.color.LIGHT_GRAY,
                                12, anchor_x='right', anchor_y='top').draw()

    def initialize(window, *args, **kwargs):
        kwargs['right_ui_margin'] = max(kwargs.get('right_ui_margin', 260), 420)
        original_init(window, *args, **kwargs)
        window._gap_timeline = GapTimeline.from_window(window, full_field_frames or window.frames)
        window.leaderboard_show_neighbor_gaps = True
        window.leaderboard_show_gaps = False
        popup = getattr(window, 'controls_popup_comp', None)
        if popup is not None:
            popup.set_lines([*popup._default_lines(), ('G', 'Switch ahead/behind and leader gaps')])
            popup.set_size(popup.width, max(popup.height, 320))

    def key_press(window, symbol, modifiers):
        if symbol == arcade.key.G:
            window.leaderboard_show_gaps = not getattr(window, 'leaderboard_show_gaps', False)
            window.leaderboard_show_neighbor_gaps = not window.leaderboard_show_gaps
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
