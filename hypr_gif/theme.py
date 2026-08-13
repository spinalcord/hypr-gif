"""Dracula theme definitions for the application UI."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


class DraculaColor(str, Enum):
    BACKGROUND = "#282a36"
    SELECTION = "#44475a"
    FOREGROUND = "#f8f8f2"
    COMMENT = "#6272a4"
    CYAN = "#8be9fd"
    GREEN = "#50fa7b"
    ORANGE = "#ffb86c"
    PINK = "#ff79c6"
    PURPLE = "#bd93f9"
    RED = "#ff5555"
    YELLOW = "#f1fa8c"


DRACULA_STYLESHEET = f"""
QWidget {{
    background-color: {DraculaColor.BACKGROUND.value};
    color: {DraculaColor.FOREGROUND.value};
    selection-background-color: {DraculaColor.SELECTION.value};
    selection-color: {DraculaColor.FOREGROUND.value};
}}

QDialog, QMessageBox {{
    background-color: {DraculaColor.BACKGROUND.value};
}}

QPushButton, QToolButton, QDialogButtonBox QPushButton {{
    background-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.FOREGROUND.value};
    border: 1px solid {DraculaColor.COMMENT.value};
    border-radius: 4px;
    padding: 5px 10px;
}}

QPushButton:hover, QToolButton:hover {{
    border-color: {DraculaColor.PINK.value};
    color: {DraculaColor.PINK.value};
}}

QPushButton:pressed, QToolButton:pressed,
QPushButton:checked, QToolButton:checked {{
    background-color: {DraculaColor.PURPLE.value};
    border-color: {DraculaColor.PURPLE.value};
    color: {DraculaColor.BACKGROUND.value};
}}

QPushButton:focus, QToolButton:focus {{
    border: 2px solid {DraculaColor.CYAN.value};
}}

QPushButton:disabled, QToolButton:disabled {{
    background-color: {DraculaColor.BACKGROUND.value};
    border-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.COMMENT.value};
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit,
QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.FOREGROUND.value};
    border: 1px solid {DraculaColor.COMMENT.value};
    border-radius: 3px;
    padding: 3px;
}}

QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
QTextEdit:hover, QPlainTextEdit:hover, QListWidget:hover,
QTreeWidget:hover, QTableWidget:hover {{
    border-color: {DraculaColor.PURPLE.value};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus,
QTreeWidget:focus, QTableWidget:focus {{
    border: 2px solid {DraculaColor.PINK.value};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QListWidget:disabled, QTreeWidget:disabled, QTableWidget:disabled {{
    background-color: {DraculaColor.BACKGROUND.value};
    border-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.COMMENT.value};
}}

QListWidget::item, QTreeWidget::item, QTableWidget::item {{
    padding: 4px;
}}

QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: {DraculaColor.COMMENT.value};
}}

QListWidget::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected {{
    background-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.PINK.value};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {DraculaColor.COMMENT.value};
    border-radius: 3px;
    background-color: {DraculaColor.SELECTION.value};
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {DraculaColor.PINK.value};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {DraculaColor.PINK.value};
    border-color: {DraculaColor.PINK.value};
}}

QSlider::groove:horizontal {{
    height: 6px;
    border-radius: 3px;
    background-color: {DraculaColor.SELECTION.value};
}}

QSlider::sub-page:horizontal {{
    border-radius: 3px;
    background-color: {DraculaColor.PURPLE.value};
}}

QSlider::handle:horizontal {{
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background-color: {DraculaColor.PINK.value};
}}

QSlider::handle:horizontal:hover {{
    background-color: {DraculaColor.CYAN.value};
}}

QSlider::handle:horizontal:disabled {{
    background-color: {DraculaColor.COMMENT.value};
}}

QToolTip {{
    background-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.FOREGROUND.value};
    border: 1px solid {DraculaColor.PURPLE.value};
    padding: 4px;
}}

QMenu {{
    background-color: {DraculaColor.BACKGROUND.value};
    color: {DraculaColor.FOREGROUND.value};
    border: 1px solid {DraculaColor.COMMENT.value};
}}

QMenu::item:selected {{
    background-color: {DraculaColor.SELECTION.value};
    color: {DraculaColor.PINK.value};
}}

QScrollBar:horizontal, QScrollBar:vertical {{
    background-color: {DraculaColor.BACKGROUND.value};
    border: none;
}}

QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{
    background-color: {DraculaColor.COMMENT.value};
    border-radius: 4px;
    min-width: 24px;
    min-height: 24px;
}}

QScrollBar::handle:horizontal:hover, QScrollBar::handle:vertical:hover {{
    background-color: {DraculaColor.PURPLE.value};
}}
""".strip()


def apply_dracula_theme(app: QApplication) -> None:
    """Apply the fixed Dracula widget theme to an application."""

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(
        QPalette.ColorRole.Window,
        QColor(DraculaColor.BACKGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.WindowText,
        QColor(DraculaColor.FOREGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.Base,
        QColor(DraculaColor.BACKGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.AlternateBase,
        QColor(DraculaColor.SELECTION.value),
    )
    palette.setColor(
        QPalette.ColorRole.ToolTipBase,
        QColor(DraculaColor.SELECTION.value),
    )
    palette.setColor(
        QPalette.ColorRole.ToolTipText,
        QColor(DraculaColor.FOREGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.Text,
        QColor(DraculaColor.FOREGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.Button,
        QColor(DraculaColor.SELECTION.value),
    )
    palette.setColor(
        QPalette.ColorRole.ButtonText,
        QColor(DraculaColor.FOREGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.BrightText,
        QColor(DraculaColor.RED.value),
    )
    palette.setColor(
        QPalette.ColorRole.Link,
        QColor(DraculaColor.CYAN.value),
    )
    palette.setColor(
        QPalette.ColorRole.LinkVisited,
        QColor(DraculaColor.PURPLE.value),
    )
    palette.setColor(
        QPalette.ColorRole.Highlight,
        QColor(DraculaColor.SELECTION.value),
    )
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor(DraculaColor.FOREGROUND.value),
    )
    palette.setColor(
        QPalette.ColorRole.PlaceholderText,
        QColor(DraculaColor.COMMENT.value),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.WindowText,
        QColor(DraculaColor.COMMENT.value),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(DraculaColor.COMMENT.value),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(DraculaColor.COMMENT.value),
    )
    app.setPalette(palette)
    app.setStyleSheet(DRACULA_STYLESHEET)
