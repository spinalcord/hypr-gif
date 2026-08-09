from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from hypr_gif.editing import GifDraft, GifFrameMetadata
from hypr_gif.editor import GifEditorDialog
from hypr_gif.recording import RecordingOptions


@pytest.fixture(scope="module")
def editor_app():
    return QApplication.instance() or QApplication([])


def make_draft(tmp_path: Path, discarded: set[int] | None = None) -> GifDraft:
    frames = []
    for index, color in enumerate(("red", "green", "blue", "yellow")):
        image = QImage(80, 50, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        frames.append(GifFrameMetadata(index, 50, image))
    return GifDraft(
        tmp_path / "draft.gif",
        tmp_path / "final.gif",
        tuple(frames),
        RecordingOptions(),
        discarded or set(),
    )


def test_editor_buttons_apply_cumulative_changes(tmp_path, editor_app) -> None:
    draft = make_draft(tmp_path)
    dialog = GifEditorDialog(draft)
    dialog.show()

    QTest.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.discard_frame_button, Qt.MouseButton.LeftButton)
    dialog.timeline.set_range(2, 3)
    QTest.mouseClick(dialog.discard_segment_button, Qt.MouseButton.LeftButton)
    assert dialog.model.discarded_indices == frozenset({1, 2, 3})

    QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
    assert dialog.model.discarded_indices == frozenset({1})
    QTest.mouseClick(dialog.redo_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert draft.discarded_indices == {1, 2, 3}


def test_editor_cancel_preserves_previously_applied_selection(tmp_path, editor_app) -> None:
    draft = make_draft(tmp_path, {0})
    dialog = GifEditorDialog(draft)
    dialog.show()

    dialog.set_current_frame(1)
    QTest.mouseClick(dialog.discard_frame_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert draft.discarded_indices == {0}


def test_editor_disables_apply_when_every_frame_is_discarded(tmp_path, editor_app) -> None:
    draft = make_draft(tmp_path)
    dialog = GifEditorDialog(draft)
    dialog.show()

    dialog.timeline.set_range(0, 3)
    QTest.mouseClick(dialog.discard_segment_button, Qt.MouseButton.LeftButton)

    assert dialog.apply_button.isEnabled() is False
    assert dialog.play_button.isEnabled() is True
    dialog.model.restore_frame(2)
    assert dialog.apply_button.isEnabled() is True
    dialog.reject()


def test_timeline_mouse_click_moves_playhead(tmp_path, editor_app) -> None:
    dialog = GifEditorDialog(make_draft(tmp_path))
    dialog.show()
    timeline = dialog.timeline

    QTest.mouseClick(
        timeline,
        Qt.MouseButton.LeftButton,
        pos=QPoint(timeline.width() // 2, timeline.height() // 2),
    )

    assert dialog.current_frame in (1, 2)
    dialog.reject()
