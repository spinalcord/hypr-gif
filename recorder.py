"""Wayland GIF Recorder application entry point."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from enum import Enum

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from hypr_gif.application import GifRecorderController
from hypr_gif.control import (
    ControlCommand,
    ControlErrorCode,
    ControlServerError,
    GifRecorderControlServer,
    send_control_command,
)
from hypr_gif.settings import RecordingSettingsStore


class SystemDependency(str, Enum):
    GRIM = "grim"
    FFMPEG = "ffmpeg"
    HYPRCTL = "hyprctl"
    GIFSICLE = "gifsicle"


def missing_system_dependencies(
    gifsicle_enabled: bool,
    program_search: Callable[[str], str | None] = shutil.which,
) -> tuple[SystemDependency, ...]:
    required = [
        SystemDependency.GRIM,
        SystemDependency.FFMPEG,
        SystemDependency.HYPRCTL,
    ]
    if gifsicle_enabled:
        required.append(SystemDependency.GIFSICLE)
    return tuple(
        dependency
        for dependency in required
        if program_search(dependency.value) is None
    )


def run_client(command_name: str) -> int:
    try:
        command = ControlCommand(command_name)
    except ValueError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": command_name,
                    "recording_state": None,
                    "selection_active": False,
                    "active_output_path": None,
                    "last_output_path": None,
                    "error": {
                        "code": ControlErrorCode.UNKNOWN_COMMAND.value,
                        "message": f"unknown command: {command_name}",
                    },
                }
            )
        )
        return 2
    response = send_control_command(command)
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response.get("ok") is True else 1


def run_application(
    arguments: list[str],
    program_search: Callable[[str], str | None] = shutil.which,
) -> int:
    preferences = RecordingSettingsStore().load()
    missing = missing_system_dependencies(
        preferences.options.gifsicle_enabled,
        program_search,
    )
    if missing:
        names = ", ".join(dependency.value for dependency in missing)
        print(
            f"Fehler: Erforderliche Programme fehlen in PATH: {names}",
            file=sys.stderr,
        )
        return 1

    app = QApplication(arguments)

    controller = GifRecorderController(app)
    try:
        server = GifRecorderControlServer(controller)
        server.start()
    except ControlServerError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    controller.recording_started.connect(
        lambda path: print(f"Aufnahme gestartet: {path}")
    )
    controller.recording_finished.connect(
        lambda path: print(f"GIF gespeichert: {path}")
    )
    controller.warning.connect(
        lambda message: print(f"Warnung: {message}", file=sys.stderr)
    )
    controller.cancelled.connect(lambda: QTimer.singleShot(0, app.quit))
    controller.error.connect(
        lambda message: print(f"Fehler: {message}", file=sys.stderr)
    )
    app.aboutToQuit.connect(server.close)
    app.aboutToQuit.connect(controller.close)
    controller.start()
    return app.exec()


def main(arguments: list[str] | None = None) -> int:
    values = list(sys.argv if arguments is None else arguments)
    if len(values) == 1:
        return run_application(values)
    if len(values) == 2:
        return run_client(values[1])
    print(
        "Verwendung: python recorder.py [record|pause|resume|stop|cancel|status]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
