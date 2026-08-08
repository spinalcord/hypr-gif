from region_selector import CornerRole, HyprlandError, Monitor, Rect
from region_selector.geometry import DragMode
from region_selector.interactions import DragController


class FakeCursor:
    def __init__(self, *positions) -> None:
        self.positions = iter(positions)

    def position(self):
        return next(self.positions)


class FailingCursor:
    def position(self):
        raise HyprlandError("cursor unavailable")


def test_drag_controller_emits_resized_geometry() -> None:
    monitor = Monitor(0, 0, 1000, 800)
    controller = DragController(24)
    changes = []
    controller.geometry_changed.connect(
        lambda geometry, active_monitor: changes.append((geometry, active_monitor))
    )
    controller.configure(
        FakeCursor((650, 150)),
        Rect(200, 200, 300, 200),
        monitor,
        (monitor,),
    )

    controller.begin(CornerRole.TOP_LEFT, DragMode.RESIZE)

    assert changes == [(Rect(476, 150, 24, 250), monitor)]
    controller.shutdown()


def test_drag_controller_reports_cursor_errors_and_stops() -> None:
    monitor = Monitor(0, 0, 1000, 800)
    controller = DragController(24)
    errors = []
    changes = []
    controller.error.connect(errors.append)
    controller.geometry_changed.connect(lambda *args: changes.append(args))
    controller.configure(
        FailingCursor(),
        Rect(200, 200, 300, 200),
        monitor,
        (monitor,),
    )

    controller.begin(CornerRole.BOTTOM_RIGHT, DragMode.MOVE)
    controller.update((700, 600))

    assert errors == ["cursor unavailable"]
    assert changes == []
