"""Drag interaction state and cursor polling."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal

from .geometry import (
    CornerRole,
    DragMode,
    Monitor,
    Rect,
    drag_corner,
    monitor_at_position,
    move_rect,
)
from .hyprland import CursorSocket, HyprlandError


class DragController(QObject):
    geometry_changed = pyqtSignal(object, object)
    error = pyqtSignal(str)

    def __init__(self, minimum_size: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._minimum_size = minimum_size
        self._cursor_socket: CursorSocket | None = None
        self._geometry = Rect(0, 0, 0, 0)
        self._monitor: Monitor | None = None
        self._monitors: tuple[Monitor, ...] = ()
        self._drag_role: CornerRole | None = None
        self._drag_mode: DragMode | None = None
        self._origin_cursor: tuple[int, int] | None = None
        self._origin_geometry: Rect | None = None
        self._configured = False

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(1000 // 60)
        self._timer.timeout.connect(self.poll)

    @property
    def drag_role(self) -> CornerRole | None:
        return self._drag_role

    @property
    def drag_mode(self) -> DragMode | None:
        return self._drag_mode

    def configure(
        self,
        cursor_socket: CursorSocket,
        geometry: Rect,
        monitor: Monitor,
        monitors: tuple[Monitor, ...],
    ) -> None:
        self.shutdown()
        self._cursor_socket = cursor_socket
        self._geometry = geometry
        self._monitor = monitor
        self._monitors = monitors
        self._configured = True

    def begin(self, role: CornerRole, mode: DragMode) -> None:
        if not self._configured or self._drag_role is not None:
            return
        cursor = self._read_cursor()
        if cursor is None:
            return
        self._drag_role = role
        self._drag_mode = mode
        self._origin_cursor = cursor
        self._origin_geometry = self._geometry
        if mode is DragMode.RESIZE:
            self.update(cursor)
        if self._configured:
            self._timer.start()

    def end(self, role: CornerRole) -> None:
        if role is not self._drag_role:
            return
        self.poll()
        self._reset_drag()

    def poll(self) -> None:
        if not self._configured or self._drag_role is None:
            return
        cursor = self._read_cursor()
        if cursor is not None:
            self.update(cursor)

    def update(self, cursor: tuple[int, int]) -> None:
        if (
            not self._configured
            or self._drag_role is None
            or self._drag_mode is None
            or self._monitor is None
        ):
            return
        if self._drag_mode is DragMode.RESIZE:
            new_geometry, _ = drag_corner(
                self._geometry,
                self._drag_role.value,
                cursor[0],
                cursor[1],
                self._monitor,
                self._minimum_size,
            )
        else:
            if self._origin_cursor is None or self._origin_geometry is None:
                return
            target_monitor = monitor_at_position(
                self._monitors,
                cursor[0],
                cursor[1],
            )
            if target_monitor is None:
                target_monitor = self._monitor
            new_geometry = move_rect(
                self._origin_geometry,
                self._origin_cursor,
                cursor,
                target_monitor,
            )
            self._monitor = target_monitor
        if new_geometry == self._geometry:
            return
        self._geometry = new_geometry
        self.geometry_changed.emit(self._geometry, self._monitor)

    def shutdown(self) -> None:
        self._configured = False
        self._timer.stop()
        self._reset_drag()
        self._cursor_socket = None
        self._monitors = ()
        self._monitor = None

    def adopt_geometry(self, geometry: Rect, monitor: Monitor) -> None:
        """Synchronize geometry after an external interaction constraint."""

        self._geometry = geometry
        self._monitor = monitor

    def _read_cursor(self) -> tuple[int, int] | None:
        if self._cursor_socket is None:
            return None
        try:
            return self._cursor_socket.position()
        except HyprlandError as exc:
            self.shutdown()
            self.error.emit(str(exc))
            return None

    def _reset_drag(self) -> None:
        self._timer.stop()
        self._drag_role = None
        self._drag_mode = None
        self._origin_cursor = None
        self._origin_geometry = None
