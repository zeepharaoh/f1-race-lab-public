from types import SimpleNamespace

import pandas as pd
import pytest

from championship_overlays import install_championship_tables, table_layout


@pytest.fixture
def interface():
    text = []
    rectangles = []
    window = SimpleNamespace(width=1280, height=720, left_ui_margin=340,
                             controls_popup_comp=SimpleNamespace(visible=False))
    arcade = SimpleNamespace(
        get_window=lambda: window,
        XYWH=lambda *values: values,
        draw_rect_filled=lambda *values: rectangles.append(values),
        draw_text=lambda value, x, y, color, size, **kwargs: text.append(
            {'value': value, 'x': x, 'y': y, 'size': size, 'color': color, **kwargs}))
    return SimpleNamespace(arcade=arcade, text=text, rectangles=rectangles, window=window,
                           DriversChampionshipOverlay=object,
                           ConstructorsChampionshipOverlay=dict)


def test_table_is_populated_before_playback_and_preserves_all_drivers(interface):
    drivers = {f'D{index:02d}': 100 - index for index in range(23)}
    snapshot = {'drivers': drivers, 'constructors': {},
                'label': 'Before Sprint · through round 16'}
    with install_championship_tables(interface, snapshot):
        table = interface.DriversChampionshipOverlay(current_driver_standings={},
                                                     live_standings={})
        table.draw()
        entities = [item['value'] for item in interface.text if item['value'] in drivers]
        assert entities == list(drivers)
        assert table.cached_lap_standings == drivers
        assert 'Before Sprint · through round 16' in [item['value'] for item in interface.text]
        table.update(12)
        assert table.cached_lap_standings == drivers
    assert interface.DriversChampionshipOverlay is object
    assert interface.ConstructorsChampionshipOverlay is dict


def test_installed_classes_restore_when_replay_fails(interface):
    with pytest.raises(RuntimeError):
        with install_championship_tables(interface, {}):
            assert interface.DriversChampionshipOverlay is not object
            raise RuntimeError('Replay failed')
    assert interface.DriversChampionshipOverlay is object
    assert interface.ConstructorsChampionshipOverlay is dict


def test_empty_snapshot_displays_coverage_and_warning(interface):
    snapshot = {'drivers': {}, 'constructors': {}, 'label': 'Coverage unavailable',
                'warning': 'Standings source unavailable. Try again later.'}
    with install_championship_tables(interface, snapshot):
        interface.ConstructorsChampionshipOverlay().draw()
    values = [item['value'] for item in interface.text]
    assert 'Constructor championship' in values
    assert 'Coverage unavailable' in values
    assert 'Standings source unavailable. Try again later.' in values
    assert 'Championship data unavailable' in values


@pytest.mark.parametrize('width,height', [(1280, 720), (960, 540), (800, 480)])
def test_compact_layout_keeps_every_driver_on_screen(width, height):
    layout = table_layout(width, height, 23)
    assert layout.rows * layout.columns >= 23
    assert layout.left + layout.width <= width - 16
    assert layout.top - layout.height >= 56
    assert layout.row_height >= 14
    if height == 480:
        assert layout.columns == 2


def test_help_and_driver_cards_do_not_cover_table(interface):
    interface.window.controls_popup_comp = SimpleNamespace(visible=True, cx=210, width=380)
    snapshot = {'drivers': {'AAA': 12.5}, 'label': 'Before Race · round 17 Sprint included'}
    with install_championship_tables(interface, snapshot):
        interface.DriversChampionshipOverlay().draw()
    center_x, center_y, width, height = interface.rectangles[0][0]
    assert center_x - width / 2 >= 412
    assert center_y - height / 2 > 120
    assert '12.5' in [item['value'] for item in interface.text]


def test_constructor_colors_use_full_session_when_replay_is_filtered(interface):
    source = SimpleNamespace(results=pd.DataFrame(
        {'TeamName': ['First team', 'Second team'], 'TeamColor': ['123456', 'abcdef']}))
    view = SimpleNamespace(source=source, results=source.results.iloc[:1])
    snapshot = {'constructors': {'First team': 10, 'Second team': 9},
                'label': 'Before Sprint · through round 16'}
    with install_championship_tables(interface, snapshot):
        interface.ConstructorsChampionshipOverlay(session=view).draw()
    second = next(item for item in interface.text if item['value'] == 'Second team')
    assert second['color'] == (171, 205, 239)


def test_hidden_table_does_not_draw(interface):
    with install_championship_tables(interface, {'drivers': {'AAA': 1}}):
        interface.DriversChampionshipOverlay(visible=False).draw()
    assert not interface.text
    assert not interface.rectangles
