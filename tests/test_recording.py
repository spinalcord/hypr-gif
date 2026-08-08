from pathlib import Path
import shutil

import pytest
from PyQt6.QtCore import QByteArray, QBuffer, QEventLoop, QObject, QProcess, QTimer
from PyQt6.QtGui import QColor, QImage, QImageReader
from PyQt6.QtWidgets import QApplication

from region_selector import Rect
from region_selector.recording import (
    CompressionPreset,
    FrameEncoder,
    FrameSource,
    FfmpegGifEncoder,
    GifRecorder,
    GrimFrameSource,
    RecordingOptions,
    RecordingState,
    ffmpeg_gif_arguments,
    gifsicle_arguments,
    grim_arguments,
)


class FakeFrameSource(FrameSource):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls = []
        self.stop_calls = 0
        self.pause_calls = 0
        self.resume_calls = []

    def start(self, region, options) -> None:
        self.start_calls.append((region, options))
        self.started.emit()

    def stop(self) -> None:
        self.stop_calls += 1
        self.stopped.emit()

    def pause(self) -> None:
        self.pause_calls += 1
        self.paused.emit()

    def resume(self, region) -> None:
        self.resume_calls.append(region)
        self.resumed.emit()


class DeferredFrameSource(FakeFrameSource):
    def pause(self) -> None:
        self.pause_calls += 1

    def resume(self, region) -> None:
        self.resume_calls.append(region)


class FakeEncoder(FrameEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls = []
        self.frames = []
        self.finish_calls = 0
        self.abort_calls = 0
        self.output_path = None

    def start(self, output_path, options) -> None:
        self.start_calls.append((output_path, options))
        self.output_path = output_path
        self.started.emit()

    def write_frame(self, frame) -> None:
        self.frames.append(frame)

    def finish(self) -> None:
        self.finish_calls += 1
        self.finished.emit(self.output_path)

    def abort(self) -> None:
        self.abort_calls += 1
        self.aborted.emit()


@pytest.fixture(scope="module")
def core_app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_grim_arguments_use_exact_logical_region() -> None:
    arguments = grim_arguments(Rect(10, 20, 300, 200), True)

    assert arguments == (
        "-s",
        "1",
        "-g",
        "10,20 300x200",
        "-t",
        "png",
        "-c",
        "-",
    )


def test_ffmpeg_arguments_build_palette_gif_pipeline(tmp_path) -> None:
    output_path = tmp_path / "capture.gif"
    arguments = ffmpeg_gif_arguments(
        RecordingOptions(frames_per_second=12, max_colors=128),
        output_path,
    )

    assert arguments[arguments.index("-framerate") + 1] == "12"
    palette_filter = arguments[arguments.index("-filter_complex") + 1]
    assert "scale=w='min(iw,960)':h=-1:flags=lanczos" in palette_filter
    assert "palettegen=stats_mode=single:max_colors=128" in palette_filter
    assert "paletteuse=new=1:dither=bayer:bayer_scale=3" in palette_filter
    assert arguments[-2:] == ("-n", str(output_path))


def test_gifsicle_arguments_only_enable_lossy_when_requested(tmp_path) -> None:
    input_path = tmp_path / "input.gif"
    output_path = tmp_path / "output.gif"

    lossless = gifsicle_arguments(
        RecordingOptions(lossy_strength=0),
        input_path,
        output_path,
    )
    lossy = gifsicle_arguments(
        RecordingOptions(lossy_strength=80),
        input_path,
        output_path,
    )

    assert lossless == (
        "--optimize=3",
        str(input_path),
        "--output",
        str(output_path),
    )
    assert lossy == (
        "--optimize=3",
        "--lossy=80",
        str(input_path),
        "--output",
        str(output_path),
    )


def test_balanced_compression_is_the_default() -> None:
    options = RecordingOptions()

    assert options.compression_preset is CompressionPreset.BALANCED
    assert (
        options.gifsicle_enabled,
        options.max_width,
        options.max_colors,
        options.lossy_strength,
    ) == (True, 960, 128, 40)


@pytest.mark.parametrize(
    "options",
    (
        {"frames_per_second": 0},
        {"frames_per_second": 61},
        {"max_colors": 1},
        {"max_colors": 257},
        {"max_width": 0},
        {"max_width": 16385},
        {"lossy_strength": -1},
        {"lossy_strength": 201},
    ),
)
def test_recording_options_reject_invalid_ranges(options) -> None:
    with pytest.raises(ValueError):
        RecordingOptions(**options)


def test_gif_recorder_coordinates_source_and_encoder(tmp_path) -> None:
    source = FakeFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    states = []
    started = []
    finished = []
    recorder.state_changed.connect(states.append)
    recorder.started.connect(started.append)
    recorder.finished.connect(finished.append)
    region = Rect(10, 20, 300, 200)
    output_path = tmp_path / "capture.gif"
    options = RecordingOptions(frames_per_second=10)

    recorder.start(region, output_path, options)
    source.frame_ready.emit(b"png-frame")
    recorder.stop()

    assert encoder.start_calls == [(output_path, options)]
    assert source.start_calls == [(region, options)]
    assert encoder.frames == [b"png-frame"]
    assert source.stop_calls == 1
    assert encoder.finish_calls == 1
    assert states == [
        RecordingState.STARTING,
        RecordingState.RECORDING,
        RecordingState.STOPPING,
        RecordingState.IDLE,
    ]
    assert started == [output_path]
    assert finished == [output_path]


def test_gif_recorder_pauses_moves_and_resumes_without_restarting_encoder(
    tmp_path,
) -> None:
    source = FakeFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    states = []
    recorder.state_changed.connect(states.append)
    first_region = Rect(10, 20, 300, 200)
    moved_region = Rect(400, 250, 300, 200)
    output_path = tmp_path / "capture.gif"
    options = RecordingOptions(frames_per_second=10)

    recorder.start(first_region, output_path, options)
    source.frame_ready.emit(b"before-pause")
    recorder.pause()

    assert recorder.state is RecordingState.PAUSED
    assert source.pause_calls == 1
    assert encoder.frames == [b"before-pause"]
    assert encoder.finish_calls == 0

    recorder.resume(moved_region)
    source.frame_ready.emit(b"after-resume")
    recorder.stop()

    assert source.start_calls == [(first_region, options)]
    assert source.resume_calls == [moved_region]
    assert encoder.start_calls == [(output_path, options)]
    assert encoder.frames == [b"before-pause", b"after-resume"]
    assert encoder.finish_calls == 1
    assert states == [
        RecordingState.STARTING,
        RecordingState.RECORDING,
        RecordingState.PAUSING,
        RecordingState.PAUSED,
        RecordingState.RESUMING,
        RecordingState.RECORDING,
        RecordingState.STOPPING,
        RecordingState.IDLE,
    ]


def test_stop_while_pause_is_waiting_for_frame_source(tmp_path) -> None:
    source = DeferredFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    recorder.start(
        Rect(0, 0, 100, 100),
        tmp_path / "capture.gif",
        RecordingOptions(),
    )

    recorder.pause()
    assert recorder.state is RecordingState.PAUSING
    recorder.stop()

    assert source.stop_calls == 1
    assert encoder.finish_calls == 1
    assert recorder.state is RecordingState.IDLE


def test_stop_from_paused_recording_finalizes_existing_frames(tmp_path) -> None:
    source = FakeFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    recorder.start(
        Rect(0, 0, 100, 100),
        tmp_path / "capture.gif",
        RecordingOptions(),
    )
    source.frame_ready.emit(b"kept-frame")
    recorder.pause()

    recorder.stop()

    assert encoder.frames == [b"kept-frame"]
    assert encoder.finish_calls == 1
    assert recorder.state is RecordingState.IDLE


def test_abort_discards_active_encoder_without_finishing_gif(tmp_path) -> None:
    source = FakeFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    aborted = []
    recorder.aborted.connect(lambda: aborted.append(True))
    recorder.start(
        Rect(0, 0, 100, 100),
        tmp_path / "draft.gif",
        RecordingOptions(),
    )
    source.frame_ready.emit(b"temporary-frame")

    recorder.abort()

    assert source.stop_calls == 1
    assert encoder.abort_calls == 1
    assert encoder.finish_calls == 0
    assert recorder.state is RecordingState.IDLE
    assert aborted == [True]


def test_stop_while_resume_is_waiting_for_frame_source(tmp_path) -> None:
    source = DeferredFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    recorder.start(
        Rect(0, 0, 100, 100),
        tmp_path / "capture.gif",
        RecordingOptions(),
    )
    recorder.pause()
    source.paused.emit()
    recorder.resume(Rect(200, 100, 100, 100))

    assert recorder.state is RecordingState.RESUMING
    recorder.stop()

    assert source.stop_calls == 1
    assert encoder.finish_calls == 1
    assert recorder.state is RecordingState.IDLE


class CompletedCapture(QObject):
    def __init__(self, frame: bytes) -> None:
        super().__init__()
        self._frame = frame

    def readAllStandardError(self):
        return QByteArray()

    def readAllStandardOutput(self):
        return QByteArray(self._frame)


def test_grim_pause_waits_for_pending_capture_before_reporting_paused(
    core_app,
) -> None:
    source = GrimFrameSource()
    process = CompletedCapture(b"png-frame")
    frames = []
    paused = []
    source.frame_ready.connect(frames.append)
    source.paused.connect(lambda: paused.append(True))
    source._active = True
    source._process = process
    source._timer.start(100)

    source.pause()

    assert source._timer.isActive() is False
    assert paused == []
    source._capture_finished(process, 0, QProcess.ExitStatus.NormalExit)
    assert frames == [b"png-frame"]
    assert paused == [True]


def test_grim_stop_replaces_pending_pause_and_keeps_last_capture(core_app) -> None:
    source = GrimFrameSource()
    process = CompletedCapture(b"last-png-frame")
    frames = []
    paused = []
    stopped = []
    source.frame_ready.connect(frames.append)
    source.paused.connect(lambda: paused.append(True))
    source.stopped.connect(lambda: stopped.append(True))
    source._active = True
    source._process = process

    source.pause()
    source.stop()
    source._capture_finished(process, 0, QProcess.ExitStatus.NormalExit)

    assert frames == [b"last-png-frame"]
    assert paused == []
    assert stopped == [True]


def test_gif_recorder_aborts_encoder_after_source_error(tmp_path) -> None:
    source = FakeFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    errors = []
    recorder.error.connect(errors.append)

    recorder.start(
        Rect(0, 0, 100, 100),
        Path(tmp_path / "capture.gif"),
        RecordingOptions(),
    )
    source.error.emit("capture unavailable")

    assert encoder.abort_calls == 1
    assert recorder.state is RecordingState.IDLE
    assert errors == ["capture unavailable"]


def test_gif_recorder_forwards_optional_encoder_warning(tmp_path) -> None:
    source = FakeFrameSource()
    encoder = FakeEncoder()
    recorder = GifRecorder(source, encoder)
    warnings = []
    recorder.warning.connect(warnings.append)

    encoder.warning.emit("optimization failed")

    assert warnings == ["optimization failed"]


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("gifsicle") is None,
    reason="ffmpeg or gifsicle is unavailable",
)
def test_ffmpeg_encoder_creates_animated_gif(core_app, tmp_path) -> None:
    frames = []
    for color_name in ("red", "green", "blue"):
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(QColor(color_name))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG") is True
        buffer.close()
        frames.append(bytes(data))

    encoder = FfmpegGifEncoder()
    output_path = tmp_path / "animated.gif"
    completed = []
    errors = []
    loop = QEventLoop()

    def feed_frames() -> None:
        for frame in frames:
            encoder.write_frame(frame)
        encoder.finish()

    encoder.started.connect(feed_frames)
    encoder.finished.connect(completed.append)
    encoder.finished.connect(loop.quit)
    encoder.error.connect(errors.append)
    encoder.error.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)

    encoder.start(output_path, RecordingOptions(frames_per_second=10))
    loop.exec()

    assert errors == []
    assert completed == [output_path]
    assert output_path.read_bytes().startswith(b"GIF89a")
    reader = QImageReader(str(output_path))
    assert reader.imageCount() == 3
    frame_channels = []
    frame_delays = []
    frame_sizes = []
    while reader.canRead():
        frame = reader.read()
        if frame.isNull():
            break
        color = frame.pixelColor(8, 8)
        frame_channels.append(
            max(range(3), key=(color.red(), color.green(), color.blue()).__getitem__)
        )
        frame_delays.append(reader.nextImageDelay())
        frame_sizes.append((frame.width(), frame.height()))
    assert frame_channels == [0, 1, 2]
    assert frame_delays == [100, 100, 100]
    assert frame_sizes == [(16, 16), (16, 16), (16, 16)]
    assert list(tmp_path.glob(".*.gifsicle.tmp")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_ffmpeg_encoder_scales_down_proportionally(core_app, tmp_path) -> None:
    image = QImage(16, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG") is True
    buffer.close()

    encoder = FfmpegGifEncoder()
    output_path = tmp_path / "scaled.gif"
    errors = []
    loop = QEventLoop()

    def feed_frame() -> None:
        encoder.write_frame(bytes(data))
        encoder.finish()

    encoder.started.connect(feed_frame)
    encoder.finished.connect(loop.quit)
    encoder.error.connect(errors.append)
    encoder.error.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)

    encoder.start(
        output_path,
        RecordingOptions(gifsicle_enabled=False, max_width=10),
    )
    loop.exec()

    reader = QImageReader(str(output_path))
    result = reader.read()
    assert errors == []
    assert (result.width(), result.height()) == (10, 5)


def test_ffmpeg_encoder_rejects_missing_enabled_gifsicle(core_app, tmp_path) -> None:
    encoder = FfmpegGifEncoder(
        gifsicle_executable="missing-gifsicle-for-recorder-test"
    )
    errors = []
    started = []
    encoder.error.connect(errors.append)
    encoder.started.connect(lambda: started.append(True))

    encoder.start(tmp_path / "capture.gif", RecordingOptions())

    assert started == []
    assert errors == [
        "GIF optimization is enabled, but gifsicle is not available in PATH"
    ]


def test_successful_postprocessing_atomically_replaces_ffmpeg_result(
    core_app,
    tmp_path,
) -> None:
    output_path = tmp_path / "capture.gif"
    temporary_path = tmp_path / ".capture.gif.gifsicle.tmp"
    output_path.write_bytes(b"ffmpeg-result")
    temporary_path.write_bytes(b"optimized-result")
    encoder = FfmpegGifEncoder()
    encoder._output_path = output_path
    encoder._temporary_path = temporary_path
    completed = []
    encoder.finished.connect(completed.append)

    encoder._complete_postprocessing(output_path)

    assert completed == [output_path]
    assert output_path.read_bytes() == b"optimized-result"
    assert temporary_path.exists() is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_gifsicle_runtime_failure_keeps_ffmpeg_result(
    core_app,
    tmp_path,
) -> None:
    failing_gifsicle = tmp_path / "failing-gifsicle"
    failing_gifsicle.write_text("#!/bin/sh\nexit 7\n")
    failing_gifsicle.chmod(0o755)

    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG") is True
    buffer.close()

    encoder = FfmpegGifEncoder(gifsicle_executable=str(failing_gifsicle))
    output_path = tmp_path / "fallback.gif"
    completed = []
    warnings = []
    errors = []
    loop = QEventLoop()

    def feed_frame() -> None:
        encoder.write_frame(bytes(data))
        encoder.finish()

    encoder.started.connect(feed_frame)
    encoder.finished.connect(completed.append)
    encoder.finished.connect(loop.quit)
    encoder.warning.connect(warnings.append)
    encoder.error.connect(errors.append)
    encoder.error.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)

    encoder.start(output_path, RecordingOptions(lossy_strength=40))
    loop.exec()

    assert errors == []
    assert completed == [output_path]
    assert len(warnings) == 1
    assert "keeping the FFmpeg GIF" in warnings[0]
    assert output_path.read_bytes().startswith(b"GIF89a")
    assert list(tmp_path.glob("*.gifsicle.tmp")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_abort_during_gifsicle_keeps_ffmpeg_result_and_removes_temporary_file(
    core_app,
    tmp_path,
) -> None:
    slow_gifsicle = tmp_path / "slow-gifsicle"
    slow_gifsicle.write_text("#!/bin/sh\nexec sleep 5\n")
    slow_gifsicle.chmod(0o755)

    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG") is True
    buffer.close()

    encoder = FfmpegGifEncoder(gifsicle_executable=str(slow_gifsicle))
    output_path = tmp_path / "aborted-postprocessing.gif"
    aborted = []
    errors = []
    loop = QEventLoop()
    poll_timer = QTimer()

    def feed_frame() -> None:
        encoder.write_frame(bytes(data))
        encoder.finish()

    def abort_postprocessing() -> None:
        stage = encoder._stage
        if stage is None or stage.name != "GIFSICLE":
            return
        poll_timer.stop()
        encoder.abort()

    encoder.started.connect(feed_frame)
    encoder.aborted.connect(lambda: aborted.append(True))
    encoder.aborted.connect(loop.quit)
    encoder.error.connect(errors.append)
    encoder.error.connect(loop.quit)
    poll_timer.timeout.connect(abort_postprocessing)
    poll_timer.start(10)
    QTimer.singleShot(5000, loop.quit)

    encoder.start(output_path, RecordingOptions())
    loop.exec()
    poll_timer.stop()

    assert errors == []
    assert aborted == [True]
    assert output_path.read_bytes().startswith(b"GIF89a")
    assert list(tmp_path.glob(".*.gifsicle.tmp")) == []


def test_ffmpeg_encoder_does_not_overwrite_existing_file(core_app, tmp_path) -> None:
    output_path = tmp_path / "existing.gif"
    output_path.write_bytes(b"existing")
    encoder = FfmpegGifEncoder()
    errors = []
    encoder.error.connect(errors.append)

    encoder.start(output_path, RecordingOptions())

    assert errors == [f"output file already exists: {output_path}"]
    assert output_path.read_bytes() == b"existing"
