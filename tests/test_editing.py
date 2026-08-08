import base64
from pathlib import Path
import shutil

import pytest
from PyQt6.QtGui import QColor, QImage

from region_selector.editing import (
    GifDraft,
    GifEditModel,
    GifFrameMetadata,
    analyze_gif,
    export_gif,
    ffmpeg_edit_arguments,
    gifsicle_delete_arguments,
)
from region_selector.recording import RecordingOptions


ANIMATED_GIF = base64.b64decode(
    "R0lGODlhAgACAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQA"
    "BwAAACwAAAAAAgACAAAIBgABCAQQEAAh+QQADAAAACwAAAAAAgACAIEAgAAAAAAA"
    "AAAAAAAIBgABCAQQEAAh+QQAFwAAACwAAAAAAgACAIEAAP8AAAAAAAAAAAAIBgAB"
    "CAQQEAAh+QQACQAAACwAAAAAAgACAIH//wAAAAAAAAAAAAAIBgABCAQQEAA7"
)


def test_edit_model_combines_frames_segments_restore_and_history() -> None:
    model = GifEditModel(8)

    model.discard_frame(1)
    model.discard_segment(3, 5)
    model.discard_segment(5, 6)
    assert model.discarded_indices == frozenset({1, 3, 4, 5, 6})

    model.restore_segment(4, 5)
    model.restore_frame(1)
    assert model.discarded_indices == frozenset({3, 6})
    model.undo()
    assert model.discarded_indices == frozenset({1, 3, 6})
    model.redo()
    assert model.discarded_indices == frozenset({3, 6})
    model.reset()
    assert model.discarded_indices == frozenset()


def test_edit_model_marks_empty_selection_invalid_but_keeps_it_editable() -> None:
    model = GifEditModel(3)

    model.discard_segment(0, 2)

    assert model.is_valid is False
    assert model.kept_indices == ()
    model.restore_frame(1)
    assert model.is_valid is True
    assert model.kept_indices == (1,)


def test_edit_model_rejects_indices_outside_the_gif() -> None:
    with pytest.raises(ValueError):
        GifEditModel(2, frozenset({2}))
    with pytest.raises(IndexError):
        GifEditModel(2).discard_frame(-1)


def _draft(tmp_path: Path, discarded: set[int]) -> GifDraft:
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    frames = tuple(GifFrameMetadata(index, 80 + index * 10, image) for index in range(4))
    source = tmp_path / "draft.gif"
    source.write_bytes(b"draft-data")
    return GifDraft(
        source,
        tmp_path / "final.gif",
        frames,
        RecordingOptions(frames_per_second=12, max_colors=64),
        discarded,
    )


def test_export_without_edits_publishes_original_bytes(tmp_path) -> None:
    draft = _draft(tmp_path, set())

    result = export_gif(draft)

    assert result.read_bytes() == b"draft-data"
    assert draft.draft_path.exists() is False


def test_export_never_overwrites_existing_target(tmp_path) -> None:
    draft = _draft(tmp_path, set())
    draft.target_path.write_bytes(b"existing-data")

    with pytest.raises(FileExistsError):
        export_gif(draft)

    assert draft.target_path.read_bytes() == b"existing-data"
    assert draft.draft_path.read_bytes() == b"draft-data"


def test_gifsicle_export_arguments_keep_sorted_original_frame_indices(tmp_path) -> None:
    draft = _draft(tmp_path, {3, 1})
    output = tmp_path / "temporary.gif"

    assert gifsicle_delete_arguments(draft, output) == (
        str(draft.draft_path),
        "--delete",
        "#1",
        "#3",
        "--output",
        str(output),
    )


def test_ffmpeg_fallback_uses_recording_options_and_frame_selection(tmp_path) -> None:
    draft = _draft(tmp_path, {1, 3})

    arguments = ffmpeg_edit_arguments(draft, tmp_path / "temporary.gif")
    filter_value = arguments[arguments.index("-filter_complex") + 1]

    assert "not(eq(n\\,1)+eq(n\\,3))" in filter_value
    assert "setpts=N/(12*TB)" in filter_value
    assert "max_colors=64" in filter_value


def test_failed_cut_export_keeps_draft_for_retry(tmp_path, monkeypatch) -> None:
    draft = _draft(tmp_path, {1})
    monkeypatch.setattr("region_selector.editing.shutil.which", lambda _name: "/bin/tool")

    class FailedResult:
        returncode = 1
        stderr = b"broken export"

    monkeypatch.setattr(
        "region_selector.editing.subprocess.run",
        lambda *_args, **_kwargs: FailedResult(),
    )

    with pytest.raises(RuntimeError, match="broken export"):
        export_gif(draft)

    assert draft.draft_path.read_bytes() == b"draft-data"
    assert draft.target_path.exists() is False


@pytest.mark.skipif(shutil.which("gifsicle") is None, reason="gifsicle unavailable")
def test_gifsicle_cut_preserves_kept_frame_order_and_delays(tmp_path) -> None:
    source = tmp_path / "animated-draft.gif"
    target = tmp_path / "animated-final.gif"
    source.write_bytes(ANIMATED_GIF)
    frames = analyze_gif(source)
    draft = GifDraft(source, target, frames, RecordingOptions(), {1})

    export_gif(draft)

    result = analyze_gif(target)
    assert len(result) == 3
    assert [frame.delay_ms for frame in result] == [70, 230, 90]
    assert [frame.image.pixelColor(0, 0).name() for frame in result] == [
        "#ff0000",
        "#0000ff",
        "#ffff00",
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_ffmpeg_fallback_exports_only_kept_frames(tmp_path, monkeypatch) -> None:
    source = tmp_path / "fallback-draft.gif"
    target = tmp_path / "fallback-final.gif"
    source.write_bytes(ANIMATED_GIF)
    draft = GifDraft(
        source,
        target,
        analyze_gif(source),
        RecordingOptions(frames_per_second=10),
        {0, 2},
    )

    executable_lookup = shutil.which
    monkeypatch.setattr(
        "region_selector.editing.shutil.which",
        lambda name: None if name == "missing-gifsicle" else executable_lookup(name),
    )

    export_gif(draft, gifsicle_executable="missing-gifsicle")

    result = analyze_gif(target)
    assert len(result) == 2
    assert [frame.delay_ms for frame in result] == [100, 100]
