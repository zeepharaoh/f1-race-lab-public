from types import SimpleNamespace
import sys

import numpy as np
import pytest

from replay_overlays import Gap, GapTimeline, install_replay_overlays, leaderboard_layout


def test_gaps_use_same_point_crossings_with_changing_speed():
    timeline = GapTimeline([0, 1, 2, 3], {'AAA': [0, 40, 60, 100],
                                       'BBB': [0, 20, 40, 80],
                                       'CCC': [0, 10, 20, 40]}, 1000)
    gaps = timeline.gaps(3)
    assert gaps['BBB']['ahead'].seconds == pytest.approx(.5)
    assert gaps['BBB']['behind'].seconds == pytest.approx(1)
    assert gaps['CCC']['leader'].seconds == pytest.approx(2)
    assert gaps['AAA']['ahead'].seconds is None
    assert gaps['CCC']['behind'].seconds is None


def test_neighbors_are_from_full_field_when_visible_scope_skips_a_driver():
    timeline = GapTimeline([0, 1, 2], {'AAA': [0, 100, 200],
                                    'BBB': [0, 75, 150], 'CCC': [0, 50, 100]}, 1000)
    assert timeline.gaps(2)['CCC']['ahead'].seconds == pytest.approx(2 / 3)
    assert timeline.gaps(2)['CCC']['leader'].seconds == pytest.approx(1)


def test_missing_history_does_not_extrapolate_or_look_ahead():
    timeline = GapTimeline([0, 1, 2], {'AAA': [100, 200, 300], 'BBB': [0, 50, 100]}, 1000)
    assert timeline.gaps(1)['BBB']['ahead'].seconds is None
    assert timeline.crossed_at('AAA', 250, 1) is None
    assert timeline.gaps(2)['BBB']['ahead'].seconds == 2


def test_lapped_cars_have_lap_labels_instead_of_guessed_seconds():
    timeline = GapTimeline([0, 1], {'AAA': [1100, 1200], 'BBB': [0, 100]}, 1000)
    assert timeline.gaps(1)['BBB']['leader'].label() == '+1 L'
    assert Gap(laps=2).label() == '+2 L'
    assert Gap().label() == '—'


def test_discontinuous_positions_and_missing_samples_are_not_interpolated():
    timeline = GapTimeline([0, 1, 2, 3, 4], {'AAA': [0, 50, 900, np.nan, 950],
                                           'BBB': [0, 25, 450, 500, 600]}, 1000)
    assert timeline.gaps(2)['BBB']['ahead'].seconds is None
    assert timeline.gaps(3)['BBB']['ahead'].seconds is None


def test_rewind_and_overtake_recompute_neighbors_from_recorded_frame():
    timeline = GapTimeline([0, 1, 2], {'AAA': [0, 50, 100], 'BBB': [0, 25, 150]}, 1000)
    assert timeline.order(2) == ['BBB', 'AAA']
    assert timeline.gaps(2)['AAA']['ahead'].seconds == pytest.approx(.4)
    assert timeline.order(1) == ['AAA', 'BBB']
    assert timeline.gaps(1)['BBB']['ahead'].seconds == pytest.approx(.5)


def test_retired_or_frozen_driver_gaps_are_unavailable():
    timeline = GapTimeline([0, 1, 2], {'AAA': [0, 50, 100], 'BBB': [0, 25, 25]}, 1000,
                           active={'BBB': [True, True, False]})
    assert timeline.gaps(2)['AAA']['behind'].seconds is None
    assert timeline.gaps(2)['BBB']['leader'].seconds is None


def test_window_projection_uses_shared_reference_and_masks_missing_coordinates():
    class Tree:
        def query(self, points):
            return np.zeros(len(points)), np.zeros(len(points), dtype=int)

    window = SimpleNamespace(_ref_total_length=1000, _ref_xs=np.array([0, 500, 1000]),
                             _ref_ys=np.array([0, 0, 0]), _ref_cumdist=np.array([0, 500, 1000]),
                             track_tree=Tree())
    frames = [{'t': 0, 'drivers': {'AAA': {'x': 100, 'y': 20, 'lap': 2, 'rel_dist': .1}}},
              {'t': 1, 'drivers': {'AAA': {'x': 150, 'y': 20, 'lap': 2, 'rel_dist': 1},
                                   'BBB': {'x': np.nan, 'y': 20, 'lap': 1}}}]
    timeline = GapTimeline.from_window(window, frames)
    assert timeline.progress['AAA'].tolist() == [1100, 1150]
    assert timeline.active['AAA'].tolist() == [True, False]
    assert timeline.order(1) == ['AAA']


def test_adapter_restores_engine_methods_and_preserves_other_shortcuts(monkeypatch):
    class Window:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def on_key_press(self, symbol, modifiers):
            self.last_key = (symbol, modifiers)

    original_init, original_key = Window.__init__, Window.on_key_press
    board = type('Board', (), {})
    interface = SimpleNamespace(LeaderboardComponent=board, F1RaceReplayWindow=Window)
    monkeypatch.setitem(sys.modules, 'arcade', SimpleNamespace(key=SimpleNamespace(G=7, P=8)))
    with pytest.raises(RuntimeError):
        with install_replay_overlays(interface, []):
            assert interface.LeaderboardComponent is not board
            window = object.__new__(Window)
            window.leaderboard_show_gaps = False
            window.on_key_press(7, 0)
            assert window.leaderboard_show_gaps and not window.leaderboard_show_neighbor_gaps
            window.on_key_press(7, 0)
            assert not window.leaderboard_show_gaps and window.leaderboard_show_neighbor_gaps
            window.on_key_press(8, 0)
            assert window.leaderboard_show_pit_stops
            window.on_key_press(7, 0)
            assert window.leaderboard_show_gaps and window.leaderboard_show_pit_stops
            window.on_key_press(8, 0)
            assert not window.leaderboard_show_pit_stops and window.leaderboard_show_gaps
            window.on_key_press(1, 2)
            assert window.last_key == (1, 2)
            raise RuntimeError('renderer stopped')
    assert interface.LeaderboardComponent is board
    assert Window.__init__ is original_init
    assert Window.on_key_press is original_key


def test_stops_button_does_not_change_gap_mode_or_driver_selection(monkeypatch):
    class Board:
        def __init__(self, **kwargs):
            self.visible = True

        def on_mouse_press(self, window, x, y, button, modifiers):
            return 'driver selection'

    class Window:
        def __init__(self, **kwargs):
            pass

        def on_key_press(self, symbol, modifiers):
            pass

    monkeypatch.setitem(sys.modules, 'arcade', SimpleNamespace(key=SimpleNamespace(G=7, P=8)))
    interface = SimpleNamespace(LeaderboardComponent=Board, F1RaceReplayWindow=Window)
    with install_replay_overlays(interface, []):
        board = interface.LeaderboardComponent()
        board.stops_toggle_rect = (10, 20, 100, 40)
        window = SimpleNamespace(leaderboard_show_gaps=True, selected_drivers=['AAA'])
        assert board.on_mouse_press(window, 50, 30, 1, 0) is True
        assert window.leaderboard_show_pit_stops and window.leaderboard_show_gaps
        assert window.selected_drivers == ['AAA']
        board.on_mouse_press(window, 50, 30, 1, 0)
        assert not window.leaderboard_show_pit_stops
        assert board.on_mouse_press(window, 50, 10, 1, 0) == 'driver selection'


def test_every_row_keeps_tyres_when_gaps_are_hidden_and_stops_are_shown(monkeypatch):
    drawn = []

    class Text:
        def __init__(self, text, x, y, color, size, **kwargs):
            self.text, self.x, self.y, self.color = text, x, y, color
            self.font_size = size

        def draw(self):
            drawn.append(self.text)

    class Board:
        def __init__(self, **kwargs):
            self.width = kwargs['width']
            self.x, self.row_height = 100, 25
            self.visible = True
            self.entries = [('AAA', (255, 0, 0), {'lap': 2, 'dist': 100}, 500),
                            ('BBB', (0, 255, 0), {'lap': 2, 'dist': 200}, 400)]
            self.show_gaps = False

    class Window:
        def __init__(self, **kwargs):
            pass

        def on_key_press(self, *args):
            pass

    colors = SimpleNamespace(WHITE=(255, 255, 255), BLACK=(0, 0, 0), LIGHT_GRAY=(200, 200, 200))
    monkeypatch.setitem(sys.modules, 'arcade', SimpleNamespace(
        key=SimpleNamespace(G=7, P=8), Text=Text, color=colors, XYWH=lambda *args: args,
        draw_rect_filled=lambda *args: None, draw_circle_filled=lambda *args: None))
    states = {'AAA': SimpleNamespace(compound='MEDIUM', in_pit=False,
                                     tyre_label=lambda: 'MED 12L', stops_label=lambda: '1 · L10'),
              'BBB': SimpleNamespace(compound='HARD', in_pit=True,
                                     tyre_label=lambda: 'HARD 6L', stops_label=lambda: '0')}
    strategy = SimpleNamespace(at=lambda code, seconds: states[code], warning='')
    timeline = SimpleNamespace(times=[200], gaps=lambda index: {})
    window = SimpleNamespace(height=900, frame_index=0, _gap_timeline=timeline,
                             selected_drivers=['AAA'], leaderboard_show_neighbor_gaps=False,
                             leaderboard_show_gaps=False, leaderboard_show_pit_stops=True)
    interface = SimpleNamespace(LeaderboardComponent=Board, F1RaceReplayWindow=Window)
    with install_replay_overlays(interface, [], strategy):
        board = interface.LeaderboardComponent()
        board.draw(window)
        assert 'MED 12L' in drawn and 'HARD 6L' in drawn
        assert '1 · L10' in drawn and 'PIT' in drawn
        assert [rect[0] for rect in board.rects] == ['AAA', 'BBB']
        assert board.rects[0][-1] < board.stops_toggle_rect[1]
        drawn.clear()
        window.leaderboard_show_pit_stops = False
        board.draw(window)
        assert 'MED 12L' in drawn and 'HARD 6L' in drawn
        assert '1 · L10' not in drawn


@pytest.mark.parametrize('height', [540, 720, 900, 1080])
def test_full_field_layout_clears_banner_and_playback_controls(height):
    layout = leaderboard_layout(height, 22)
    assert layout.title_y + 12 < height - 70
    bottom = layout.title_y - 56 - 22 * layout.row_height
    assert bottom >= 160
    assert layout.data_font * 4 / 3 + 4 <= layout.row_height + 1e-6
