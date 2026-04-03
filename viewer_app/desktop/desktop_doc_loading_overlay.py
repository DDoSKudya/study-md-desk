"""
This module implements a PyQt6-based web view host with a centered
loading overlay and animated spinner.

It is used to show a semi-transparent overlay on top of a web page while
content is loading, with theme-aware styling.

The _ArcSpinner widget draws a circular arc using QPainter and advances
its rotation on a QTimer tick to create a spinning animation.

The DocLoadingOverlay widget contains the spinner centered in a vertical
layout and applies dark, sepia, or light translucent backgrounds based
on a qt_mode argument.

The ShellWebHost widget wraps a QWebEngineView and a DocLoadingOverlay,
keeps both resized to its own rectangle in resizeEvent, toggles overlay
visibility via set_doc_loading_visible, and delegates theming to
apply_doc_overlay_theme.

The module also defines several constants for spinner geometry,
animation  timing, and overlay colors, which control the appearance and
behavior of the loading indicator.

In a broader system, this host widget would be embedded wherever web
content is shown so the user gets visual feedback during page or
document loading.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QHideEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

_SPINNER_SIZE_PX = 48
_SPINNER_TICK_MS = 45
_SPINNER_STEP_DEGREES = 18
_SPINNER_MARGIN_PX = 8
_SPINNER_ARC_SPAN_DEGREES = 280
_SPINNER_ARC_OFFSET_DEGREES = 90

_OVERLAY_DARK_BG = "background-color: rgba(11, 18, 32, 245);"
_OVERLAY_SEPIA_BG = "background-color: rgba(255, 247, 230, 245);"
_OVERLAY_LIGHT_BG = "background-color: rgba(255, 255, 255, 245);"


class _ArcSpinner(QWidget):
    """
    Hosts a web view widget with a document loading overlay.

    This widget manages sizing and visibility of a loading indicator
    over the web content.

    The shell arranges the provided web view as its background and
    places a semi-transparent overlay on top to signal loading states.
    It keeps both widgets synchronized with its own geometry so that the
    overlay always covers the web content area.

    Attributes:
        web_view (QWebEngineView):
            The embedded web view that displays the document content.

    # Methods:

        set_doc_loading_visible(
            visible: bool
        ) -> None:
            Controls whether the loading overlay is shown on top of the
            web view when documents are loading.

        apply_doc_overlay_theme(
            qt_mode: str
        ) -> None:
            Updates the appearance of the loading overlay to match the
            current application theme or color mode.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rotation: int = 0
        self._timer: QTimer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(_SPINNER_SIZE_PX, _SPINNER_SIZE_PX)

    def _tick(self) -> None:
        """
        Advances the spinners rotation angle for the next animation
        frame.

        This method is called on each timer tick to keep the loading
        spinner visually rotating.

        It increments the current rotation by a fixed step, wraps it
        within a full circle, and requests a repaint so the new arc
        position is drawn.
        """
        self._rotation = (self._rotation + _SPINNER_STEP_DEGREES) % 360
        self.update()

    def showEvent(self, a0: QShowEvent | None) -> None:  # noqa: N802
        """
        Starts the spinner animation when the widget becomes visible.

        This method ensures that the loading indicator begins rotating
        as soon as the spinner is shown on screen.

        It delegates to the base show event handler and then starts the
        internal timer that drives the spinners frame updates.
        """
        super().showEvent(a0)
        self._timer.start(_SPINNER_TICK_MS)

    def hideEvent(self, a0: QHideEvent | None) -> None:  # noqa: N802
        """
        Stops the spinner animation when the widget is hidden.

        This method ensures that no unnecessary timer events are
        processed while the spinner is not visible.

        It halts the internal timer that drives the rotation and then
        delegates to the base hide event handler to complete the
        standard widget hiding behavior.
        """
        self._timer.stop()
        super().hideEvent(a0)

    def paintEvent(self, a0: QPaintEvent | None) -> None:  # noqa: N802
        """
        Renders the current spinner frame as a rotating arc.

        This event handler draws a partial circular stroke whose
        position reflects the spinners current rotation angle.

        It configures an anti-aliased painter with a colored
        round-capped pen, computes an inset rectangle, and paints an
        arc segment offset and spanned by the configured degrees.
        """
        super().paintEvent(a0)
        p: QPainter = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen: QPen = QPen(QColor(56, 189, 248))
        pen.setWidth(4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = _SPINNER_MARGIN_PX
        rect: QRect = QRect(m, m, self.width() - 2 * m, self.height() - 2 * m)
        p.drawArc(
            rect,
            (self._rotation - _SPINNER_ARC_OFFSET_DEGREES) * 16,
            _SPINNER_ARC_SPAN_DEGREES * 16,
        )


class DocLoadingOverlay(QWidget):
    """
    Hosts a web view widget with a document loading overlay.

    This widget manages sizing and visibility of a loading indicator
    over the web content.

    The shell arranges the provided web view as its background and
    places a semi-transparent overlay on top to signal loading states.
    It keeps both widgets synchronized with its own geometry so that the
    overlay always covers the web content area.

    Attributes:
        web_view (QWebEngineView):
            The embedded web view that displays the document content.

    # Methods:

        set_doc_loading_visible(
            visible: bool
        ) -> None:
            Controls whether the loading overlay is shown on top of the
            web view when documents are loading.

        apply_doc_overlay_theme(
            qt_mode: str
        ) -> None:
            Updates the appearance of the loading overlay to match the
            current application theme or color mode.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        self._spinner = _ArcSpinner(self)
        layout.addWidget(
            self._spinner, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        layout.addStretch(1)
        self.apply_theme("dark")
        self.hide()

    def apply_theme(self, qt_mode: str) -> None:
        """
        Applies a visual theme to the loading overlay background.

        This method selects an appropriate background color based on the
        requested mode.

        Args:
            qt_mode (str):
                The desired theme mode name, such as "dark", "sepia", or
                "light". The value is case-insensitive, and falsy values
                default to "light".
        """
        mode: str = (qt_mode or "light").lower()
        if mode == "dark":
            self.setStyleSheet(_OVERLAY_DARK_BG)
        elif mode == "sepia":
            self.setStyleSheet(_OVERLAY_SEPIA_BG)
        else:
            self.setStyleSheet(_OVERLAY_LIGHT_BG)


class ShellWebHost(QWidget):
    """
    Encapsulates a web view and a document loading overlay widget.

    This host keeps the browser content and the loading indicator
    visually stacked and aligned within a single parent container.

    The class owns a web engine view used to render documents and a
    themed overlay that can be shown while content is loading. It
    ensures the overlay always sits above the web view and tracks the
    host geometry so both widgets resize together.

    Attributes:
        web_view (QWebEngineView):
            The embedded web view that displays the document content.

    # Methods:

        resizeEvent(
            a0: QResizeEvent | None
        ) -> None:
            Handles host resize events to keep the web view and overlay
            stretched to the full available area.
        set_doc_loading_visible(
            visible: bool
        ) -> None:
            Shows or hides the loading overlay above the web view
            depending on whether content is currently loading.
        apply_doc_overlay_theme(
            qt_mode: str
        ) -> None:
            Applies a visual theme to the loading overlay so that its
            background matches the requested appearance mode.
        web_view() -> QWebEngineView:
            Returns the embedded web view instance for direct
            interaction with the rendered document content.
    """

    def __init__(self, web_view: QWebEngineView) -> None:
        super().__init__()
        self._web = web_view
        self._web.setParent(self)
        self._overlay = DocLoadingOverlay(self)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    def resizeEvent(self, a0: QResizeEvent | None) -> None:  # noqa: N802
        """
        Handles resizing of the host to keep child widgets aligned.

        This event ensures that both the web view and the loading
        overlay always fill the host widget after any size change.

        Args:
            a0 (QResizeEvent | None):
                The resize event carrying the new size information for
                the host widget. May be None when invoked
                programmatically.
        """
        super().resizeEvent(a0)
        self._web.setGeometry(self.rect())
        self._overlay.setGeometry(self.rect())

    def set_doc_loading_visible(self, visible: bool) -> None:
        """
        Toggles visibility of the document loading overlay.

        This method shows or hides the loading indicator above the web
        view based on the requested state.

        Args:
            visible (bool):
                Whether the loading overlay should be visible.
                If True, the overlay is displayed above the web content;
                if False, it is hidden.
        """
        self._overlay.setVisible(visible)
        if visible:
            self._overlay.raise_()

    def apply_doc_overlay_theme(self, qt_mode: str) -> None:
        """
        Updates the theme of the document loading overlay.

        This method forwards the requested theme mode to the underlying
        overlay widget so its background appearance matches the
        application style.

        Args:
            qt_mode (str):
                The desired theme mode name, such as "dark", "sepia", or
                "light". The value is interpreted by the overlay to
                choose an appropriate background style.
        """
        self._overlay.apply_theme(qt_mode)

    @property
    def web_view(self) -> QWebEngineView:
        """
        Provides access to the hosted web view widget.

        This property exposes the underlying web engine view so callers
        can interact with the displayed document content.

        Returns:
            QWebEngineView:
                The web view instance embedded in this shell, which
                renders and manages the document content.
        """
        return self._web
