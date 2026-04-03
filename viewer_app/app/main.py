"""
This module defines the main entry point for launching the desktop
application.

It ensures the shared application context is initialized before starting
the GUI runtime.

It imports get_app_context to construct or retrieve the global
application context and run_desktop_app to start the desktop app.

The single main function calls get_app_context() (for side-effectful
initialization) and then invokes run_desktop_app() to hand control over
to the desktop application loop.

In the broader system, this file acts as the top-level bootstrapper that
wires up context and then starts the desktop UI process.
"""

from __future__ import annotations

from viewer_app.app.context import get_app_context
from viewer_app.desktop import run_desktop_app


def main() -> None:
    """
    Entry point for the desktop application.

    This function initializes the application context and runs the
    desktop application.
    """
    get_app_context()
    run_desktop_app()
