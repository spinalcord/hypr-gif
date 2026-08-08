from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from region_selector import Rect, SelectionInteractionMode
from region_selector.application import GifRecorderController
from region_selector.editing import DraftState, GifFrameMetadata
from region_selector.recording import RecordingState
from region_selector.settings import RecordingSettingsStore


def configure_output(settings: QSettings, tmp_path) -> None:
    settings.setValue("recording/output_directory", str(tmp_path))
    settings.setValue("recording/filename_prefix", "test-recording")


class FakeSelector(QObject):
    geometry_changed = pyqtSignal(object)
    confirmed = pyqtSignal(object)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.interaction_enabled = True
        self.interaction_mode = SelectionInteractionMode.FULL
        self.confirm_enabled = True
        self.confirm_text = ""
        self.geometry = Rect(10, 20, 300, 200)
        self.capture_geometry = Rect(12, 22, 296, 196)
        self.resolve_calls = 0
        self.ants_visible_calls = []
        self.ants_visibility_result = True
        self.events = []
        self.start_calls = 0
        self.close_calls = 0
        self.cancel_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancelled.emit()

    def set_interaction_enabled(self, enabled) -> None:
        self.interaction_enabled = enabled

    def set_interaction_mode(self, mode) -> None:
        self.interaction_mode = mode
        self.interaction_enabled = mode is not SelectionInteractionMode.DISABLED

    def set_marching_ants_visible(self, visible) -> bool:
        self.ants_visible_calls.append(visible)
        self.events.append(("ants", visible))
        return self.ants_visibility_result

    def resolve_capture_geometry(self):
        self.resolve_calls += 1
        self.events.append(("resolve", self.capture_geometry))
        return self.capture_geometry

    def set_confirm_presentation(self, text, _icon) -> None:
        self.confirm_text = text

    def set_confirm_enabled(self, enabled) -> None:
        self.confirm_enabled = enabled


class FakeRecorder(QObject):
    state_changed = pyqtSignal(object)
    started = pyqtSignal(object)
    finished = pyqtSignal(object)
    warning = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.state = RecordingState.IDLE
        self.start_calls = []
        self.stop_calls = 0
        self.pause_calls = 0
        self.resume_calls = []
        self.output_path = None
        self.events = []

    def _set_state(self, state) -> None:
        self.state = state
        self.state_changed.emit(state)

    def start(self, region, output_path, options) -> None:
        self.start_calls.append((region, output_path, options))
        self.events.append(("start", region))
        self.output_path = output_path
        self._set_state(RecordingState.STARTING)
        self._set_state(RecordingState.RECORDING)
        self.started.emit(output_path)

    def stop(self) -> None:
        self.stop_calls += 1
        self._set_state(RecordingState.STOPPING)
        self._set_state(RecordingState.IDLE)
        self.finished.emit(self.output_path)

    def pause(self) -> None:
        self.pause_calls += 1
        self._set_state(RecordingState.PAUSING)
        self._set_state(RecordingState.PAUSED)

    def resume(self, region) -> None:
        self.resume_calls.append(region)
        self.events.append(("resume", region))
        self._set_state(RecordingState.RESUMING)
        self._set_state(RecordingState.RECORDING)


class FileFakeRecorder(FakeRecorder):
    def stop(self) -> None:
        self.output_path.write_bytes(b"temporary-gif")
        super().stop()


class ImmediateAnalyzer(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    @property
    def busy(self) -> bool:
        return False

    def start(self, _path) -> None:
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        self.finished.emit((GifFrameMetadata(0, 100, image),))

    def stop(self) -> None:
        pass


class ImmediateExporter(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    @property
    def busy(self) -> bool:
        return False

    def start(self, draft) -> None:
        draft.target_path.write_bytes(draft.draft_path.read_bytes())
        draft.draft_path.unlink()
        self.finished.emit(draft.target_path)

    def stop(self) -> None:
        pass


def test_controller_turns_record_action_into_draft_actions(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    store = RecordingSettingsStore(settings)
    selector = FakeSelector()
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=store,
        recorder=recorder,
        selector=selector,
    )
    finished = []
    controller.recording_finished.connect(finished.append)
    region = Rect(10, 20, 300, 200)

    selector.confirmed.emit(region)

    assert len(recorder.start_calls) == 1
    assert recorder.start_calls[0][0] == selector.capture_geometry
    assert selector.resolve_calls == 1
    assert selector.interaction_enabled is False
    assert selector.interaction_mode is SelectionInteractionMode.DISABLED
    assert selector.confirm_text == "Stop"
    assert controller.pause_action.isEnabled() is True
    assert controller.settings_action.isEnabled() is False

    selector.confirmed.emit(region)

    assert recorder.stop_calls == 1
    assert selector.interaction_enabled is False
    assert selector.confirm_text == "Save"
    assert controller.settings_action.isVisible() is False
    assert controller.edit_action.isVisible() is True
    assert controller.discard_action.isVisible() is True
    assert controller.draft_state is DraftState.READY
    assert finished == []
    assert controller.discard_draft().ok is True
    assert selector.interaction_enabled is True
    assert selector.confirm_text == "Record"
    controller.close()


def test_controller_pauses_moves_and_resumes_with_updated_region(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    selector = FakeSelector()
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=selector,
    )

    selector.confirmed.emit(selector.geometry)
    assert selector.ants_visible_calls == [False]
    assert recorder.events[0] == ("start", selector.capture_geometry)
    assert selector.events[:2] == [
        ("resolve", selector.capture_geometry),
        ("ants", False),
    ]

    controller.pause_action.trigger()

    assert recorder.pause_calls == 1
    assert selector.ants_visible_calls == [False, True]
    assert selector.interaction_mode is SelectionInteractionMode.MOVE_ONLY
    assert selector.confirm_enabled is True
    assert controller.pause_action.text() == "Resume"
    assert controller.pause_action.isEnabled() is True
    assert controller.settings_action.isEnabled() is False

    selector.geometry = Rect(400, 250, 300, 200)
    selector.capture_geometry = Rect(402, 252, 296, 196)
    controller.pause_action.trigger()

    assert selector.ants_visible_calls == [False, True, False]
    assert recorder.resume_calls == [selector.capture_geometry]
    assert selector.events[-2:] == [
        ("resolve", selector.capture_geometry),
        ("ants", False),
    ]
    assert selector.interaction_mode is SelectionInteractionMode.DISABLED
    assert controller.pause_action.text() == "Pause"
    assert controller.pause_action.isEnabled() is True

    selector.confirmed.emit(selector.geometry)
    assert selector.ants_visible_calls == [False, True, False, True]
    assert recorder.stop_calls == 1
    controller.close()


def test_controller_stops_safely_when_hiding_ants_for_resume_fails(
    tmp_path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    selector = FakeSelector()
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=selector,
    )
    selector.confirmed.emit(selector.geometry)
    controller.pause_action.trigger()
    selector.ants_visibility_result = False

    controller.pause_action.trigger()

    assert recorder.resume_calls == []
    assert recorder.stop_calls == 1
    assert recorder.state is RecordingState.IDLE
    controller.close()


def test_controller_does_not_start_when_capture_geometry_cannot_be_resolved(
    tmp_path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    selector = FakeSelector()
    selector.capture_geometry = None
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=selector,
    )

    selector.confirmed.emit(selector.geometry)

    assert recorder.start_calls == []
    assert selector.ants_visible_calls == []
    controller.close()


def test_controller_stops_paused_recording_when_resume_geometry_fails(
    tmp_path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    selector = FakeSelector()
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=selector,
    )
    selector.confirmed.emit(selector.geometry)
    controller.pause_action.trigger()
    selector.capture_geometry = None

    controller.pause_action.trigger()

    assert recorder.resume_calls == []
    assert recorder.stop_calls == 1
    assert recorder.state is RecordingState.IDLE
    assert selector.ants_visible_calls == [False, True, True]
    controller.close()


def test_public_controller_commands_validate_state_and_report_paths(
    tmp_path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    selector = FakeSelector()
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=selector,
    )

    result = controller.record()
    assert result.ok is False
    assert result.error_code.value == "selection_not_ready"

    controller.start()
    assert controller.control_status().selection_active is True
    selector.geometry_changed.emit(selector.geometry)
    result = controller.record()

    assert result.ok is True
    active_path = controller.control_status().active_output_path
    assert recorder.state is RecordingState.RECORDING
    assert controller.control_status().active_output_path == active_path
    assert recorder.output_path != active_path
    assert controller.record().error_code.value == "invalid_state"
    assert controller.resume().error_code.value == "invalid_state"
    assert controller.cancel().error_code.value == "invalid_state"

    assert controller.pause().ok is True
    assert recorder.state is RecordingState.PAUSED
    assert controller.pause().error_code.value == "invalid_state"
    assert controller.resume().ok is True
    assert recorder.state is RecordingState.RECORDING
    assert controller.stop().ok is True

    status = controller.control_status()
    assert status.recording_state is RecordingState.IDLE
    assert status.active_output_path == active_path
    assert status.last_output_path is None
    assert controller.stop().error_code.value == "invalid_state"
    assert controller.pause().error_code.value == "invalid_state"
    assert controller.resume().error_code.value == "invalid_state"

    cancelled = []
    controller.cancelled.connect(lambda: cancelled.append(True))
    assert controller.cancel().ok is True
    assert selector.cancel_calls == 0
    assert cancelled == [True]
    assert controller.control_status().selection_active is False
    controller.close()


def test_public_stop_accepts_starting_and_paused_states(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)

    for state in (RecordingState.STARTING, RecordingState.PAUSED):
        selector = FakeSelector()
        recorder = FakeRecorder()
        recorder.state = state
        recorder.output_path = tmp_path / f"{state.name.lower()}.gif"
        controller = GifRecorderController(
            app,
            settings_store=RecordingSettingsStore(settings),
            recorder=recorder,
            selector=selector,
        )

        assert controller.stop().ok is True
        assert recorder.stop_calls == 1
        assert recorder.state is RecordingState.IDLE
        controller.close()


def test_controller_forwards_recording_warning(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    recorder = FakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=FakeSelector(),
    )
    warnings = []
    controller.warning.connect(warnings.append)

    recorder.warning.emit("optimization failed")

    assert warnings == ["optimization failed"]
    controller.close()


def test_recording_is_only_published_and_signalled_after_save(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    configure_output(settings, tmp_path)
    selector = FakeSelector()
    recorder = FileFakeRecorder()
    controller = GifRecorderController(
        app,
        settings_store=RecordingSettingsStore(settings),
        recorder=recorder,
        selector=selector,
        analyzer=ImmediateAnalyzer(),
        exporter=ImmediateExporter(),
    )
    finished = []
    controller.recording_finished.connect(finished.append)
    selector.geometry_changed.emit(selector.geometry)

    assert controller.record().ok is True
    public_path = controller.control_status().active_output_path
    assert public_path is not None
    assert public_path != recorder.output_path
    assert public_path.exists() is False
    assert controller.stop().ok is True
    assert controller.draft_state is DraftState.READY
    assert finished == []

    assert controller.save().ok is True
    assert public_path.read_bytes() == b"temporary-gif"
    assert controller.control_status().active_output_path is None
    assert controller.control_status().last_output_path == public_path
    assert finished == [public_path]
    controller.close()
