from region_selector import (
    DockSide,
    Monitor,
    Rect,
    centered_start_rect,
    choose_dock_side,
    dot_positions,
    drag_corner,
    fit_rect_to_monitor,
    is_fullscreen,
    largest_outside_side,
    monitor_at_position,
    move_rect,
    normalize_rect,
    place_toolbar,
    reserve_toolbar_space,
    toolbar_placement,
)
from region_selector.geometry import CornerRole, DragMode, edge_geometries


MONITOR = Monitor(100, 50, 1000, 800, "test")


def test_normalize_rect_handles_both_negative_axes() -> None:
    assert normalize_rect(Rect(20, 30, -12, -25)) == Rect(8, 5, 12, 25)


def test_fit_rect_preserves_size_when_repositioning() -> None:
    assert fit_rect_to_monitor(Rect(1050, -100, 200, 200), MONITOR) == Rect(
        900, 50, 200, 200
    )


def test_fit_rect_caps_oversized_selection() -> None:
    assert fit_rect_to_monitor(Rect(-100, -100, 2000, 900), MONITOR) == Rect(
        100, 50, 1000, 800
    )


def test_monitor_at_position_supports_negative_coordinates_and_edges() -> None:
    left = Monitor(-800, 0, 800, 600, "left")
    right = Monitor(0, 0, 1000, 800, "right")
    monitors = (left, right)

    assert monitor_at_position(monitors, -1, 200) == left
    assert monitor_at_position(monitors, 0, 200) == right
    assert monitor_at_position(monitors, 1000, 200) is None


def test_move_rect_preserves_size_and_clamps_to_target_monitor() -> None:
    assert move_rect(Rect(200, 200, 300, 200), (500, 400), (900, 100), MONITOR) == Rect(
        600, 50, 300, 200
    )


def test_move_rect_caps_selection_for_smaller_target_monitor() -> None:
    target = Monitor(1200, 100, 200, 150, "small")
    assert move_rect(Rect(200, 200, 300, 250), (500, 400), (1250, 120), target) == Rect(
        1200, 100, 200, 150
    )


def test_default_selection_is_centered_and_capped() -> None:
    assert centered_start_rect(MONITOR) == Rect(280, 210, 640, 480)
    assert centered_start_rect(Monitor(-20, 10, 320, 200)) == Rect(-20, 10, 320, 200)


def test_dot_windows_are_diagonally_outside_rect_corners() -> None:
    assert dot_positions(Rect(100, 200, 50, 80), 24) == {
        "tl": (76, 176),
        "tr": (150, 176),
        "bl": (76, 280),
        "br": (150, 280),
    }


def test_odd_sized_dot_windows_remain_entirely_outside() -> None:
    assert dot_positions(Rect(100, 200, 50, 80), 23) == {
        "tl": (77, 177),
        "tr": (150, 177),
        "bl": (77, 280),
        "br": (150, 280),
    }


def test_edge_windows_are_outside_rect_edges() -> None:
    assert edge_geometries(Rect(100, 200, 50, 80), 2) == {
        "top": Rect(100, 198, 50, 2),
        "right": Rect(150, 200, 2, 80),
        "bottom": Rect(100, 280, 50, 2),
        "left": Rect(98, 200, 2, 80),
    }


def test_edge_windows_keep_valid_sizes_for_zero_area_rect() -> None:
    assert edge_geometries(Rect(100, 200, 0, 0), 3) == {
        "top": Rect(100, 197, 1, 3),
        "right": Rect(100, 200, 3, 1),
        "bottom": Rect(100, 200, 1, 3),
        "left": Rect(97, 200, 3, 1),
    }


def test_crossing_is_clamped_to_minimum_width() -> None:
    rect, role = drag_corner(
        Rect(200, 200, 300, 200), "br", 100, 400, MONITOR, 24
    )
    assert rect == Rect(200, 200, 24, 200)
    assert role == "br"


def test_crossing_is_clamped_to_minimum_height() -> None:
    rect, role = drag_corner(
        Rect(200, 200, 300, 200), "bl", 150, 100, MONITOR, 24
    )
    assert rect == Rect(150, 200, 350, 24)
    assert role == "bl"


def test_diagonal_crossing_stops_on_the_original_side() -> None:
    rect, role = drag_corner(
        Rect(200, 200, 300, 200), "tr", 100, 500, MONITOR, 24
    )
    assert rect == Rect(200, 376, 24, 24)
    assert role == "tr"


def test_each_corner_keeps_its_role_when_crossing_anchor() -> None:
    rect = Rect(200, 200, 300, 200)
    cases = {
        "tl": ((700, 600), Rect(476, 376, 24, 24)),
        "tr": ((100, 600), Rect(200, 376, 24, 24)),
        "bl": ((700, 100), Rect(476, 200, 24, 24)),
        "br": ((100, 100), Rect(200, 200, 24, 24)),
    }

    for role, (cursor, expected) in cases.items():
        resized, returned_role = drag_corner(rect, role, *cursor, MONITOR, 24)
        assert resized == expected
        assert returned_role == role


def test_unconstrained_axis_continues_resizing() -> None:
    rect, role = drag_corner(
        Rect(200, 200, 300, 200), "br", 100, 700, MONITOR, 24
    )
    assert rect == Rect(200, 200, 24, 500)
    assert role == "br"


def test_drag_is_clamped_to_monitor() -> None:
    rect, role = drag_corner(
        Rect(200, 200, 300, 200), "br", 5000, -5000, MONITOR, 24
    )
    assert rect == Rect(200, 200, 900, 24)
    assert role == "br"


def test_toolbar_docking_uses_order_and_skips_touched_edges() -> None:
    monitor = Monitor(-1000, -200, 1000, 800)
    horizontal = (240, 40)
    vertical = (70, 260)

    assert (
        choose_dock_side(
            Rect(-800, 0, 500, 300), monitor, horizontal, vertical, 8
        )
        is DockSide.BOTTOM
    )
    assert (
        choose_dock_side(
            Rect(-800, 300, 500, 300), monitor, horizontal, vertical, 8
        )
        is DockSide.TOP
    )
    assert (
        choose_dock_side(
            Rect(-800, -200, 500, 800), monitor, horizontal, vertical, 8
        )
        is DockSide.LEFT
    )
    assert (
        choose_dock_side(
            Rect(-1000, -200, 500, 800), monitor, horizontal, vertical, 8
        )
        is DockSide.RIGHT
    )


def test_toolbar_largest_space_tie_uses_default_order() -> None:
    monitor = Monitor(0, 0, 1000, 800)
    rect = Rect(100, 100, 800, 600)

    assert largest_outside_side(rect, monitor) is DockSide.BOTTOM
    placement = toolbar_placement(
        rect,
        monitor,
        (1200, 110),
        (110, 900),
        (300, 110),
        (110, 300),
        8,
    )
    assert placement.side is DockSide.BOTTOM
    assert placement.compact is True


def test_toolbar_placement_clamps_to_negative_monitor_coordinates() -> None:
    monitor = Monitor(-1000, -200, 1000, 800)
    geometry = place_toolbar(
        Rect(-990, 0, 40, 100), monitor, DockSide.BOTTOM, (300, 40), 8
    )

    assert geometry == Rect(-1000, 108, 300, 40)


def test_exact_fullscreen_is_the_only_fullscreen_state() -> None:
    monitor = Monitor(-1000, -200, 1000, 800)

    assert is_fullscreen(Rect(-1000, -200, 1000, 800), monitor) is True
    assert is_fullscreen(Rect(-999, -200, 999, 800), monitor) is False
    placement = toolbar_placement(
        Rect(-1000, -200, 1000, 800),
        monitor,
        (300, 40),
        (70, 300),
        (100, 40),
        (70, 100),
        handle_size=(24, 18),
    )
    assert placement.fullscreen is True
    assert placement.collapsed is True
    assert placement.geometry == Rect(-512, 582, 24, 18)


def test_move_reservation_preserves_size_then_shrinks_only_if_needed() -> None:
    monitor = Monitor(0, 0, 1000, 800)

    assert reserve_toolbar_space(
        Rect(0, 20, 1000, 740),
        monitor,
        DockSide.BOTTOM,
        (300, 50),
        10,
    ) == Rect(0, 0, 1000, 740)
    assert reserve_toolbar_space(
        Rect(0, 10, 1000, 780),
        monitor,
        DockSide.BOTTOM,
        (300, 50),
        10,
    ) == Rect(0, 0, 1000, 740)


def test_resize_reservation_keeps_the_opposite_anchor() -> None:
    monitor = Monitor(0, 0, 1000, 800)

    assert reserve_toolbar_space(
        Rect(100, 100, 500, 660),
        monitor,
        DockSide.BOTTOM,
        (300, 50),
        10,
        mode=DragMode.RESIZE,
        role=CornerRole.BOTTOM_RIGHT,
    ) == Rect(100, 100, 500, 640)
    assert reserve_toolbar_space(
        Rect(100, 20, 500, 680),
        monitor,
        DockSide.TOP,
        (300, 50),
        10,
        mode=DragMode.RESIZE,
        role=CornerRole.TOP_LEFT,
    ) == Rect(100, 60, 500, 640)


def test_reservation_explicitly_allows_exact_fullscreen() -> None:
    monitor = Monitor(0, 0, 1000, 800)
    fullscreen = Rect(0, 0, 1000, 800)

    assert reserve_toolbar_space(
        fullscreen, monitor, DockSide.BOTTOM, (300, 50), 10
    ) == fullscreen
