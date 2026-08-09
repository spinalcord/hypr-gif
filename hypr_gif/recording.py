"""Scalable frame-source and encoder abstractions for GIF recording."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, Qt, pyqtSignal

from region_selector.geometry import Rect, normalize_rect


class RecordingState(Enum):
    IDLE = auto()
    STARTING = auto()
    RECORDING = auto()
    PAUSING = auto()
    PAUSED = auto()
    RESUMING = auto()
    STOPPING = auto()


class EncodingStage(Enum):
    FFMPEG = auto()
    GIFSICLE = auto()


class CompressionPreset(str, Enum):
    HIGH_QUALITY = "high_quality"
    BALANCED = "balanced"
    SMALL_FILE = "small_file"
    CUSTOM = "custom"


COMPRESSION_PRESET_VALUES = {
    CompressionPreset.HIGH_QUALITY: (True, 1440, 256, 0),
    CompressionPreset.BALANCED: (True, 960, 128, 40),
    CompressionPreset.SMALL_FILE: (True, 720, 96, 80),
}


@dataclass(frozen=True, slots=True)
class RecordingOptions:
    frames_per_second: int = 15
    max_colors: int = 128
    include_cursor: bool = False
    compression_preset: CompressionPreset = CompressionPreset.BALANCED
    gifsicle_enabled: bool = True
    max_width: int = 960
    lossy_strength: int = 40

    def __post_init__(self) -> None:
        try:
            preset = CompressionPreset(self.compression_preset)
        except ValueError as exc:
            raise ValueError("compression_preset is invalid") from exc
        object.__setattr__(self, "compression_preset", preset)
        if not 1 <= self.frames_per_second <= 60:
            raise ValueError("frames_per_second must be between 1 and 60")
        if not 2 <= self.max_colors <= 256:
            raise ValueError("max_colors must be between 2 and 256")
        if not 1 <= self.max_width <= 16384:
            raise ValueError("max_width must be between 1 and 16384")
        if not 0 <= self.lossy_strength <= 200:
            raise ValueError("lossy_strength must be between 0 and 200")


def compression_preset_values(
    preset: CompressionPreset,
) -> tuple[bool, int, int, int]:
    preset = CompressionPreset(preset)
    if preset is CompressionPreset.CUSTOM:
        raise ValueError("custom compression has no preset values")
    return COMPRESSION_PRESET_VALUES[preset]


def grim_arguments(region: Rect, include_cursor: bool) -> tuple[str, ...]:
    region = normalize_rect(region)
    arguments = [
        "-s",
        "1",
        "-g",
        f"{region.x},{region.y} {region.width}x{region.height}",
        "-t",
        "png",
    ]
    if include_cursor:
        arguments.append("-c")
    arguments.append("-")
    return tuple(arguments)


def ffmpeg_gif_arguments(
    options: RecordingOptions,
    output_path: Path,
) -> tuple[str, ...]:
    palette_filter = (
        f"[0:v]scale=w='min(iw,{options.max_width})':h=-1:flags=lanczos,"
        "split[frames][palette_input];"
        "[palette_input]palettegen=stats_mode=single:"
        f"max_colors={options.max_colors}[palette];"
        "[frames][palette]paletteuse=new=1:dither=bayer:bayer_scale=3"
    )
    return (
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-framerate",
        str(options.frames_per_second),
        "-vcodec",
        "png",
        "-i",
        "pipe:0",
        "-filter_complex",
        palette_filter,
        "-loop",
        "0",
        "-n",
        str(output_path),
    )


def gifsicle_arguments(
    options: RecordingOptions,
    input_path: Path,
    output_path: Path,
) -> tuple[str, ...]:
    arguments = ["--optimize=3"]
    if options.lossy_strength > 0:
        arguments.append(f"--lossy={options.lossy_strength}")
    arguments.extend((str(input_path), "--output", str(output_path)))
    return tuple(arguments)


class FrameSource(QObject):
    frame_ready = pyqtSignal(bytes)
    started = pyqtSignal()
    paused = pyqtSignal()
    resumed = pyqtSignal()
    stopped = pyqtSignal()
    error = pyqtSignal(str)

    def start(self, region: Rect, options: RecordingOptions) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def resume(self, region: Rect) -> None:
        raise NotImplementedError


class FrameEncoder(QObject):
    started = pyqtSignal()
    finished = pyqtSignal(object)
    aborted = pyqtSignal()
    warning = pyqtSignal(str)
    error = pyqtSignal(str)

    def start(self, output_path: Path, options: RecordingOptions) -> None:
        raise NotImplementedError

    def write_frame(self, frame: bytes) -> None:
        raise NotImplementedError

    def finish(self) -> None:
        raise NotImplementedError

    def abort(self) -> None:
        raise NotImplementedError


class GrimFrameSource(FrameSource):
    def __init__(
        self,
        executable: str = "grim",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._executable = executable
        self._region = Rect(0, 0, 0, 0)
        self._options = RecordingOptions()
        self._active = False
        self._paused = False
        self._pause_pending = False
        self._stop_pending = False
        self._process: QProcess | None = None
        self._last_frame: bytes | None = None

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._capture_frame)

    @property
    def active(self) -> bool:
        return self._active

    def start(self, region: Rect, options: RecordingOptions) -> None:
        if self._active or self._paused or self._process is not None:
            self.error.emit("the frame source is already active")
            return
        region = normalize_rect(region)
        if region.width <= 0 or region.height <= 0:
            self.error.emit("the recording region must have a positive size")
            return
        self._region = region
        self._options = options
        self._active = True
        self._paused = False
        self._pause_pending = False
        self._stop_pending = False
        self._last_frame = None
        self._timer.setInterval(max(1, round(1000 / options.frames_per_second)))
        self._timer.start()
        self.started.emit()
        self._capture_frame()

    def stop(self) -> None:
        if not self._active and not self._paused and not self._pause_pending:
            if self._process is None and not self._stop_pending:
                self.stopped.emit()
            return
        self._active = False
        self._paused = False
        self._pause_pending = False
        self._stop_pending = True
        self._timer.stop()
        if self._process is None:
            self._complete_stop()

    def pause(self) -> None:
        if self._paused:
            self.paused.emit()
            return
        if not self._active:
            self.error.emit("the frame source is not recording")
            return
        self._active = False
        self._pause_pending = True
        self._timer.stop()
        if self._process is None:
            self._complete_pause()

    def resume(self, region: Rect) -> None:
        if not self._paused or self._process is not None:
            self.error.emit("the frame source is not paused")
            return
        region = normalize_rect(region)
        if region.width <= 0 or region.height <= 0:
            self.error.emit("the recording region must have a positive size")
            return
        self._region = region
        self._paused = False
        self._active = True
        self._timer.start()
        self.resumed.emit()
        self._capture_frame()

    def _capture_frame(self) -> None:
        if not self._active:
            return
        if self._process is not None:
            if self._last_frame is not None:
                self.frame_ready.emit(self._last_frame)
            return
        process = QProcess(self)
        self._process = process
        process.finished.connect(
            lambda exit_code, exit_status: self._capture_finished(
                process, exit_code, exit_status
            )
        )
        process.errorOccurred.connect(
            lambda process_error: self._process_failed(process, process_error)
        )
        process.start(
            self._executable,
            list(grim_arguments(self._region, self._options.include_cursor)),
            QProcess.OpenModeFlag.ReadOnly,
        )

    def _capture_finished(
        self,
        process: QProcess,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if process is not self._process:
            return
        standard_error = bytes(process.readAllStandardError()).decode(
            errors="replace"
        )
        frame = bytes(process.readAllStandardOutput())
        self._release_process(process)
        if exit_status is not QProcess.ExitStatus.NormalExit or exit_code != 0:
            detail = standard_error.strip() or f"grim exited with code {exit_code}"
            self._fail(detail)
            return
        if not frame:
            self._fail("grim returned an empty frame")
            return
        self._last_frame = frame
        self.frame_ready.emit(frame)
        if self._stop_pending:
            self._complete_stop()
        elif self._pause_pending:
            self._complete_pause()

    def _process_failed(
        self,
        process: QProcess,
        process_error: QProcess.ProcessError,
    ) -> None:
        if process is not self._process:
            return
        if process_error is not QProcess.ProcessError.FailedToStart:
            return
        message = process.errorString() or "grim could not be started"
        self._release_process(process)
        self._fail(message)

    def _release_process(self, process: QProcess) -> None:
        self._process = None
        process.deleteLater()

    def _fail(self, message: str) -> None:
        self._active = False
        self._paused = False
        self._pause_pending = False
        self._stop_pending = False
        self._last_frame = None
        self._timer.stop()
        self.error.emit(f"screen capture failed: {message}")

    def _complete_pause(self) -> None:
        self._pause_pending = False
        self._paused = True
        self.paused.emit()

    def _complete_stop(self) -> None:
        self._stop_pending = False
        self._last_frame = None
        self.stopped.emit()


class FfmpegGifEncoder(FrameEncoder):
    def __init__(
        self,
        executable: str = "ffmpeg",
        gifsicle_executable: str = "gifsicle",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._executable = executable
        self._gifsicle_executable = gifsicle_executable
        self._process: QProcess | None = None
        self._output_path: Path | None = None
        self._temporary_path: Path | None = None
        self._options: RecordingOptions | None = None
        self._stage: EncodingStage | None = None
        self._aborting = False

    @property
    def active(self) -> bool:
        return self._process is not None

    def start(self, output_path: Path, options: RecordingOptions) -> None:
        if self._process is not None:
            self.error.emit("the GIF encoder is already active")
            return
        output_path = Path(output_path)
        if output_path.exists():
            self.error.emit(f"output file already exists: {output_path}")
            return
        if (
            options.gifsicle_enabled
            and shutil.which(self._gifsicle_executable) is None
        ):
            self.error.emit(
                "GIF optimization is enabled, but gifsicle is not available in PATH"
            )
            return
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error.emit(f"output directory could not be created: {exc}")
            return

        self._output_path = output_path
        self._options = options
        self._aborting = False
        self._start_process(
            EncodingStage.FFMPEG,
            self._executable,
            list(ffmpeg_gif_arguments(options, output_path)),
            QProcess.OpenModeFlag.WriteOnly,
        )

    def write_frame(self, frame: bytes) -> None:
        process = self._process
        if process is None or process.state() is not QProcess.ProcessState.Running:
            return
        process.write(frame)

    def finish(self) -> None:
        process = self._process
        if process is None:
            return
        process.closeWriteChannel()

    def abort(self) -> None:
        process = self._process
        if process is None:
            self._remove_temporary_output()
            self._reset()
            self.aborted.emit()
            return
        self._aborting = True
        process.kill()

    def _start_process(
        self,
        stage: EncodingStage,
        executable: str,
        arguments: list[str],
        mode: QProcess.OpenModeFlag,
    ) -> None:
        process = QProcess(self)
        self._process = process
        self._stage = stage
        if stage is EncodingStage.FFMPEG:
            process.started.connect(self.started.emit)
        process.finished.connect(
            lambda exit_code, exit_status: self._process_finished(
                process, exit_code, exit_status
            )
        )
        process.errorOccurred.connect(
            lambda process_error: self._process_failed(process, process_error)
        )
        process.start(executable, arguments, mode)

    def _process_finished(
        self,
        process: QProcess,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        if process is not self._process:
            return
        standard_error = bytes(process.readAllStandardError()).decode(
            errors="replace"
        )
        stage = self._stage
        output_path = self._output_path
        aborting = self._aborting
        self._release_process(process)
        if aborting:
            if stage is EncodingStage.FFMPEG:
                self._remove_partial_output(output_path)
            self._remove_temporary_output()
            self._reset()
            self.aborted.emit()
            return
        if exit_status is not QProcess.ExitStatus.NormalExit or exit_code != 0:
            executable = "ffmpeg" if stage is EncodingStage.FFMPEG else "gifsicle"
            detail = standard_error.strip() or (
                f"{executable} exited with code {exit_code}"
            )
            if stage is EncodingStage.GIFSICLE:
                self._postprocessing_failed(detail)
                return
            self._remove_partial_output(output_path)
            self._reset()
            self.error.emit(f"GIF encoding failed: {detail}")
            return
        if output_path is None or not output_path.is_file():
            self._reset()
            self.error.emit("GIF encoding finished without an output file")
            return
        if stage is EncodingStage.FFMPEG:
            self._start_postprocessing(output_path)
            return
        self._complete_postprocessing(output_path)

    def _process_failed(
        self,
        process: QProcess,
        process_error: QProcess.ProcessError,
    ) -> None:
        if process is not self._process:
            return
        if process_error is not QProcess.ProcessError.FailedToStart:
            return
        stage = self._stage
        executable = "ffmpeg" if stage is EncodingStage.FFMPEG else "gifsicle"
        message = process.errorString() or f"{executable} could not be started"
        output_path = self._output_path
        self._release_process(process)
        if stage is EncodingStage.GIFSICLE:
            self._postprocessing_failed(message)
            return
        self._remove_partial_output(output_path)
        self._reset()
        self.error.emit(f"GIF encoding failed: {message}")

    def _start_postprocessing(self, output_path: Path) -> None:
        options = self._options
        if options is None or not options.gifsicle_enabled:
            self._reset()
            self.finished.emit(output_path)
            return
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output_path.name}.",
                suffix=".gifsicle.tmp",
                dir=output_path.parent,
            )
            os.close(file_descriptor)
        except OSError as exc:
            self._postprocessing_failed(
                f"temporary output could not be created: {exc}"
            )
            return
        self._temporary_path = Path(temporary_name)
        self._start_process(
            EncodingStage.GIFSICLE,
            self._gifsicle_executable,
            list(gifsicle_arguments(options, output_path, self._temporary_path)),
            QProcess.OpenModeFlag.ReadOnly,
        )

    def _complete_postprocessing(self, output_path: Path) -> None:
        temporary_path = self._temporary_path
        if temporary_path is None or not temporary_path.is_file():
            self._postprocessing_failed("gifsicle produced no output file")
            return
        try:
            if temporary_path.stat().st_size == 0:
                self._postprocessing_failed("gifsicle produced an empty output file")
                return
        except OSError as exc:
            self._postprocessing_failed(f"gifsicle output could not be read: {exc}")
            return
        try:
            temporary_path.replace(output_path)
        except OSError as exc:
            self._postprocessing_failed(
                f"optimized GIF could not be installed: {exc}"
            )
            return
        self._temporary_path = None
        self._reset()
        self.finished.emit(output_path)

    def _postprocessing_failed(self, detail: str) -> None:
        output_path = self._output_path
        self._remove_temporary_output()
        self._reset()
        self.warning.emit(
            f"GIF optimization failed; keeping the FFmpeg GIF: {detail}"
        )
        if output_path is not None and output_path.is_file():
            self.finished.emit(output_path)
            return
        self.error.emit("GIF optimization failed and the FFmpeg GIF is unavailable")

    def _release_process(self, process: QProcess) -> None:
        self._process = None
        self._stage = None
        process.deleteLater()

    def _reset(self) -> None:
        self._process = None
        self._output_path = None
        self._temporary_path = None
        self._options = None
        self._stage = None
        self._aborting = False

    def _remove_temporary_output(self) -> None:
        temporary_path = self._temporary_path
        self._temporary_path = None
        if temporary_path is None:
            return
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _remove_partial_output(self, output_path: Path | None) -> None:
        if output_path is None:
            return
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass


class GifRecorder(QObject):
    state_changed = pyqtSignal(object)
    started = pyqtSignal(object)
    paused = pyqtSignal()
    resumed = pyqtSignal(object)
    finished = pyqtSignal(object)
    aborted = pyqtSignal()
    warning = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        frame_source: FrameSource | None = None,
        encoder: FrameEncoder | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame_source = (
            frame_source
            if frame_source is not None
            else GrimFrameSource(parent=self)
        )
        self._encoder = (
            encoder if encoder is not None else FfmpegGifEncoder(parent=self)
        )
        self._state = RecordingState.IDLE
        self._output_path: Path | None = None
        self._pending_region = Rect(0, 0, 0, 0)
        self._pending_options = RecordingOptions()
        self._source_active = False
        self._encoder_active = False
        self._pending_error: str | None = None
        self._aborting = False

        self._frame_source.started.connect(self._source_started)
        self._frame_source.paused.connect(self._source_paused)
        self._frame_source.resumed.connect(self._source_resumed)
        self._frame_source.stopped.connect(self._source_stopped)
        self._frame_source.frame_ready.connect(self._encoder.write_frame)
        self._frame_source.error.connect(self._source_failed)
        self._encoder.started.connect(self._encoder_started)
        self._encoder.finished.connect(self._encoder_finished)
        self._encoder.aborted.connect(self._encoder_aborted)
        self._encoder.warning.connect(self.warning.emit)
        self._encoder.error.connect(self._encoder_failed)

    @property
    def state(self) -> RecordingState:
        return self._state

    def start(
        self,
        region: Rect,
        output_path: Path,
        options: RecordingOptions,
    ) -> None:
        if self._state is not RecordingState.IDLE:
            self.error.emit("a recording is already active")
            return
        self._output_path = Path(output_path)
        self._pending_region = region
        self._pending_options = options
        self._pending_error = None
        self._aborting = False
        self._encoder_active = True
        self._set_state(RecordingState.STARTING)
        self._encoder.start(self._output_path, options)

    def stop(self) -> None:
        if self._state is RecordingState.IDLE:
            return
        self._set_state(RecordingState.STOPPING)
        if self._source_active:
            self._frame_source.stop()
        elif self._encoder_active:
            self._encoder.finish()

    def abort(self) -> None:
        if self._state is RecordingState.IDLE:
            return
        self._aborting = True
        self._set_state(RecordingState.STOPPING)
        if self._source_active:
            self._frame_source.stop()
        elif self._encoder_active:
            self._encoder.abort()
        else:
            self._complete_abort()

    def pause(self) -> None:
        if self._state is not RecordingState.RECORDING:
            return
        self._set_state(RecordingState.PAUSING)
        if self._state is not RecordingState.PAUSING:
            return
        self._frame_source.pause()

    def resume(self, region: Rect) -> None:
        if self._state is not RecordingState.PAUSED:
            return
        self._pending_region = region
        self._set_state(RecordingState.RESUMING)
        if self._state is not RecordingState.RESUMING:
            return
        self._frame_source.resume(region)

    def _encoder_started(self) -> None:
        if self._state is not RecordingState.STARTING:
            self._encoder.finish()
            return
        self._source_active = True
        self._frame_source.start(self._pending_region, self._pending_options)

    def _source_started(self) -> None:
        if self._state is not RecordingState.STARTING:
            return
        self._set_state(RecordingState.RECORDING)
        self.started.emit(self._output_path)

    def _source_paused(self) -> None:
        if self._state is RecordingState.STOPPING:
            self._frame_source.stop()
            return
        if self._state is not RecordingState.PAUSING:
            return
        self._set_state(RecordingState.PAUSED)
        self.paused.emit()

    def _source_resumed(self) -> None:
        if self._state is RecordingState.STOPPING:
            self._frame_source.stop()
            return
        if self._state is not RecordingState.RESUMING:
            return
        self._set_state(RecordingState.RECORDING)
        self.resumed.emit(self._pending_region)

    def _source_stopped(self) -> None:
        self._source_active = False
        if not self._encoder_active:
            if self._aborting:
                self._complete_abort()
                return
            self._complete_pending_error()
            return
        if self._pending_error is not None or self._aborting:
            self._encoder.abort()
        else:
            self._encoder.finish()

    def _source_failed(self, message: str) -> None:
        self._source_active = False
        self._pending_error = message
        self._set_state(RecordingState.STOPPING)
        if self._encoder_active:
            self._encoder.abort()
        else:
            self._complete_pending_error()

    def _encoder_finished(self, output_path: Path) -> None:
        self._encoder_active = False
        if self._aborting:
            try:
                Path(output_path).unlink(missing_ok=True)
            except OSError:
                pass
            if self._source_active:
                self._frame_source.stop()
            else:
                self._complete_abort()
            return
        if self._source_active:
            self._frame_source.stop()
        self._output_path = None
        self._set_state(RecordingState.IDLE)
        self.finished.emit(output_path)

    def _encoder_aborted(self) -> None:
        self._encoder_active = False
        if self._source_active:
            self._frame_source.stop()
            return
        if self._aborting:
            self._complete_abort()
            return
        self._complete_pending_error()

    def _encoder_failed(self, message: str) -> None:
        self._encoder_active = False
        self._pending_error = message
        self._set_state(RecordingState.STOPPING)
        if self._source_active:
            self._frame_source.stop()
        else:
            self._complete_pending_error()

    def _complete_pending_error(self) -> None:
        message = self._pending_error
        if message is None:
            return
        self._pending_error = None
        self._output_path = None
        self._set_state(RecordingState.IDLE)
        self.error.emit(message)

    def _complete_abort(self) -> None:
        self._aborting = False
        self._pending_error = None
        self._output_path = None
        self._set_state(RecordingState.IDLE)
        self.aborted.emit()

    def _set_state(self, state: RecordingState) -> None:
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state)
