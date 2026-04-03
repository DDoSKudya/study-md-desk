"""
The run_desktop_app function imports main from
viewer_app.desktop.legacy_app at call time and invokes it, thereby
starting the application's main loop.

In the broader system, this file offers a stable, importable way to
launch the desktop app without depending directly on the legacy module
structure.
"""

__all__ = ["run_desktop_app"]


def run_desktop_app() -> None:
    """
    Starts the desktop viewer application.

    This convenience wrapper delegates to the legacy desktop entry point
    so callers can launch the app via a single importable function.

    This function does not return a value; it runs the applications main
    loop and exits when the application terminates.
    """
    from viewer_app.desktop.legacy_app import main

    main()
