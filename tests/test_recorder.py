import json
from types import SimpleNamespace

import recorder
from hypr_gif.control import ControlCommand, ControlErrorCode


def test_missing_system_dependencies_returns_all_required_in_stable_order() -> None:
    available = {"gifsicle"}

    assert recorder.missing_system_dependencies(
        gifsicle_enabled=True,
        program_search=lambda name: name if name in available else None,
    ) == (
        recorder.SystemDependency.GRIM,
        recorder.SystemDependency.FFMPEG,
        recorder.SystemDependency.HYPRCTL,
    )


def test_missing_system_dependencies_requires_enabled_gifsicle() -> None:
    available = {"grim", "ffmpeg", "hyprctl"}

    def search(name):
        return name if name in available else None

    assert recorder.missing_system_dependencies(True, search) == (
        recorder.SystemDependency.GIFSICLE,
    )
    assert recorder.missing_system_dependencies(False, search) == ()


def test_application_does_not_start_when_dependencies_are_missing(
    monkeypatch,
    capsys,
) -> None:
    class FakeSettingsStore:
        def load(self):
            return SimpleNamespace(options=SimpleNamespace(gifsicle_enabled=True))

    def fail_if_started(_arguments):
        raise AssertionError("QApplication must not be created")

    monkeypatch.setattr(recorder, "RecordingSettingsStore", FakeSettingsStore)
    monkeypatch.setattr(recorder, "QApplication", fail_if_started)

    result = recorder.run_application(
        ["recorder.py"],
        program_search=lambda _name: None,
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "Fehler: Erforderliche Programme fehlen in PATH: "
        "grim, ffmpeg, hyprctl, gifsicle\n"
    )


def test_application_starts_when_dependencies_are_available(monkeypatch) -> None:
    events = []

    class FakeSignal:
        def connect(self, callback):
            events.append(("connect", callback))

    class FakeApplication:
        def __init__(self, arguments):
            events.append(("application", arguments))
            self.aboutToQuit = FakeSignal()

        def exec(self):
            events.append(("exec",))
            return 23

        def quit(self):
            pass

    class FakeController:
        def __init__(self, app):
            events.append(("controller", app))
            self.recording_started = FakeSignal()
            self.recording_finished = FakeSignal()
            self.warning = FakeSignal()
            self.cancelled = FakeSignal()
            self.error = FakeSignal()

        def start(self):
            events.append(("controller-start",))

        def close(self):
            pass

    class FakeServer:
        def __init__(self, controller):
            events.append(("server", controller))

        def start(self):
            events.append(("server-start",))

        def close(self):
            pass

    class FakeSettingsStore:
        def load(self):
            return SimpleNamespace(options=SimpleNamespace(gifsicle_enabled=False))

    monkeypatch.setattr(recorder, "RecordingSettingsStore", FakeSettingsStore)
    monkeypatch.setattr(recorder, "QApplication", FakeApplication)
    monkeypatch.setattr(recorder, "GifRecorderController", FakeController)
    monkeypatch.setattr(recorder, "GifRecorderControlServer", FakeServer)

    result = recorder.run_application(
        ["recorder.py"],
        program_search=lambda name: f"/usr/bin/{name}",
    )

    assert result == 23
    assert [event[0] for event in events if event[0] != "connect"] == [
        "application",
        "controller",
        "server",
        "server-start",
        "controller-start",
        "exec",
    ]


def test_cli_prints_json_response_and_uses_success_exit_code(
    monkeypatch,
    capsys,
) -> None:
    expected = {
        "ok": True,
        "command": "status",
        "recording_state": "idle",
        "selection_active": True,
        "active_output_path": None,
        "last_output_path": None,
    }
    calls = []

    def send(command):
        calls.append(command)
        return expected

    monkeypatch.setattr(recorder, "send_control_command", send)

    assert recorder.main(["recorder.py", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert calls == [ControlCommand.STATUS]


def test_cli_uses_failure_exit_code_for_unreachable_server(
    monkeypatch,
    capsys,
) -> None:
    response = {
        "ok": False,
        "command": "record",
        "recording_state": None,
        "selection_active": False,
        "active_output_path": None,
        "last_output_path": None,
        "error": {
            "code": ControlErrorCode.SERVER_UNAVAILABLE.value,
            "message": "server unavailable",
        },
    }
    monkeypatch.setattr(recorder, "send_control_command", lambda _command: response)

    assert recorder.main(["recorder.py", "record"]) == 1
    assert json.loads(capsys.readouterr().out) == response


def test_cli_rejects_unknown_command_as_json(capsys) -> None:
    assert recorder.main(["recorder.py", "rewind"]) == 2

    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is False
    assert response["command"] == "rewind"
    assert response["error"]["code"] == "unknown_command"
