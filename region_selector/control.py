"""Local JSON control protocol for a running GIF recorder."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import json
import os
from pathlib import Path
import socket
import stat
from typing import TYPE_CHECKING, Mapping

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .recording import RecordingState

if TYPE_CHECKING:
    from .application import GifRecorderController


SOCKET_FILENAME = "wayland-gif-recorder.sock"
MAX_REQUEST_BYTES = 64 * 1024


class ControlCommand(str, Enum):
    RECORD = "record"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    CANCEL = "cancel"
    STATUS = "status"


class ControlErrorCode(str, Enum):
    INVALID_JSON = "invalid_json"
    INVALID_REQUEST = "invalid_request"
    MISSING_COMMAND = "missing_command"
    UNKNOWN_COMMAND = "unknown_command"
    REQUEST_TOO_LARGE = "request_too_large"
    INVALID_STATE = "invalid_state"
    SELECTION_NOT_READY = "selection_not_ready"
    SERVER_ALREADY_RUNNING = "server_already_running"
    SERVER_UNAVAILABLE = "server_unavailable"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ControlActionResult:
    ok: bool
    error_code: ControlErrorCode | None = None
    message: str | None = None

    @classmethod
    def accepted(cls) -> ControlActionResult:
        return cls(True)

    @classmethod
    def rejected(
        cls,
        error_code: ControlErrorCode,
        message: str,
    ) -> ControlActionResult:
        return cls(False, error_code, message)


@dataclass(frozen=True, slots=True)
class ControlStatus:
    recording_state: RecordingState
    selection_active: bool
    active_output_path: Path | None
    last_output_path: Path | None


class ControlRequestError(ValueError):
    def __init__(self, code: ControlErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ControlServerError(RuntimeError):
    def __init__(self, code: ControlErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def default_socket_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    runtime_directory = values.get("XDG_RUNTIME_DIR")
    if not runtime_directory:
        raise ControlServerError(
            ControlErrorCode.SERVER_UNAVAILABLE,
            "XDG_RUNTIME_DIR is not set",
        )
    return Path(runtime_directory) / SOCKET_FILENAME


def parse_request(data: bytes) -> ControlCommand:
    if len(data) > MAX_REQUEST_BYTES:
        raise ControlRequestError(
            ControlErrorCode.REQUEST_TOO_LARGE,
            f"request exceeds {MAX_REQUEST_BYTES} bytes",
        )
    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlRequestError(
            ControlErrorCode.INVALID_JSON,
            "request is not valid UTF-8 JSON",
        ) from exc
    if not isinstance(request, dict):
        raise ControlRequestError(
            ControlErrorCode.INVALID_REQUEST,
            "request must be a JSON object",
        )
    if "command" not in request:
        raise ControlRequestError(
            ControlErrorCode.MISSING_COMMAND,
            "request is missing the command field",
        )
    value = request["command"]
    if not isinstance(value, str):
        raise ControlRequestError(
            ControlErrorCode.INVALID_REQUEST,
            "command must be a string",
        )
    try:
        return ControlCommand(value)
    except ValueError as exc:
        raise ControlRequestError(
            ControlErrorCode.UNKNOWN_COMMAND,
            f"unknown command: {value}",
        ) from exc


def response_payload(
    command: ControlCommand | None,
    status: ControlStatus | None,
    result: ControlActionResult,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": result.ok,
        "command": command.value if command is not None else None,
        "recording_state": (
            status.recording_state.name.lower() if status is not None else None
        ),
        "selection_active": (
            status.selection_active if status is not None else False
        ),
        "active_output_path": (
            str(status.active_output_path)
            if status is not None and status.active_output_path is not None
            else None
        ),
        "last_output_path": (
            str(status.last_output_path)
            if status is not None and status.last_output_path is not None
            else None
        ),
    }
    if not result.ok:
        payload["error"] = {
            "code": result.error_code.value if result.error_code else None,
            "message": result.message or "request failed",
        }
    return payload


class GifRecorderControlServer(QObject):
    def __init__(
        self,
        controller: GifRecorderController,
        socket_path: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._socket_path = Path(socket_path) if socket_path else default_socket_path()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept_connections)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self._owns_socket = False

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def start(self) -> None:
        self._prepare_socket_path()
        if not self._server.listen(str(self._socket_path)):
            raise ControlServerError(
                ControlErrorCode.SERVER_UNAVAILABLE,
                f"could not open control socket: {self._server.errorString()}",
            )
        self._owns_socket = True
        try:
            self._socket_path.chmod(0o600)
        except OSError as exc:
            self._server.close()
            self._remove_owned_socket()
            raise ControlServerError(
                ControlErrorCode.SERVER_UNAVAILABLE,
                f"could not restrict control socket permissions: {exc}",
            ) from exc

    def close(self) -> None:
        for connection in tuple(self._buffers):
            connection.abort()
        self._buffers.clear()
        if self._server.isListening():
            self._server.close()
        if self._owns_socket:
            self._remove_owned_socket()
            self._owns_socket = False

    def _prepare_socket_path(self) -> None:
        parent = self._socket_path.parent
        if not parent.is_dir():
            raise ControlServerError(
                ControlErrorCode.SERVER_UNAVAILABLE,
                f"control socket directory does not exist: {parent}",
            )
        if not self._socket_path.exists() and not self._socket_path.is_symlink():
            return

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(self._socket_path))
        except OSError as exc:
            if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                raise ControlServerError(
                    ControlErrorCode.SERVER_UNAVAILABLE,
                    f"could not inspect existing control socket: {exc}",
                ) from exc
        else:
            raise ControlServerError(
                ControlErrorCode.SERVER_ALREADY_RUNNING,
                "another Wayland GIF Recorder instance is already running",
            )
        finally:
            probe.close()

        try:
            metadata = self._socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ControlServerError(
                ControlErrorCode.SERVER_UNAVAILABLE,
                "existing control socket path is not a user-owned socket",
            )
        try:
            self._socket_path.unlink()
        except OSError as exc:
            raise ControlServerError(
                ControlErrorCode.SERVER_UNAVAILABLE,
                f"could not remove stale control socket: {exc}",
            ) from exc

    def _remove_owned_socket(self) -> None:
        try:
            metadata = self._socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.getuid():
            try:
                self._socket_path.unlink()
            except OSError:
                pass

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            if connection is None:
                continue
            self._buffers[connection] = bytearray()
            connection.readyRead.connect(
                lambda connection=connection: self._read_request(connection)
            )
            connection.disconnected.connect(
                lambda connection=connection: self._connection_closed(connection)
            )
            self._read_request(connection)

    def _read_request(self, connection: QLocalSocket) -> None:
        buffer = self._buffers.get(connection)
        if buffer is None:
            return
        buffer.extend(bytes(connection.readAll()))
        if len(buffer) > MAX_REQUEST_BYTES:
            self._send_error(
                connection,
                None,
                ControlErrorCode.REQUEST_TOO_LARGE,
                f"request exceeds {MAX_REQUEST_BYTES} bytes",
            )
            return
        newline = buffer.find(b"\n")
        if newline < 0:
            return
        line = bytes(buffer[:newline])
        try:
            command = parse_request(line)
        except ControlRequestError as exc:
            self._send_error(connection, None, exc.code, str(exc))
            return
        self._dispatch(connection, command)

    def _dispatch(
        self,
        connection: QLocalSocket,
        command: ControlCommand,
    ) -> None:
        actions = {
            ControlCommand.RECORD: self._controller.record,
            ControlCommand.PAUSE: self._controller.pause,
            ControlCommand.RESUME: self._controller.resume,
            ControlCommand.STOP: self._controller.stop,
            ControlCommand.CANCEL: self._controller.cancel,
        }
        action = actions.get(command)
        try:
            result = (
                action() if action is not None else ControlActionResult.accepted()
            )
        except Exception as exc:
            result = ControlActionResult.rejected(
                ControlErrorCode.INTERNAL_ERROR,
                f"command failed: {exc}",
            )
        payload = response_payload(command, self._controller.control_status(), result)
        self._send_payload(connection, payload)

    def _send_error(
        self,
        connection: QLocalSocket,
        command: ControlCommand | None,
        code: ControlErrorCode,
        message: str,
    ) -> None:
        result = ControlActionResult.rejected(code, message)
        payload = response_payload(command, self._controller.control_status(), result)
        self._send_payload(connection, payload)

    def _send_payload(
        self,
        connection: QLocalSocket,
        payload: dict[str, object],
    ) -> None:
        self._buffers.pop(connection, None)
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        connection.write(encoded)
        connection.flush()
        connection.disconnectFromServer()

    def _connection_closed(self, connection: QLocalSocket) -> None:
        self._buffers.pop(connection, None)
        connection.deleteLater()


def send_control_command(
    command: ControlCommand,
    socket_path: Path | None = None,
    timeout: float = 2.0,
) -> dict[str, object]:
    try:
        path = Path(socket_path) if socket_path else default_socket_path()
    except ControlServerError as exc:
        return response_payload(
            command,
            None,
            ControlActionResult.rejected(exc.code, str(exc)),
        )
    request = json.dumps({"command": command.value}).encode("utf-8") + b"\n"
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(request)
        response = bytearray()
        while b"\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > MAX_REQUEST_BYTES:
                return response_payload(
                    command,
                    None,
                    ControlActionResult.rejected(
                        ControlErrorCode.INVALID_RESPONSE,
                        "server response is too large",
                    ),
                )
    except OSError as exc:
        return response_payload(
            command,
            None,
            ControlActionResult.rejected(
                ControlErrorCode.SERVER_UNAVAILABLE,
                f"could not connect to the running recorder: {exc}",
            ),
        )
    finally:
        client.close()

    line, separator, _remainder = bytes(response).partition(b"\n")
    if not separator:
        return response_payload(
            command,
            None,
            ControlActionResult.rejected(
                ControlErrorCode.INVALID_RESPONSE,
                "server closed the connection without a complete response",
            ),
        )
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return response_payload(
            command,
            None,
            ControlActionResult.rejected(
                ControlErrorCode.INVALID_RESPONSE,
                "server returned invalid JSON",
            ),
        )
    return payload
