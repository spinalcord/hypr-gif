from datetime import datetime

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from region_selector.recording import CompressionPreset, RecordingOptions
from region_selector.settings import (
    CompressionSettingsPage,
    RecordingPreferences,
    RecordingSettingsPage,
    RecordingSettingsStore,
    SettingsDialog,
)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def make_store(tmp_path) -> RecordingSettingsStore:
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    return RecordingSettingsStore(settings)


def test_recording_settings_round_trip(tmp_path) -> None:
    store = make_store(tmp_path)
    preferences = RecordingPreferences(
        output_directory=tmp_path / "gifs",
        filename_prefix="demo",
        options=RecordingOptions(
            frames_per_second=24,
            max_colors=128,
            include_cursor=True,
            compression_preset=CompressionPreset.CUSTOM,
            gifsicle_enabled=False,
            max_width=1200,
            lossy_strength=17,
        ),
    )

    store.save(preferences)

    assert store.load() == preferences


def test_next_output_path_avoids_existing_recording(tmp_path) -> None:
    store = make_store(tmp_path)
    output_directory = tmp_path / "gifs"
    output_directory.mkdir()
    store.save(
        RecordingPreferences(
            output_directory,
            "demo",
            RecordingOptions(),
        )
    )
    existing = output_directory / "demo_2026-08-08_12-30-00.gif"
    existing.touch()

    output_path = store.next_output_path(datetime(2026, 8, 8, 12, 30, 0))

    assert output_path == output_directory / "demo_2026-08-08_12-30-00_2.gif"


def test_settings_dialog_registers_independent_pages(app, tmp_path) -> None:
    dialog = SettingsDialog()
    page = RecordingSettingsPage(make_store(tmp_path))

    dialog.register_page(page)

    assert dialog.pages == (page,)
    with pytest.raises(ValueError, match="already registered"):
        dialog.register_page(page)
    dialog.close()


def test_old_recording_settings_keep_explicit_values(tmp_path) -> None:
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    settings.setValue("recording/frames_per_second", 24)
    settings.setValue("recording/max_colors", 256)
    settings.setValue("recording/include_cursor", True)

    options = RecordingSettingsStore(settings).load().options

    assert options.frames_per_second == 24
    assert options.max_colors == 256
    assert options.include_cursor is True
    assert options.compression_preset is CompressionPreset.CUSTOM
    assert options.gifsicle_enabled is True
    assert options.max_width == 960
    assert options.lossy_strength == 40


def test_recording_page_validates_and_saves(app, tmp_path) -> None:
    store = make_store(tmp_path)
    page = RecordingSettingsPage(store)
    page._output_directory.setText(str(tmp_path / "exports"))
    page._filename_prefix.setText("tutorial")
    page._frames_per_second.setValue(20)
    page._include_cursor.setChecked(True)

    assert page.validate() is None
    page.save()

    assert store.load() == RecordingPreferences(
        output_directory=tmp_path / "exports",
        filename_prefix="tutorial",
        options=RecordingOptions(frames_per_second=20, include_cursor=True),
    )


@pytest.mark.parametrize(
    ("preset", "expected"),
    (
        (CompressionPreset.HIGH_QUALITY, (True, 1440, 256, 0)),
        (CompressionPreset.BALANCED, (True, 960, 128, 40)),
        (CompressionPreset.SMALL_FILE, (True, 720, 96, 80)),
    ),
)
def test_compression_page_applies_presets(app, tmp_path, preset, expected) -> None:
    page = CompressionSettingsPage(make_store(tmp_path))

    page._preset.setCurrentIndex(page._preset.findData(preset))

    assert (
        page._gifsicle_enabled.isChecked(),
        page._max_width.value(),
        page._max_colors.value(),
        page._lossy_strength.value(),
    ) == expected


def test_manual_compression_change_selects_custom(app, tmp_path) -> None:
    page = CompressionSettingsPage(make_store(tmp_path))

    page._max_width.setValue(800)

    assert page._preset.currentData() is CompressionPreset.CUSTOM


def test_settings_pages_save_only_their_own_values(app, tmp_path) -> None:
    store = make_store(tmp_path)
    recording_page = RecordingSettingsPage(store)
    compression_page = CompressionSettingsPage(store)

    recording_page._output_directory.setText(str(tmp_path / "exports"))
    recording_page._filename_prefix.setText("tutorial")
    recording_page._frames_per_second.setValue(25)
    recording_page._include_cursor.setChecked(True)
    compression_page._preset.setCurrentIndex(
        compression_page._preset.findData(CompressionPreset.SMALL_FILE)
    )

    recording_page.save()
    compression_page.save()

    assert store.load() == RecordingPreferences(
        output_directory=tmp_path / "exports",
        filename_prefix="tutorial",
        options=RecordingOptions(
            frames_per_second=25,
            max_colors=96,
            include_cursor=True,
            compression_preset=CompressionPreset.SMALL_FILE,
            gifsicle_enabled=True,
            max_width=720,
            lossy_strength=80,
        ),
    )
