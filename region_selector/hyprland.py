"""Hyprland IPC and cursor access for region selection."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .geometry import (
    CORNER_ROLES,
    EDGE_ROLES,
    TOOLBAR_ROLE,
    EdgeRole,
    Monitor,
    Rect,
)


class HyprlandError(RuntimeError):
    """A readable failure raised by the small Hyprland IPC adapter."""


def monitor_from_json(value: Mapping[str, Any]) -> Monitor:
    """Parse a Hyprland monitor object into logical compositor coordinates."""

    try:
        x = int(value["x"])
        y = int(value["y"])
        pixel_width = int(value["width"])
        pixel_height = int(value["height"])
        scale = float(value.get("scale", 1.0))
        transform = int(value.get("transform", 0))
        name = str(value.get("name", ""))
    except (KeyError, TypeError, ValueError) as exc:
        raise HyprlandError(f"invalid monitor JSON: {exc}") from exc
    if scale <= 0 or pixel_width < 0 or pixel_height < 0:
        raise HyprlandError("invalid monitor JSON: dimensions and scale are invalid")
    if transform in (1, 3, 5, 7):
        pixel_width, pixel_height = pixel_height, pixel_width
    return Monitor(
        x=x,
        y=y,
        width=round(pixel_width / scale),
        height=round(pixel_height / scale),
        name=name,
    )


def monitors_from_json(values: Any) -> tuple[Monitor, ...]:
    """Parse all active Hyprland monitors in compositor coordinates."""

    if not isinstance(values, list):
        raise HyprlandError("Hyprland monitor response is not a JSON list")
    if not values:
        raise HyprlandError("Hyprland monitor response contains no monitors")
    try:
        return tuple(monitor_from_json(value) for value in values)
    except (AttributeError, TypeError) as exc:
        raise HyprlandError("Hyprland monitor response contains an invalid monitor") from exc


def focused_monitor_from_json(values: Any) -> Monitor:
    if not isinstance(values, list):
        raise HyprlandError("Hyprland monitor response is not a JSON list")
    focused = [
        item
        for item in values
        if isinstance(item, dict) and item.get("focused") is True
    ]
    if len(focused) != 1:
        raise HyprlandError(
            f"expected exactly one focused Hyprland monitor, found {len(focused)}"
        )
    return monitor_from_json(focused[0])


def match_dot_windows(
    clients: Any, pid: int, titles: Mapping[str, str]
) -> dict[str, str]:
    """Uniquely map expected role titles to safe Hyprland addresses."""

    return _match_windows(clients, pid, titles, "dot")


def capture_geometry_from_json(
    clients: Any,
    addresses: Mapping[str, str],
) -> Rect:
    """Resolve the capture rectangle from four compositor edge windows."""

    if not isinstance(clients, list):
        raise HyprlandError("Hyprland client response is not a JSON list")

    edges: dict[str, Rect] = {}
    for role in EDGE_ROLES:
        address = addresses.get(role)
        if not isinstance(address, str) or not _safe_address(address):
            raise HyprlandError(
                f"selection edge window {role!r} has an invalid address"
            )
        matches = [
            item
            for item in clients
            if isinstance(item, dict) and item.get("address") == address
        ]
        if len(matches) != 1:
            raise HyprlandError(
                f"could not uniquely find selection edge window {role!r}: "
                f"found {len(matches)}"
            )
        client = matches[0]
        if client.get("mapped") is not True:
            raise HyprlandError(f"selection edge window {role!r} is not mapped")
        edges[role] = _client_geometry(client, role)

    if len({addresses[role] for role in EDGE_ROLES}) != len(EDGE_ROLES):
        raise HyprlandError(
            "multiple selection edges resolved to the same Hyprland window"
        )

    top = edges[EdgeRole.TOP.value]
    right = edges[EdgeRole.RIGHT.value]
    bottom = edges[EdgeRole.BOTTOM.value]
    left = edges[EdgeRole.LEFT.value]
    # FIX: Derive the capture area from compositor-confirmed edge geometry.
    geometry = Rect(
        left.right,
        top.bottom,
        right.x - left.right,
        bottom.y - top.bottom,
    )
    if geometry.width <= 0 or geometry.height <= 0:
        raise HyprlandError(
            "selection edge windows do not leave a positive capture rectangle"
        )

    horizontal_edges = (top, bottom)
    vertical_edges = (left, right)
    horizontal_edges_join_sides = all(
        left.x <= edge.x <= left.right
        and right.x <= edge.right <= right.right
        for edge in horizontal_edges
    )
    vertical_edges_join_sides = all(
        top.y <= edge.y <= top.bottom
        and bottom.y <= edge.bottom <= bottom.bottom
        for edge in vertical_edges
    )
    if not horizontal_edges_join_sides or not vertical_edges_join_sides:
        raise HyprlandError(
            "selection edge windows have gaps or overlaps at their shared corners"
        )
    return geometry


def _client_geometry(client: Mapping[str, Any], role: str) -> Rect:
    at = client.get("at")
    size = client.get("size")
    if (
        not isinstance(at, list)
        or len(at) != 2
        or not isinstance(size, list)
        or len(size) != 2
    ):
        raise HyprlandError(
            f"selection edge window {role!r} has invalid geometry"
        )
    values = (*at, *size)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise HyprlandError(
            f"selection edge window {role!r} has invalid geometry"
        )
    geometry = Rect(at[0], at[1], size[0], size[1])
    if geometry.width <= 0 or geometry.height <= 0:
        raise HyprlandError(
            f"selection edge window {role!r} must have a positive size"
        )
    return geometry


def _match_windows(
    clients: Any,
    pid: int,
    titles: Mapping[str, str],
    window_kind: str,
) -> dict[str, str]:
    if not isinstance(clients, list):
        raise HyprlandError("Hyprland client response is not a JSON list")

    result: dict[str, str] = {}
    for role, title in titles.items():
        matches = [
            item
            for item in clients
            if isinstance(item, dict)
            and item.get("pid") == pid
            and item.get("title") == title
            and item.get("mapped", True) is True
        ]
        if len(matches) != 1:
            raise HyprlandError(
                f"could not uniquely find {window_kind} window {role!r}: "
                f"found {len(matches)}"
            )
        address = matches[0].get("address")
        if not isinstance(address, str) or not _safe_address(address):
            raise HyprlandError(
                f"{window_kind} window {role!r} has an invalid address"
            )
        result[role] = address
    if len(set(result.values())) != len(result):
        raise HyprlandError(
            f"multiple {window_kind} titles resolved to the same Hyprland window"
        )
    return result


def _safe_address(address: str) -> bool:
    if not address.startswith("0x") or len(address) <= 2:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in address[2:])


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _subprocess_runner(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        check=False,
        timeout=2.0,
    )


class HyprlandIPC:
    """Minimal, injectable wrapper around hyprctl window-management calls."""

    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner or _subprocess_runner

    def _run(self, *arguments: str) -> str:
        try:
            completed = self._runner(("hyprctl", *arguments))
        except (OSError, subprocess.SubprocessError) as exc:
            raise HyprlandError(f"could not run hyprctl: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise HyprlandError(f"hyprctl failed: {detail}")
        return completed.stdout

    def _json(self, command: str, *, refresh: bool = False) -> Any:
        arguments = ("-j", "-r", command) if refresh else ("-j", command)
        output = self._run(*arguments)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise HyprlandError(f"hyprctl {command} returned invalid JSON") from exc

    def focused_monitor(self) -> Monitor:
        return focused_monitor_from_json(self._json("monitors"))

    def monitors(self) -> tuple[Monitor, ...]:
        return monitors_from_json(self._json("monitors"))

    def clients(self, *, refresh: bool = False) -> Any:
        return self._json("clients", refresh=refresh)

    def capture_geometry(self, addresses: Mapping[str, str]) -> Rect:
        return capture_geometry_from_json(
            self.clients(refresh=True),
            addresses,
        )

    def find_dots(self, pid: int, titles: Mapping[str, str]) -> dict[str, str]:
        return match_dot_windows(self.clients(), pid, titles)

    def find_selection_windows(
        self, pid: int, titles: Mapping[str, str]
    ) -> dict[str, str]:
        return _match_windows(self.clients(), pid, titles, "selection")

    def _batch(self, commands: Sequence[str]) -> None:
        if not commands:
            return
        output = self._run("--batch", ";".join(commands))
        replies = [line.strip() for line in output.splitlines() if line.strip()]
        failures = [reply for reply in replies if reply.lower() != "ok"]
        if failures:
            raise HyprlandError(f"Hyprland rejected a command: {failures[0]}")

    def configure_dots(
        self,
        addresses: Mapping[str, str],
        positions: Mapping[str, tuple[int, int]],
        dot_size: int,
    ) -> None:
        commands: list[str] = []
        for role in CORNER_ROLES:
            address = addresses[role]
            x, y = positions[role]
            commands.extend(
                _configure_window_commands(
                    role,
                    address,
                    Rect(x, y, dot_size, dot_size),
                    dot_size // 2,
                )
            )
        self._batch(commands)

    def configure_selection_windows(
        self,
        addresses: Mapping[str, str],
        positions: Mapping[str, tuple[int, int]],
        dot_size: int,
        edges: Mapping[str, Rect],
        toolbar_geometry: Rect | None = None,
    ) -> None:
        commands: list[str] = []
        for role in CORNER_ROLES:
            x, y = positions[role]
            commands.extend(
                _configure_window_commands(
                    role,
                    addresses[role],
                    Rect(x, y, dot_size, dot_size),
                    dot_size // 2,
                )
            )
        for role in EDGE_ROLES:
            commands.extend(
                _configure_window_commands(role, addresses[role], edges[role], 0)
            )
        toolbar_geometry = toolbar_geometry or edges.get(TOOLBAR_ROLE)
        if toolbar_geometry is not None:
            commands.extend(
                _configure_window_commands(
                    TOOLBAR_ROLE,
                    addresses[TOOLBAR_ROLE],
                    toolbar_geometry,
                    0,
                )
            )
        self._batch(commands)

    def move_dots(
        self,
        addresses: Mapping[str, str],
        positions: Mapping[str, tuple[int, int]],
    ) -> None:
        commands = []
        for role in CORNER_ROLES:
            address = addresses[role]
            if not _safe_address(address):
                raise HyprlandError(f"unsafe Hyprland address for {role!r}")
            x, y = positions[role]
            commands.append(
                "dispatch hl.dsp.window.move({ "
                f"x = {x}, y = {y}, window = 'address:{address}' }})"
            )
        self._batch(commands)

    def update_selection_windows(
        self,
        addresses: Mapping[str, str],
        positions: Mapping[str, tuple[int, int]],
        edges: Mapping[str, Rect],
        toolbar_geometry: Rect | None = None,
    ) -> None:
        commands: list[str] = []
        for role in CORNER_ROLES:
            address = addresses[role]
            if not _safe_address(address):
                raise HyprlandError(f"unsafe Hyprland address for {role!r}")
            x, y = positions[role]
            commands.append(
                "dispatch hl.dsp.window.move({ "
                f"x = {x}, y = {y}, window = 'address:{address}' }})"
            )
        for role in EDGE_ROLES:
            address = addresses[role]
            if not _safe_address(address):
                raise HyprlandError(f"unsafe Hyprland address for {role!r}")
            geometry = edges[role]
            window = f"window = 'address:{address}'"
            commands.extend(
                (
                    "dispatch hl.dsp.window.resize({ "
                    f"x = {geometry.width}, y = {geometry.height}, {window} }})",
                    "dispatch hl.dsp.window.move({ "
                    f"x = {geometry.x}, y = {geometry.y}, {window} }})",
                )
            )
        toolbar_geometry = toolbar_geometry or edges.get(TOOLBAR_ROLE)
        if toolbar_geometry is not None:
            address = addresses[TOOLBAR_ROLE]
            if not _safe_address(address):
                raise HyprlandError(
                    f"unsafe Hyprland address for {TOOLBAR_ROLE!r}"
                )
            geometry = toolbar_geometry
            window = f"window = 'address:{address}'"
            commands.extend(
                (
                    "dispatch hl.dsp.window.resize({ "
                    f"x = {geometry.width}, y = {geometry.height}, {window} }})",
                    "dispatch hl.dsp.window.move({ "
                    f"x = {geometry.x}, y = {geometry.y}, {window} }})",
                )
            )
        self._batch(commands)


def _configure_window_commands(
    role: str,
    address: str,
    geometry: Rect,
    rounding: int,
) -> tuple[str, ...]:
    if not _safe_address(address):
        raise HyprlandError(f"unsafe Hyprland address for {role!r}")
    window = f"window = 'address:{address}'"

    def set_prop(prop: str, value: str) -> str:
        return (
            "dispatch hl.dsp.window.set_prop({ "
            f"prop = '{prop}', value = '{value}', {window} }})"
        )

    return (
        f"dispatch hl.dsp.window.float({{ action = 'enable', {window} }})",
        set_prop("opaque", "1"),
        set_prop("opacity", "1"),
        set_prop("opacity_override", "1"),
        set_prop("no_blur", "1"),
        set_prop("no_shadow", "1"),
        set_prop("border_size", "0"),
        set_prop("no_anim", "1"),
        set_prop("rounding", str(rounding)),
        "dispatch hl.dsp.window.resize({ "
        f"x = {geometry.width}, y = {geometry.height}, {window} }})",
        "dispatch hl.dsp.window.move({ "
        f"x = {geometry.x}, y = {geometry.y}, {window} }})",
    )


class CursorSocket:
    """Read cursor positions directly from Hyprland's command socket."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "CursorSocket":
        environment = os.environ if environment is None else environment
        signature = environment.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not signature:
            raise HyprlandError("HYPRLAND_INSTANCE_SIGNATURE is not set")
        runtime = environment.get("XDG_RUNTIME_DIR")
        candidates = []
        if runtime:
            candidates.append(Path(runtime) / "hypr" / signature / ".socket.sock")
        candidates.append(Path("/tmp/hypr") / signature / ".socket.sock")
        for path in candidates:
            try:
                if stat.S_ISSOCK(path.stat().st_mode):
                    return cls(path)
            except OSError:
                continue
        rendered = ", ".join(str(path) for path in candidates)
        raise HyprlandError(f"Hyprland command socket was not found ({rendered})")

    def position(self) -> tuple[int, int]:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.1)
                connection.connect(str(self.path))
                connection.sendall(b"j/cursorpos")
                chunks = []
                while chunk := connection.recv(4096):
                    chunks.append(chunk)
                response = b"".join(chunks)
        except OSError as exc:
            raise HyprlandError(f"could not read Hyprland cursor socket: {exc}") from exc
        if not response:
            raise HyprlandError("Hyprland cursor socket returned an empty response")
        try:
            value = json.loads(response.decode("utf-8"))
            return int(value["x"]), int(value["y"])
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise HyprlandError(
                "Hyprland cursor socket returned invalid JSON"
            ) from exc
