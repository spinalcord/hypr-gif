from PyQt6.QtGui import QColor, QPalette

from hypr_gif.theme import (
    DRACULA_STYLESHEET,
    DraculaColor,
    apply_dracula_theme,
)


def test_apply_dracula_theme_configures_application() -> None:
    class ThemeApplication:
        def __init__(self) -> None:
            self.style_name = None
            self.palette = None
            self.stylesheet = None

        def setStyle(self, name) -> None:
            self.style_name = name

        def setPalette(self, palette) -> None:
            self.palette = palette

        def setStyleSheet(self, stylesheet) -> None:
            self.stylesheet = stylesheet

    app = ThemeApplication()

    apply_dracula_theme(app)

    assert app.style_name == "Fusion"
    assert app.palette.color(QPalette.ColorRole.Window) == QColor(
        DraculaColor.BACKGROUND.value
    )
    assert app.palette.color(QPalette.ColorRole.WindowText) == QColor(
        DraculaColor.FOREGROUND.value
    )
    assert app.palette.color(QPalette.ColorRole.Highlight) == QColor(
        DraculaColor.SELECTION.value
    )
    assert app.stylesheet == DRACULA_STYLESHEET


def test_dracula_stylesheet_covers_colors_and_widget_states() -> None:
    assert {color.value for color in DraculaColor} == {
        "#282a36",
        "#44475a",
        "#f8f8f2",
        "#6272a4",
        "#8be9fd",
        "#50fa7b",
        "#ffb86c",
        "#ff79c6",
        "#bd93f9",
        "#ff5555",
        "#f1fa8c",
    }
    for color in (
        DraculaColor.BACKGROUND,
        DraculaColor.SELECTION,
        DraculaColor.FOREGROUND,
        DraculaColor.COMMENT,
        DraculaColor.CYAN,
        DraculaColor.PINK,
        DraculaColor.PURPLE,
    ):
        assert color.value in DRACULA_STYLESHEET

    for selector in (
        "QPushButton:hover",
        "QPushButton:focus",
        "QPushButton:disabled",
        "QLineEdit:focus",
        "QListWidget::item:selected",
        "QSlider::handle:horizontal:hover",
        "QSlider::handle:horizontal:disabled",
        "QToolTip",
    ):
        assert selector in DRACULA_STYLESHEET
