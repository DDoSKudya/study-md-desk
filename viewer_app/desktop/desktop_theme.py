"""
This module defines a helper for applying a consistent visual theme to a
Qt-based desktop application.

Its main role is to centralize light, dark, and sepia color schemes and
apply them as a global stylesheet to the app.

The file contains a single function, apply_qt_theme, which accepts a Qt
application object and a mode string.

Inside the function, it normalizes the mode, chooses a palette of colors
for window backgrounds, panels, borders, text, accents, and scrollbar
states, and then builds a large Qt style sheet string using f-strings.

The constructed style sheet targets common Qt widgets such as
QMainWindow, QWidget, QMenuBar, QMenu, QStatusBar, QSplitter,
QTabWidget, QTabBar, QLineEdit, QComboBox, QPushButton, and QScrollBar,
setting their colors, borders, padding, and hover states.

In the broader system, this function is likely called during application
startup or when the user switches themes, ensuring a uniform
look-and-feel across all top-level and common widgets.
"""

from __future__ import annotations

from typing import Any


def apply_qt_theme(app: Any, mode: str) -> None:
    """
    Apply a high-level light, dark, or sepia theme to a Qt application.

    This function selects a predefined color palette based on the
    requested mode and installs a corresponding global Qt stylesheet on
    the given application instance.

    Args:
        app (Any): Qt application object (for example, a QApplication)
        whose setStyleSheet method will be called to apply the theme.
    mode (str):
        Theme name indicating which palette to use; recognized values
        are "light", "dark", and "sepia" (case-insensitive), with any
        other or empty value defaulting to the light theme.
    """
    mode = (mode or "light").lower()
    if mode == "dark":
        window_bg = "#0b1220"
        panel_bg = "#111827"
        panel_bg_alt = "#0f172a"
        border = "#223049"
        border_soft = "#1a2438"
        text = "#e5e7eb"
        text_muted = "#94a3b8"
        accent = "#38bdf8"
        scroll_thumb = "rgba(148, 163, 184, 0.40)"
        scroll_hover = "rgba(226, 232, 240, 0.55)"
    elif mode == "sepia":
        window_bg = "#fff7e6"
        panel_bg = "#fff3d6"
        panel_bg_alt = "#fde68a"
        border = "#f59e0b33"
        border_soft = "#f59e0b1f"
        text = "#3f2d1e"
        text_muted = "#6b4b2a"
        accent = "#b45309"
        scroll_thumb = "rgba(120, 53, 15, 0.26)"
        scroll_hover = "rgba(120, 53, 15, 0.36)"
    else:
        window_bg = "#f8fafc"
        panel_bg = "#ffffff"
        panel_bg_alt = "#f8fafc"
        border = "#e5e7eb"
        border_soft = "#dbe3ee"
        text = "#111827"
        text_muted = "#64748b"
        accent = "#0ea5e9"
        scroll_thumb = "#cbd5e1"
        scroll_hover = "#94a3b8"
    app.setStyleSheet(
        f"QMainWindow, QWidget {{ background: {window_bg}; color: {text}; }}"
        f"QMenuBar {{ background: {panel_bg_alt}; color: {text}; border-bottom: 1px solid {border}; }}"
        "QMenuBar::item { padding: 4px 8px; background: transparent; }"
        f"QMenuBar::item:selected {{ background: {panel_bg}; color: {text}; }}"
        f"QMenu {{ background: {panel_bg}; color: {text}; border: 1px solid {border}; }}"
        f"QMenu::item:selected {{ background: {panel_bg_alt}; }}"
        f"QStatusBar {{ background: {panel_bg_alt}; color: {text_muted}; border-top: 1px solid {border}; }}"
        f"QSplitter {{ background: {window_bg}; }}"
        f"QSplitter::handle {{ background: {border}; margin: 0; }}"
        f"QSplitter::handle:hover {{ background: {accent}; }}"
        f"QTabWidget::pane {{ border: 1px solid {border}; background: {panel_bg}; top: -1px; }}"
        f"QTabBar::tab {{ background: {panel_bg_alt}; color: {text_muted}; border: 1px solid {border}; padding: 6px 10px; margin-right: 2px; }}"
        f"QTabBar::tab:selected {{ background: {panel_bg}; color: {text}; border-bottom-color: {panel_bg}; }}"
        f"QTabBar::tab:hover {{ color: {text}; }}"
        f"QLineEdit, QComboBox {{ background: {panel_bg}; color: {text}; border: 1px solid {border_soft}; padding: 6px 8px; }}"
        f"QPushButton {{ background: {panel_bg_alt}; color: {text}; border: 1px solid {border}; padding: 6px 10px; }}"
        f"QPushButton:hover {{ border-color: {accent}; }}"
        "QScrollBar:vertical { border: none; background: transparent; width: 8px; margin: 0; }"
        "QScrollBar:horizontal { border: none; background: transparent; height: 8px; margin: 0; }"
        f"QScrollBar::handle:vertical {{ background: {scroll_thumb}; min-height: 28px; border-radius: 4px; margin: 1px; }}"
        f"QScrollBar::handle:horizontal {{ background: {scroll_thumb}; min-width: 28px; border-radius: 4px; margin: 1px; }}"
        f"QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {scroll_hover}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; subcontrol-origin: margin; }"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; subcontrol-origin: margin; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }"
    )
