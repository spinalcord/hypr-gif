"""Geometry types and operations for region selection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final


class CornerRole(str, Enum):
    TOP_LEFT = "tl"
    TOP_RIGHT = "tr"
    BOTTOM_LEFT = "bl"
    BOTTOM_RIGHT = "br"


class EdgeRole(str, Enum):
    TOP = "top"
    RIGHT = "right"
    BOTTOM = "bottom"
    LEFT = "left"


class DragMode(Enum):
    MOVE = auto()
    RESIZE = auto()


class DockSide(str, Enum):
    BOTTOM = "bottom"
    TOP = "top"
    LEFT = "left"
    RIGHT = "right"


CORNER_ROLES: Final[tuple[str, ...]] = tuple(role.value for role in CornerRole)
EDGE_ROLES: Final[tuple[str, ...]] = tuple(role.value for role in EdgeRole)
TOOLBAR_ROLE: Final[str] = "toolbar"
DOCK_SIDES: Final[tuple[DockSide, ...]] = (
    DockSide.BOTTOM,
    DockSide.TOP,
    DockSide.LEFT,
    DockSide.RIGHT,
)


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class Monitor:
    x: int
    y: int
    width: int
    height: int
    name: str = ""

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class ToolbarPlacement:
    side: DockSide
    geometry: Rect
    compact: bool
    fullscreen: bool = False
    collapsed: bool = False


def is_fullscreen(rect: Rect, monitor: Monitor) -> bool:
    """Return whether *rect* exactly covers *monitor*."""

    rect = normalize_rect(rect)
    return (
        rect.x == monitor.x
        and rect.y == monitor.y
        and rect.width == monitor.width
        and rect.height == monitor.height
    )


def dock_is_horizontal(side: DockSide) -> bool:
    """Return whether actions docked on *side* use a horizontal layout."""

    return side in (DockSide.BOTTOM, DockSide.TOP)


def outside_space(rect: Rect, monitor: Monitor, side: DockSide) -> int:
    """Return selection-to-monitor space on one side in logical pixels."""

    rect = normalize_rect(rect)
    spaces = {
        DockSide.BOTTOM: monitor.bottom - rect.bottom,
        DockSide.TOP: rect.y - monitor.y,
        DockSide.LEFT: rect.x - monitor.x,
        DockSide.RIGHT: monitor.right - rect.right,
    }
    return max(spaces[side], 0)


def toolbar_size_for_side(
    side: DockSide,
    horizontal_size: tuple[int, int],
    vertical_size: tuple[int, int],
) -> tuple[int, int]:
    """Select the measured toolbar size matching one dock orientation."""

    return horizontal_size if dock_is_horizontal(side) else vertical_size


def toolbar_fits(
    rect: Rect,
    monitor: Monitor,
    side: DockSide,
    size: tuple[int, int],
    gap: int,
) -> bool:
    """Return whether a toolbar fits outside the selection on *side*."""

    if gap < 0:
        raise ValueError("toolbar gap must not be negative")
    width, height = size
    if width < 0 or height < 0:
        raise ValueError("toolbar dimensions must not be negative")
    if dock_is_horizontal(side):
        return (
            width <= monitor.width
            and outside_space(rect, monitor, side) >= height + gap
        )
    return (
        height <= monitor.height
        and outside_space(rect, monitor, side) >= width + gap
    )


def choose_dock_side(
    rect: Rect,
    monitor: Monitor,
    horizontal_size: tuple[int, int],
    vertical_size: tuple[int, int],
    gap: int = 8,
) -> DockSide | None:
    """Choose the first side that fits, using the documented dock order."""

    for side in DOCK_SIDES:
        size = toolbar_size_for_side(side, horizontal_size, vertical_size)
        if toolbar_fits(rect, monitor, side, size, gap):
            return side
    return None


def largest_outside_side(
    rect: Rect,
    monitor: Monitor,
    candidates: Sequence[DockSide] = DOCK_SIDES,
) -> DockSide:
    """Choose the side with most space, preserving dock order on a tie."""

    if not candidates:
        raise ValueError("at least one dock side is required")
    return max(candidates, key=lambda side: outside_space(rect, monitor, side))


def place_toolbar(
    rect: Rect,
    monitor: Monitor,
    side: DockSide,
    size: tuple[int, int],
    gap: int = 8,
    *,
    inside: bool = False,
) -> Rect:
    """Place and clamp a measured toolbar on its owning monitor."""

    if gap < 0:
        raise ValueError("toolbar gap must not be negative")
    width = min(max(int(size[0]), 0), monitor.width)
    height = min(max(int(size[1]), 0), monitor.height)
    rect = normalize_rect(rect)
    if side in (DockSide.BOTTOM, DockSide.TOP):
        x = rect.x + (rect.width - width) // 2
        x = min(max(x, monitor.x), monitor.right - width)
        if side is DockSide.BOTTOM:
            y = rect.bottom - height if inside else rect.bottom + gap
        else:
            y = rect.y if inside else rect.y - gap - height
    else:
        y = rect.y + (rect.height - height) // 2
        y = min(max(y, monitor.y), monitor.bottom - height)
        if side is DockSide.RIGHT:
            x = rect.right - width if inside else rect.right + gap
        else:
            x = rect.x if inside else rect.x - gap - width
    x = min(max(x, monitor.x), monitor.right - width)
    y = min(max(y, monitor.y), monitor.bottom - height)
    return Rect(x, y, width, height)


def toolbar_placement(
    rect: Rect,
    monitor: Monitor,
    full_horizontal_size: tuple[int, int],
    full_vertical_size: tuple[int, int],
    compact_horizontal_size: tuple[int, int],
    compact_vertical_size: tuple[int, int],
    gap: int = 8,
    *,
    fullscreen_collapsed: bool = True,
    handle_size: tuple[int, int] = (0, 0),
) -> ToolbarPlacement:
    """Compute dock side, presentation mode, and monitor-local geometry."""

    if is_fullscreen(rect, monitor):
        compact = full_horizontal_size[0] > monitor.width
        size = handle_size if fullscreen_collapsed else (
            compact_horizontal_size if compact else full_horizontal_size
        )
        return ToolbarPlacement(
            DockSide.BOTTOM,
            place_toolbar(rect, monitor, DockSide.BOTTOM, size, 0, inside=True),
            compact,
            True,
            fullscreen_collapsed,
        )

    side = choose_dock_side(
        rect, monitor, full_horizontal_size, full_vertical_size, gap
    )
    compact = side is None
    if side is None:
        side = largest_outside_side(rect, monitor)
    size = toolbar_size_for_side(
        side,
        compact_horizontal_size if compact else full_horizontal_size,
        compact_vertical_size if compact else full_vertical_size,
    )
    return ToolbarPlacement(
        side,
        place_toolbar(rect, monitor, side, size, gap),
        compact,
    )


def reserve_toolbar_space(
    rect: Rect,
    monitor: Monitor,
    side: DockSide,
    size: tuple[int, int],
    gap: int = 8,
    *,
    mode: DragMode = DragMode.MOVE,
    role: CornerRole | None = None,
) -> Rect:
    """Make the minimum selection adjustment needed for an outside toolbar.

    Moves preserve selection size whenever the monitor permits it. Resizes only
    constrain the moving edge; their opposite anchor remains fixed.
    """

    rect = fit_rect_to_monitor(rect, monitor)
    if is_fullscreen(rect, monitor) or toolbar_fits(rect, monitor, side, size, gap):
        return rect
    required = (size[1] if dock_is_horizontal(side) else size[0]) + gap
    if mode is DragMode.RESIZE:
        if role is None:
            raise ValueError("a corner role is required for resize reservation")
        return _reserve_resize_edge(rect, monitor, side, required, role)
    return _reserve_move_space(rect, monitor, side, required)


def _reserve_move_space(
    rect: Rect, monitor: Monitor, side: DockSide, required: int
) -> Rect:
    if side is DockSide.BOTTOM:
        available = max(monitor.height - required, 0)
        height = min(rect.height, available)
        y = min(rect.y, monitor.bottom - required - height)
        return Rect(rect.x, max(y, monitor.y), rect.width, height)
    if side is DockSide.TOP:
        available = max(monitor.height - required, 0)
        height = min(rect.height, available)
        y = max(rect.y, monitor.y + required)
        y = min(y, monitor.bottom - height)
        return Rect(rect.x, y, rect.width, height)
    if side is DockSide.RIGHT:
        available = max(monitor.width - required, 0)
        width = min(rect.width, available)
        x = min(rect.x, monitor.right - required - width)
        return Rect(max(x, monitor.x), rect.y, width, rect.height)
    available = max(monitor.width - required, 0)
    width = min(rect.width, available)
    x = max(rect.x, monitor.x + required)
    x = min(x, monitor.right - width)
    return Rect(x, rect.y, width, rect.height)


def _reserve_resize_edge(
    rect: Rect,
    monitor: Monitor,
    side: DockSide,
    required: int,
    role: CornerRole,
) -> Rect:
    moving_left = role in (CornerRole.TOP_LEFT, CornerRole.BOTTOM_LEFT)
    moving_top = role in (CornerRole.TOP_LEFT, CornerRole.TOP_RIGHT)
    if side is DockSide.BOTTOM and not moving_top:
        bottom = min(rect.bottom, monitor.bottom - required)
        return Rect(rect.x, rect.y, rect.width, max(bottom - rect.y, 0))
    if side is DockSide.TOP and moving_top:
        y = max(rect.y, monitor.y + required)
        return Rect(rect.x, min(y, rect.bottom), rect.width, max(rect.bottom - y, 0))
    if side is DockSide.RIGHT and not moving_left:
        right = min(rect.right, monitor.right - required)
        return Rect(rect.x, rect.y, max(right - rect.x, 0), rect.height)
    if side is DockSide.LEFT and moving_left:
        x = max(rect.x, monitor.x + required)
        return Rect(min(x, rect.right), rect.y, max(rect.right - x, 0), rect.height)
    return rect


def normalize_rect(rect: Rect) -> Rect:
    """Return *rect* with a top-left origin and non-negative dimensions."""

    x1, x2 = rect.x, rect.x + rect.width
    y1, y2 = rect.y, rect.y + rect.height
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return Rect(left, top, right - left, bottom - top)


def fit_rect_to_monitor(rect: Rect, monitor: Monitor) -> Rect:
    """Normalize and reposition/resize a rectangle so it fits one monitor."""

    if monitor.width < 0 or monitor.height < 0:
        raise ValueError("monitor dimensions must not be negative")
    rect = normalize_rect(rect)
    width = min(rect.width, monitor.width)
    height = min(rect.height, monitor.height)
    x = min(max(rect.x, monitor.x), monitor.right - width)
    y = min(max(rect.y, monitor.y), monitor.bottom - height)
    return Rect(x, y, width, height)


def monitor_at_position(
    monitors: Sequence[Monitor], x: int, y: int
) -> Monitor | None:
    """Return the monitor containing a global logical position."""

    for monitor in monitors:
        if monitor.x <= x < monitor.right and monitor.y <= y < monitor.bottom:
            return monitor
    return None


def move_rect(
    rect: Rect,
    origin_cursor: tuple[int, int],
    cursor: tuple[int, int],
    monitor: Monitor,
) -> Rect:
    """Translate a rectangle by a cursor delta and fit it to one monitor."""

    delta_x = int(cursor[0]) - int(origin_cursor[0])
    delta_y = int(cursor[1]) - int(origin_cursor[1])
    return fit_rect_to_monitor(
        Rect(rect.x + delta_x, rect.y + delta_y, rect.width, rect.height),
        monitor,
    )


def centered_start_rect(
    monitor: Monitor, desired_width: int = 640, desired_height: int = 480
) -> Rect:
    """Create the default centered selection, capped to the monitor size."""

    width = min(max(desired_width, 0), monitor.width)
    height = min(max(desired_height, 0), monitor.height)
    return Rect(
        monitor.x + (monitor.width - width) // 2,
        monitor.y + (monitor.height - height) // 2,
        width,
        height,
    )


def corner_points(rect: Rect) -> dict[str, tuple[int, int]]:
    rect = normalize_rect(rect)
    return {
        CornerRole.TOP_LEFT.value: (rect.x, rect.y),
        CornerRole.TOP_RIGHT.value: (rect.right, rect.y),
        CornerRole.BOTTOM_LEFT.value: (rect.x, rect.bottom),
        CornerRole.BOTTOM_RIGHT.value: (rect.right, rect.bottom),
    }


def dot_positions(rect: Rect, dot_size: int) -> dict[str, tuple[int, int]]:
    """Return top-left coordinates for dots diagonally outside each corner."""

    if dot_size <= 0:
        raise ValueError("dot_size must be positive")
    rect = normalize_rect(rect)
    # FIX: Keep selection markers outside the capture rectangle.
    return {
        CornerRole.TOP_LEFT.value: (rect.x - dot_size, rect.y - dot_size),
        CornerRole.TOP_RIGHT.value: (rect.right, rect.y - dot_size),
        CornerRole.BOTTOM_LEFT.value: (rect.x - dot_size, rect.bottom),
        CornerRole.BOTTOM_RIGHT.value: (rect.right, rect.bottom),
    }


def edge_geometries(rect: Rect, edge_width: int) -> dict[str, Rect]:
    """Return window geometries immediately outside the four selection edges."""

    if edge_width <= 0:
        raise ValueError("edge_width must be positive")
    rect = normalize_rect(rect)
    horizontal_length = max(rect.width, 1)
    vertical_length = max(rect.height, 1)
    return {
        EdgeRole.TOP.value: Rect(
            rect.x, rect.y - edge_width, horizontal_length, edge_width
        ),
        EdgeRole.RIGHT.value: Rect(
            rect.right, rect.y, edge_width, vertical_length
        ),
        EdgeRole.BOTTOM.value: Rect(
            rect.x, rect.bottom, horizontal_length, edge_width
        ),
        EdgeRole.LEFT.value: Rect(
            rect.x - edge_width, rect.y, edge_width, vertical_length
        ),
    }


def drag_corner(
    rect: Rect,
    role: str,
    cursor_x: int,
    cursor_y: int,
    monitor: Monitor,
    minimum_size: int = 1,
) -> tuple[Rect, str]:
    """Move one corner without crossing the anchored opposite corner.

    The opposite corner is the anchor. Cursor coordinates are clamped to the
    monitor and the moving corner stays at least *minimum_size* away from the
    anchor on each axis whenever the monitor has enough room.
    """

    try:
        corner_role = CornerRole(role)
    except ValueError as exc:
        raise ValueError(f"unknown corner role: {role!r}") from exc
    if minimum_size <= 0:
        raise ValueError("minimum_size must be positive")

    points = corner_points(rect)
    opposite_roles = {
        CornerRole.TOP_LEFT: CornerRole.BOTTOM_RIGHT,
        CornerRole.TOP_RIGHT: CornerRole.BOTTOM_LEFT,
        CornerRole.BOTTOM_LEFT: CornerRole.TOP_RIGHT,
        CornerRole.BOTTOM_RIGHT: CornerRole.TOP_LEFT,
    }
    anchor_x, anchor_y = points[opposite_roles[corner_role].value]
    cursor_x = min(max(int(cursor_x), monitor.x), monitor.right)
    cursor_y = min(max(int(cursor_y), monitor.y), monitor.bottom)

    if corner_role in (CornerRole.TOP_LEFT, CornerRole.BOTTOM_LEFT):
        minimum_width = min(minimum_size, anchor_x - monitor.x)
        cursor_x = min(cursor_x, anchor_x - minimum_width)
        x = cursor_x
        width = anchor_x - cursor_x
    else:
        minimum_width = min(minimum_size, monitor.right - anchor_x)
        cursor_x = max(cursor_x, anchor_x + minimum_width)
        x = anchor_x
        width = cursor_x - anchor_x

    if corner_role in (CornerRole.TOP_LEFT, CornerRole.TOP_RIGHT):
        minimum_height = min(minimum_size, anchor_y - monitor.y)
        cursor_y = min(cursor_y, anchor_y - minimum_height)
        y = cursor_y
        height = anchor_y - cursor_y
    else:
        minimum_height = min(minimum_size, monitor.bottom - anchor_y)
        cursor_y = max(cursor_y, anchor_y + minimum_height)
        y = anchor_y
        height = cursor_y - anchor_y

    return Rect(x, y, width, height), corner_role.value
