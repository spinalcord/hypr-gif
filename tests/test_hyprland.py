import json
import subprocess
from pathlib import Path

import pytest

from region_selector import (
    CursorSocket,
    HyprlandError,
    HyprlandIPC,
    Monitor,
    Rect,
    capture_geometry_from_json,
    focused_monitor_from_json,
    match_dot_windows,
    monitor_from_json,
    monitors_from_json,
)


class FakeRunner:
    def __init__(self, replies: list[tuple[int, str, str]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, arguments):
        self.calls.append(tuple(arguments))
        returncode, stdout, stderr = self.replies.pop(0)
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


def test_monitor_parser_accounts_for_scale_and_rotation() -> None:
    assert monitor_from_json(
        {
            "x": -100,
            "y": 20,
            "width": 2400,
            "height": 1600,
            "scale": 2,
            "transform": 1,
            "name": "DP-1",
        }
    ) == Monitor(-100, 20, 800, 1200, "DP-1")


def test_focused_monitor_requires_exactly_one_match() -> None:
    data = [
        {"x": 0, "y": 0, "width": 100, "height": 80, "focused": False},
        {"x": 100, "y": 0, "width": 200, "height": 100, "focused": True},
    ]
    assert focused_monitor_from_json(data) == Monitor(100, 0, 200, 100)
    with pytest.raises(HyprlandError, match="exactly one focused"):
        focused_monitor_from_json([])


def test_all_monitors_parser_preserves_layout_order() -> None:
    data = [
        {"x": -800, "y": 0, "width": 800, "height": 600, "name": "DP-1"},
        {"x": 0, "y": 0, "width": 1920, "height": 1080, "name": "DP-2"},
    ]
    assert monitors_from_json(data) == (
        Monitor(-800, 0, 800, 600, "DP-1"),
        Monitor(0, 0, 1920, 1080, "DP-2"),
    )
    with pytest.raises(HyprlandError, match="no monitors"):
        monitors_from_json([])


def test_dot_matching_uses_exact_title_and_pid() -> None:
    titles = {"tl": "token-tl", "tr": "token-tr"}
    clients = [
        {"pid": 42, "title": "token-tl", "address": "0xabc", "mapped": True},
        {"pid": 42, "title": "token-tr", "address": "0xdef", "mapped": True},
        {"pid": 99, "title": "token-tr", "address": "0x111", "mapped": True},
    ]
    assert match_dot_windows(clients, 42, titles) == {"tl": "0xabc", "tr": "0xdef"}


def test_dot_matching_reports_duplicates_and_missing_windows() -> None:
    duplicate = [
        {"pid": 42, "title": "dot", "address": "0xabc"},
        {"pid": 42, "title": "dot", "address": "0xdef"},
    ]
    with pytest.raises(HyprlandError, match="found 2"):
        match_dot_windows(duplicate, 42, {"tl": "dot"})
    with pytest.raises(HyprlandError, match="found 0"):
        match_dot_windows([], 42, {"tl": "dot"})


def _edge_clients(
    *,
    top=(10, 18, 100, 2),
    right=(110, 20, 2, 80),
    bottom=(10, 100, 100, 2),
    left=(8, 20, 2, 80),
):
    geometries = {
        "top": top,
        "right": right,
        "bottom": bottom,
        "left": left,
    }
    return [
        {
            "address": f"0x{index}",
            "mapped": True,
            "at": [geometry[0], geometry[1]],
            "size": [geometry[2], geometry[3]],
        }
        for index, geometry in enumerate(geometries.values(), 1)
    ]


def _edge_addresses():
    return {
        "top": "0x1",
        "right": "0x2",
        "bottom": "0x3",
        "left": "0x4",
    }


def test_capture_geometry_uses_the_four_inner_edges() -> None:
    assert capture_geometry_from_json(
        _edge_clients(), _edge_addresses()
    ) == Rect(10, 20, 100, 80)


def test_capture_geometry_accounts_for_inward_top_and_left_edges() -> None:
    clients = _edge_clients(
        top=(10, 20, 100, 2),
        left=(10, 20, 2, 80),
    )

    assert capture_geometry_from_json(clients, _edge_addresses()) == Rect(
        12, 22, 98, 78
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda clients: clients.pop(), "found 0"),
        (lambda clients: clients.append(dict(clients[0])), "found 2"),
        (lambda clients: clients[0].update(mapped=False), "not mapped"),
        (lambda clients: clients[0].update(size=[0, 2]), "positive size"),
        (lambda clients: clients[0].update(at=[10]), "invalid geometry"),
        (
            lambda clients: clients[0].update(at=[20, 18]),
            "gaps or overlaps",
        ),
    ),
)
def test_capture_geometry_rejects_invalid_edge_windows(mutate, message) -> None:
    clients = _edge_clients()
    mutate(clients)

    with pytest.raises(HyprlandError, match=message):
        capture_geometry_from_json(clients, _edge_addresses())


def test_json_adapter_reports_malformed_json() -> None:
    runner = FakeRunner([(0, "not-json", "")])
    with pytest.raises(HyprlandError, match="invalid JSON"):
        HyprlandIPC(runner).focused_monitor()


def test_capture_geometry_refreshes_hyprland_client_state() -> None:
    runner = FakeRunner([(0, json.dumps(_edge_clients()), "")])

    assert HyprlandIPC(runner).capture_geometry(_edge_addresses()) == Rect(
        10, 20, 100, 80
    )
    assert runner.calls == [("hyprctl", "-j", "-r", "clients")]


def test_hyprctl_failure_includes_stderr() -> None:
    runner = FakeRunner([(1, "", "socket unavailable")])
    with pytest.raises(HyprlandError, match="socket unavailable"):
        HyprlandIPC(runner).clients()


def test_configure_and_move_use_single_lua_batches() -> None:
    runner = FakeRunner([(0, "ok\n" * 44, ""), (0, "ok\n" * 4, "")])
    ipc = HyprlandIPC(runner)
    addresses = {"tl": "0x1", "tr": "0x2", "bl": "0x3", "br": "0x4"}
    positions = {"tl": (1, 2), "tr": (3, 4), "bl": (5, 6), "br": (7, 8)}
    ipc.configure_dots(addresses, positions, 24)
    ipc.move_dots(addresses, positions)

    assert runner.calls[0][:2] == ("hyprctl", "--batch")
    config_batch = runner.calls[0][2]
    assert config_batch.count("hl.dsp.window.float") == 4
    for address in addresses.values():
        assert config_batch.count(
            "hl.dsp.window.pin({ action = 'enable', "
            f"window = 'address:{address}' }})"
        ) == 1
    assert config_batch.count("prop = 'rounding', value = '12'") == 4
    assert config_batch.count("prop = 'border_size', value = '0'") == 4
    assert config_batch.count("hl.dsp.window.resize") == 4
    assert runner.calls[1][2].count("hl.dsp.window.move") == 4


def test_selection_windows_are_configured_and_updated_in_single_batches() -> None:
    runner = FakeRunner([(0, "ok\n" * 88, ""), (0, "ok\n" * 12, "")])
    ipc = HyprlandIPC(runner)
    addresses = {
        "tl": "0x1",
        "tr": "0x2",
        "bl": "0x3",
        "br": "0x4",
        "top": "0x5",
        "right": "0x6",
        "bottom": "0x7",
        "left": "0x8",
    }
    positions = {"tl": (1, 2), "tr": (3, 4), "bl": (5, 6), "br": (7, 8)}
    edges = {
        "top": Rect(10, 19, 100, 2),
        "right": Rect(109, 20, 2, 80),
        "bottom": Rect(10, 99, 100, 2),
        "left": Rect(9, 20, 2, 80),
    }

    ipc.configure_selection_windows(addresses, positions, 24, edges, monitor="DP-1")
    ipc.update_selection_windows(addresses, positions, edges)

    config_batch = runner.calls[0][2]
    assert config_batch.count("hl.dsp.window.float") == 8
    assert config_batch.count("monitor = 'DP-1', follow = false") == 8
    for address in addresses.values():
        float_command = (
            "hl.dsp.window.float({ action = 'enable', "
            f"window = 'address:{address}' }})"
        )
        monitor_command = (
            "hl.dsp.window.move({ monitor = 'DP-1', follow = false, "
            f"window = 'address:{address}' }})"
        )
        pin_command = (
            "hl.dsp.window.pin({ action = 'enable', "
            f"window = 'address:{address}' }})"
        )
        assert config_batch.index(float_command) < config_batch.index(monitor_command)
        assert config_batch.index(monitor_command) < config_batch.index(pin_command)
        assert config_batch.count(
            "hl.dsp.window.pin({ action = 'enable', "
            f"window = 'address:{address}' }})"
        ) == 1
    assert config_batch.count("prop = 'rounding', value = '12'") == 4
    assert config_batch.count("prop = 'rounding', value = '0'") == 4
    assert config_batch.count("hl.dsp.window.resize") == 8
    update_batch = runner.calls[1][2]
    assert update_batch.count("hl.dsp.window.resize") == 4
    assert update_batch.count("hl.dsp.window.move") == 8


def test_toolbar_is_configured_and_updated_with_the_other_eight_windows() -> None:
    runner = FakeRunner([(0, "ok\n" * 99, ""), (0, "ok\n" * 14, "")])
    ipc = HyprlandIPC(runner)
    addresses = {
        "tl": "0x1",
        "tr": "0x2",
        "bl": "0x3",
        "br": "0x4",
        "top": "0x5",
        "right": "0x6",
        "bottom": "0x7",
        "left": "0x8",
        "toolbar": "0x9",
    }
    positions = {"tl": (1, 2), "tr": (3, 4), "bl": (5, 6), "br": (7, 8)}
    edges = {
        "top": Rect(10, 19, 100, 2),
        "right": Rect(109, 20, 2, 80),
        "bottom": Rect(10, 99, 100, 2),
        "left": Rect(9, 20, 2, 80),
    }
    toolbar_geometry = Rect(20, 110, 180, 40)

    ipc.configure_selection_windows(
        addresses, positions, 24, edges, toolbar_geometry, "HDMI-A-1"
    )
    ipc.update_selection_windows(addresses, positions, edges, toolbar_geometry)

    config_batch = runner.calls[0][2]
    assert config_batch.count("hl.dsp.window.float") == 9
    assert config_batch.count("monitor = 'HDMI-A-1', follow = false") == 9
    for address in addresses.values():
        assert config_batch.count(
            "hl.dsp.window.pin({ action = 'enable', "
            f"window = 'address:{address}' }})"
        ) == 1
    assert "address:0x9" in config_batch
    update_batch = runner.calls[1][2]
    assert update_batch.count("hl.dsp.window.resize") == 5
    assert update_batch.count("hl.dsp.window.move") == 9
    assert "address:0x9" in update_batch


def test_monitor_change_reassigns_all_windows_before_geometry_updates() -> None:
    runner = FakeRunner([(0, "ok\n" * 41, "")])
    ipc = HyprlandIPC(runner)
    addresses = {
        "tl": "0x1",
        "tr": "0x2",
        "bl": "0x3",
        "br": "0x4",
        "top": "0x5",
        "right": "0x6",
        "bottom": "0x7",
        "left": "0x8",
        "toolbar": "0x9",
    }
    positions = {"tl": (1, 2), "tr": (3, 4), "bl": (5, 6), "br": (7, 8)}
    edges = {
        role: Rect(10, 10, 20, 2)
        for role in ("top", "right", "bottom", "left")
    }

    ipc.update_selection_windows(
        addresses, positions, edges, Rect(20, 30, 100, 40), "DP-2"
    )

    batch = runner.calls[0][2]
    assert batch.count("action = 'disable'") == 9
    assert batch.count("monitor = 'DP-2', follow = false") == 9
    assert batch.count("action = 'enable'") == 9
    commands = batch.split(";")
    assert all("action = 'disable'" in command for command in commands[:9])
    assert all("monitor = 'DP-2'" in command for command in commands[9:18])
    assert all("action = 'enable'" in command for command in commands[18:27])
    assert "x = 1, y = 2" in commands[27]


@pytest.mark.parametrize("monitor", ("", "DP-1';bad"))
def test_invalid_target_monitor_is_rejected(monitor: str) -> None:
    addresses = {
        "tl": "0x1",
        "tr": "0x2",
        "bl": "0x3",
        "br": "0x4",
        "top": "0x5",
        "right": "0x6",
        "bottom": "0x7",
        "left": "0x8",
    }
    positions = {"tl": (0, 0), "tr": (1, 0), "bl": (0, 1), "br": (1, 1)}
    edges = {
        role: Rect(0, 0, 2, 2)
        for role in ("top", "right", "bottom", "left")
    }
    with pytest.raises(HyprlandError, match="target monitor"):
        HyprlandIPC(FakeRunner([])).configure_selection_windows(
            addresses, positions, 24, edges, monitor=monitor
        )


def test_batch_rejection_is_readable() -> None:
    runner = FakeRunner([(0, "ok\ninvalid property\n", "")])
    with pytest.raises(HyprlandError, match="invalid property"):
        HyprlandIPC(runner).move_dots(
            {"tl": "0x1", "tr": "0x2", "bl": "0x3", "br": "0x4"},
            {"tl": (0, 0), "tr": (1, 0), "bl": (0, 1), "br": (1, 1)},
        )


def test_missing_cursor_socket_has_readable_error(tmp_path: Path) -> None:
    environment = {
        "HYPRLAND_INSTANCE_SIGNATURE": "missing",
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    with pytest.raises(HyprlandError, match="socket was not found"):
        CursorSocket.from_environment(environment)


def test_cursor_socket_reads_hyprland_json(tmp_path: Path, monkeypatch) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.connected_to = None
            self.sent = None
            self.responses = iter(
                (json.dumps({"x": -12, "y": 345}).encode(), b"")
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, path):
            self.connected_to = path

        def sendall(self, payload):
            self.sent = payload

        def recv(self, _size):
            return next(self.responses)

    fake = FakeSocket()
    monkeypatch.setattr("region_selector.hyprland.socket.socket", lambda *_args: fake)
    path = tmp_path / "cursor.sock"
    assert CursorSocket(path).position() == (-12, 345)
    assert fake.connected_to == str(path)
    assert fake.sent == b"j/cursorpos"
