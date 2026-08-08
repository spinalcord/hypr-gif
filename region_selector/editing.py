"""GIF draft analysis, temporal editing state, and final export."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader

from .recording import RecordingOptions


class DraftState(Enum):
    NONE = auto()
    READY = auto()
    SAVING = auto()


@dataclass(frozen=True, slots=True)
class GifFrameMetadata:
    index: int
    delay_ms: int
    image: QImage | None = field(default=None, compare=False, repr=False)


@dataclass(slots=True)
class GifDraft:
    draft_path: Path
    target_path: Path
    frames: tuple[GifFrameMetadata, ...]
    options: RecordingOptions
    discarded_indices: set[int] = field(default_factory=set)
    analysis_error: str | None = None

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def kept_indices(self) -> tuple[int, ...]:
        return tuple(
            index
            for index in range(self.frame_count)
            if index not in self.discarded_indices
        )

    @property
    def can_export(self) -> bool:
        return bool(self.kept_indices)

    def apply_discarded_indices(self, indices: set[int] | frozenset[int]) -> None:
        invalid = {index for index in indices if not 0 <= index < self.frame_count}
        if invalid:
            raise ValueError("discarded frame indices are out of range")
        self.discarded_indices = set(indices)

    def remove(self) -> None:
        try:
            self.draft_path.unlink(missing_ok=True)
        except OSError:
            pass


class GifEditModel(QObject):
    changed = pyqtSignal()

    def __init__(
        self,
        frame_count: int,
        discarded_indices: set[int] | frozenset[int] = frozenset(),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if frame_count <= 0:
            raise ValueError("frame_count must be positive")
        self._frame_count = frame_count
        self._discarded = self._validated(discarded_indices)
        self._history: list[frozenset[int]] = [self._discarded]
        self._history_index = 0

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def discarded_indices(self) -> frozenset[int]:
        return self._discarded

    @property
    def kept_indices(self) -> tuple[int, ...]:
        return tuple(
            index
            for index in range(self._frame_count)
            if index not in self._discarded
        )

    @property
    def is_valid(self) -> bool:
        return len(self._discarded) < self._frame_count

    @property
    def can_undo(self) -> bool:
        return self._history_index > 0

    @property
    def can_redo(self) -> bool:
        return self._history_index + 1 < len(self._history)

    def is_discarded(self, index: int) -> bool:
        self._check_index(index)
        return index in self._discarded

    def discard_frame(self, index: int) -> None:
        self._check_index(index)
        self._replace(self._discarded | {index})

    def restore_frame(self, index: int) -> None:
        self._check_index(index)
        self._replace(self._discarded - {index})

    def toggle_frame(self, index: int) -> None:
        if self.is_discarded(index):
            self.restore_frame(index)
        else:
            self.discard_frame(index)

    def discard_segment(self, first: int, last: int) -> None:
        selected = self._segment(first, last)
        self._replace(self._discarded | selected)

    def restore_segment(self, first: int, last: int) -> None:
        selected = self._segment(first, last)
        self._replace(self._discarded - selected)

    def toggle_segment(self, first: int, last: int) -> None:
        selected = self._segment(first, last)
        if selected <= self._discarded:
            self._replace(self._discarded - selected)
        else:
            self._replace(self._discarded | selected)

    def reset(self) -> None:
        self._replace(frozenset())

    def undo(self) -> None:
        if not self.can_undo:
            return
        self._history_index -= 1
        self._discarded = self._history[self._history_index]
        self.changed.emit()

    def redo(self) -> None:
        if not self.can_redo:
            return
        self._history_index += 1
        self._discarded = self._history[self._history_index]
        self.changed.emit()

    def _replace(self, values: set[int] | frozenset[int]) -> None:
        next_value = self._validated(values)
        if next_value == self._discarded:
            return
        del self._history[self._history_index + 1 :]
        self._history.append(next_value)
        self._history_index += 1
        self._discarded = next_value
        self.changed.emit()

    def _validated(self, values: set[int] | frozenset[int]) -> frozenset[int]:
        result = frozenset(values)
        if any(index < 0 or index >= self._frame_count for index in result):
            raise ValueError("discarded frame indices are out of range")
        return result

    def _check_index(self, index: int) -> None:
        if not 0 <= index < self._frame_count:
            raise IndexError("frame index is out of range")

    def _segment(self, first: int, last: int) -> frozenset[int]:
        self._check_index(first)
        self._check_index(last)
        lower, upper = sorted((first, last))
        return frozenset(range(lower, upper + 1))


def analyze_gif(path: Path) -> tuple[GifFrameMetadata, ...]:
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    frames: list[GifFrameMetadata] = []
    while reader.canRead():
        image = reader.read()
        if image.isNull():
            break
        delay_ms = reader.nextImageDelay()
        frames.append(
            GifFrameMetadata(
                len(frames),
                max(0, delay_ms),
                image,
            )
        )
    if not frames:
        detail = reader.errorString().strip() or "the GIF contains no readable frames"
        raise RuntimeError(f"GIF analysis failed: {detail}")
    return tuple(frames)


class _AnalysisThread(QThread):
    analyzed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = path

    def run(self) -> None:
        try:
            frames = analyze_gif(self._path)
        except (OSError, RuntimeError) as exc:
            self.failed.emit(str(exc))
            return
        self.analyzed.emit(frames)


class GifAnalyzer(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: _AnalysisThread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, path: Path) -> None:
        if self._thread is not None:
            self.error.emit("GIF analysis is already running")
            return
        path = Path(path)
        if not path.is_file():
            self.error.emit(f"GIF analysis failed: file does not exist: {path}")
            return
        thread = _AnalysisThread(path, self)
        self._thread = thread
        thread.analyzed.connect(self._complete)
        thread.failed.connect(self._fail)
        thread.finished.connect(thread.deleteLater)
        self.busy_changed.emit(True)
        thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        thread.requestInterruption()
        thread.wait()
        self._thread = None

    def _complete(self, frames: tuple[GifFrameMetadata, ...]) -> None:
        self._thread = None
        self.busy_changed.emit(False)
        self.finished.emit(frames)

    def _fail(self, message: str) -> None:
        self._thread = None
        self.busy_changed.emit(False)
        self.error.emit(message)


def gifsicle_delete_arguments(
    draft: GifDraft,
    output_path: Path,
) -> tuple[str, ...]:
    selectors = tuple(f"#{index}" for index in sorted(draft.discarded_indices))
    return (
        str(draft.draft_path),
        "--delete",
        *selectors,
        "--output",
        str(output_path),
    )


def ffmpeg_edit_arguments(
    draft: GifDraft,
    output_path: Path,
) -> tuple[str, ...]:
    discarded = sorted(draft.discarded_indices)
    conditions = "+".join(f"eq(n\\,{index})" for index in discarded)
    selection = f"not({conditions})"
    options = draft.options
    palette_filter = (
        f"[0:v]select='{selection}',setpts=N/({options.frames_per_second}*TB),"
        f"fps={options.frames_per_second},"
        f"scale=w='min(iw,{options.max_width})':h=-1:flags=lanczos,"
        "split[frames][palette_input];"
        "[palette_input]palettegen=stats_mode=single:"
        f"max_colors={options.max_colors}[palette];"
        "[frames][palette]paletteuse=new=1:dither=bayer:bayer_scale=3"
    )
    return (
        "-hide_banner",
        "-loglevel",
        "error",
        "-ignore_loop",
        "1",
        "-i",
        str(draft.draft_path),
        "-filter_complex",
        palette_filter,
        "-loop",
        "0",
        "-n",
        str(output_path),
    )


def _temporary_export_path(target_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".export.tmp.gif",
        dir=target_path.parent,
    )
    os.close(descriptor)
    path = Path(name)
    path.unlink()
    return path


def _install_without_overwrite(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError as exc:
        raise OSError(f"final GIF could not be installed: {exc}") from exc
    source.unlink()


def export_gif(
    draft: GifDraft,
    *,
    gifsicle_executable: str = "gifsicle",
    ffmpeg_executable: str = "ffmpeg",
) -> Path:
    target_path = Path(draft.target_path)
    if target_path.exists():
        raise FileExistsError(f"output file already exists: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not draft.can_export:
        raise ValueError("at least one GIF frame must be kept")
    if not draft.discarded_indices:
        _install_without_overwrite(draft.draft_path, target_path)
        return target_path

    temporary_path = _temporary_export_path(target_path)
    try:
        gifsicle = shutil.which(gifsicle_executable)
        if gifsicle is not None:
            command = (gifsicle, *gifsicle_delete_arguments(draft, temporary_path))
        else:
            ffmpeg = shutil.which(ffmpeg_executable) or ffmpeg_executable
            command = (ffmpeg, *ffmpeg_edit_arguments(draft, temporary_path))
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            executable = Path(command[0]).name
            raise RuntimeError(
                f"GIF export failed with {executable}: "
                f"{detail or f'exit code {result.returncode}'}"
            )
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("GIF export produced no output file")
        _install_without_overwrite(temporary_path, target_path)
        draft.remove()
        return target_path
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


class _ExportThread(QThread):
    exported = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        draft: GifDraft,
        gifsicle_executable: str,
        ffmpeg_executable: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._draft = draft
        self._gifsicle_executable = gifsicle_executable
        self._ffmpeg_executable = ffmpeg_executable

    def run(self) -> None:
        try:
            result = export_gif(
                self._draft,
                gifsicle_executable=self._gifsicle_executable,
                ffmpeg_executable=self._ffmpeg_executable,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed.emit(str(exc))
            return
        self.exported.emit(result)


class GifExporter(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)

    def __init__(
        self,
        gifsicle_executable: str = "gifsicle",
        ffmpeg_executable: str = "ffmpeg",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._gifsicle_executable = gifsicle_executable
        self._ffmpeg_executable = ffmpeg_executable
        self._thread: _ExportThread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, draft: GifDraft) -> None:
        if self._thread is not None:
            self.error.emit("GIF export is already running")
            return
        thread = _ExportThread(
            draft,
            self._gifsicle_executable,
            self._ffmpeg_executable,
            self,
        )
        self._thread = thread
        thread.exported.connect(self._complete)
        thread.failed.connect(self._fail)
        thread.finished.connect(thread.deleteLater)
        self.busy_changed.emit(True)
        thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        thread.requestInterruption()
        thread.wait()
        self._thread = None

    def _complete(self, path: Path) -> None:
        self._thread = None
        self.busy_changed.emit(False)
        self.finished.emit(path)

    def _fail(self, message: str) -> None:
        self._thread = None
        self.busy_changed.emit(False)
        self.error.emit(message)
