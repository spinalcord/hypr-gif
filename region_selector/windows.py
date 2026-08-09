"""Qt windows and their Hyprland presentation lifecycle."""

from __future__ import annotations

import inspect
import os
import uuid
from collections.abc import Mapping, Sequence

from PyQt6.QtCore import QEventLoop, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
)
from PyQt6.QtWidgets import QBoxLayout, QStyle, QToolButton, QVBoxLayout, QWidget

from .geometry import (
    CORNER_ROLES,
    EDGE_ROLES,
    TOOLBAR_ROLE,
    CornerRole,
    DockSide,
    DragMode,
    EdgeRole,
    Monitor,
    Rect,
    ToolbarPlacement,
    dock_is_horizontal,
    dot_positions,
    edge_geometries,
    is_fullscreen,
    largest_outside_side,
    reserve_toolbar_space,
    toolbar_fits,
    toolbar_placement,
    toolbar_size_for_side,
)
from .hyprland import HyprlandError, HyprlandIPC


ANT_DASH_LENGTH = 4
DOT_OUTLINE_WIDTH = 2


def _accepts_positional_argument(callback: object, count: int) -> bool:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return True
    positional = 0
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= count


class DotWindow(QWidget):
    drag_started = pyqtSignal(object, object)
    drag_ended = pyqtSignal(object)
    confirm_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(
        self,
        role: CornerRole,
        title: str,
        size: int,
        color: QColor,
    ) -> None:
        super().__init__(None)
        self.role = role
        self._color = color
        self._closing = False
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def close_from_group(self) -> None:
        self._closing = True
        self.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            mode = (
                DragMode.RESIZE
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                else DragMode.MOVE
            )
            self.drag_started.emit(self.role, mode)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_ended.emit(self.role)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.confirm_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(event.rect(), Qt.GlobalColor.white)
        inset = min(DOT_OUTLINE_WIDTH, self.width() // 2, self.height() // 2)
        if inset > 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._color)
            painter.drawEllipse(self.rect().adjusted(inset, inset, -inset, -inset))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        event.ignore()
        QTimer.singleShot(0, self.cancel_requested.emit)


class BorderWindow(QWidget):
    def __init__(self, role: EdgeRole, title: str, geometry: Rect) -> None:
        super().__init__(None)
        self.role = role
        self._phase = 0
        self._closing = False
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resize_to_geometry(geometry)

    def resize_to_geometry(self, geometry: Rect) -> None:
        self.setFixedSize(geometry.width, geometry.height)

    def set_phase(self, phase: int) -> None:
        phase %= ANT_DASH_LENGTH * 2
        if self._phase == phase:
            return
        self._phase = phase
        self.update()

    def close_from_group(self) -> None:
        self._closing = True
        self.close()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(event.rect(), Qt.GlobalColor.white)
        period = ANT_DASH_LENGTH * 2
        start = self._phase - period
        if self.role in (EdgeRole.TOP, EdgeRole.BOTTOM):
            for x in range(start, self.width(), period):
                painter.fillRect(
                    x, 0, ANT_DASH_LENGTH, self.height(), Qt.GlobalColor.black
                )
        else:
            for y in range(start, self.height(), period):
                painter.fillRect(
                    0, y, self.width(), ANT_DASH_LENGTH, Qt.GlobalColor.black
                )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        event.ignore()


class ToolbarWindow(QWidget):
    confirm_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    layout_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        actions: Sequence[QAction],
        confirm_text: str = "Confirm",
        confirm_icon: QIcon | None = None,
    ) -> None:
        super().__init__(None)
        self._closing = False
        self._side = DockSide.BOTTOM
        self._compact = False
        self._fullscreen = False
        self._collapsed = False
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.confirm_action = QAction(
            confirm_icon
            if confirm_icon is not None
            else self.style().standardIcon(
                QStyle.StandardPixmap.SP_DialogApplyButton
            ),
            confirm_text,
            self,
        )
        self.cancel_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton),
            "Cancel",
            self,
        )
        self.confirm_action.triggered.connect(self.confirm_requested.emit)
        self.cancel_action.triggered.connect(self.cancel_requested.emit)
        self._actions = (self.confirm_action, *actions, self.cancel_action)

        self._action_widget = QWidget(self)
        self._action_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._action_layout.setContentsMargins(4, 4, 4, 4)
        self._action_layout.setSpacing(4)
        self._action_widget.setLayout(self._action_layout)
        self._buttons: list[QToolButton] = []
        for action in self._actions:
            button = QToolButton(self._action_widget)
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            self._action_layout.addWidget(button)
            self._buttons.append(button)
            action.changed.connect(self.layout_requested.emit)

        self.handle_button = QToolButton(self)
        self.handle_button.setAutoRaise(True)
        self.handle_button.setArrowType(Qt.ArrowType.UpArrow)
        self.handle_button.setToolTip("Show toolbar")
        self.handle_button.clicked.connect(self._toggle_fullscreen)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        self._main_layout.addWidget(self._action_widget)
        self._main_layout.addWidget(
            self.handle_button, 0, Qt.AlignmentFlag.AlignHCenter
        )
        self.configure(DockSide.BOTTOM, False, False, False)

    @property
    def actions(self) -> tuple[QAction, ...]:
        return self._actions

    @property
    def buttons(self) -> tuple[QToolButton, ...]:
        return tuple(self._buttons)

    @property
    def side(self) -> DockSide:
        return self._side

    @property
    def compact(self) -> bool:
        return self._compact

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def action_size(self, side: DockSide, compact: bool) -> tuple[int, int]:
        self._set_action_presentation(side, compact)
        hint = self._action_widget.sizeHint()
        return hint.width(), hint.height()

    def handle_size(self) -> tuple[int, int]:
        hint = self.handle_button.sizeHint()
        return hint.width(), hint.height()

    def fullscreen_size(self, compact: bool, collapsed: bool) -> tuple[int, int]:
        handle_width, handle_height = self.handle_size()
        if collapsed:
            return handle_width, handle_height
        action_width, action_height = self.action_size(DockSide.BOTTOM, compact)
        return max(action_width, handle_width), action_height + handle_height

    def configure(
        self,
        side: DockSide,
        compact: bool,
        fullscreen: bool,
        collapsed: bool,
    ) -> None:
        self._side = side
        self._compact = compact
        self._fullscreen = fullscreen
        self._collapsed = fullscreen and collapsed
        self._set_action_presentation(side, compact)
        self._action_widget.setVisible(not self._collapsed)
        self.handle_button.setVisible(fullscreen)
        self.handle_button.setArrowType(
            Qt.ArrowType.UpArrow if self._collapsed else Qt.ArrowType.DownArrow
        )
        self.handle_button.setToolTip(
            "Show toolbar" if self._collapsed else "Hide toolbar"
        )
        self._main_layout.invalidate()

    def resize_to_geometry(self, geometry: Rect) -> None:
        self.setFixedSize(geometry.width, geometry.height)

    def close_from_group(self) -> None:
        self._closing = True
        self.close()

    def set_selection_interaction_enabled(self, enabled: bool) -> None:
        self.cancel_action.setEnabled(enabled)

    def set_confirm_presentation(self, text: str, icon: QIcon) -> None:
        self.confirm_action.setText(text)
        self.confirm_action.setIcon(icon)

    def set_confirm_enabled(self, enabled: bool) -> None:
        self.confirm_action.setEnabled(enabled)

    def set_cancel_visible(self, visible: bool) -> None:
        self.cancel_action.setVisible(visible)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        event.ignore()
        QTimer.singleShot(0, self.cancel_requested.emit)

    def _set_action_presentation(self, side: DockSide, compact: bool) -> None:
        direction = (
            QBoxLayout.Direction.LeftToRight
            if dock_is_horizontal(side)
            else QBoxLayout.Direction.TopToBottom
        )
        self._action_layout.setDirection(direction)
        for action, button in zip(self._actions, self._buttons, strict=True):
            button.setVisible(action.isVisible())
            if compact and not action.icon().isNull():
                style = Qt.ToolButtonStyle.ToolButtonIconOnly
            elif action.icon().isNull():
                style = Qt.ToolButtonStyle.ToolButtonTextOnly
            else:
                style = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            button.setToolButtonStyle(style)
        self._action_layout.invalidate()

    def _toggle_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._collapsed = not self._collapsed
        self.layout_requested.emit()


class SelectionWindowGroup(QObject):
    ready = pyqtSignal()
    error = pyqtSignal(str)
    drag_started = pyqtSignal(object, object)
    drag_ended = pyqtSignal(object)
    confirm_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(
        self,
        hyprland: HyprlandIPC,
        dot_size: int,
        color: QColor,
        ants_width: int,
        ants_interval_ms: int,
        toolbar_actions: Sequence[QAction] = (),
        toolbar_gap: int = 8,
        confirm_text: str = "Confirm",
        confirm_icon: QIcon | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._hyprland = hyprland
        self._dot_size = dot_size
        self._color = color
        self._ants_width = ants_width
        self._toolbar_actions = tuple(toolbar_actions)
        self._toolbar_gap = toolbar_gap
        self._confirm_text = confirm_text
        self._confirm_icon = confirm_icon
        self._windows: dict[CornerRole, DotWindow] = {}
        self._border_windows: dict[EdgeRole, BorderWindow] = {}
        self._toolbar_window: ToolbarWindow | None = None
        self._toolbar_placement: ToolbarPlacement | None = None
        self._addresses: dict[str, str] = {}
        self._titles: dict[str, str] = {}
        self._geometry = Rect(0, 0, 0, 0)
        self._starting = False
        self._active = False
        self._monitor: Monitor | None = None
        self._fullscreen = False
        self._discovery_attempts = 0

        self._discovery_timer = QTimer(self)
        self._discovery_timer.setInterval(25)
        self._discovery_timer.timeout.connect(self._discover_windows)

        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(ants_interval_ms)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._animation_phase = 0
        self._ants_visible = True

    @property
    def windows(self) -> Mapping[CornerRole, DotWindow]:
        return self._windows

    @property
    def border_windows(self) -> Mapping[EdgeRole, BorderWindow]:
        return self._border_windows

    @property
    def geometry(self) -> Rect:
        return self._geometry

    @property
    def toolbar_window(self) -> ToolbarWindow | None:
        return self._toolbar_window

    @property
    def toolbar_placement(self) -> ToolbarPlacement | None:
        return self._toolbar_placement

    def start(self, geometry: Rect, monitor: Monitor | None = None) -> Rect:
        if self._starting or self._active:
            return self._geometry
        if monitor is None:
            monitor = Monitor(geometry.x, geometry.y, geometry.width, geometry.height)
        self._geometry = geometry
        self._monitor = monitor
        token = uuid.uuid4().hex
        pid = os.getpid()
        self._titles = {
            role: f"WaylandGifRegionDot-{pid}-{token}-{role}"
            for role in CORNER_ROLES
        } | {
            role: f"WaylandGifRegionBorder-{pid}-{token}-{role}"
            for role in EDGE_ROLES
        }
        self._titles[TOOLBAR_ROLE] = f"WaylandGifRegionToolbar-{pid}-{token}"
        self._windows = {
            role: DotWindow(
                role,
                self._titles[role.value],
                self._dot_size,
                self._color,
            )
            for role in CornerRole
        }
        edges = edge_geometries(geometry, self._ants_width)
        self._border_windows = {
            role: BorderWindow(role, self._titles[role.value], edges[role.value])
            for role in EdgeRole
        }
        self._toolbar_window = ToolbarWindow(
            self._titles[TOOLBAR_ROLE],
            self._toolbar_actions,
            self._confirm_text,
            self._confirm_icon,
        )
        geometry = self._layout_toolbar(geometry, monitor, DragMode.MOVE, None)
        self._geometry = geometry
        edges = edge_geometries(geometry, self._ants_width)
        for role, window in self._border_windows.items():
            window.resize_to_geometry(edges[role.value])
        for window in self._windows.values():
            window.drag_started.connect(self.drag_started.emit)
            window.drag_ended.connect(self.drag_ended.emit)
            window.confirm_requested.connect(self.confirm_requested.emit)
            window.cancel_requested.connect(self.cancel_requested.emit)
        self._toolbar_window.confirm_requested.connect(self.confirm_requested.emit)
        self._toolbar_window.cancel_requested.connect(self.cancel_requested.emit)
        self._toolbar_window.layout_requested.connect(self._relayout_toolbar)

        self._starting = True
        self._discovery_attempts = 0
        self._animation_phase = 0
        self._ants_visible = True
        for window in self._border_windows.values():
            window.show()
        for window in self._windows.values():
            window.show()
        self._toolbar_window.show()
        self._discovery_timer.start()
        self._discover_windows()
        return geometry

    def move(
        self,
        geometry: Rect,
        monitor: Monitor | None = None,
        mode: DragMode = DragMode.MOVE,
        role: CornerRole | None = None,
    ) -> Rect:
        if not self._active:
            return geometry
        monitor = monitor or self._monitor
        if monitor is None:
            return geometry
        geometry = self._layout_toolbar(geometry, monitor, mode, role)
        edges = edge_geometries(geometry, self._ants_width)
        for role, window in self._border_windows.items():
            window.resize_to_geometry(edges[role.value])
        self._update_selection_windows(
            dot_positions(geometry, self._dot_size),
            edges,
            monitor.name if monitor != self._monitor else None,
        )
        self._geometry = geometry
        self._monitor = monitor
        return geometry

    def close(self) -> None:
        self._active = False
        self._starting = False
        self._discovery_timer.stop()
        self._animation_timer.stop()
        for window in self._windows.values():
            window.close_from_group()
            window.deleteLater()
        for window in self._border_windows.values():
            window.close_from_group()
            window.deleteLater()
        if self._toolbar_window is not None:
            self._toolbar_window.close_from_group()
            self._toolbar_window.deleteLater()
        self._windows.clear()
        self._border_windows.clear()
        self._toolbar_window = None
        self._toolbar_placement = None
        self._monitor = None
        self._fullscreen = False
        self._ants_visible = True
        self._addresses.clear()
        self._titles.clear()

    def set_interaction_enabled(self, enabled: bool) -> None:
        for window in self._windows.values():
            window.setEnabled(enabled)
        if self._toolbar_window is not None:
            self._toolbar_window.set_selection_interaction_enabled(enabled)

    def set_confirm_presentation(self, text: str, icon: QIcon) -> None:
        if self._toolbar_window is None:
            return
        self._toolbar_window.set_confirm_presentation(text, icon)

    def set_confirm_enabled(self, enabled: bool) -> None:
        if self._toolbar_window is not None:
            self._toolbar_window.set_confirm_enabled(enabled)

    def set_cancel_visible(self, visible: bool) -> None:
        if self._toolbar_window is not None:
            self._toolbar_window.set_cancel_visible(visible)

    def set_marching_ants_visible(self, visible: bool) -> None:
        if not self._active:
            raise HyprlandError("selection windows are not ready")
        if visible == self._ants_visible:
            return
        if not visible:
            self._animation_timer.stop()
            for window in self._border_windows.values():
                window.hide()
            self._ants_visible = False
            return
        self._restore_selection_edges()
        self._ants_visible = True
        self._animation_timer.start()

    def _restore_selection_edges(self) -> None:
        for window in self._border_windows.values():
            window.show()

        loop = QEventLoop(self)
        timer = QTimer(loop)
        timer.setInterval(25)
        attempts = 0
        finished = False
        restored = False
        failure: HyprlandError | None = None

        def discover() -> None:
            nonlocal attempts, failure, finished, restored
            attempts += 1
            try:
                addresses = self._hyprland.find_selection_windows(
                    os.getpid(), self._titles
                )
            except HyprlandError as exc:
                failure = exc
                if attempts < 80:
                    return
                finished = True
                timer.stop()
                loop.quit()
                return
            try:
                self._configure_selection_windows(
                    addresses,
                    dot_positions(self._geometry, self._dot_size),
                    edge_geometries(self._geometry, self._ants_width),
                    self._monitor.name if self._monitor is not None else "",
                )
            except HyprlandError as exc:
                failure = exc
                finished = True
                timer.stop()
                loop.quit()
            else:
                self._addresses = addresses
                finished = True
                restored = True
                timer.stop()
                loop.quit()

        timer.timeout.connect(discover)
        timer.start()
        discover()
        if not finished:
            loop.exec()
        if restored:
            return
        for window in self._border_windows.values():
            window.hide()
        raise HyprlandError(
            "selection edge windows were not restored within 2 seconds: "
            f"{failure}"
        )

    def resolve_capture_geometry(self) -> Rect:
        if not self._active:
            raise HyprlandError("selection windows are not ready")
        return self._hyprland.capture_geometry(self._addresses)

    def _discover_windows(self) -> None:
        if not self._starting:
            return
        self._discovery_attempts += 1
        try:
            addresses = self._hyprland.find_selection_windows(
                os.getpid(), self._titles
            )
        except HyprlandError as exc:
            if self._discovery_attempts < 80:
                return
            self._fail(f"selection windows were not found within 2 seconds: {exc}")
            return
        try:
            self._configure_selection_windows(
                addresses,
                dot_positions(self._geometry, self._dot_size),
                edge_geometries(self._geometry, self._ants_width),
                self._monitor.name if self._monitor is not None else "",
            )
        except HyprlandError as exc:
            self._fail(str(exc))
            return
        self._addresses = addresses
        self._discovery_timer.stop()
        self._starting = False
        self._active = True
        self._animation_timer.start()
        self.ready.emit()

    def _layout_toolbar(
        self,
        geometry: Rect,
        monitor: Monitor,
        mode: DragMode,
        role: CornerRole | None,
    ) -> Rect:
        toolbar = self._toolbar_window
        if toolbar is None:
            return geometry
        fullscreen = is_fullscreen(geometry, monitor)
        if fullscreen and (not self._fullscreen or monitor != self._monitor):
            collapsed = True
        elif fullscreen:
            collapsed = toolbar.collapsed
        else:
            collapsed = False

        full_horizontal = toolbar.action_size(DockSide.BOTTOM, False)
        full_vertical = toolbar.action_size(DockSide.LEFT, False)
        compact_horizontal = toolbar.action_size(DockSide.BOTTOM, True)
        compact_vertical = toolbar.action_size(DockSide.LEFT, True)
        handle_size = toolbar.handle_size()
        placement = toolbar_placement(
            geometry,
            monitor,
            full_horizontal,
            full_vertical,
            compact_horizontal,
            compact_vertical,
            self._toolbar_gap,
            fullscreen_collapsed=collapsed,
            handle_size=handle_size,
        )
        if fullscreen:
            size = toolbar.fullscreen_size(placement.compact, collapsed)
            placement = ToolbarPlacement(
                DockSide.BOTTOM,
                toolbar_placement(
                    geometry,
                    monitor,
                    size,
                    full_vertical,
                    size,
                    compact_vertical,
                    0,
                    fullscreen_collapsed=False,
                ).geometry,
                placement.compact,
                True,
                collapsed,
            )
        else:
            compact_size = toolbar_size_for_side(
                placement.side, compact_horizontal, compact_vertical
            )
            if placement.compact and not toolbar_fits(
                geometry,
                monitor,
                placement.side,
                compact_size,
                self._toolbar_gap,
            ):
                side = placement.side
                if mode is DragMode.RESIZE and role is not None:
                    movable_sides = {
                        CornerRole.TOP_LEFT: (DockSide.TOP, DockSide.LEFT),
                        CornerRole.TOP_RIGHT: (DockSide.TOP, DockSide.RIGHT),
                        CornerRole.BOTTOM_LEFT: (DockSide.BOTTOM, DockSide.LEFT),
                        CornerRole.BOTTOM_RIGHT: (DockSide.BOTTOM, DockSide.RIGHT),
                    }[role]
                    if side not in movable_sides:
                        side = largest_outside_side(
                            geometry, monitor, movable_sides
                        )
                        compact_size = toolbar_size_for_side(
                            side, compact_horizontal, compact_vertical
                        )
                geometry = reserve_toolbar_space(
                    geometry,
                    monitor,
                    side,
                    compact_size,
                    self._toolbar_gap,
                    mode=mode,
                    role=role,
                )
                placement = toolbar_placement(
                    geometry,
                    monitor,
                    full_horizontal,
                    full_vertical,
                    compact_horizontal,
                    compact_vertical,
                    self._toolbar_gap,
                )

        toolbar.configure(
            placement.side,
            placement.compact,
            placement.fullscreen,
            placement.collapsed,
        )
        toolbar.resize_to_geometry(placement.geometry)
        self._toolbar_placement = placement
        self._fullscreen = fullscreen
        return geometry

    def _relayout_toolbar(self) -> None:
        toolbar = self._toolbar_window
        monitor = self._monitor
        if toolbar is None or monitor is None:
            return
        self._layout_toolbar(self._geometry, monitor, DragMode.MOVE, None)
        if not self._active:
            return
        try:
            self._update_selection_windows(
                dot_positions(self._geometry, self._dot_size),
                edge_geometries(self._geometry, self._ants_width),
            )
        except HyprlandError as exc:
            self._fail(str(exc))

    def _configure_selection_windows(
        self,
        addresses: Mapping[str, str],
        positions: Mapping[str, tuple[int, int]],
        edges: Mapping[str, Rect],
        monitor: str,
    ) -> None:
        callback = self._hyprland.configure_selection_windows
        toolbar_geometry = self._toolbar_placement.geometry
        if _accepts_positional_argument(callback, 6):
            callback(
                addresses,
                positions,
                self._dot_size,
                edges,
                toolbar_geometry,
                monitor,
            )
            return
        if _accepts_positional_argument(callback, 5):
            callback(addresses, positions, self._dot_size, edges, toolbar_geometry)
            return
        callback(
            addresses,
            positions,
            self._dot_size,
            dict(edges) | {TOOLBAR_ROLE: toolbar_geometry},
        )

    def _update_selection_windows(
        self,
        positions: Mapping[str, tuple[int, int]],
        edges: Mapping[str, Rect],
        monitor: str | None = None,
    ) -> None:
        callback = self._hyprland.update_selection_windows
        toolbar_geometry = self._toolbar_placement.geometry
        if _accepts_positional_argument(callback, 5):
            callback(
                self._addresses,
                positions,
                edges,
                toolbar_geometry,
                monitor,
            )
            return
        if _accepts_positional_argument(callback, 4):
            callback(self._addresses, positions, edges, toolbar_geometry)
            return
        callback(
            self._addresses,
            positions,
            dict(edges) | {TOOLBAR_ROLE: toolbar_geometry},
        )

    def _advance_animation(self) -> None:
        self._animation_phase = (self._animation_phase + 1) % (
            ANT_DASH_LENGTH * 2
        )
        for window in self._border_windows.values():
            window.set_phase(self._animation_phase)

    def _fail(self, message: str) -> None:
        self.close()
        self.error.emit(message)
