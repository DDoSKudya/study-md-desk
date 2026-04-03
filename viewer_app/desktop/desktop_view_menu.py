"""
This module defines and wires up the "View" and "Settings" menus for a
PyQt6-based markdown viewer desktop application.

It controls how menu items reflect and manipulate the visibility state
of panels inside an embedded web-based viewer.

It declares type aliases for view menu action mappings and a sync
callback type, plus constants for default panel keys and View menu
entries.

The _shell_panel_mapping_from_json helper parses JSON from the web page
into a safe mapping of panel keys to visibility flags, returning an
empty mapping on bad input.

The _apply_shell_panel_checkmarks function applies a stored panel state
mapping to QAction checkmarks in the View menu, including a special
case for an "interpreter" panel if present.

The install_view_menu function creates the View menu, adds checkable
actions for each known panel entry, and returns a dict of those actions
keyed by panel identifiers.

The connect_view_panel_actions function connects those actions to
JavaScript functions in the embedded QWebEngineView, so toggling a menu
item shows or hides the corresponding panel in the web content.

The inner set_panel and wire functions encapsulate running JavaScript on
the page and wiring each specific QAction to its panel key.

The build_sync_menu_from_page function constructs a callback that, when
invoked, runs JavaScript in the page to fetch current panel state,
parses it, updates the View menu actions, adjusts the "online" action
using an external loader, and triggers external panel sync hooks.

This callback is intended to be used with page load events to keep the
Qt UI in sync with the web-based viewer's internal panel configuration.

The install_settings_menu function adds a Settings menu with a single
action that opens the application's settings UI when triggered.

Overall, this module acts as the bridge between the Qt menu system and
the web-based viewer, ensuring that panel visibility is synchronized in
both directions and providing a hook into application settings.
"""


from __future__ import annotations

import json

from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QMainWindow, QMenu, QMenuBar
from typing import Callable, Final, Mapping, TypeAlias

ViewMenuActions: TypeAlias = dict[str, QAction]
SyncMenuFromPageFn: TypeAlias = Callable[[bool], None]

_DEFAULT_SHELL_PANEL_KEYS: Final[tuple[str, str, str]] = (
    "files",
    "toc",
    "content",
)

_VIEW_MENU_ENTRIES: Final[tuple[tuple[str, str], ...]] = (
    ("files", "&Files"),
    ("toc", "&Contents"),
    ("content", "&Document"),
    ("online", "&Online"),
)


def _shell_panel_mapping_from_json(raw: str | None) -> Mapping[str, object]:
    """
    Parse a JSON-encoded shell panel state mapping.

    This safely converts a raw string into a dictionary describing panel
    visibility, falling back to an empty mapping on invalid input.

    Args:
        raw (str | None):
            Raw JSON string expected to encode a mapping of panel keys
            to truthy or falsy values, or None/whitespace when no state
            is available.

    Returns:
        Mapping[str, object]:
            Dictionary mapping panel identifiers to their stored state
            when parsing succeeds and the result is a mapping, or an
            empty dictionary when the input is missing, malformed, or
            not a mapping.
    """
    if raw is None or not raw.strip():
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _apply_shell_panel_checkmarks(
    state: Mapping[str, object],
    view_actions: ViewMenuActions,
) -> None:
    """
    Apply persisted shell panel visibility to View menu checkmarks.

    This updates menu actions so their checked state mirrors the stored
    panel configuration.

    Args:
        state (Mapping[str, object]):
            Mapping of panel keys to truthy or falsy values indicating
            whether each panel should be considered visible.
        view_actions (ViewMenuActions):
            Dictionary of View menu actions keyed by panel identifiers,
            whose checkmarks will be updated to reflect the given state.
    """
    for key in _DEFAULT_SHELL_PANEL_KEYS:
        action: QAction | None = view_actions.get(key)
        if action is None:
            continue
        value: object | bool = state.get(key, True)
        action.setChecked(bool(value))
    interp: QAction | None = view_actions.get("interpreter")
    if interp is not None:
        interp.setChecked(bool(state.get("interpreter", False)))


def install_view_menu(
    *,
    window: QMainWindow,
    menu_bar: QMenuBar,
    view: QWebEngineView,
) -> ViewMenuActions:
    """
    Create and populate the View menu with panel toggle actions.

    This builds a set of checkable actions for core viewer panels and
    adds them to the main window's View menu, returning the created
    actions for further wiring and synchronization.

    Args:
        window (QMainWindow):
            Main application window that will own the created actions
            and menus.
        menu_bar (QMenuBar):
            Menu bar instance where the View menu will be added or
            extended with panel actions.
        view (QWebEngineView):
            Web view associated with the panels, provided for symmetry
            even though it is not directly used when installing the
            menu.

    Returns:
        ViewMenuActions:
            Mapping of panel keys to their corresponding QAction
            instances, each configured as a checkable menu item in the
            View menu.
    """
    view_menu: QMenu | None = menu_bar.addMenu("&View")
    if view_menu is None:
        raise RuntimeError("QMenuBar.addMenu returned None for View menu")
    view_actions: ViewMenuActions = {}
    for key, label in _VIEW_MENU_ENTRIES:
        action: QAction = QAction(label, window)
        action.setCheckable(True)
        action.setChecked(True)
        view_actions[key] = action
        view_menu.addAction(action)
    return view_actions


def connect_view_panel_actions(
    *,
    view: QWebEngineView,
    view_actions: ViewMenuActions,
) -> None:
    """
    Wire View menu panel actions to the embedded web view.

    This connects checkable menu actions to JavaScript hooks in the
    viewer so that toggling a menu item shows or hides the
    corresponding shell panel in the web content.

    Args:
        view (QWebEngineView):
            Web view hosting the markdown viewer page whose panels will
            be manipulated via JavaScript.
        view_actions (ViewMenuActions):
            Mapping of panel identifiers to their QAction instances that
            will be monitored for state changes and forwarded to the web
            view.
    """
    page: QWebEnginePage | None = view.page()
    if page is None:
        return

    def set_panel(key: str, checked: bool) -> None:
        """
        Set the visibility of a shell panel in the embedded web view.

        This JavaScript function is called when a View menu item is
        triggered to update the visibility of the corresponding shell
        panel in the web content.

        Args:
            key (str):
                Identifier of the panel to be toggled.
            checked (bool):
                True if the panel should be shown, False if it should be
                hidden.
        """
        script = (
            "window.mdViewerSetPanel && window.mdViewerSetPanel("
            f"{json.dumps(key)}, {json.dumps(checked)});"
        )
        page.runJavaScript(script)

    def wire(key: str) -> None:
        """
        Wire a View menu action to a shell panel in the embedded web
        view.

        This connects a checkable menu item to JavaScript hooks so that
        toggling the menu item updates the visibility of the
        corresponding shell panel in the web content.

        Args:
            key (str):
                Identifier of the panel to be wired.
        """
        action: QAction = view_actions[key]

        def on_triggered(_checked: bool) -> None:
            """
            Handle the triggered state change of a View menu action.

            This callback is invoked when the user checks or unchecks a
            View menu item, forwarding the new visibility state to the
            embedded web view via JavaScript.

            Args:
                _checked (bool):
                    True if the menu item is now checked, False if it is
                    unchecked.
            """
            set_panel(key=key, checked=action.isChecked())

        action.triggered.connect(on_triggered)

    for key in _DEFAULT_SHELL_PANEL_KEYS:
        wire(key)


def build_sync_menu_from_page(
    *,
    view: QWebEngineView,
    view_actions: ViewMenuActions,
    load_online_checked: Callable[[], bool],
    sync_external_panels: Callable[[], None],
) -> SyncMenuFromPageFn:
    """
    Build a function that synchronizes the View menu from the web page.

    This creates a callback that queries the embedded viewer for panel
    state and updates menu checkmarks and external panels accordingly.

    Args:
        view (QWebEngineView):
            Web view hosting the markdown viewer page whose panels will
            be manipulated via JavaScript.
        view_actions (ViewMenuActions):
            Mapping of panel identifiers to their QAction instances that
            will be monitored for state changes and forwarded to the
            web view.
        load_online_checked (Callable[[], bool]):
            Function that reports whether the "online" panel should
            appear checked, used to keep the online action in sync with
            external loading logic.
        sync_external_panels (Callable[[], None]):
            Function invoked after menu actions are updated so that any
            non-menu panel controls can mirror the new state.

    Returns:
        SyncMenuFromPageFn:
            Function that accepts a boolean load flag and, when invoked,
            fetches panel visibility from the page, updates View menu
            actions, and triggers external panel synchronization.
    """

    def sync_menu_from_page(_loaded_ok: bool) -> None:
        """
        Synchronize View menu checkmarks from the embedded web page.

        This callback queries the web viewer for current panel
        visibility, updates local menu items to match, and triggers
        external panel synchronization.

        Args:
            _loaded_ok (bool):
                Indicates whether the page load completed successfully,
                typically supplied by the web view's load-finished
                signal.
        """
        page: QWebEnginePage | None = view.page()
        if page is None:
            return

        def callback(result: str | None) -> None:
            """
            Callback to handle the result of querying panel state from
            the web page.

            This inner function processes the JSON string returned by
            the JavaScript query, converting it into a dictionary of
            panel visibility states, and then updates the local menu
            actions to reflect these states.

            Args:
                result (str | None):
                    The JSON string returned by the JavaScript query, or
                    None if the query failed.
            """
            state: Mapping[str, object] = _shell_panel_mapping_from_json(
                raw=result
            )
            _apply_shell_panel_checkmarks(state, view_actions)
            online: QAction | None = view_actions.get("online")
            if online is not None:
                online.setChecked(load_online_checked())

            sync_external_panels()

        page.runJavaScript(
            "window.mdViewerGetPanels && window.mdViewerGetPanels();",
            callback,
        )

    return sync_menu_from_page


def install_settings_menu(
    *,
    window: QMainWindow,
    menu_bar: QMenuBar,
    open_settings_ui: Callable[[], None],
) -> None:
    """
    Install the Settings menu and its entry in the main window.

    This helper adds a Settings menu with an action that opens the
    application's settings user interface when triggered.

    Args:
        window (QMainWindow):
            Main application window that will own the Settings menu and
            its actions.
        enu_bar (QMenuBar):
            Menu bar instance to which the Settings menu will be added
            or extended.
        open_settings_ui (Callable[[], None]):
            Function invoked when the Settings action is triggered,
            expected to display the settings user interface.
    """
    settings_menu: QMenu | None = menu_bar.addMenu("&Settings")
    if settings_menu is None:
        raise RuntimeError("QMenuBar.addMenu returned None for Settings menu")
    settings_action: QAction = QAction("Open settings (UI)", window)
    settings_action.setShortcut("Ctrl+,")
    settings_action.triggered.connect(open_settings_ui)
    settings_menu.addAction(settings_action)
