"""Public region-selection session facade."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum, auto

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon
from PyQt6.QtWidgets import QApplication

from .geometry import (
    CornerRole,
    DragMode,
    Monitor,
    Rect,
    centered_start_rect,
    fit_rect_to_monitor,
    normalize_rect,
)
from .hyprland import CursorSocket, HyprlandError, HyprlandIPC
from .interactions import DragController
from .windows import SelectionWindowGroup


class SelectionInteractionMode(Enum):
    FULL = auto()
    MOVE_ONLY = auto()
    DISABLED = auto()


class RegionSelector(QObject):
    geometry_changed = pyqtSignal(object)
    confirmed = pyqtSignal(object)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        initial_rect: Rect | None = None,
        dot_size: int = 24,
        dot_color: str = "#ff3b30",
        ants_width: int = 2,
        ants_interval_ms: int = 80,
        toolbar_actions: Sequence[QAction] = (),
        toolbar_gap: int = 8,
        confirm_text: str = "Confirm",
        confirm_icon: QIcon | None = None,
        auto_close_on_confirm: bool = True,
        *,
        hyprland: HyprlandIPC | None = None,
        cursor_socket: CursorSocket | None = None,
    ) -> None:
        super().__init__()
        if dot_size <= 0:
            raise ValueError("dot_size must be positive")
        if ants_width <= 0:
            raise ValueError("ants_width must be positive")
        if ants_interval_ms <= 0:
            raise ValueError("ants_interval_ms must be positive")
        if toolbar_gap < 0:
            raise ValueError("toolbar_gap must not be negative")
        if not confirm_text.strip():
            raise ValueError("confirm_text must not be empty")
        if confirm_icon is not None and not isinstance(confirm_icon, QIcon):
            raise TypeError("confirm_icon must be a QIcon")
        actions = tuple(toolbar_actions)
        if any(not isinstance(action, QAction) for action in actions):
            raise TypeError("toolbar_actions must contain QAction instances")
        color = QColor(dot_color)
        if not color.isValid():
            raise ValueError(f"invalid dot_color: {dot_color!r}")

        self._initial_rect = initial_rect
        self._hyprland = hyprland or HyprlandIPC()
        self._cursor_socket = cursor_socket
        self._geometry = normalize_rect(initial_rect or Rect(0, 0, 0, 0))
        self._monitor: Monitor | None = None
        self._active = False
        self._starting = False
        self._auto_close_on_confirm = auto_close_on_confirm
        self._interaction_mode = SelectionInteractionMode.FULL

        self._window_group = SelectionWindowGroup(
            self._hyprland,
            int(dot_size),
            color,
            int(ants_width),
            int(ants_interval_ms),
            actions,
            int(toolbar_gap),
            confirm_text,
            confirm_icon,
            self,
        )
        self._drag_controller = DragController(int(dot_size), self)
        self._window_group.ready.connect(self._on_windows_ready)
        self._window_group.error.connect(self._fail)
        self._window_group.drag_started.connect(self._begin_drag)
        self._window_group.drag_ended.connect(self._end_drag)
        self._window_group.confirm_requested.connect(self.confirm)
        self._window_group.cancel_requested.connect(self.cancel)
        self._drag_controller.geometry_changed.connect(self._apply_geometry)
        self._drag_controller.error.connect(self._fail)

    @property
    def geometry(self) -> Rect:
        return self._geometry

    def start(self) -> None:
        if self._active or self._starting:
            return
        if QApplication.instance() is None:
            self.error.emit("RegionSelector.start() requires an existing QApplication")
            return
        try:
            monitor = self._hyprland.focused_monitor()
            monitors = self._hyprland.monitors()
            if self._cursor_socket is None:
                self._cursor_socket = CursorSocket.from_environment()
            geometry = (
                fit_rect_to_monitor(self._initial_rect, monitor)
                if self._initial_rect is not None
                else centered_start_rect(monitor)
            )
        except (HyprlandError, OSError, ValueError) as exc:
            self._fail(str(exc))
            return

        self._monitor = monitor
        self._geometry = geometry
        self._drag_controller.configure(
            self._cursor_socket,
            geometry,
            monitor,
            monitors,
        )
        self._starting = True
        geometry = self._window_group.start(geometry, monitor)
        self._geometry = geometry
        if not self._active and not self._starting:
            return
        self._drag_controller.adopt_geometry(geometry, monitor)

    def confirm(self) -> None:
        if not self._active and not self._starting:
            return
        value = self._geometry
        if self._auto_close_on_confirm:
            self._shutdown()
        self.confirmed.emit(value)

    def set_interaction_enabled(self, enabled: bool) -> None:
        mode = (
            SelectionInteractionMode.FULL
            if enabled
            else SelectionInteractionMode.DISABLED
        )
        self.set_interaction_mode(mode)

    def set_interaction_mode(self, mode: SelectionInteractionMode) -> None:
        if not isinstance(mode, SelectionInteractionMode):
            raise TypeError("mode must be a SelectionInteractionMode")
        self._interaction_mode = mode
        self._window_group.set_interaction_enabled(
            mode is not SelectionInteractionMode.DISABLED
        )
        toolbar = self._window_group.toolbar_window
        if toolbar is not None:
            toolbar.set_selection_interaction_enabled(
                mode is SelectionInteractionMode.FULL
            )

    def set_marching_ants_visible(self, visible: bool) -> bool:
        try:
            self._window_group.set_marching_ants_visible(visible)
        except HyprlandError as exc:
            self.error.emit(str(exc))
            return False
        return True

    def set_overlay_visible(self, visible: bool) -> bool:
        try:
            self._window_group.set_overlay_visible(visible)
        except HyprlandError as exc:
            self.error.emit(str(exc))
            return False
        return True

    def resolve_capture_geometry(self) -> Rect | None:
        """Return the capture area confirmed by the compositor."""

        try:
            return self._window_group.resolve_capture_geometry()
        except HyprlandError as exc:
            self.error.emit(str(exc))
            return None

    def set_confirm_presentation(self, text: str, icon: QIcon) -> None:
        if not text.strip():
            raise ValueError("confirm action text must not be empty")
        self._window_group.set_confirm_presentation(text, icon)

    def set_confirm_enabled(self, enabled: bool) -> None:
        self._window_group.set_confirm_enabled(enabled)

    def set_cancel_visible(self, visible: bool) -> None:
        self._window_group.set_cancel_visible(visible)

    def close(self) -> None:
        self._shutdown()

    def cancel(self) -> None:
        if not self._active and not self._starting:
            return
        if self._interaction_mode is not SelectionInteractionMode.FULL:
            return
        self._shutdown()
        self.cancelled.emit()

    def _on_windows_ready(self) -> None:
        if not self._starting:
            return
        self._starting = False
        self._active = True
        self._geometry = self._window_group.geometry
        if self._monitor is not None:
            self._drag_controller.adopt_geometry(self._geometry, self._monitor)
        self.geometry_changed.emit(self._geometry)

    def _begin_drag(self, role: CornerRole, mode: DragMode) -> None:
        if (
            self._active
            and self._interaction_mode
            is not SelectionInteractionMode.DISABLED
        ):
            if self._interaction_mode is SelectionInteractionMode.MOVE_ONLY:
                mode = DragMode.MOVE
            self._drag_controller.begin(role, mode)

    def _end_drag(self, role: CornerRole) -> None:
        if self._active:
            self._drag_controller.end(role)

    def _apply_geometry(self, geometry: Rect, monitor: Monitor) -> None:
        if not self._active:
            return
        try:
            mode = self._drag_controller.drag_mode or DragMode.MOVE
            role = self._drag_controller.drag_role
            geometry = self._window_group.move(geometry, monitor, mode, role)
        except HyprlandError as exc:
            self._fail(str(exc))
            return
        self._geometry = geometry
        self._monitor = monitor
        self._drag_controller.adopt_geometry(geometry, monitor)
        self.geometry_changed.emit(self._geometry)

    def _shutdown(self) -> None:
        self._active = False
        self._starting = False
        self._interaction_mode = SelectionInteractionMode.FULL
        self._drag_controller.shutdown()
        self._window_group.close()
        self._monitor = None

    def _fail(self, message: str) -> None:
        self._shutdown()
        self.error.emit(message)
