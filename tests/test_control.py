from concurrent.futures import ThreadPoolExecutor
import socket
import stat
import time

import pytest
from PyQt6.QtWidgets import QApplication

from hypr_gif.control import (
    MAX_REQUEST_BYTES,
    ControlActionResult,
    ControlCommand,
    ControlErrorCode,
    ControlRequestError,
    ControlServerError,
    ControlStatus,
    GifRecorderControlServer,
    default_socket_path,
    parse_request,
    send_control_command,
)
from hypr_gif.recording import RecordingState


class FakeControlController:
    def __init__(self) -> None:
        self.commands = []

    def control_status(self) -> ControlStatus:
        return ControlStatus(
            RecordingState.IDLE,
            True,
            None,
            None,
        )

    def record(self) -> ControlActionResult:
        self.commands.append(ControlCommand.RECORD)
        return ControlActionResult.accepted()

    def pause(self) -> ControlActionResult:
        self.commands.append(ControlCommand.PAUSE)
        return ControlActionResult.accepted()

    def resume(self) -> ControlActionResult:
        self.commands.append(ControlCommand.RESUME)
        return ControlActionResult.accepted()

    def stop(self) -> ControlActionResult:
        self.commands.append(ControlCommand.STOP)
        return ControlActionResult.accepted()

    def cancel(self) -> ControlActionResult:
        self.commands.append(ControlCommand.CANCEL)
        return ControlActionResult.accepted()


class FailingControlController(FakeControlController):
    def record(self) -> ControlActionResult:
        raise RuntimeError("test failure")


@pytest.mark.parametrize("command", tuple(ControlCommand))
def test_parse_request_accepts_all_commands(command) -> None:
    assert parse_request(f'{{"command":"{command.value}"}}'.encode()) is command


@pytest.mark.parametrize(
    ("request_data", "error_code"),
    (
        (b"not json", ControlErrorCode.INVALID_JSON),
        (b"[]", ControlErrorCode.INVALID_REQUEST),
        (b"{}", ControlErrorCode.MISSING_COMMAND),
        (b'{"command":3}', ControlErrorCode.INVALID_REQUEST),
        (b'{"command":"rewind"}', ControlErrorCode.UNKNOWN_COMMAND),
        (b"x" * (MAX_REQUEST_BYTES + 1), ControlErrorCode.REQUEST_TOO_LARGE),
    ),
)
def test_parse_request_rejects_invalid_requests(request_data, error_code) -> None:
    with pytest.raises(ControlRequestError) as error:
        parse_request(request_data)

    assert error.value.code is error_code


def test_default_socket_path_uses_xdg_runtime_directory(tmp_path) -> None:
    assert default_socket_path({"XDG_RUNTIME_DIR": str(tmp_path)}) == (
        tmp_path / "wayland-gif-recorder.sock"
    )


def test_server_and_client_roundtrip_status(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    controller = FakeControlController()
    socket_path = tmp_path / "control.sock"
    server = GifRecorderControlServer(controller, socket_path)
    server.start()

    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            send_control_command,
            ControlCommand.STATUS,
            socket_path,
        )
        deadline = time.monotonic() + 3
        while not pending.done() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        response = pending.result(timeout=0.1)

    assert response == {
        "ok": True,
        "command": "status",
        "recording_state": "idle",
        "selection_active": True,
        "active_output_path": None,
        "last_output_path": None,
    }
    assert controller.commands == []
    server.close()
    assert socket_path.exists() is False


def test_server_removes_user_owned_stale_socket(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    socket_path = tmp_path / "control.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()

    server = GifRecorderControlServer(FakeControlController(), socket_path)
    server.start()

    assert socket_path.exists() is True
    app.processEvents()
    server.close()


def test_second_server_is_rejected_without_removing_active_socket(tmp_path) -> None:
    socket_path = tmp_path / "control.sock"
    first = GifRecorderControlServer(FakeControlController(), socket_path)
    second = GifRecorderControlServer(FakeControlController(), socket_path)
    first.start()

    with pytest.raises(ControlServerError) as error:
        second.start()

    assert error.value.code is ControlErrorCode.SERVER_ALREADY_RUNNING
    second.close()
    assert socket_path.exists() is True
    first.close()


def test_client_reports_unreachable_server(tmp_path) -> None:
    response = send_control_command(
        ControlCommand.STATUS,
        tmp_path / "missing.sock",
        timeout=0.05,
    )

    assert response["ok"] is False
    assert response["command"] == "status"
    assert response["error"]["code"] == "server_unavailable"


def test_server_returns_stable_error_for_controller_failure(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    socket_path = tmp_path / "control.sock"
    server = GifRecorderControlServer(FailingControlController(), socket_path)
    server.start()

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            send_control_command,
            ControlCommand.RECORD,
            socket_path,
        )
        deadline = time.monotonic() + 3
        while not pending.done() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        response = pending.result(timeout=0.1)

    assert response["ok"] is False
    assert response["command"] == "record"
    assert response["error"]["code"] == "internal_error"
    server.close()
