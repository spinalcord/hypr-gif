import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QImage
from PyQt6.QtWidgets import QApplication, QBoxLayout, QStyle

from region_selector import (
    CornerRole,
    DockSide,
    HyprlandError,
    Monitor,
    Rect,
    RegionSelector,
    SelectionInteractionMode,
)
from region_selector.geometry import DragMode, EdgeRole
from region_selector.windows import BorderWindow, DotWindow, ToolbarWindow


class FakeHyprland:
    def __init__(self) -> None:
        self.configured = []
        self.moves = []
        self.capture_geometry_result = Rect(102, 102, 296, 196)
        self.capture_geometry_calls = []

    def focused_monitor(self):
        return Monitor(0, 0, 1000, 800, "focused")

    def monitors(self):
        return (
            Monitor(0, 0, 1000, 800, "focused"),
            Monitor(1000, 0, 800, 600, "second"),
        )

    def find_selection_windows(self, _pid, titles):
        return {role: f"0x{index + 1}" for index, role in enumerate(titles)}

    def configure_selection_windows(
        self,
        addresses,
        positions,
        dot_size,
        edges,
        toolbar_geometry=None,
        monitor=None,
    ):
        self.configured.append(
            (
                dict(addresses),
                dict(positions),
                dot_size,
                dict(edges),
                toolbar_geometry,
                monitor,
            )
        )

    def update_selection_windows(
        self, addresses, positions, edges, toolbar_geometry=None, monitor=None
    ):
        self.moves.append(
            (dict(addresses), dict(positions), dict(edges), toolbar_geometry, monitor)
        )

    def capture_geometry(self, addresses):
        self.capture_geometry_calls.append(dict(addresses))
        return self.capture_geometry_result


class FakeCursor:
    def __init__(self, *positions) -> None:
        self.positions = iter(positions)

    def position(self):
        return next(self.positions)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_start_resize_and_confirm_emit_public_signals(app) -> None:
    hyprland = FakeHyprland()
    changed = []
    confirmed = []
    selector = RegionSelector(
        initial_rect=Rect(200, 200, 300, 200),
        hyprland=hyprland,
        cursor_socket=FakeCursor((650, 150)),
    )
    selector.geometry_changed.connect(changed.append)
    selector.confirmed.connect(confirmed.append)

    selector.start()
    assert selector.geometry == Rect(200, 200, 300, 200)
    assert changed == [Rect(200, 200, 300, 200)]
    assert len(hyprland.configured) == 1
    assert len(hyprland.configured[0][0]) == 9
    assert hyprland.configured[0][5] == "focused"
    assert hyprland.configured[0][1] == {
        "tl": (176, 176),
        "tr": (500, 176),
        "bl": (176, 400),
        "br": (500, 400),
    }
    assert hyprland.configured[0][3] == {
        "top": Rect(200, 198, 300, 2),
        "right": Rect(500, 200, 2, 200),
        "bottom": Rect(200, 400, 300, 2),
        "left": Rect(198, 200, 2, 200),
    }
    assert (
        hyprland.configured[0][4]
        == selector._window_group.toolbar_placement.geometry
    )
    assert selector._window_group.toolbar_window is not None
    assert len(selector._window_group.border_windows) == 4
    assert selector._window_group._animation_timer.isActive() is True
    selector._window_group._advance_animation()
    assert {
        window._phase for window in selector._window_group.border_windows.values()
    } == {1}

    selector._drag_controller.begin(CornerRole.TOP_LEFT, DragMode.RESIZE)
    assert selector.geometry == Rect(476, 150, 24, 250)
    assert changed[-1] == selector.geometry
    assert len(hyprland.moves) == 1
    assert hyprland.moves[0][4] is None
    assert hyprland.moves[0][1] == {
        "tl": (452, 126),
        "tr": (500, 126),
        "bl": (452, 400),
        "br": (500, 400),
    }

    selector.confirm()
    assert confirmed == [Rect(476, 150, 24, 250)]
    assert selector._window_group.windows == {}
    assert selector._window_group.border_windows == {}
    assert selector._window_group.toolbar_window is None
    assert selector._window_group._animation_timer.isActive() is False


def test_move_drag_switches_monitor_and_preserves_size(app) -> None:
    hyprland = FakeHyprland()
    changes = []
    selector = RegionSelector(
        initial_rect=Rect(200, 200, 300, 200),
        hyprland=hyprland,
        cursor_socket=FakeCursor((500, 400)),
    )
    selector.geometry_changed.connect(changes.append)
    selector.start()

    selector._drag_controller.begin(CornerRole.BOTTOM_RIGHT, DragMode.MOVE)
    selector._drag_controller.update((1100, 300))

    assert selector.geometry == Rect(1000, 100, 300, 200)
    assert changes[-1] == selector.geometry
    assert len(hyprland.moves) == 1
    assert hyprland.moves[0][4] == "second"
    toolbar_geometry = selector._window_group.toolbar_placement.geometry
    assert 1000 <= toolbar_geometry.x < toolbar_geometry.right <= 1800
    assert 0 <= toolbar_geometry.y < toolbar_geometry.bottom <= 600

    selector._window_group.move(selector.geometry, hyprland.monitors()[1])
    assert hyprland.moves[-1][4] is None
    selector.cancel()


def test_dot_window_translates_mouse_modifiers_to_drag_modes(app) -> None:
    class FakeMousePress:
        def __init__(self, modifiers) -> None:
            self._modifiers = modifiers
            self.accepted = False

        def button(self):
            return Qt.MouseButton.LeftButton

        def modifiers(self):
            return self._modifiers

        def accept(self):
            self.accepted = True

    window = DotWindow(
        CornerRole.TOP_LEFT,
        "test-dot",
        24,
        QColor("#ff3b30"),
    )
    calls = []
    window.drag_started.connect(lambda role, mode: calls.append((role, mode)))

    move_event = FakeMousePress(Qt.KeyboardModifier.NoModifier)
    window.mousePressEvent(move_event)
    resize_event = FakeMousePress(Qt.KeyboardModifier.ShiftModifier)
    window.mousePressEvent(resize_event)

    assert calls == [
        (CornerRole.TOP_LEFT, DragMode.MOVE),
        (CornerRole.TOP_LEFT, DragMode.RESIZE),
    ]
    assert move_event.accepted is True
    assert resize_event.accepted is True
    window.close_from_group()


def test_dot_window_renders_configured_fill_with_white_outline(app) -> None:
    window = DotWindow(
        CornerRole.TOP_LEFT,
        "test-dot-paint",
        24,
        QColor("#000000"),
    )
    image = QImage(window.size(), QImage.Format.Format_RGB32)
    window.render(image)

    assert image.pixelColor(12, 12) == QColor("#000000")
    assert image.pixelColor(0, 12) == QColor("#ffffff")
    window.close_from_group()


def test_border_window_renders_and_advances_black_white_pattern(app) -> None:
    window = BorderWindow(EdgeRole.TOP, "test-border", Rect(0, 0, 16, 2))
    first = QImage(window.size(), QImage.Format.Format_RGB32)
    window.render(first)
    window.set_phase(1)
    second = QImage(window.size(), QImage.Format.Format_RGB32)
    window.render(second)

    assert first.pixelColor(0, 0) == QColor("#000000")
    assert first.pixelColor(4, 0) == QColor("#ffffff")
    assert second.pixelColor(0, 0) == QColor("#ffffff")
    assert second.pixelColor(1, 0) == QColor("#000000")
    window.close_from_group()


def test_cancel_emits_signal(app) -> None:
    signals = []
    selector = RegionSelector(
        hyprland=FakeHyprland(),
        cursor_socket=FakeCursor(),
    )
    selector.cancelled.connect(lambda: signals.append("cancelled"))
    selector.start()
    selector.cancel()
    assert signals == ["cancelled"]


def test_window_configuration_failure_stops_animation_and_closes_windows(app) -> None:
    class FailingHyprland(FakeHyprland):
        def configure_selection_windows(
            self, _addresses, _positions, _dot_size, _edges
        ):
            raise HyprlandError("configuration failed")

    errors = []
    selector = RegionSelector(
        hyprland=FailingHyprland(),
        cursor_socket=FakeCursor(),
    )
    selector.error.connect(errors.append)

    selector.start()

    assert errors == ["configuration failed"]
    assert selector._window_group.windows == {}
    assert selector._window_group.border_windows == {}
    assert selector._window_group._animation_timer.isActive() is False


@pytest.mark.parametrize(
    ("argument", "message"),
    (({"ants_width": 0}, "ants_width"), ({"ants_interval_ms": 0}, "ants_interval_ms")),
)
def test_invalid_ant_options_are_rejected(argument, message) -> None:
    with pytest.raises(ValueError, match=message):
        RegionSelector(**argument)


def test_toolbar_switches_orientation_and_preserves_iconless_text(app) -> None:
    icon_action = QAction(
        app.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon), "Files"
    )
    text_action = QAction("Short")
    toolbar = ToolbarWindow("test-toolbar", (icon_action, text_action))

    toolbar.configure(DockSide.LEFT, True, False, False)

    assert toolbar._action_layout.direction() is QBoxLayout.Direction.TopToBottom
    assert toolbar.buttons[1].toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonIconOnly
    assert toolbar.buttons[2].toolButtonStyle() is Qt.ToolButtonStyle.ToolButtonTextOnly
    assert toolbar.buttons[2].text() == "Short"
    toolbar.configure(DockSide.BOTTOM, False, False, False)
    assert toolbar._action_layout.direction() is QBoxLayout.Direction.LeftToRight
    toolbar.close_from_group()


def test_toolbar_orders_actions_and_forwards_confirm_cancel(app) -> None:
    extra = QAction("Extra")
    toolbar = ToolbarWindow("test-toolbar-actions", (extra,))
    signals = []
    toolbar.confirm_requested.connect(lambda: signals.append("confirm"))
    toolbar.cancel_requested.connect(lambda: signals.append("cancel"))

    assert toolbar.actions == (toolbar.confirm_action, extra, toolbar.cancel_action)
    assert extra.parent() is None
    toolbar.confirm_action.trigger()
    toolbar.cancel_action.trigger()

    assert signals == ["confirm", "cancel"]
    toolbar.close_from_group()


def test_fullscreen_toolbar_starts_collapsed_and_toggles(app) -> None:
    hyprland = FakeHyprland()
    selector = RegionSelector(
        initial_rect=Rect(0, 0, 1000, 800),
        hyprland=hyprland,
        cursor_socket=FakeCursor(),
    )
    selector.start()
    toolbar = selector._window_group.toolbar_window

    assert toolbar is not None
    assert toolbar.collapsed is True
    assert toolbar.handle_button.isVisible() is True
    assert selector._window_group.toolbar_placement.fullscreen is True
    toolbar.handle_button.click()
    assert toolbar.collapsed is False
    assert len(hyprland.moves) == 1
    toolbar.handle_button.click()
    assert toolbar.collapsed is True
    monitor = Monitor(0, 0, 1000, 800, "focused")
    selector._window_group.move(Rect(0, 0, 900, 700), monitor)
    assert selector._window_group.toolbar_placement.fullscreen is False
    assert toolbar.collapsed is False
    selector._window_group.move(Rect(0, 0, 1000, 800), monitor)
    assert toolbar.collapsed is True
    selector.cancel()


def test_toolbar_options_are_validated() -> None:
    with pytest.raises(ValueError, match="toolbar_gap"):
        RegionSelector(toolbar_gap=-1)
    with pytest.raises(TypeError, match="QAction"):
        RegionSelector(toolbar_actions=(object(),))
    with pytest.raises(TypeError, match="QIcon"):
        RegionSelector(confirm_icon=object())


def test_selector_can_remain_open_for_recording_controls(app) -> None:
    confirmed = []
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        confirm_text="Record",
        auto_close_on_confirm=False,
        hyprland=FakeHyprland(),
        cursor_socket=FakeCursor(),
    )
    selector.confirmed.connect(confirmed.append)
    selector.start()

    selector.confirm()

    assert confirmed == [Rect(100, 100, 300, 200)]
    assert selector._window_group.toolbar_window is not None
    assert selector._window_group.toolbar_window.confirm_action.text() == "Record"
    selector.close()


def test_recording_mode_disables_selection_but_keeps_confirm_action(app) -> None:
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=FakeHyprland(),
        cursor_socket=FakeCursor(),
    )
    selector.start()
    toolbar = selector._window_group.toolbar_window

    selector.set_interaction_enabled(False)

    assert toolbar is not None
    assert toolbar.confirm_action.isEnabled() is True
    assert toolbar.cancel_action.isEnabled() is False
    assert all(
        not window.isEnabled()
        for window in selector._window_group.windows.values()
    )
    selector.close()


def test_move_only_mode_ignores_shift_resize_and_blocks_cancel(app) -> None:
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=FakeHyprland(),
        cursor_socket=FakeCursor((250, 200)),
    )
    cancelled = []
    selector.cancelled.connect(lambda: cancelled.append(True))
    selector.start()
    selector.set_interaction_mode(SelectionInteractionMode.MOVE_ONLY)

    selector._begin_drag(CornerRole.TOP_LEFT, DragMode.RESIZE)
    selector._drag_controller.update((350, 250))

    assert selector.geometry == Rect(200, 150, 300, 200)
    assert selector._drag_controller.drag_mode is DragMode.MOVE
    selector.cancel()
    assert cancelled == []
    assert selector._window_group.toolbar_window is not None
    selector.close()


def test_marching_ants_visibility_unmaps_and_restores_edge_windows(app) -> None:
    hyprland = FakeHyprland()
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=hyprland,
        cursor_socket=FakeCursor(),
    )
    selector.start()
    addresses = dict(selector._window_group._addresses)

    assert selector.set_marching_ants_visible(False) is True
    assert selector._window_group._animation_timer.isActive() is False
    assert dict(selector._window_group._addresses) == addresses
    assert len(selector._window_group.border_windows) == 4
    assert all(
        not window.isVisible()
        for window in selector._window_group.border_windows.values()
    )
    assert selector.set_marching_ants_visible(True) is True
    assert selector._window_group._animation_timer.isActive() is True
    assert all(
        window.isVisible()
        for window in selector._window_group.border_windows.values()
    )
    assert len(hyprland.configured) == 2
    assert hyprland.configured[1][5] == "focused"
    selector.close()


def test_overlay_visibility_unmaps_and_restores_all_windows(app) -> None:
    hyprland = FakeHyprland()
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=hyprland,
        cursor_socket=FakeCursor(),
    )
    selector.start()
    toolbar = selector._window_group.toolbar_window
    geometry = selector.geometry
    selector.set_interaction_mode(SelectionInteractionMode.MOVE_ONLY)
    selector.set_confirm_enabled(False)

    assert toolbar is not None
    assert selector.set_overlay_visible(False) is True
    assert selector._window_group._animation_timer.isActive() is False
    assert all(
        not window.isVisible()
        for window in selector._window_group.windows.values()
    )
    assert all(
        not window.isVisible()
        for window in selector._window_group.border_windows.values()
    )
    assert toolbar.isVisible() is False

    assert selector.set_overlay_visible(True) is True
    assert selector._window_group._animation_timer.isActive() is True
    assert all(
        window.isVisible() for window in selector._window_group.windows.values()
    )
    assert all(
        window.isVisible()
        for window in selector._window_group.border_windows.values()
    )
    assert toolbar.isVisible() is True
    assert selector.geometry == geometry
    assert selector._interaction_mode is SelectionInteractionMode.MOVE_ONLY
    assert toolbar.confirm_action.isEnabled() is False
    assert len(hyprland.configured) == 2
    selector.close()


def test_overlay_restore_preserves_hidden_marching_ants(app) -> None:
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=FakeHyprland(),
        cursor_socket=FakeCursor(),
    )
    selector.start()

    assert selector.set_overlay_visible(False) is True
    assert selector.set_marching_ants_visible(False) is True
    assert selector.set_overlay_visible(True) is True
    assert selector._window_group._animation_timer.isActive() is False
    assert all(
        not window.isVisible()
        for window in selector._window_group.border_windows.values()
    )
    assert all(
        window.isVisible() for window in selector._window_group.windows.values()
    )
    assert selector._window_group.toolbar_window.isVisible() is True
    selector.close()


def test_overlay_visibility_restore_failure_is_reported(app) -> None:
    class FailingVisibilityHyprland(FakeHyprland):
        def configure_selection_windows(
            self, addresses, positions, dot_size, edges, toolbar_geometry=None
        ):
            if self.configured:
                raise HyprlandError("overlay restore failed")
            super().configure_selection_windows(
                addresses, positions, dot_size, edges, toolbar_geometry
            )

    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=FailingVisibilityHyprland(),
        cursor_socket=FakeCursor(),
    )
    errors = []
    selector.error.connect(errors.append)
    selector.start()

    assert selector.set_overlay_visible(False) is True
    assert selector.set_overlay_visible(True) is False
    assert errors == [
        "selection overlay windows were not restored within 2 seconds: "
        "overlay restore failed"
    ]
    assert all(
        not window.isVisible()
        for window in selector._window_group.windows.values()
    )
    assert all(
        not window.isVisible()
        for window in selector._window_group.border_windows.values()
    )
    assert selector._window_group.toolbar_window.isVisible() is False
    selector.close()


def test_marching_ants_visibility_failure_is_reported(app) -> None:
    class FailingVisibilityHyprland(FakeHyprland):
        def configure_selection_windows(
            self, addresses, positions, dot_size, edges, toolbar_geometry=None
        ):
            if self.configured:
                raise HyprlandError("edge restore failed")
            super().configure_selection_windows(
                addresses, positions, dot_size, edges, toolbar_geometry
            )

    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=FailingVisibilityHyprland(),
        cursor_socket=FakeCursor(),
    )
    errors = []
    selector.error.connect(errors.append)
    selector.start()

    assert selector.set_marching_ants_visible(False) is True
    assert selector.set_marching_ants_visible(True) is False
    assert errors == [
        "selection edge windows were not restored within 2 seconds: "
        "edge restore failed"
    ]
    assert selector._window_group._animation_timer.isActive() is False
    selector.close()


def test_capture_geometry_is_resolved_from_current_selection_windows(app) -> None:
    hyprland = FakeHyprland()
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=hyprland,
        cursor_socket=FakeCursor(),
    )
    selector.start()

    assert selector.resolve_capture_geometry() == Rect(102, 102, 296, 196)
    assert hyprland.capture_geometry_calls == [
        dict(selector._window_group._addresses)
    ]
    selector.close()


def test_capture_geometry_failure_is_reported_without_fallback(app) -> None:
    class FailingGeometryHyprland(FakeHyprland):
        def capture_geometry(self, _addresses):
            raise HyprlandError("edge geometry is inconsistent")

    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        auto_close_on_confirm=False,
        hyprland=FailingGeometryHyprland(),
        cursor_socket=FakeCursor(),
    )
    errors = []
    selector.error.connect(errors.append)
    selector.start()

    assert selector.resolve_capture_geometry() is None
    assert errors == ["edge geometry is inconsistent"]
    assert selector._window_group.toolbar_window is not None
    selector.close()


def test_toolbar_relayouts_when_action_text_changes(app) -> None:
    hyprland = FakeHyprland()
    changing_action = QAction("Pause")
    selector = RegionSelector(
        initial_rect=Rect(100, 100, 300, 200),
        toolbar_actions=(changing_action,),
        auto_close_on_confirm=False,
        hyprland=hyprland,
        cursor_socket=FakeCursor(),
    )
    selector.start()

    changing_action.setText("Resume recording")

    assert len(hyprland.moves) == 1
    assert selector._window_group.toolbar_window.buttons[1].text() == (
        "Resume recording"
    )
    selector.close()


def test_toolbar_size_excludes_hidden_actions(app) -> None:
    visible_action = QAction("Visible")
    hidden_action = QAction("A very wide hidden action")
    toolbar = ToolbarWindow("visibility-toolbar", (visible_action, hidden_action))
    full_width, _height = toolbar.action_size(DockSide.BOTTOM, False)

    hidden_action.setVisible(False)
    hidden_width, _height = toolbar.action_size(DockSide.BOTTOM, False)

    assert toolbar.buttons[2].isHidden() is True
    assert hidden_width < full_width
    toolbar.close_from_group()
