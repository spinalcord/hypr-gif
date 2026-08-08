"""Mouse-operated dialog for temporal GIF frame editing."""

from __future__ import annotations

from enum import Enum, auto

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .editing import GifDraft, GifEditModel


class TimelineDragMode(Enum):
    NONE = auto()
    PLAYHEAD = auto()
    RANGE_START = auto()
    RANGE_END = auto()


class MouseScrubber(QSlider):
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton and self.width() > 0:
            value = QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                round(event.position().x()),
                self.width(),
            )
            self.setValue(value)
            event.accept()
            return
        super().mousePressEvent(event)


class PreviewLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setStyleSheet("background: #171717; color: #dddddd;")

    def set_image(self, image) -> None:
        self._image = image
        self._update_pixmap()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self) -> None:
        if self._image is None or self._image.isNull():
            self.clear()
            return
        pixmap = self._image.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(QPixmap.fromImage(pixmap))


class GifTimeline(QWidget):
    playhead_changed = pyqtSignal(int)
    range_changed = pyqtSignal(int, int)

    def __init__(
        self,
        model: GifEditModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._playhead = 0
        self._range_start = 0
        self._range_end = model.frame_count - 1
        self._drag_mode = TimelineDragMode.NONE
        self.setMinimumHeight(76)
        self.setMouseTracking(True)
        model.changed.connect(self.update)

    @property
    def playhead(self) -> int:
        return self._playhead

    @property
    def range_start(self) -> int:
        return self._range_start

    @property
    def range_end(self) -> int:
        return self._range_end

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(720, 84)

    def set_playhead(self, index: int) -> None:
        index = max(0, min(index, self._model.frame_count - 1))
        if index == self._playhead:
            return
        self._playhead = index
        self.update()

    def set_range(self, first: int, last: int) -> None:
        first = max(0, min(first, self._model.frame_count - 1))
        last = max(0, min(last, self._model.frame_count - 1))
        first, last = sorted((first, last))
        if (first, last) == (self._range_start, self._range_end):
            return
        self._range_start = first
        self._range_end = last
        self.update()
        self.range_changed.emit(first, last)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = self._track_rect()
        painter.fillRect(track, QColor("#353535"))
        frame_width = track.width() / self._model.frame_count
        for index in range(self._model.frame_count):
            left = track.left() + round(index * frame_width)
            right = track.left() + round((index + 1) * frame_width)
            color = (
                QColor("#a53b3b")
                if self._model.is_discarded(index)
                else QColor("#5d8b62")
            )
            painter.fillRect(
                QRect(left, track.top(), max(1, right - left), track.height()),
                color,
            )
        painter.setPen(QPen(QColor("#e0a030"), 3))
        start_x = self._x_for_index(self._range_start)
        end_x = self._x_for_index(self._range_end)
        painter.drawRect(
            QRect(
                start_x,
                track.top() - 6,
                max(1, end_x - start_x),
                track.height() + 12,
            )
        )
        painter.setPen(QPen(QColor("#f4c15d"), 7))
        painter.drawLine(start_x, track.top() - 10, start_x, track.bottom() + 10)
        painter.drawLine(end_x, track.top() - 10, end_x, track.bottom() + 10)
        playhead_x = self._x_for_frame_center(self._playhead)
        painter.setPen(QPen(QColor("#54a7ff"), 3))
        painter.drawLine(playhead_x, 3, playhead_x, self.height() - 3)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = round(event.position().x())
        start_x = self._x_for_index(self._range_start)
        end_x = self._x_for_index(self._range_end)
        if abs(x - start_x) <= 10:
            self._drag_mode = TimelineDragMode.RANGE_START
        elif abs(x - end_x) <= 10:
            self._drag_mode = TimelineDragMode.RANGE_END
        else:
            self._drag_mode = TimelineDragMode.PLAYHEAD
        self._apply_mouse_x(x)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_mode is TimelineDragMode.NONE:
            super().mouseMoveEvent(event)
            return
        self._apply_mouse_x(round(event.position().x()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_mouse_x(round(event.position().x()))
            self._drag_mode = TimelineDragMode.NONE
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _apply_mouse_x(self, x: int) -> None:
        index = self._index_for_x(x)
        if self._drag_mode is TimelineDragMode.RANGE_START:
            self.set_range(index, self._range_end)
        elif self._drag_mode is TimelineDragMode.RANGE_END:
            self.set_range(self._range_start, index)
        elif self._drag_mode is TimelineDragMode.PLAYHEAD:
            if index != self._playhead:
                self._playhead = index
                self.update()
                self.playhead_changed.emit(index)

    def _track_rect(self) -> QRect:
        return self.rect().adjusted(14, 22, -14, -22)

    def _index_for_x(self, x: int) -> int:
        track = self._track_rect()
        ratio = (x - track.left()) / max(1, track.width())
        return max(
            0,
            min(
                round(ratio * (self._model.frame_count - 1)),
                self._model.frame_count - 1,
            ),
        )

    def _x_for_frame_center(self, index: int) -> int:
        track = self._track_rect()
        return track.left() + round(
            (index + 0.5) * track.width() / self._model.frame_count
        )

    def _x_for_index(self, index: int) -> int:
        track = self._track_rect()
        if index == self._model.frame_count - 1:
            return track.right()
        return track.left() + round(
            index * track.width() / max(1, self._model.frame_count - 1)
        )


class GifEditorDialog(QDialog):
    applied = pyqtSignal(object)

    def __init__(
        self,
        draft: GifDraft,
        parent: QWidget | None = None,
    ) -> None:
        if not draft.frames:
            raise ValueError("the GIF draft has no analyzed frames")
        super().__init__(parent)
        self._draft = draft
        self.model = GifEditModel(
            draft.frame_count,
            frozenset(draft.discarded_indices),
            self,
        )
        self._current_frame = 0
        self._busy = False
        self.setWindowTitle("Edit GIF")
        self.setMinimumSize(800, 620)
        self.setSizeGripEnabled(True)

        self.preview_label = PreviewLabel(self)
        self.preview_label.setObjectName("gifPreview")
        self.status_label = QLabel(self)
        self.status_label.setObjectName("frameStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.previous_button = QPushButton("Previous frame", self)
        self.play_button = QPushButton("Play", self)
        self.next_button = QPushButton("Next frame", self)
        self.scrubber = MouseScrubber(Qt.Orientation.Horizontal, self)
        self.scrubber.setRange(0, draft.frame_count - 1)
        self.scrubber.setObjectName("frameScrubber")

        self.timeline = GifTimeline(self.model, self)
        self.timeline.setObjectName("gifTimeline")
        self.discard_frame_button = QPushButton("Discard frame", self)
        self.discard_segment_button = QPushButton("Discard segment", self)
        self.undo_button = QPushButton("Undo", self)
        self.redo_button = QPushButton("Redo", self)
        self.reset_button = QPushButton("Reset", self)
        self.apply_button = QPushButton("Apply", self)
        self.cancel_button = QPushButton("Cancel", self)

        playback_layout = QHBoxLayout()
        playback_layout.addWidget(self.previous_button)
        playback_layout.addWidget(self.play_button)
        playback_layout.addWidget(self.next_button)
        playback_layout.addWidget(self.scrubber, 1)

        edit_layout = QHBoxLayout()
        edit_layout.addWidget(self.discard_frame_button)
        edit_layout.addWidget(self.discard_segment_button)
        edit_layout.addStretch(1)
        edit_layout.addWidget(self.undo_button)
        edit_layout.addWidget(self.redo_button)
        edit_layout.addWidget(self.reset_button)

        action_layout = QHBoxLayout()
        action_layout.addStretch(1)
        action_layout.addWidget(self.apply_button)
        action_layout.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.preview_label, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(playback_layout)
        layout.addWidget(self.timeline)
        layout.addLayout(edit_layout)
        layout.addLayout(action_layout)

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._advance_playback)
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.play_button.clicked.connect(self._toggle_playback)
        self.next_button.clicked.connect(lambda: self._step(1))
        self.scrubber.valueChanged.connect(self.set_current_frame)
        self.timeline.playhead_changed.connect(self.set_current_frame)
        self.discard_frame_button.clicked.connect(self._toggle_current_frame)
        self.discard_segment_button.clicked.connect(self._toggle_segment)
        self.undo_button.clicked.connect(self.model.undo)
        self.redo_button.clicked.connect(self.model.redo)
        self.reset_button.clicked.connect(self.model.reset)
        self.apply_button.clicked.connect(self._apply)
        self.cancel_button.clicked.connect(self.reject)
        self.model.changed.connect(self._model_changed)
        self.finished.connect(lambda _result: self._play_timer.stop())

        self.set_current_frame(0)
        self._model_changed()

    @property
    def current_frame(self) -> int:
        return self._current_frame

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._update_controls()

    def set_current_frame(self, index: int) -> None:
        index = max(0, min(index, self._draft.frame_count - 1))
        self._current_frame = index
        self.scrubber.blockSignals(True)
        self.scrubber.setValue(index)
        self.scrubber.blockSignals(False)
        self.timeline.set_playhead(index)
        frame = self._draft.frames[index]
        self.preview_label.set_image(frame.image)
        self._update_controls()

    def _toggle_playback(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self.play_button.setText("Play")
            return
        if not self.model.is_valid:
            return
        if self.model.is_discarded(self._current_frame):
            self._advance_playback()
        self._play_timer.start(self._current_delay())
        self.play_button.setText("Pause")

    def _advance_playback(self) -> None:
        kept = self.model.kept_indices
        if not kept:
            self._play_timer.stop()
            self.play_button.setText("Play")
            return
        next_values = [index for index in kept if index > self._current_frame]
        self.set_current_frame(next_values[0] if next_values else kept[0])
        self._play_timer.setInterval(self._current_delay())

    def _current_delay(self) -> int:
        return max(10, self._draft.frames[self._current_frame].delay_ms)

    def _step(self, direction: int) -> None:
        self.set_current_frame(
            (self._current_frame + direction) % self._draft.frame_count
        )

    def _toggle_current_frame(self) -> None:
        self.model.toggle_frame(self._current_frame)

    def _toggle_segment(self) -> None:
        self.model.toggle_segment(
            self.timeline.range_start,
            self.timeline.range_end,
        )

    def _apply(self) -> None:
        if self._busy or not self.model.is_valid:
            return
        self._draft.apply_discarded_indices(set(self.model.discarded_indices))
        self.applied.emit(self._draft)
        self.accept()

    def _model_changed(self) -> None:
        if not self.model.is_valid and self._play_timer.isActive():
            self._play_timer.stop()
            self.play_button.setText("Play")
        self._update_controls()

    def _update_controls(self) -> None:
        discarded = self.model.is_discarded(self._current_frame)
        status = "discarded" if discarded else "kept"
        self.status_label.setText(
            f"Frame {self._current_frame + 1} of {self._draft.frame_count} — {status}"
        )
        self.discard_frame_button.setText(
            "Restore frame" if discarded else "Discard frame"
        )
        selected = frozenset(
            range(self.timeline.range_start, self.timeline.range_end + 1)
        )
        self.discard_segment_button.setText(
            "Restore segment"
            if selected <= self.model.discarded_indices
            else "Discard segment"
        )
        editable = not self._busy
        for widget in (
            self.previous_button,
            self.play_button,
            self.next_button,
            self.scrubber,
            self.timeline,
            self.discard_frame_button,
            self.discard_segment_button,
            self.reset_button,
        ):
            widget.setEnabled(editable)
        self.undo_button.setEnabled(editable and self.model.can_undo)
        self.redo_button.setEnabled(editable and self.model.can_redo)
        self.apply_button.setEnabled(editable and self.model.is_valid)
        self.cancel_button.setEnabled(editable)
