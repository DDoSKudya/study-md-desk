"""
This module encapsulates small lifecycle helpers for a PyQt-based
desktop viewer, covering main web view setup, periodic TTS
highlighting, and application shutdown cleanup.

It defines type aliases for callback signatures and two lightweight
Protocols that describe the minimal interfaces of Qt-like signals and
an application object exposing an about-to-quit signal.

The private _cache_busted_url function generates a QUrl with a
millisecond timestamp query parameter to force the main HTML shell to
reload without using cache.

The install_main_view_runtime function initializes a QWebEngineView with
this cache-busted URL and connects its loadFinished signal to a callback
that synchronizes menus with the page state.

The install_tts_highlight_timer function creates, configures, and starts
a QTimer that periodically calls a provided text-to-speech highlight
dispatcher.

The install_about_to_quit_cleanup function wires a cleanup callback into
the applications aboutToQuit signal so resources are released reliably
during shutdown.

Within the broader system, this module serves as glue between Qt
event-driven lifecycle and higher-level application logic, keeping view
loading, TTS timing, and cleanup behavior consistent and centralized.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from typing import Callable, Protocol, TypeAlias

SyncMenuFromPageFn: TypeAlias = Callable[[bool], None]
DispatchTtsHighlightFn: TypeAlias = Callable[[], None]
CleanupFn: TypeAlias = Callable[[], None]


class _SignalProtocol(Protocol):
    """
    Describes the minimal interface expected from a Qt-like signal
    object.

    This protocol is used to type-check connections without depending on
    concrete Qt signal classes.

    It models only the ability to connect callables that will be invoked
    when the signal is emitted.

    # Methods:

        connect(
            slot: Callable[..., None]
        ) -> None:
            Registers a callable to be invoked whenever the signal
            fires.
    """

    def connect(self, slot: Callable[..., None]) -> None: ...


class _AboutToQuitApp(Protocol):
    """
    Represents an application object that exposes an about-to-quit
    signal.

    This protocol is used to type-check cleanup registration without
    depending on a concrete Qt application class.

    It models only the presence of an aboutToQuit signal-like attribute
    that can be connected to arbitrary cleanup callables.

    Attributes:
        aboutToQuit (_SignalProtocol):
            A signal-like object that fires just before the application
            quits, allowing clients to register cleanup handlers.
    """

    aboutToQuit: _SignalProtocol  # noqa: N815


def _cache_busted_url(base_url: str) -> QUrl:
    """
    Generates a cache-busted URL based on a base address and timestamp.

    This helper appends a millisecond-resolution query parameter to
    force browsers or web views to bypass cached content.

    The function is useful when loading a local HTML shell that should
    always reflect the latest version without relying on cache
    invalidation headers.

    Args:
        base_url (str):
            The base URL to which a cache-busting query parameter will
            be appended.

    Returns:
        QUrl:
            A Qt URL object representing the base URL with an added _
            query parameter containing the current time in
            milliseconds.
    """
    return QUrl(f"{base_url}?_={int(time.time() * 1000)}")


def install_main_view_runtime(
    *,
    view: QWebEngineView,
    base_url: str,
    sync_menu_from_page: SyncMenuFromPageFn,
) -> None:
    """
    Initializes the main document web view with a cache-busted URL and
    menu sync behavior.

    This helper ensures the shell HTML is freshly loaded and that
    application menus stay aligned with the page state.

    The function sets the views URL with a timestamp query parameter
    and hooks the load-finished signal to a caller-provided
    synchronization callback.

    Args:
        view (QWebEngineView):
            The main web view that will display the document shell and
            emit load-finished events.
        base_url (str):
            The base URL of the shell page to be loaded into the web
            view before cache busting is applied.
        sync_menu_from_page (SyncMenuFromPageFn):
            A callback that refreshes application menus based on the
            current page state once loading completes.
    """

    def on_load_finished(_ok: bool) -> None:
        """
        Starts and returns a timer that periodically dispatches TTS
        highlight events.

        This helper centralizes creation and configuration of the QTimer
        used to drive text-to-speech highlighting.

        The function sets the timer interval, connects its timeout to
        the provided callback, and starts it so highlighting continues
        on a fixed schedule.

        Args:
            dispatch_tts_highlight (DispatchTtsHighlightFn):
                A callable that performs a single highlight update when
                invoked on each timer tick.
            interval_ms (int):
                The timer interval in milliseconds that controls how
                frequently highlight updates are dispatched.

        Returns:
            QTimer:
                The running Qt timer instance responsible for triggering
                periodic TTS highlight callbacks.
        """
        sync_menu_from_page(_ok)

    view.setUrl(_cache_busted_url(base_url))
    view.loadFinished.connect(on_load_finished)


def install_tts_highlight_timer(
    *,
    dispatch_tts_highlight: DispatchTtsHighlightFn,
    interval_ms: int = 180,
) -> QTimer:
    """
    Starts and returns a timer that periodically dispatches TTS
    highlight events.

    This helper centralizes creation and configuration of the QTimer
    used to drive text-to-speech highlighting.

    The function sets the timer interval, connects its timeout to the
    provided callback, and starts it so highlighting continues on a
    fixed schedule.

    Args:
        dispatch_tts_highlight (DispatchTtsHighlightFn):
            A callable that performs a single highlight update when
            invoked on each timer tick.
        interval_ms (int):
            The timer interval in milliseconds that controls how
            frequently highlight updates are dispatched.

    Returns:
        QTimer:
            The running Qt timer instance responsible for triggering
            periodic TTS highlight callbacks.
    """
    timer: QTimer = QTimer()
    timer.setInterval(interval_ms)
    timer.timeout.connect(dispatch_tts_highlight)
    timer.start()
    return timer


def install_about_to_quit_cleanup(
    *,
    app: _AboutToQuitApp,
    cleanup: CleanupFn,
) -> None:
    """
    Registers a cleanup callback to run just before the application
    quits.

    This helper centralizes wiring of shutdown logic to the frameworks
    about-to-quit signal.

    The function connects the provided cleanup callable to the
    applications aboutToQuit signal so resources can be released
    reliably during shutdown.

    Args:
        app (_AboutToQuitApp):
            The application-like object that exposes an aboutToQuit
            signal used to trigger cleanup.
        cleanup (CleanupFn):
            A no-argument callable that performs finalization work when
            the application is about to exit.
    """
    app.aboutToQuit.connect(cleanup)
