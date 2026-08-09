"""Application-level recording and GIF draft controller."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QFileDialog, QStyle

from .control import (
    ControlActionResult,
    ControlCommand,
    ControlErrorCode,
    ControlStatus,
)
from .editing import DraftState, GifAnalyzer, GifDraft, GifExporter
from .editor import GifEditorDialog
from region_selector.geometry import Rect
from region_selector.selector import RegionSelector, SelectionInteractionMode

from .recording import GifRecorder, RecordingOptions, RecordingState
from .settings import (
    CompressionSettingsPage,
    RecordingSettingsPage,
    RecordingSettingsStore,
    SettingsDialog,
)


EditorFactory = Callable[[GifDraft], GifEditorDialog]


class GifRecorderController(QObject):
    recording_started = pyqtSignal(object)
    recording_finished = pyqtSignal(object)
    draft_ready = pyqtSignal(object)
    draft_discarded = pyqtSignal()
    cancelled = pyqtSignal()
    warning = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        app: QApplication,
        settings_store: RecordingSettingsStore | None = None,
        recorder: GifRecorder | None = None,
        selector: RegionSelector | None = None,
        parent: QObject | None = None,
        *,
        analyzer: GifAnalyzer | None = None,
        exporter: GifExporter | None = None,
        editor_factory: EditorFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._settings_store = settings_store or RecordingSettingsStore()
        self._recorder = recorder or GifRecorder(parent=self)
        self._analyzer = analyzer or GifAnalyzer(self)
        self._exporter = exporter or GifExporter(parent=self)
        self._editor_factory = editor_factory or GifEditorDialog
        self._selection_active = False
        self._selection_ready_for_recording = False
        self._active_output_path: Path | None = None
        self._last_output_path: Path | None = None
        self._recording_draft_path: Path | None = None
        self._recording_options: RecordingOptions | None = None
        self._draft: GifDraft | None = None
        self._draft_state = DraftState.NONE
        self._editor_dialog: GifEditorDialog | None = None
        self._closing = False
        self._discard_recording_on_finish = False

        style = app.style()
        self.settings_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Settings",
            self,
        )
        self.settings_action.setToolTip("Open recording settings")
        self.pause_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPause),
            "Pause",
            self,
        )
        self.pause_action.setToolTip("Pause recording")
        self.pause_action.setEnabled(False)
        self.edit_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Edit",
            self,
        )
        self.edit_action.setToolTip("Edit GIF frames")
        self.edit_action.setVisible(False)
        self.discard_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon),
            "Discard",
            self,
        )
        self.discard_action.setToolTip("Discard GIF draft")
        self.discard_action.setVisible(False)

        self._selector = selector or RegionSelector(
            toolbar_actions=(
                self.pause_action,
                self.settings_action,
                self.edit_action,
                self.discard_action,
            ),
            confirm_text="Record",
            confirm_icon=style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            auto_close_on_confirm=False,
        )
        self._settings_dialog = SettingsDialog()
        self._settings_dialog.register_page(
            RecordingSettingsPage(self._settings_store)
        )
        self._settings_dialog.register_page(
            CompressionSettingsPage(self._settings_store)
        )

        self.settings_action.triggered.connect(self._settings_dialog.open)
        self.pause_action.triggered.connect(self._pause_action_requested)
        self.edit_action.triggered.connect(self.edit)
        self.discard_action.triggered.connect(self.discard_draft)
        self._selector.geometry_changed.connect(self._selection_ready)
        self._selector.confirmed.connect(self._record_action_requested)
        self._selector.cancelled.connect(self._selection_cancelled)
        self._selector.error.connect(self._selection_failed)
        self._recorder.started.connect(self._recording_started)
        self._recorder.state_changed.connect(self._recording_state_changed)
        self._recorder.finished.connect(self._recording_finished)
        self._recorder.warning.connect(self.warning.emit)
        self._recorder.error.connect(self._recording_failed)
        self._analyzer.finished.connect(self._analysis_finished)
        self._analyzer.error.connect(self._analysis_failed)
        self._exporter.finished.connect(self._export_finished)
        self._exporter.error.connect(self._export_failed)

    @property
    def selector(self) -> RegionSelector:
        return self._selector

    @property
    def recorder(self) -> GifRecorder:
        return self._recorder

    @property
    def settings_dialog(self) -> SettingsDialog:
        return self._settings_dialog

    @property
    def draft_state(self) -> DraftState:
        return self._draft_state

    @property
    def draft(self) -> GifDraft | None:
        return self._draft

    @property
    def editor_dialog(self) -> GifEditorDialog | None:
        return self._editor_dialog

    def start(self) -> None:
        self._selection_active = True
        self._selection_ready_for_recording = False
        self._selector.start()

    def close(self) -> None:
        self._closing = True
        if self._recorder.state is not RecordingState.IDLE:
            if isinstance(self._recorder, GifRecorder):
                self._recorder.abort()
            else:
                self._recorder.stop()
        self._analyzer.stop()
        self._exporter.stop()
        if self._editor_dialog is not None:
            self._editor_dialog.close()
            self._editor_dialog = None
        self._remove_draft_files()
        self._settings_dialog.close()
        self._selector.close()
        self._selection_active = False
        self._selection_ready_for_recording = False

    def record(self) -> ControlActionResult:
        if self._draft_state is not DraftState.NONE or self._draft is not None:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "an unsaved GIF draft must be saved or discarded first",
            )
        if self._recorder.state is not RecordingState.IDLE:
            return self._invalid_state(ControlCommand.RECORD, RecordingState.IDLE)
        if not self._selection_ready_for_recording:
            return ControlActionResult.rejected(
                ControlErrorCode.SELECTION_NOT_READY,
                "the selection is not ready for recording",
            )
        capture_geometry = self._selector.resolve_capture_geometry()
        if capture_geometry is None:
            return ControlActionResult.rejected(
                ControlErrorCode.SELECTION_NOT_READY,
                "the capture geometry could not be resolved",
            )
        if not self._selector.set_marching_ants_visible(False):
            return ControlActionResult.rejected(
                ControlErrorCode.SELECTION_NOT_READY,
                "the selection border could not be hidden",
            )
        preferences = self._settings_store.load()
        target_path = self._settings_store.next_output_path()
        try:
            draft_path = self._create_recording_draft_path(target_path)
        except OSError as exc:
            # FIX: Restore selection feedback when draft allocation fails.
            self._selector.set_marching_ants_visible(True)
            return ControlActionResult.rejected(
                ControlErrorCode.INTERNAL_ERROR,
                f"temporary GIF draft could not be created: {exc}",
            )
        self._active_output_path = target_path
        self._recording_draft_path = draft_path
        self._recording_options = preferences.options
        self._discard_recording_on_finish = False
        self._apply_state_controls(RecordingState.STARTING)
        self._recorder.start(capture_geometry, draft_path, preferences.options)
        return ControlActionResult.accepted()

    def pause(self) -> ControlActionResult:
        if self._recorder.state is not RecordingState.RECORDING:
            return self._invalid_state(ControlCommand.PAUSE, RecordingState.RECORDING)
        self._apply_state_controls(RecordingState.PAUSING)
        self._recorder.pause()
        return ControlActionResult.accepted()

    def resume(self) -> ControlActionResult:
        if self._recorder.state is not RecordingState.PAUSED:
            return self._invalid_state(ControlCommand.RESUME, RecordingState.PAUSED)
        capture_geometry = self._selector.resolve_capture_geometry()
        if capture_geometry is None:
            self._discard_recording_on_finish = True
            self._recorder.stop()
            return ControlActionResult.rejected(
                ControlErrorCode.SELECTION_NOT_READY,
                "the capture geometry could not be resolved",
            )
        if not self._selector.set_marching_ants_visible(False):
            self._discard_recording_on_finish = True
            self._recorder.stop()
            return ControlActionResult.rejected(
                ControlErrorCode.SELECTION_NOT_READY,
                "the selection border could not be hidden",
            )
        self._apply_state_controls(RecordingState.RESUMING)
        self._recorder.resume(capture_geometry)
        return ControlActionResult.accepted()

    def stop(self) -> ControlActionResult:
        allowed_states = (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.PAUSED,
        )
        if self._recorder.state not in allowed_states:
            return self._invalid_state(ControlCommand.STOP, *allowed_states)
        self._draft_state = DraftState.SAVING
        self._apply_state_controls(RecordingState.STOPPING)
        self._recorder.stop()
        return ControlActionResult.accepted()

    def save(self) -> ControlActionResult:
        draft = self._draft
        if self._draft_state is not DraftState.READY or draft is None:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "a ready GIF draft is required for saving",
            )
        if not draft.can_export:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "at least one GIF frame must be kept",
            )
        target_path = self._resolve_target_conflict(draft.target_path)
        if target_path is None:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "saving was cancelled",
            )
        draft.target_path = target_path
        self._active_output_path = target_path
        self._draft_state = DraftState.SAVING
        self._show_draft_actions()
        if self._editor_dialog is not None:
            self._editor_dialog.set_busy(True)
        self._exporter.start(draft)
        return ControlActionResult.accepted()

    def edit(self) -> ControlActionResult:
        draft = self._draft
        if self._draft_state is not DraftState.READY or draft is None:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "a ready GIF draft is required for editing",
            )
        if not draft.frames:
            self.retry_analysis()
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "the GIF draft must be analyzed before editing",
            )
        if self._editor_dialog is not None:
            self._editor_dialog.raise_()
            self._editor_dialog.activateWindow()
            return ControlActionResult.accepted()
        dialog = self._editor_factory(draft)
        self._editor_dialog = dialog
        dialog.finished.connect(self._editor_closed)
        dialog.open()
        return ControlActionResult.accepted()

    def retry_analysis(self) -> ControlActionResult:
        if self._draft_state is not DraftState.READY or self._draft is None:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "a GIF draft with a failed analysis is required",
            )
        self._draft.analysis_error = None
        self._draft_state = DraftState.SAVING
        self._show_draft_actions()
        self._analyzer.start(self._draft.draft_path)
        return ControlActionResult.accepted()

    def discard_draft(self) -> ControlActionResult:
        if self._draft is None and self._recording_draft_path is None:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "there is no GIF draft to discard",
            )
        if self._exporter.busy:
            return ControlActionResult.rejected(
                ControlErrorCode.INVALID_STATE,
                "the GIF draft is currently being saved",
            )
        if self._editor_dialog is not None:
            self._editor_dialog.reject()
            self._editor_dialog = None
        self._remove_draft_files()
        self._draft = None
        self._draft_state = DraftState.NONE
        self._active_output_path = None
        self._recording_options = None
        self._restore_selection_controls()
        self.draft_discarded.emit()
        return ControlActionResult.accepted()

    def cancel(self) -> ControlActionResult:
        if self._recorder.state is not RecordingState.IDLE:
            return self._invalid_state(ControlCommand.CANCEL, RecordingState.IDLE)
        if self._draft is not None or self._recording_draft_path is not None:
            result = self.discard_draft()
            if not result.ok:
                return result
            self._selector.close()
            self._selection_active = False
            self._selection_ready_for_recording = False
            self.cancelled.emit()
            return ControlActionResult.accepted()
        self._selector.cancel()
        return ControlActionResult.accepted()

    def control_status(self) -> ControlStatus:
        return ControlStatus(
            recording_state=self._recorder.state,
            selection_active=self._selection_active,
            active_output_path=self._active_output_path,
            last_output_path=self._last_output_path,
        )

    def _create_recording_draft_path(self, target_path: Path) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target_path.name}.",
            suffix=".draft.gif",
            dir=target_path.parent,
        )
        os.close(descriptor)
        path = Path(name)
        path.unlink()
        return path

    def _selection_ready(self, _geometry: Rect) -> None:
        self._selection_active = True
        self._selection_ready_for_recording = True
        if (
            self._recorder.state is RecordingState.IDLE
            and self._draft_state is DraftState.NONE
        ):
            self._show_record_action()

    def _record_action_requested(self, region: Rect) -> None:
        del region
        self._selection_active = True
        self._selection_ready_for_recording = True
        if self._recorder.state in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.PAUSED,
        ):
            self.stop()
        elif self._draft_state is DraftState.READY:
            self.save()
        elif self._draft_state is DraftState.NONE:
            self.record()

    def _pause_action_requested(self) -> None:
        if self._recorder.state is RecordingState.RECORDING:
            self.pause()
        elif self._recorder.state is RecordingState.PAUSED:
            self.resume()

    def _recording_started(self, _draft_path: Path) -> None:
        if self._active_output_path is not None:
            self.recording_started.emit(self._active_output_path)

    def _recording_state_changed(self, state: RecordingState) -> None:
        if state is RecordingState.PAUSED:
            if not self._selector.set_marching_ants_visible(True):
                self._discard_recording_on_finish = True
                self._recorder.stop()
                return
        elif state is RecordingState.IDLE:
            if not self._selector.set_marching_ants_visible(True):
                return
        self._apply_state_controls(state)

    def _recording_finished(self, draft_path: Path) -> None:
        options = self._recording_options or RecordingOptions()
        target_path = self._active_output_path
        self._recording_draft_path = Path(draft_path)
        if self._discard_recording_on_finish:
            self._discard_recording_on_finish = False
            self._active_output_path = None
            self._recording_options = None
            self._draft_state = DraftState.NONE
            self._remove_draft_files()
            self._restore_selection_controls()
            return
        if target_path is None:
            self._remove_draft_files()
            return
        if self._closing:
            self._remove_draft_files()
            return
        self._draft = GifDraft(Path(draft_path), target_path, (), options)
        self._draft_state = DraftState.SAVING
        self._show_draft_actions()
        self._analyzer.start(Path(draft_path))

    def _analysis_finished(self, frames: object) -> None:
        draft = self._draft
        if draft is None:
            return
        if self._closing:
            self._remove_draft_files()
            return
        draft.frames = tuple(frames)
        draft.analysis_error = None
        self._draft_state = DraftState.READY
        self._show_draft_actions()
        self.draft_ready.emit(draft)

    def _analysis_failed(self, message: str) -> None:
        draft = self._draft
        if draft is None:
            return
        draft.analysis_error = message
        self._draft_state = DraftState.READY
        self._show_draft_actions()
        self.error.emit(message)

    def _export_finished(self, output_path: Path) -> None:
        self._last_output_path = Path(output_path)
        self._active_output_path = None
        self._recording_draft_path = None
        self._recording_options = None
        self._draft = None
        self._draft_state = DraftState.NONE
        if self._editor_dialog is not None:
            self._editor_dialog.close()
            self._editor_dialog = None
        self._restore_selection_controls()
        self.recording_finished.emit(Path(output_path))

    def _export_failed(self, message: str) -> None:
        self._draft_state = DraftState.READY
        if self._editor_dialog is not None:
            self._editor_dialog.set_busy(False)
        self._show_draft_actions()
        self.error.emit(message)

    def _recording_failed(self, message: str) -> None:
        self._remove_draft_files()
        self._draft = None
        self._draft_state = DraftState.NONE
        self._active_output_path = None
        self._recording_options = None
        self.error.emit(message)

    def _selection_failed(self, message: str) -> None:
        self._selection_active = False
        self._selection_ready_for_recording = False
        self.error.emit(message)
        if self._recorder.state is RecordingState.IDLE:
            self.cancelled.emit()
        else:
            self._discard_recording_on_finish = True
            self._recorder.stop()

    def _selection_cancelled(self) -> None:
        self._selection_active = False
        self._selection_ready_for_recording = False
        self.cancelled.emit()

    def _editor_closed(self, _result: int) -> None:
        self._editor_dialog = None
        if self._draft_state is DraftState.READY:
            self._show_draft_actions()

    def _resolve_target_conflict(self, target_path: Path) -> Path | None:
        if not target_path.exists():
            return target_path
        selected, _filter = QFileDialog.getSaveFileName(
            None,
            "Save GIF",
            str(target_path),
            "GIF images (*.gif)",
        )
        if not selected:
            return None
        result = Path(selected)
        if result.suffix.lower() != ".gif":
            result = result.with_suffix(".gif")
        if result.exists():
            self.error.emit(f"output file already exists: {result}")
            return None
        return result

    def _invalid_state(
        self,
        command: ControlCommand,
        *allowed_states: RecordingState,
    ) -> ControlActionResult:
        allowed = ", ".join(state.name.lower() for state in allowed_states)
        return ControlActionResult.rejected(
            ControlErrorCode.INVALID_STATE,
            f"{command.value} requires recording state {allowed}; current state is "
            f"{self._recorder.state.name.lower()}",
        )

    def _remove_draft_files(self) -> None:
        paths = {
            path
            for path in (
                self._recording_draft_path,
                self._draft.draft_path if self._draft is not None else None,
            )
            if path is not None
        }
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._recording_draft_path = None

    def _set_cancel_visible(self, visible: bool) -> None:
        if isinstance(self._selector, RegionSelector):
            self._selector.set_cancel_visible(visible)

    def _restore_selection_controls(self) -> None:
        self._selector.set_interaction_mode(SelectionInteractionMode.FULL)
        self.settings_action.setVisible(True)
        self.settings_action.setEnabled(True)
        self.pause_action.setVisible(True)
        self.pause_action.setEnabled(False)
        self.edit_action.setVisible(False)
        self.discard_action.setVisible(False)
        self._set_cancel_visible(True)
        self._set_pause_presentation(False)
        self._show_record_action()

    def _show_record_action(self) -> None:
        self._selector.set_confirm_presentation(
            "Record",
            self._app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
        )
        self._selector.set_confirm_enabled(True)

    def _show_draft_actions(self) -> None:
        ready = self._draft_state is DraftState.READY
        has_frames = self._draft is not None and bool(self._draft.frames)
        can_retry_analysis = (
            self._draft is not None and self._draft.analysis_error is not None
        )
        can_save = self._draft is not None and self._draft.can_export
        self._selector.set_interaction_mode(SelectionInteractionMode.DISABLED)
        self.settings_action.setVisible(False)
        self.pause_action.setVisible(False)
        self.edit_action.setVisible(True)
        self.discard_action.setVisible(True)
        self.edit_action.setEnabled(ready and (has_frames or can_retry_analysis))
        self.discard_action.setEnabled(ready)
        self._set_cancel_visible(False)
        self._selector.set_confirm_presentation(
            "Save",
            self._app.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
        )
        self._selector.set_confirm_enabled(ready and can_save)

    def _apply_state_controls(self, state: RecordingState) -> None:
        if state is RecordingState.IDLE:
            if self._draft_state is DraftState.NONE:
                self._restore_selection_controls()
            else:
                self._show_draft_actions()
            return

        self.settings_action.setVisible(True)
        self.pause_action.setVisible(True)
        self.edit_action.setVisible(False)
        self.discard_action.setVisible(False)
        self._set_cancel_visible(True)
        self.settings_action.setEnabled(False)
        self._selector.set_confirm_presentation(
            "Stop",
            self._app.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop),
        )
        if state is RecordingState.PAUSED:
            self._selector.set_interaction_mode(SelectionInteractionMode.MOVE_ONLY)
            self._selector.set_confirm_enabled(True)
            self._set_pause_presentation(True)
            self.pause_action.setEnabled(True)
            return

        self._selector.set_interaction_mode(SelectionInteractionMode.DISABLED)
        self._set_pause_presentation(False)
        if state is RecordingState.RECORDING:
            self._selector.set_confirm_enabled(True)
            self.pause_action.setEnabled(True)
        elif state is RecordingState.STARTING:
            self._selector.set_confirm_enabled(True)
            self.pause_action.setEnabled(False)
        else:
            self._selector.set_confirm_enabled(False)
            self.pause_action.setEnabled(False)

    def _set_pause_presentation(self, resume: bool) -> None:
        if resume:
            self.pause_action.setText("Resume")
            self.pause_action.setIcon(
                self._app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            )
            self.pause_action.setToolTip("Resume recording")
        else:
            self.pause_action.setText("Pause")
            self.pause_action.setIcon(
                self._app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
            )
            self.pause_action.setToolTip("Pause recording")
