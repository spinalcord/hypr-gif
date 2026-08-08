"""Persistent application settings and extensible settings pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QSettings, QStandardPaths, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .recording import (
    CompressionPreset,
    RecordingOptions,
    compression_preset_values,
)


INVALID_FILENAME_CHARACTERS = re.compile(r"[\\/:*?\"<>|]")


class RecordingSettingKey(str, Enum):
    OUTPUT_DIRECTORY = "recording/output_directory"
    FILENAME_PREFIX = "recording/filename_prefix"
    FRAMES_PER_SECOND = "recording/frames_per_second"
    MAX_COLORS = "recording/max_colors"
    INCLUDE_CURSOR = "recording/include_cursor"


class CompressionSettingKey(str, Enum):
    PRESET = "compression/preset"
    GIFSICLE_ENABLED = "compression/gifsicle_enabled"
    MAX_WIDTH = "compression/max_width"
    LOSSY_STRENGTH = "compression/lossy_strength"


def default_output_directory() -> Path:
    movies = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.MoviesLocation
    )
    if movies:
        return Path(movies) / "Wayland GIF Recorder"
    return Path.cwd() / "recordings"


@dataclass(frozen=True, slots=True)
class RecordingPreferences:
    output_directory: Path
    filename_prefix: str
    options: RecordingOptions


class RecordingSettingsStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = (
            settings
            if settings is not None
            else QSettings("WaylandGifRecorder", "WaylandGifRecorder")
        )

    def load(self) -> RecordingPreferences:
        output_directory = Path(
            self._settings.value(
                RecordingSettingKey.OUTPUT_DIRECTORY.value,
                str(default_output_directory()),
                type=str,
            )
        )
        filename_prefix = self._settings.value(
            RecordingSettingKey.FILENAME_PREFIX.value,
            "recording",
            type=str,
        )
        frames_per_second = self._settings.value(
            RecordingSettingKey.FRAMES_PER_SECOND.value,
            15,
            type=int,
        )
        max_colors = self._settings.value(
            RecordingSettingKey.MAX_COLORS.value,
            128,
            type=int,
        )
        include_cursor = self._settings.value(
            RecordingSettingKey.INCLUDE_CURSOR.value,
            False,
            type=bool,
        )
        compression_settings_exist = any(
            self._settings.contains(key.value) for key in CompressionSettingKey
        )
        preset_value = self._settings.value(
            CompressionSettingKey.PRESET.value,
            CompressionPreset.BALANCED.value,
            type=str,
        )
        if not compression_settings_exist and max_colors != 128:
            preset_value = CompressionPreset.CUSTOM.value
        gifsicle_enabled = self._settings.value(
            CompressionSettingKey.GIFSICLE_ENABLED.value,
            True,
            type=bool,
        )
        max_width = self._settings.value(
            CompressionSettingKey.MAX_WIDTH.value,
            960,
            type=int,
        )
        lossy_strength = self._settings.value(
            CompressionSettingKey.LOSSY_STRENGTH.value,
            40,
            type=int,
        )
        try:
            options = RecordingOptions(
                frames_per_second=frames_per_second,
                max_colors=max_colors,
                include_cursor=include_cursor,
                compression_preset=CompressionPreset(preset_value),
                gifsicle_enabled=gifsicle_enabled,
                max_width=max_width,
                lossy_strength=lossy_strength,
            )
        except (TypeError, ValueError):
            options = RecordingOptions(
                frames_per_second=frames_per_second
                if 1 <= frames_per_second <= 60
                else 15,
                max_colors=max_colors if 2 <= max_colors <= 256 else 128,
                include_cursor=include_cursor,
            )
        if not filename_prefix or INVALID_FILENAME_CHARACTERS.search(filename_prefix):
            filename_prefix = "recording"
        return RecordingPreferences(output_directory, filename_prefix, options)

    def save(self, preferences: RecordingPreferences) -> None:
        self._save_recording_values(preferences)
        self._save_compression_values(preferences.options)
        self._settings.sync()

    def save_recording(self, preferences: RecordingPreferences) -> None:
        self._save_recording_values(preferences)
        self._settings.sync()

    def save_compression(self, options: RecordingOptions) -> None:
        self._save_compression_values(options)
        self._settings.sync()

    def _save_recording_values(self, preferences: RecordingPreferences) -> None:
        self._settings.setValue(
            RecordingSettingKey.OUTPUT_DIRECTORY.value,
            str(preferences.output_directory),
        )
        self._settings.setValue(
            RecordingSettingKey.FILENAME_PREFIX.value,
            preferences.filename_prefix,
        )
        self._settings.setValue(
            RecordingSettingKey.FRAMES_PER_SECOND.value,
            preferences.options.frames_per_second,
        )
        self._settings.setValue(
            RecordingSettingKey.INCLUDE_CURSOR.value,
            preferences.options.include_cursor,
        )

    def _save_compression_values(self, options: RecordingOptions) -> None:
        self._settings.setValue(
            CompressionSettingKey.PRESET.value,
            options.compression_preset.value,
        )
        self._settings.setValue(
            CompressionSettingKey.GIFSICLE_ENABLED.value,
            options.gifsicle_enabled,
        )
        self._settings.setValue(
            CompressionSettingKey.MAX_WIDTH.value,
            options.max_width,
        )
        self._settings.setValue(
            RecordingSettingKey.MAX_COLORS.value,
            options.max_colors,
        )
        self._settings.setValue(
            CompressionSettingKey.LOSSY_STRENGTH.value,
            options.lossy_strength,
        )

    def next_output_path(self, now: datetime | None = None) -> Path:
        preferences = self.load()
        timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
        base_name = f"{preferences.filename_prefix}_{timestamp}"
        candidate = preferences.output_directory / f"{base_name}.gif"
        suffix = 2
        while candidate.exists():
            candidate = preferences.output_directory / f"{base_name}_{suffix}.gif"
            suffix += 1
        return candidate


class SettingsPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title

    @property
    def title(self) -> str:
        return self._title

    def load(self) -> None:
        raise NotImplementedError

    def validate(self) -> str | None:
        raise NotImplementedError

    def save(self) -> None:
        raise NotImplementedError


class RecordingSettingsPage(SettingsPage):
    def __init__(
        self,
        store: RecordingSettingsStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Recording", parent)
        self._store = store

        self._output_directory = QLineEdit(self)
        browse_button = QPushButton("Browse…", self)
        browse_button.clicked.connect(self._browse_output_directory)
        directory_row = QWidget(self)
        directory_layout = QHBoxLayout(directory_row)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        directory_layout.addWidget(self._output_directory, 1)
        directory_layout.addWidget(browse_button)

        self._filename_prefix = QLineEdit(self)
        self._filename_prefix.setPlaceholderText("recording")

        self._frames_per_second = QSpinBox(self)
        self._frames_per_second.setRange(1, 60)
        self._frames_per_second.setSuffix(" FPS")

        self._include_cursor = QCheckBox("Include mouse cursor", self)

        form = QFormLayout(self)
        form.addRow("Output directory", directory_row)
        form.addRow("Filename prefix", self._filename_prefix)
        form.addRow("Frame rate", self._frames_per_second)
        form.addRow("", self._include_cursor)

        self._output_directory.textChanged.connect(self.changed.emit)
        self._filename_prefix.textChanged.connect(self.changed.emit)
        self._frames_per_second.valueChanged.connect(self.changed.emit)
        self._include_cursor.toggled.connect(self.changed.emit)
        self.load()

    def load(self) -> None:
        preferences = self._store.load()
        self._output_directory.setText(str(preferences.output_directory))
        self._filename_prefix.setText(preferences.filename_prefix)
        self._frames_per_second.setValue(
            preferences.options.frames_per_second
        )
        self._include_cursor.setChecked(preferences.options.include_cursor)

    def validate(self) -> str | None:
        if not self._output_directory.text().strip():
            return "Choose an output directory."
        prefix = self._filename_prefix.text().strip()
        if not prefix:
            return "Enter a filename prefix."
        if INVALID_FILENAME_CHARACTERS.search(prefix):
            return "The filename prefix contains an invalid character."
        return None

    def save(self) -> None:
        current = self._store.load()
        preferences = RecordingPreferences(
            output_directory=Path(self._output_directory.text().strip()).expanduser(),
            filename_prefix=self._filename_prefix.text().strip(),
            options=replace(
                current.options,
                frames_per_second=self._frames_per_second.value(),
                include_cursor=self._include_cursor.isChecked(),
            ),
        )
        self._store.save_recording(preferences)

    def _browse_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose output directory",
            self._output_directory.text(),
        )
        if directory:
            self._output_directory.setText(directory)


class CompressionSettingsPage(SettingsPage):
    def __init__(
        self,
        store: RecordingSettingsStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("GIF Compression", parent)
        self._store = store
        self._updating_controls = False

        self._preset = QComboBox(self)
        self._preset.addItem("High Quality", CompressionPreset.HIGH_QUALITY)
        self._preset.addItem("Balanced", CompressionPreset.BALANCED)
        self._preset.addItem("Small File", CompressionPreset.SMALL_FILE)
        self._preset.addItem("Custom", CompressionPreset.CUSTOM)

        self._gifsicle_enabled = QCheckBox("Optimize with gifsicle", self)

        self._max_width = QSpinBox(self)
        self._max_width.setRange(1, 16384)
        self._max_width.setSuffix(" px")
        self._max_width.setSingleStep(80)

        self._max_colors = QSpinBox(self)
        self._max_colors.setRange(2, 256)
        self._max_colors.setSingleStep(8)

        self._lossy_strength = QSpinBox(self)
        self._lossy_strength.setRange(0, 200)

        form = QFormLayout(self)
        form.addRow("Preset", self._preset)
        form.addRow("", self._gifsicle_enabled)
        form.addRow("Maximum width", self._max_width)
        form.addRow("Maximum colors", self._max_colors)
        form.addRow("Lossy strength", self._lossy_strength)

        self._preset.currentIndexChanged.connect(self._preset_changed)
        self._gifsicle_enabled.toggled.connect(self._detail_changed)
        self._max_width.valueChanged.connect(self._detail_changed)
        self._max_colors.valueChanged.connect(self._detail_changed)
        self._lossy_strength.valueChanged.connect(self._detail_changed)
        self.load()

    def load(self) -> None:
        options = self._store.load().options
        self._updating_controls = True
        try:
            index = self._preset.findData(options.compression_preset)
            self._preset.setCurrentIndex(index)
            self._gifsicle_enabled.setChecked(options.gifsicle_enabled)
            self._max_width.setValue(options.max_width)
            self._max_colors.setValue(options.max_colors)
            self._lossy_strength.setValue(options.lossy_strength)
        finally:
            self._updating_controls = False

    def validate(self) -> str | None:
        return None

    def save(self) -> None:
        current = self._store.load().options
        preset = self._preset.currentData()
        options = replace(
            current,
            compression_preset=preset,
            gifsicle_enabled=self._gifsicle_enabled.isChecked(),
            max_width=self._max_width.value(),
            max_colors=self._max_colors.value(),
            lossy_strength=self._lossy_strength.value(),
        )
        self._store.save_compression(options)

    def _preset_changed(self) -> None:
        if self._updating_controls:
            return
        preset = self._preset.currentData()
        if preset is not CompressionPreset.CUSTOM:
            values = compression_preset_values(preset)
            self._updating_controls = True
            try:
                self._gifsicle_enabled.setChecked(values[0])
                self._max_width.setValue(values[1])
                self._max_colors.setValue(values[2])
                self._lossy_strength.setValue(values[3])
            finally:
                self._updating_controls = False
        self.changed.emit()

    def _detail_changed(self) -> None:
        if self._updating_controls:
            return
        custom_index = self._preset.findData(CompressionPreset.CUSTOM)
        self._updating_controls = True
        try:
            self._preset.setCurrentIndex(custom_index)
        finally:
            self._updating_controls = False
        self.changed.emit()


class SettingsDialog(QDialog):
    settings_applied = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Wayland GIF Recorder settings")
        self.resize(620, 360)
        self._pages: list[SettingsPage] = []

        self._page_list = QListWidget(self)
        self._page_list.setMaximumWidth(180)
        self._page_stack = QStackedWidget(self)
        self._empty_label = QLabel("No settings pages are registered.", self)
        self._page_stack.addWidget(self._empty_label)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self._page_list)
        content_layout.addWidget(self._page_stack, 1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self._accept)
        self._buttons.rejected.connect(self.reject)
        apply_button = self._buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addLayout(content_layout, 1)
        layout.addWidget(self._buttons)

        self._page_list.currentRowChanged.connect(self._page_stack.setCurrentIndex)

    @property
    def pages(self) -> tuple[SettingsPage, ...]:
        return tuple(self._pages)

    def register_page(self, page: SettingsPage) -> None:
        if page in self._pages:
            raise ValueError("the settings page is already registered")
        if any(existing.title == page.title for existing in self._pages):
            raise ValueError(f"a settings page named {page.title!r} already exists")
        if not self._pages:
            self._page_stack.removeWidget(self._empty_label)
            self._empty_label.deleteLater()
        self._pages.append(page)
        self._page_list.addItem(page.title)
        self._page_stack.addWidget(page)
        if len(self._pages) == 1:
            self._page_list.setCurrentRow(0)

    def open(self) -> None:
        for page in self._pages:
            page.load()
        super().open()

    def apply(self) -> bool:
        for index, page in enumerate(self._pages):
            message = page.validate()
            if message is not None:
                self._page_list.setCurrentRow(index)
                QMessageBox.warning(self, "Invalid settings", message)
                return False
        for page in self._pages:
            page.save()
        self.settings_applied.emit()
        return True

    def _accept(self) -> None:
        if self.apply():
            self.accept()
