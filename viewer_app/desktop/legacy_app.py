"""
This module implements the desktop “Study MD Desk” application
that renders Markdown content with an embedded HTTP server, GUI, and
text-to-speech support.

It wires together a local web server, a PyQt6 shell, view and file
menus, an online side panel, and TTS orchestration, then runs the Qt
event loop as the main process.

It defines configuration and state helpers, including
DesktopAppSettings, global paths/constants, and functions to load and
update app config and JSON-backed state.

It contains several dataclasses (DesktopShell, TtsRuntimeState,
TtsOrchestratorActions, TtsControlBindings, TtsActionStateInputs) that
encapsulate Qt objects and TTS control/action bundles.

It provides helper functions to install the File and View menus,
settings menu, and to synchronize menu state and external panel URLs
with the current web page and settings.

It builds and attaches an “online panel” side UI using
build_online_panel, connecting it to the main view via a JavaScript
bridge and BridgeActions for chat prompts, TTS, and theme switching.

It sets up TTS runtime by configuring eSpeak and Piper controllers,
wiring them into a higher-level orchestrator and UI controls, and
streaming TTS chunks back into the view as text highlights.

It includes utility functions to run JavaScript safely in a
QWebEngineView, manage current document paths, extract and split text
for TTS, and read/write state and project metadata.

It starts an embedded HTTP server (start_local_server) for serving the
viewer content, choosing ports and failing fast with clear errors if
dependencies or ports are missing.

It configures the initial UI theme from settings or state, applies it to
the Qt app, and manages theme changes via a callback used by both
desktop menus and the online panel.

It registers a cleanup callback that stops TTS engines, shuts down the
server, and tears down the Python runner executor when the Qt
application is about to quit.

Its main() function coordinates dependency checks, server startup, shell
creation, menu and runtime installation, TTS and panel wiring, and
finally shows the main window and blocks in app.exec().
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress

import configparser
import queue
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenuBar, QStatusBar
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Protocol,
    cast,
)

from viewer_app.app.context import AppContext, get_app_context
from viewer_app.core.tts_text import TtsTextPipeline
from viewer_app.desktop.desktop_actions import install_global_actions
from viewer_app.desktop.desktop_bridge import BridgeActions
from viewer_app.desktop.desktop_file_menu import install_file_menu
from viewer_app.desktop.desktop_lifecycle import (
    install_about_to_quit_cleanup,
    install_main_view_runtime,
    install_tts_highlight_timer,
)
from viewer_app.desktop.desktop_online_panel import (
    OnlinePanelDependencies,
    OnlinePanelRefs,
    build_online_panel,
    focus_chat_and_copy_prompt,
    sync_external_panel_urls,
)
from viewer_app.desktop.desktop_runtime import (
    DEFAULT_CHAT_URL,
    DEFAULT_SANDBOX_URL,
    DEFAULT_TRANSLATE_URL,
    ExternalUrls,
    apply_tts_ini_settings,
    configure_web_profile,
    load_external_urls,
    read_app_setting,
)
from viewer_app.desktop.desktop_theme import apply_qt_theme
from viewer_app.desktop.desktop_tts_controllers import (
    OfflineTtsController as DesktopOfflineTtsController,
)
from viewer_app.desktop.desktop_tts_controllers import PiperTtsController
from viewer_app.desktop.desktop_tts_controllers import (
    PiperTtsController as DesktopPiperTtsController,
)
from viewer_app.desktop.desktop_tts_controls import (
    adjust_tts_speed,
    get_sentence_silence,
    get_tts_speed,
    set_piper_voice,
    set_sentence_silence,
    set_tts_speed,
)
from viewer_app.desktop.desktop_tts_orchestrator import (
    TtsActionDependencies,
    build_tts_actions,
)
from viewer_app.desktop.desktop_tts_state import (
    clear_tts_cursor,
    current_doc_id,
    load_tts_cursor_for_current_doc,
    read_current_md_text,
    save_tts_cursor,
)
from viewer_app.desktop.desktop_view_menu import (
    build_sync_menu_from_page,
    connect_view_panel_actions,
    install_settings_menu,
    install_view_menu,
)
from viewer_app.desktop.desktop_web_helpers import (
    build_tts_sync_script,
    extract_tts_sync_text,
    resolve_online_panel_visible,
)
from viewer_app.runtime.config import (
    AppConfig,
)
from viewer_app.runtime.config import (
    update_app_config_key as _config_update_app_config_key,
)
from viewer_app.runtime.paths import AppPaths
from viewer_app.runtime.projects import ProjectsService
from viewer_app.runtime.python_runner import shutdown_runner_executor
from viewer_app.runtime.server_runtime import RunningServer, start_local_server
from viewer_app.runtime.state import StateDict, StateStore

if TYPE_CHECKING:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWebEngineCore import QWebEngineProfile
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication, QMainWindow, QMenuBar

type ShowStatusFn = Callable[[str, int], None]


class _ShutdownCapable(Protocol):
    """
    Protocol for objects that support a cooperative shutdown operation.

    This defines a minimal interface used to signal background services
    or servers to terminate cleanly.

    # Methods:

        shutdown():
            Request that the implementing object perform any necessary
            cleanup and stop its ongoing work.
    """

    def shutdown(self) -> None: ...


def _warn_if_default_plans_dir_missing() -> None:
    """
    Warn to stderr when the default plans directory is not available.

    This helps users discover that they must open or configure a project
    before content can be loaded.

    Returns:
        None:
            This function does not return a value. It only writes
            diagnostic warnings to the standard error stream when the
            directory is missing.
    """
    if not _APP_SETTINGS.plans_dir.exists():
        _stderr_message(f"Default folder not found: {_APP_SETTINGS.plans_dir}")
        _stderr_message("Use File -> Open project...")


def _install_main_file_menu(
    *,
    window: QMainWindow,
    menu_bar: QMenuBar,
    view: QWebEngineView,
    base_url: str,
    current_path: list[str],
) -> None:
    """
    Install the main File menu for the desktop application window.

    This wires file-related actions into the menu bar using the shared
    file menu helper.

    Args:
        window (QMainWindow):
            Main application window that will own the installed File
            menu and receive triggered actions.
        menu_bar (QMenuBar):
            Menu bar instance to which the File menu and its actions
            should be added.
        view (QWebEngineView):
            Web view displaying the current document, used by file
            actions such as reload or navigation helpers.
        base_url (str):
            Base HTTP URL pointing at the local server root that backs
            the viewer content.
        current_path (list[str]):
            Mutable list representing the current logical path within
            the plans directory, updated by file navigation actions.
    """
    install_file_menu(
        window=window,
        menu_bar=menu_bar,
        view=view,
        base_url=base_url,
        current_path=current_path,
        load_state_json=_load_state_json,
        get_projects_state=_get_projects_state,  # pyright: ignore[reportArgumentType]
        set_active_project=_set_active_project,
        touch_project_recent=_touch_project_recent,
        save_course_parts=_save_course_parts,
        toggle_pin_project=_toggle_pin_project,
    )


def _install_main_view_menu_and_connect(
    *,
    window: QMainWindow,
    menu_bar: QMenuBar,
    view: QWebEngineView,
) -> dict[str, QAction]:
    """
    Install the main View menu and connect its actions to the panel UI.

    This wires view-related menu items into the web view so layout and
    panel visibility can be controlled from the menu bar.

    Args:
        window (QMainWindow):
            Main application window that will own the View menu and
            receive triggered actions.
        menu_bar (QMenuBar):
            Menu bar instance to which the View menu and its actions
            should be added.
        view (QWebEngineView):
            Web view displaying the current document whose panels and
            layout are controlled by the View menu actions.

    Returns:
        dict[str, QAction]:
            Mapping of action identifiers to the created QAction
            instances, allowing further customization or reuse elsewhere
            in the code.
    """
    view_acts: dict[str, QAction] = install_view_menu(
        window=window, menu_bar=menu_bar, view=view
    )
    return (
        connect_view_panel_actions(view=view, view_actions=view_acts)
        or view_acts
    )


def _sync_external_panels_factory(
    online_panel_ref: list[OnlinePanelRefs | None],
) -> Callable[[], None]:
    """
    Create a callable that synchronizes external panel URLs from
    settings.

    This factory captures a shared reference to the online panel so the
    returned function can refresh its URLs whenever invoked.

    Args:
        online_panel_ref (list[OnlinePanelRefs | None]):
            Single-element list holding the current online panel
            references, or None when no panel is attached, used to
            access and update the panel in a mutable way.

    Returns:
        Callable[[], None]:
            Zero-argument callable that, when called, reads the
            configured chat, sandbox, and translate URLs and applies
            them to the active online panel if present.
    """

    def sync_external_panels() -> None:
        """
        Create a callable that synchronizes external panel URLs from
        settings.

        This factory captures a shared reference to the online panel so
        the returned function can refresh its URLs whenever invoked.

        Args:
            online_panel_ref (list[OnlinePanelRefs | None]):
                Single-element list holding the current online panel
                references, or None when no panel is attached, used to
                access and update the panel in a mutable way.

        Returns:
            Callable[[], None]:
                Zero-argument callable that, when called, reads the
                configured chat, sandbox, and translate URLs and
                applies them to the active online panel if present.
        """
        online_panel: OnlinePanelRefs | None = online_panel_ref[0]
        if online_panel is not None:
            sync_external_panel_urls(
                refs=online_panel,
                chat_url=read_app_setting(
                    settings_path=SETTINGS_PATH,
                    key="chatUrl",
                    default=DEFAULT_CHAT_URL,
                ),
                sandbox_url=read_app_setting(
                    settings_path=SETTINGS_PATH,
                    key="sandboxUrl",
                    default=DEFAULT_SANDBOX_URL,
                ),
                translate_url=read_app_setting(
                    settings_path=SETTINGS_PATH,
                    key="translateUrl",
                    default=DEFAULT_TRANSLATE_URL,
                ),
            )

    return sync_external_panels


def _main_sync_menu_from_page_factory(
    *,
    view: QWebEngineView,
    view_acts: dict[str, QAction],
    sync_external_panels: Callable[[], None],
) -> Callable[..., None]:
    """
    Create a factory for synchronizing the View menu with the page
    state.

    This returns a callable that updates menu checkmarks and external
    panel URLs based on the current viewer state.

    Args:
        view (QWebEngineView):
            Web view whose current page and JavaScript API are used to
            derive panel visibility and layout information.
        view_acts (dict[str, QAction]):
            Mapping from logical action identifiers to the corresponding
            View menu QAction instances that should be synchronized.
        sync_external_panels (Callable[[], None]):
            Callback that refreshes external panel URLs, ensuring the
            online panel reflects the latest configuration.

    Returns:
        Callable[..., None]:
            Callable that, when invoked, reads the latest state from the
            web view and persisted storage and applies it to the View
            menu and external panels.
    """
    return build_sync_menu_from_page(
        view=view,
        view_actions=view_acts,
        load_online_checked=lambda: resolve_online_panel_visible(
            state=_load_state_json()
        ),
        sync_external_panels=sync_external_panels,
    )


def _main_open_app_settings_ui(view: QWebEngineView) -> Callable[[], None]:
    """
    Create a callable that opens the in-page application settings UI.

    This factory wraps a JavaScript invocation so settings can be shown
    from non-web code.

    Args:
        view (QWebEngineView):
            Web view hosting the Markdown viewer page that exposes the
            settings UI JavaScript API.

    Returns:
        Callable[[], None]:
            Zero-argument callable that, when invoked, triggers the
            JavaScript hook to display the application settings
            interface inside the web view.
    """

    def open_settings_ui() -> None:
        """
        Open the in-page application settings interface.

        This invokes the viewer's JavaScript hook to display settings
        inside the current web view.

        Returns:
            None:
                This function does not return a value. It performs a
                side effect by executing JavaScript in the web view to
                show the settings UI.
        """
        _run_javascript_on_view(
            view,
            js="window.mdViewerOpenAppSettings && window.mdViewerOpenAppSettings();",
        )

    return open_settings_ui


def _main_run_view_js_factory(view: QWebEngineView) -> Callable[[str], None]:
    """
    Create a factory that runs JavaScript in the main document view.

    This wraps the low-level JavaScript execution helper so callers only
    need to supply JS source code.

    Args:
        view (QWebEngineView):
            Web view whose page context will be used to execute
            JavaScript snippets.

    Returns:
        Callable[[str], None]:
            Callable that takes a JavaScript source string and evaluates
            it in the context of the current page, ignoring errors if
            the view is no longer valid.
    """

    def run_view_js(js: str) -> None:
        """
        Execute a JavaScript snippet in the main document view.

        This delegates to the shared helper to run code inside the page
        context associated with the primary web view.

        Args:
            js (str):
                JavaScript source code string to evaluate in the context
                of the current document view.
        """
        _run_javascript_on_view(view, js)

    return run_view_js


def _main_bridge_send_prompt_factory(
    online_panel_ref: list[OnlinePanelRefs | None],
) -> Callable[[str], None]:
    """
    Create a factory for sending prompts into the online chat panel.

    This returns a callable that focuses the chat view and inserts the
    given prompt when a panel is attached.

    Args:
        online_panel_ref (list[OnlinePanelRefs | None]):
            Single-element list holding the current online panel
            references, or None when no panel is attached, used to
            access the chat view in a mutable way.

    Returns:
        Callable[[str], None]:
            Callable that accepts a prompt string and, when invoked,
            focuses the chat view and copies the prompt into the online
            panel if present.
    """

    def bridge_send_prompt(prompt: str) -> None:
        panel: OnlinePanelRefs | None = online_panel_ref[0]
        if panel is not None:
            focus_chat_and_copy_prompt(panel.chat_view, prompt)

    return bridge_send_prompt


def _build_main_bridge_actions(
    *,
    send_prompt_to_chat: Callable[[str], None],
    tts_runtime: TtsRuntimeState,
    apply_qt_theme: Callable[[str], None],
) -> BridgeActions:
    """
    Build the main bridge action bundle for the online panel.

    This connects chat prompting, text-to-speech controls, and theme
    switching so the web-based UI can drive desktop features.

    Args:
        send_prompt_to_chat (Callable[[str], None]):
            Callable that sends a prompt string into the active online
            chat panel, focusing the chat view when invoked.
        tts_runtime (TtsRuntimeState):
            Text-to-speech runtime state providing orchestrator actions
            and control bindings used to speak text, manage playback,
            and adjust settings.
        apply_qt_theme (Callable[[str], None]):
            Callable that applies a named Qt theme mode to the
            application, such as switching between light and dark
            appearances.

    Returns:
        BridgeActions:
            Bridge action dataclass exposing chat, TTS, and theme
            callbacks that can be invoked from the JavaScript bridge
            layer.
    """
    return BridgeActions(
        send_prompt_to_chat=send_prompt_to_chat,
        tts_speak_text=tts_runtime.orchestrator.tts_speak_text,
        tts_set_piper_voice=tts_runtime.controls.tts_set_piper_voice,
        tts_speak_current_doc=tts_runtime.orchestrator.tts_read_document,
        tts_toggle_pause=tts_runtime.orchestrator.pause_toggle_and_persist,
        tts_stop=tts_runtime.orchestrator.stop_and_clear,
        tts_get_speed=tts_runtime.controls.tts_get_speed,
        tts_adjust_speed=tts_runtime.controls.tts_adjust_speed,
        tts_set_speed=tts_runtime.controls.tts_set_speed,
        tts_get_sentence_silence=tts_runtime.controls.tts_get_sentence_silence,
        tts_set_sentence_silence=tts_runtime.controls.tts_set_sentence_silence,
        apply_qt_theme=apply_qt_theme,
    )


def _attach_online_panel(
    *,
    window: QMainWindow,
    menu_bar: QMenuBar,
    view: QWebEngineView,
    profile: QWebEngineProfile,
    external_urls: ExternalUrls,
    view_acts: dict[str, QAction],
    online_panel_ref: list[OnlinePanelRefs | None],
    tts_runtime: TtsRuntimeState,
    apply_qt_theme: Callable[[str], None],
) -> None:
    """
    Attach the online panel UI to the main desktop window.

    This wires the web-based side panel into the viewer, connecting
    state persistence, bridge actions, and initial theme configuration.

    Args:
        window (QMainWindow):
            Main application window that will host the online panel
            alongside the primary document view.
        menu_bar (QMenuBar):
            Menu bar instance used to install and manage actions
            associated with the online panel.
        view (QWebEngineView):
            Main document web view that shares layout and JavaScript
            context with the online panel.
        profile (QWebEngineProfile):
            Web engine profile providing cache, storage, and network
            configuration for the online panel pages.
        external_urls (ExternalUrls):
            Container of external service URLs used to initialize panel
            destinations such as chat, sandbox, and translation views.
        view_acts (dict[str, QAction]):
            Mapping of view-related actions whose state may be
            synchronized with the presence and visibility of the online
            panel.
        online_panel_ref (list[OnlinePanelRefs | None]):
            Single-element list used as a mutable reference to the
            current online panel instance, updated to point at the
            newly attached panel.
        tts_runtime (TtsRuntimeState):
            Text-to-speech runtime state whose actions and controls are
            exposed to the online panel via the bridge.
        apply_qt_theme (Callable[[str], None]):
            Callable that applies a named Qt theme mode, allowing the
            online panel to request theme changes.
    """
    run_view_js: Callable[[str], None] = _main_run_view_js_factory(view)
    bridge_actions: BridgeActions = _build_main_bridge_actions(
        send_prompt_to_chat=_main_bridge_send_prompt_factory(online_panel_ref),
        tts_runtime=tts_runtime,
        apply_qt_theme=apply_qt_theme,
    )
    online_panel_ref[0] = build_online_panel(
        deps=OnlinePanelDependencies(
            window=window,
            menu_bar=menu_bar,
            view=view,
            profile=profile,
            external_urls=external_urls,
            view_acts=view_acts,
            load_state_json=_load_state_json_any,
            update_state_nested_dict=_update_state_nested_dict,
            run_view_js=run_view_js,
            initial_qt_theme=_resolve_initial_theme(),
            bridge_actions=bridge_actions,
        )
    )


def _main_desktop_cleanup_factory(
    *,
    tts_runtime: TtsRuntimeState,
    server: _ShutdownCapable,
) -> Callable[[], None]:
    """
    Create a cleanup callback for the desktop runtime.

    This bundles TTS shutdown, server termination, and worker executor
    teardown into a single callable suitable for application exit
    hooks.

    Args:
        tts_runtime (TtsRuntimeState):
            Text-to-speech runtime state whose eSpeak and Piper
            controllers should be stopped during cleanup.
        server (_ShutdownCapable):
            Server-like object that exposes a shutdown() method used to
            stop the embedded HTTP runtime.

    Returns:
        Callable[[], None]:
            Zero-argument callable that, when invoked, performs
            best-effort TTS shutdown, stops the server, and tears down
            the Python runner executor.
    """

    def cleanup() -> None:
        with suppress(OSError, RuntimeError):
            tts_runtime.tts_espeak.stop()
            tts_runtime.tts_piper.stop()
        server.shutdown()
        shutdown_runner_executor()

    return cleanup


@dataclass(frozen=True)
class TtsOrchestratorActions:
    """
    TtsOrchestratorActions groups high-level text-to-speech operations.

    It provides callable hooks for speaking text, reading documents, and
    controlling playback from the UI or bridge layer.

    Attributes:
        tts_speak_text (Callable[[str], None]):
            Function that speaks an arbitrary text string via the active
            TTS engine.
        tts_read_document (Callable[[], None]):
            Function that starts reading the entire current document
            from the beginning or a saved cursor.
        tts_read_selection_or_document (Callable[[], None]):
            Function that reads the current text selection, falling back
            to the full document when no selection exists.
        pause_toggle_and_persist (Callable[[], None]):
            Function that toggles between paused and playing states and
            persists the updated cursor position.
        stop_and_clear (Callable[[], None]):
            Function that stops any ongoing TTS playback and clears
            stored cursor or queue state.
        ask_current_selection_in_chat (Callable[[], None]):
            Function that sends the current text selection to an
            external chat or assistant panel for further interaction.
    """

    tts_speak_text: Callable[[str], None]
    tts_read_document: Callable[[], None]
    tts_read_selection_or_document: Callable[[], None]
    pause_toggle_and_persist: Callable[[], None]
    stop_and_clear: Callable[[], None]
    ask_current_selection_in_chat: Callable[[], None]


@dataclass(frozen=True)
class TtsControlBindings:
    """
    TtsControlBindings collects UI bindings for text-to-speech controls.

    It exposes callbacks for selecting voices, tuning playback, and
    synchronizing highlights with the document view.

    Attributes:
        tts_set_piper_voice (Callable[[str], None]):
            Function that sets the active Piper voice by its identifier
            and updates related configuration.
        tts_get_speed (Callable[[], str]):
            Function that returns a human-readable representation of the
            current TTS playback speed.
        tts_adjust_speed (Callable[[float], str]):
            Function that adjusts the current TTS speed by a delta and
            returns the resulting speed as a human-readable string.
        tts_set_speed (Callable[[float], None]):
            Function that sets the absolute TTS playback speed to a
            specific value.
        tts_get_sentence_silence (Callable[[], str]):
            Function that returns the current sentence pause duration as
            a human-readable string.
        tts_set_sentence_silence (Callable[[float], None]):
            Function that sets the pause duration inserted between
            sentences during TTS playback.
        dispatch_tts_highlight (Callable[[], None]):
            Function that triggers synchronization between the latest
            TTS playback position and the on-screen text highlight.
    """

    tts_set_piper_voice: Callable[[str], None]
    tts_get_speed: Callable[[], str]
    tts_adjust_speed: Callable[[float], str]
    tts_set_speed: Callable[[float], None]
    tts_get_sentence_silence: Callable[[], str]
    tts_set_sentence_silence: Callable[[float], None]
    dispatch_tts_highlight: Callable[[], None]


def _tts_actions_from_factory(raw: dict[str, Any]) -> TtsOrchestratorActions:
    """
    Create a TtsOrchestratorActions instance from a raw action mapping.

    This safely casts untyped factory outputs into strongly typed
    orchestrator callbacks.

    Args:
        raw (dict[str, Any]):
            Dictionary produced by the TTS action factory, expected to
            contain callables under well-known keys such as
            "tts_speak_text" and "pause_toggle_and_persist".

    Returns:
        TtsOrchestratorActions:
            Dataclass bundling all high-level text-to-speech
            orchestration actions as typed callables for use by the
            desktop runtime.
    """
    return TtsOrchestratorActions(
        tts_speak_text=cast(Callable[[str], None], raw["tts_speak_text"]),
        tts_read_document=cast(Callable[[], None], raw["tts_read_document"]),
        tts_read_selection_or_document=cast(
            Callable[[], None], raw["tts_read_selection_or_document"]
        ),
        pause_toggle_and_persist=cast(
            Callable[[], None], raw["pause_toggle_and_persist"]
        ),
        stop_and_clear=cast(Callable[[], None], raw["stop_and_clear"]),
        ask_current_selection_in_chat=cast(
            Callable[[], None], raw["ask_current_selection_in_chat"]
        ),
    )


def _run_javascript_on_view(view: QWebEngineView, js: str) -> None:
    """
    Execute a JavaScript snippet in the given web view if possible.

    This safely ignores errors that can occur when the underlying page
    is no longer valid.

    Args:
        view (QWebEngineView):
            Web view whose associated page should run the provided
            JavaScript code.
        js (str):
            JavaScript source code to evaluate within the context of the
            current page.
    """
    page: QWebEnginePage | None = view.page()
    if page is None:
        return
    with suppress(RuntimeError):
        page.runJavaScript(js)


APP_CONTEXT: AppContext = get_app_context()
APP_PATHS: AppPaths = APP_CONTEXT.paths
REPO_ROOT: Final[Path] = APP_PATHS.app_root
DEFAULT_DIR: Final[Path] = APP_PATHS.plans_dir
SETTINGS_PATH: Final[Path] = APP_PATHS.settings_path
TTS_RULES_PATH: Final[Path] = APP_PATHS.tts_rules_path
_STATE_STORE: StateStore = APP_CONTEXT.state
_PROJECTS_SERVICE: ProjectsService = APP_CONTEXT.projects
_TTS_TEXT_PIPELINE: TtsTextPipeline = TtsTextPipeline(
    rules_path=TTS_RULES_PATH
)


@dataclass
class DesktopAppSettings:
    """
    DesktopAppSettings stores user-facing metadata for the desktop app.

    It holds the active plans directory and text used in the window
    title and subtitle.

    Attributes:
        plans_dir (Path):
            Filesystem path to the root directory containing study plans
            and Markdown materials shown in the app.
        app_title (str):
            Title text displayed in the main window caption bar.
        app_subtitle (str):
            Descriptive subtitle used in UI elements to summarize the
            app's purpose.
    """

    plans_dir: Path
    app_title: str = "Study MD Desk"
    app_subtitle: str = (
        "Comfortable reading and learning from Markdown materials"
    )


_APP_SETTINGS: DesktopAppSettings = DesktopAppSettings(
    plans_dir=DEFAULT_DIR if DEFAULT_DIR.exists() else REPO_ROOT,
)


def _load_app_config() -> None:
    """
    Reload the desktop application configuration into in-memory
    settings.

    This keeps the global DesktopAppSettings in sync with the persisted
    AppConfig.
    """
    cfg: AppConfig = APP_CONTEXT.reload_config()
    _APP_SETTINGS.plans_dir = cfg.plans_dir
    _APP_SETTINGS.app_title = cfg.app_title
    _APP_SETTINGS.app_subtitle = cfg.app_subtitle


def _update_app_config_key(key: str, value: str) -> None:
    """
    Update a single key in the persisted app configuration.

    This also refreshes in-memory desktop settings to reflect the
    change.

    Args:
        key (str):
            Configuration key name to update, such as "plansDir" or
            "appTitle".
        value (str):
            New string value to write for the given configuration key.
    """
    _config_update_app_config_key(paths=APP_PATHS, key=key, value=value)
    _load_app_config()


_load_app_config()


if "QT_QPA_PLATFORM" not in os.environ and os.environ.get("WAYLAND_DISPLAY"):
    os.environ["QT_QPA_PLATFORM"] = "wayland"


def _stderr_message(message: str) -> None:
    """
    Write a message line to standard error for diagnostic output.

    This provides a lightweight helper for reporting non-fatal runtime
    issues.

    Args:
        message (str):
            Text message to write to the process stderr stream, without
            a trailing newline.
    """
    sys.stderr.write(f"{message}\n")


def _ensure_runtime_dependencies() -> None:
    """
    Verify that required runtime Python dependencies are installed.

    This ensures the desktop application fails fast with a clear message
    when core libraries are missing.

    Raises:
        SystemExit:
            Raised with an installation hint if the markdown (and
            related syntax highlighting) package cannot be imported at
            runtime.
    """
    try:
        __import__(name="markdown")
    except ImportError as exc:
        raise SystemExit("pip install markdown pygments") from exc


def _import_qt_bindings() -> tuple[
    type[QApplication],
    type[QMainWindow],
    type[QMenuBar],
    type[QTimer],
    type[QWebEngineView],
    type[QWebEngineProfile],
]:
    """
    Import and validate the required Qt binding classes at runtime.

    This centralizes error handling so missing GUI dependencies fail
    fast with a clear installation hint.

    Returns:
        (
            type[QApplication],
            type[QMainWindow],
            type[QMenuBar],
            type[QTimer],
            type[QWebEngineView],
            type[QWebEngineProfile]
        ):
            Tuple containing the Qt application, main window, menu bar,
            timer, web engine view, and web engine profile classes used
            by the desktop shell.

    Raises:
        SystemExit:
            Raised with a pip installation command if the Qt bindings
            cannot be imported.
    """
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWidgets import QApplication, QMainWindow, QMenuBar
    except ImportError as exc:
        raise SystemExit(
            "pip install PyQt6 PyQt6-WebEngine PyQt6-QtWebChannel"
        ) from exc
    return (
        QApplication,
        QMainWindow,
        QMenuBar,
        QTimer,
        QWebEngineView,
        QWebEngineProfile,
    )


def _start_http_runtime() -> RunningServer:
    """
    Start the embedded HTTP runtime used by the desktop shell.

    This launches a threaded local server hosting the viewer HTTP
    handler.

    Returns:
        RunningServer:
            Wrapper containing the running server instance and its base
            URL for use by the desktop application.

    Raises:
        SystemExit:
            Raised if the HTTP handler modules cannot be imported or if
            all ports in the 8765-8774 range are unavailable.
    """
    try:
        from viewer_app.http.http_handler import Handler as HttpHandler
        from viewer_app.http.http_server import ThreadedServer
    except ImportError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        return start_local_server(
            server_cls=ThreadedServer, handler_cls=HttpHandler
        )
    except OSError as exc:
        raise SystemExit(
            "Failed to start local server: ports 8765-8774 are busy"
        ) from exc


def _save_state_to_json(payload: Mapping[str, object]) -> None:
    """
    Persist a partial state update to the JSON-backed store.

    This safely ignores filesystem errors while applying the patch.

    Args:
        payload (Mapping[str, object]):
            Mapping of top-level state keys to updated values to be
            merged into the persisted application state.
    """
    with suppress(OSError):
        _STATE_STORE.update(patch=dict[str, object](payload))


def _update_state_nested_dict(key: str, updates: Mapping[str, object]) -> None:
    """
    Merge nested dictionary updates into a top-level state key.

    This reads the current JSON state, overlays the updates, and saves
    the combined result.

    Args:
        key (str):
            Top-level state key whose value should be treated as a
            nested dictionary to merge into.
        updates (Mapping[str, object]):
            Mapping of nested keys and values to overlay on top of the
            existing dictionary stored under the given state key.
    """
    st: StateDict = _load_state_json()
    raw_base: object = st.get(key)
    base: dict[str, object] = raw_base if isinstance(raw_base, dict) else {}
    merged: dict[str, object] = {**base, **dict[str, object](updates)}
    _save_state_to_json(payload={key: merged})


def _get_projects_state(
    st: StateDict | None = None,
) -> dict[str, list[object]]:
    """
    Extract the pinned and recent projects lists from persisted state.

    This normalizes the projects section into list-based collections for
    menu and UI rendering.

    Args:
        st (StateDict | None):
            Optional full application state mapping; when omitted or
            invalid, the state is loaded from the JSON-backed store.

    Returns:
        dict[str, list[object]]:
            Dictionary with "pinned" and "recent" keys, each mapped to a
            list of project descriptor objects, defaulting to empty
            lists when missing or malformed.
    """
    state: StateDict = st if isinstance(st, dict) else _load_state_json()
    projects_raw: object = state.get("projects")
    projects: dict[str, object] = (
        projects_raw if isinstance(projects_raw, dict) else {}
    )
    pinned_raw: object = projects.get("pinned")
    recent_raw: object = projects.get("recent")
    pinned: list[object] = pinned_raw if isinstance(pinned_raw, list) else []
    recent: list[object] = recent_raw if isinstance(recent_raw, list) else []
    return {"pinned": pinned, "recent": recent}


def _set_active_project(root: str) -> None:
    """
    Set the given project root as the active project in the runtime.

    This updates global project state so subsequent operations target
    this project.

    Args:
        root (str):
            Filesystem path or identifier of the project to mark as
            active in the projects service.
    """
    _PROJECTS_SERVICE.set_active(root)


def _touch_project_recent(
    root: str, name: str | None = None, limit: int = 18
) -> None:
    """
    Record the given project as recently used in the projects service.

    This updates the recent projects list, optionally naming the entry
    and enforcing a maximum list size.

    Args:
        root (str):
            Filesystem path or identifier of the project that should be
            marked as recently opened.
        name (str | None):
            Optional human-readable project name to associate with the
            recent entry when available.
        limit (int):
            Maximum number of recent projects to retain after inserting
            this entry.
    """
    _PROJECTS_SERVICE.touch_recent(root, name=name, limit=limit)


def _toggle_pin_project(root: str) -> bool:
    """
    Toggle the pinned state of the given project in the projects
    service.

    This flips whether the project appears in the pinned projects list.

    Args:
        root (str):
            Filesystem path or identifier of the project whose pinned
            status should be toggled.

    Returns:
        bool:
            True if the project is pinned after the toggle operation, or
            False if it is unpinned.
    """
    return _PROJECTS_SERVICE.toggle_pin(root)


def _save_course_parts(root: str) -> None:
    """
    Persist the current course parts index for the given project.

    This updates stored metadata so future sessions can reuse the
    computed course structure.

    Args:
        root (str):
            Filesystem path or identifier of the project whose course
            parts metadata should be saved.
    """
    _PROJECTS_SERVICE.save_course_parts(root)


def _load_state_json() -> StateDict:
    """
    Load the current application state from the persistent store.

    This provides a snapshot of all saved settings and runtime metadata.

    Returns:
        StateDict:
            Dictionary representing the full persisted application state
            as loaded from the JSON-backed StateStore.
    """
    return _STATE_STORE.load()


def _load_state_json_any() -> dict[str, Any]:
    """
    Load the current application state as a loosely typed mapping.

    This is convenient when callers need a generic dictionary without
    StateDict type constraints.

    Returns:
        dict[str, Any]:
            Dictionary containing the full persisted application state,
            with arbitrary value types as stored in the underlying
            StateStore.
    """
    return cast(dict[str, Any], _load_state_json())


def _get_current_doc_fs_path() -> tuple[Path | None, dict[str, object]]:
    """
    Resolve the filesystem path of the currently open Markdown document.

    This validates the stored currentDoc state and returns both the path
    and its metadata when available.

    Returns:
        tuple[Path | None, dict[str, object]]:
            Tuple containing the absolute filesystem Path of the current
            document when it exists and is a .md file, or None if the
            path is missing or invalid, along with the raw currentDoc
            metadata dictionary in all cases.
    """
    st: StateDict = _load_state_json()
    cd_raw: object = st.get("currentDoc")
    cd: dict[str, object] = cd_raw if isinstance(cd_raw, dict) else {}
    rel_path: str = str(cd.get("path") or "").strip()
    root: str = str(cd.get("root") or "").strip()
    if not rel_path:
        return None, cd
    view_root: Path = (
        Path(root).expanduser().resolve() if root else _APP_SETTINGS.plans_dir
    )
    try:
        fs_path: Path = (view_root / rel_path).resolve()
        fs_path.relative_to(view_root)
    except (OSError, ValueError):
        return None, cd
    if not fs_path.exists() or fs_path.suffix.lower() != ".md":
        return None, cd
    return fs_path, cd


def _extract_tts_text_from_markdown(md_text: str) -> str:
    """
    Extract human-friendly speech text from raw Markdown content.

    This strips markup and applies TTS-specific cleanup rules.

    Args:
        md_text (str):
            Markdown source text from which to derive a plain-text
            string suitable for text-to-speech playback.

    Returns:
        str:
            Cleaned plain-text representation of the input Markdown,
            optimized for use by the TTS engine.
    """
    return _TTS_TEXT_PIPELINE.extract_text_from_markdown(md_text)


def _split_for_tts(text: str) -> list[str]:
    """
    Split prepared text into segments suitable for TTS playback.

    This uses the configured TTS rules to break the input into speakable
    chunks.

    Args:
        text (str):
            Full plain-text string that should be divided into smaller
            segments for sequential text-to-speech processing.

    Returns:
        list[str]:
            List of text fragments in playback order, each representing
            a sentence or logical speech unit.
    """
    return _TTS_TEXT_PIPELINE.split_for_tts(text)


def _resolve_initial_theme() -> str:
    """
    Resolve the initial UI theme to apply when the desktop app starts.

    This prefers explicit configuration values and falls back to a
    default light theme when none are valid.

    Returns:
        str:
            Theme name string, one of "white", "sepia" or "dark", chosen
            from the INI settings file or persisted reader preferences,
            defaulting to "white".
    """
    parser: ConfigParser = configparser.ConfigParser()
    if SETTINGS_PATH.exists():
        with suppress(configparser.Error, OSError):
            parser.read(filenames=SETTINGS_PATH, encoding="utf-8")
    if parser.has_section(section="app"):
        theme_name = str(parser["app"].get("theme", "") or "").strip().lower()
        if theme_name in {"white", "sepia", "dark"}:
            return theme_name
    state: StateDict = _load_state_json()
    reader_prefs_raw: object = state.get("readerPrefs")
    if isinstance(reader_prefs_raw, dict):
        theme_name: str = str(reader_prefs_raw.get("theme") or "").lower()
        if theme_name in {"white", "sepia", "dark"}:
            return theme_name
    return "white"


@dataclass
class DesktopShell:
    """
    DesktopShell aggregates the core Qt objects for the desktop viewer.

    It provides a convenient container for the running application,
    window, web engine, navigation context, and theme integration.

    Attributes:
        app (QApplication):
            Qt application instance that owns the main event loop and
            GUI lifecycle.
        window (QMainWindow):
            Top-level main window that hosts the reader view and menus.
        profile (QWebEngineProfile):
            Web engine profile that configures caching, storage, and
            handler settings for the embedded browser.
        view (QWebEngineView):
            Web view widget used to render the Markdown-based reader UI.
        current_path (list[str]):
            Stack-like list representing the current project or document
            root path used for navigation.
        external_urls (ExternalUrls):
            Collection of external service URLs used by the embedded
            online panels and integrations.
        apply_qt_theme (Callable[[str], None]):
            Callback that applies a named Qt theme mode to the
            application, such as "white", "sepia", or "dark".
    """

    app: QApplication
    window: QMainWindow
    profile: QWebEngineProfile
    view: QWebEngineView
    current_path: list[str]
    external_urls: ExternalUrls
    apply_qt_theme: Callable[[str], None]


def _create_desktop_shell(
    *,
    qt_application_cls: type[QApplication],
    qt_main_window_cls: type[QMainWindow],
    qt_web_profile_cls: type[QWebEngineProfile],
    qt_web_engine_view_cls: type[QWebEngineView],
) -> DesktopShell:
    """
    Create and initialize the main desktop shell for the application.

    This constructs the Qt application, main window, web profile, and
    web view, and wires them together into a single DesktopShell
    container.

    Args:
        qt_application_cls (type[QApplication]):
            Concrete Qt application class used to create the GUI
            application instance.
        qt_main_window_cls (type[QMainWindow]):
            Concrete main window class used to construct the top-level
            application window.
        qt_web_profile_cls (type[QWebEngineProfile]):
            Concrete web engine profile class used to configure browser
            settings, caching, and URL handling.
        qt_web_engine_view_cls (type[QWebEngineView]):
            Concrete web engine view class responsible for rendering the
            HTML-based reader interface inside the main window.

    Returns:
        DesktopShell:
            Aggregated structure containing the initialized Qt
            application, main window, web engine profile, web view,
            current path stack, external URL configuration, and theme
            application callback.
    """
    app: QApplication = qt_application_cls(sys.argv)
    app.setStyle("Fusion")

    def apply_theme(mode: str) -> None:
        """
        Apply the selected theme mode to the Qt application.

        This updates the global application palette and style to match
        the requested theme.

        Args:
            mode (str):
                Theme name to apply to the running Qt application, such
                as "white", "sepia", or "dark".
        """
        apply_qt_theme(app, mode)

    apply_theme(mode=_resolve_initial_theme())
    window: QMainWindow = qt_main_window_cls()
    window.setWindowTitle(_APP_SETTINGS.app_title)
    icon_path: Path = REPO_ROOT / "icon.ico"
    if icon_path.is_file():
        window_icon: QIcon = QIcon(str(icon_path))
        if not window_icon.isNull():
            app.setWindowIcon(window_icon)
            window.setWindowIcon(window_icon)
    window.resize(1400, 850)
    window.setMinimumSize(800, 500)
    with suppress(RuntimeError):
        status: QStatusBar | None = window.statusBar()
        if status is not None:
            status.showMessage("Ready")
    state: StateDict = _load_state_json()
    active_root: str = str(state.get("activeProjectRoot") or "")
    current_path: list[str] = [
        active_root.strip() or str(_APP_SETTINGS.plans_dir)
    ]
    profile: QWebEngineProfile = qt_web_profile_cls("study_md_desk", app)
    configure_web_profile(
        profile, app_root=REPO_ROOT  # pyright: ignore[reportArgumentType]
    )
    external_urls: ExternalUrls = load_external_urls(
        settings_path=SETTINGS_PATH, load_state_json=_load_state_json_any
    )
    view: QWebEngineView = qt_web_engine_view_cls(profile)
    return DesktopShell(
        app=app,
        window=window,
        profile=profile,
        view=view,
        current_path=current_path,
        external_urls=external_urls,
        apply_qt_theme=apply_theme,
    )


@dataclass
class TtsRuntimeState:
    """
    TtsRuntimeState groups together all runtime objects used for desktop
    text-to-speech.

    It provides a single structure that orchestrates TTS engines,
    playback coordination, and UI control bindings.

    Attributes:
        tts_espeak (DesktopOfflineTtsController):
            Offline TTS controller for the eSpeak engine, responsible
            for reading text using the system voice.
        tts_piper (DesktopPiperTtsController):
            Offline TTS controller for the Piper engine, providing
            higher quality synthesized speech and voice selection.
        tts_engine (str):
            Name of the currently active TTS engine, such as "espeak" or
            "piper", used to route playback and configuration changes.
        orchestrator (TtsOrchestratorActions):
            High-level action bundle that coordinates reading, pausing,
            stopping, and cursor management for TTS playback.
        controls (TtsControlBindings):
            UI-facing bindings for adjusting TTS settings like voice,
            speed, and sentence silence, and for dispatching highlight
            events into the viewer.
    """

    tts_espeak: DesktopOfflineTtsController
    tts_piper: DesktopPiperTtsController
    tts_engine: str
    orchestrator: TtsOrchestratorActions
    controls: TtsControlBindings


@dataclass(frozen=True)
class TtsActionStateInputs:
    """
    TtsActionStateInputs aggregates all inputs required to build TTS
    action state.

    It encapsulates UI references, engine configuration, and accessors
    used to coordinate text-to-speech behavior.

    Attributes:
        window (QMainWindow):
            Main application window whose status and lifecycle are used
            when running TTS actions and showing progress.
        view (QWebEngineView):
            Web view displaying the current document that TTS actions
            read from and synchronize highlights with.
        qt_timer_cls (type[QTimer]):
            Qt timer class used to schedule UI callbacks and dispatch
            TTS updates back onto the main thread.
        tts_engine (str):
            Name of the active TTS engine that determines which runtime
            implementation should handle playback.
        tts_piper (DesktopPiperTtsController):
            Piper TTS controller instance used for high-quality offline
            speech synthesis and engine-specific configuration.
        get_active_tts (Callable[[], object]):
            Callable that returns the currently active TTS controller
            object used for issuing playback commands.
        get_online_panel (Callable[[], OnlinePanelRefs | None]):
            Callable that resolves the current online panel references,
            or None when no panel is active, enabling chat and UI
            integration.
    """

    window: QMainWindow
    view: QWebEngineView
    qt_timer_cls: type[QTimer]
    tts_engine: str
    tts_piper: DesktopPiperTtsController
    get_active_tts: Callable[[], object]
    get_online_panel: Callable[[], OnlinePanelRefs | None]


def _show_window_status(
    window: QMainWindow, message: str, timeout: int = 0
) -> None:
    """
    Show a transient status message in the main application window.

    This updates the window's status bar text for an optional duration.

    Args:
        window (QMainWindow):
            Main application window whose status bar should display the
            message.
        message (str):
            Human-readable status text to show to the user.
        timeout (int):
            Number of milliseconds to keep the message visible before it
            clears automatically; 0 shows it indefinitely.
    """
    status_bar: QStatusBar | None = window.statusBar()
    if status_bar is not None:
        status_bar.showMessage(message, msecs=timeout)


def _send_prompt_to_online_panel(
    get_online_panel: Callable[[], OnlinePanelRefs | None],
    prompt: str,
) -> None:
    """
    Send a prompt string into the active online panel chat box.

    This routes user-entered or generated prompts to the embedded chat
    UI when it is available.

    Args:
        get_online_panel (Callable[[], OnlinePanelRefs | None]):
            Callable that returns the current online panel references,
            or None when no panel is active.
        prompt (str):
            Text prompt that should be inserted into and focused in the
            panel's chat view for sending or editing.
    """
    panel: OnlinePanelRefs | None = get_online_panel()
    if panel is not None:
        focus_chat_and_copy_prompt(panel.chat_view, prompt)


def _current_document_id() -> str:
    """
    Return a stable identifier for the currently open document.

    This identifier is derived from the current document's filesystem
    path and is used to associate persisted state with that file.

    Returns:
        str:
            String identifier representing the current document,
            suitable for indexing TTS cursors and other per-document
            metadata.
    """
    return current_doc_id(get_current_doc_fs_path=_get_current_doc_fs_path)


def _build_tts_actions_state(
    *, inputs: TtsActionStateInputs
) -> tuple[TtsOrchestratorActions, ShowStatusFn]:
    """
    Build the text-to-speech action orchestrator and status callback.

    This wires together TTS dependencies so that playback, cursor
    management, and chat actions can be triggered from the desktop UI.

    Args:
        inputs (TtsActionStateInputs):
            Aggregated inputs that provide window and view references,
            TTS engine selection, Piper controller, timer class, and
            online panel accessors used to configure the action layer.

    Returns:
        tuple[TtsOrchestratorActions, ShowStatusFn]:
            Tuple containing the orchestrator object that exposes
            high-level TTS actions such as reading, pausing, and
            stopping, and a callable used to display status messages in
            the application window.
    """

    def run_in_background(task: Callable[[], None]) -> None:
        """
        Run a callable in a background worker thread.

        This allows long-running or blocking tasks to execute without
        freezing the Qt user interface.

        Args:
            task (Callable[[], None]):
                Zero-argument callable that performs the background work
                and does not return a value.
        """
        threading.Thread(target=task, daemon=True).start()

    def show_status(message: str, timeout: int = 0) -> None:
        """
        Display a status message in the desktop window from TTS actions.

        This delegates to the shared window status helper so TTS
        operations can report progress and feedback to the user.

        Args:
            message (str):
                Human-readable status text describing the current
                TTS-related action or outcome.
            timeout (int):
                Number of milliseconds to keep the message visible
                before it clears automatically; 0 shows it
                indefinitely.
        """
        _show_window_status(inputs.window, message, timeout)

    raw_actions: dict[str, Any] = (
        build_tts_actions(  # pyright: ignore[reportAssignmentType]
            deps=TtsActionDependencies(
                window=inputs.window,
                view=inputs.view,  # pyright: ignore[reportArgumentType]
                get_active_tts=inputs.get_active_tts,  # pyright: ignore[reportArgumentType]
                get_tts_engine=lambda: inputs.tts_engine,
                tts_piper=inputs.tts_piper,
                read_current_md_text=lambda: read_current_md_text(
                    get_current_doc_fs_path=_get_current_doc_fs_path,
                    extract_tts_text_from_markdown=_extract_tts_text_from_markdown,
                ),
                load_tts_cursor_for_current_doc=lambda: load_tts_cursor_for_current_doc(
                    load_state_json=_load_state_json,
                    current_doc_id_value=_current_document_id(),
                ),
                save_tts_cursor=lambda idx: save_tts_cursor(
                    save_state_json=_save_state_to_json,
                    current_doc_id_value=_current_document_id(),
                    idx=idx,
                ),
                clear_tts_cursor=lambda: clear_tts_cursor(
                    save_state_json=_save_state_to_json
                ),
                split_for_tts=_split_for_tts,
                send_prompt_to_chat=lambda prompt: _send_prompt_to_online_panel(
                    inputs.get_online_panel, prompt
                ),
                show_status=show_status,
                run_in_background=run_in_background,
                dispatch_to_ui=lambda task: inputs.qt_timer_cls.singleShot(
                    0, task
                ),
            )
        )
    )
    orchestrator: TtsOrchestratorActions = _tts_actions_from_factory(
        raw=raw_actions
    )
    install_global_actions(
        window=inputs.window,
        tts_read_document=orchestrator.tts_read_document,
        tts_read_selection_or_document=orchestrator.tts_read_selection_or_document,
        pause_toggle_and_persist=orchestrator.pause_toggle_and_persist,
        stop_and_clear=orchestrator.stop_and_clear,
        ask_current_selection_in_chat=orchestrator.ask_current_selection_in_chat,
    )
    return orchestrator, show_status


def _build_tts_control_state(  # noqa: C901
    *,
    view: QWebEngineView,
    tts_engine: str,
    tts_piper: DesktopPiperTtsController,
    tts_events: queue.Queue[dict[str, str]],
    show_status: ShowStatusFn,
) -> TtsControlBindings:
    """
    Build the text-to-speech control bindings for the desktop viewer.

    This creates a bundle of callbacks that adjust engine settings and
    drive highlight synchronization in the rendered document.

    Args:
        view (QWebEngineView):
            Web view that renders the current document and receives
            JavaScript-based TTS highlight updates.
        tts_engine (str):
            Name of the active TTS engine whose configuration will be
            read and updated by the control bindings.
        tts_piper (DesktopPiperTtsController):
            Piper TTS controller instance whose voice, speed, and
            silence parameters are modified through the controls.
        tts_events (queue.Queue[dict[str, str]]):
            Queue carrying TTS event dictionaries that are drained to
            drive highlight synchronization.
        show_status (ShowStatusFn):
            Callback used to display human-readable status messages when
            TTS configuration changes or errors occur.

    Returns:
        TtsControlBindings:
            Structured set of callable bindings that expose voice
            selection, speed and silence adjustment, and highlight
            dispatch functions for use by the UI and bridge layer.
    """

    def set_voice(voice_id: str) -> None:
        """
        Select a Piper TTS voice for subsequent playback.

        This updates the active Piper configuration so future speech
        uses the requested voice variant.

        Args:
            voice_id (str):
                Identifier of the desired Piper voice, such as a
                language-code and style name, that should become the
                new default for synthesis.
        """
        set_piper_voice(
            repo_root=REPO_ROOT,
            tts_piper=tts_piper,
            update_app_config_key=_update_app_config_key,
            show_status=show_status,
            voice_id=voice_id,
        )

    def get_speed() -> str:
        """
        Return the current text-to-speech playback speed as a label.

        This exposes the effective speed setting so UI elements can
        display or announce it.

        Returns:
            str:
                Human-readable speed label derived from the active TTS
                engine configuration, such as "0.9x", "1.0x", or
                "1.2x".
            )
        """
        return get_tts_speed(tts_engine=tts_engine, tts_piper=tts_piper)

    def adjust_speed_value(delta: float) -> str:
        """
        Adjust the text-to-speech playback speed by a relative amount.

        This updates the engine configuration and returns a label
        describing the new effective speed.

        Args:
            delta (float):
                Relative change to apply to the current playback speed,
                where positive values speed up playback and negative
                values slow it down.

        Returns:
            str:
                Human-readable speed label representing the updated TTS
                configuration, such as "0.9x", "1.0x", or "1.2x".
        """
        return adjust_tts_speed(
            tts_engine=tts_engine,
            tts_piper=tts_piper,
            update_app_config_key=_update_app_config_key,
            delta=delta,
        )

    def set_speed_value(value: float) -> None:
        """
        Set the absolute text-to-speech playback speed.

        This updates the engine configuration so future speech uses the
        specified speed factor.

        Args:
            value (float):
                Absolute playback speed multiplier to apply, where 1.0
                is normal speed, values greater than 1.0 are faster,
                and values less than 1.0 are slower.
        """
        set_tts_speed(
            tts_engine=tts_engine,
            tts_piper=tts_piper,
            update_app_config_key=_update_app_config_key,
            value=value,
        )

    def get_sentence_silence_value() -> str:
        """
        Return the configured silence duration between spoken sentences.

        This exposes the effective pause setting so UI elements can
        display or announce it.

        Returns:
            str:
                Human-readable label representing the current sentence
                silence duration used by the active TTS engine, such as
                "0.2s", "0.5s", or "1.0s".
        """
        return get_sentence_silence(tts_engine=tts_engine, tts_piper=tts_piper)

    def set_sentence_silence_value(value: float) -> None:
        """
        Set the silence duration to insert between spoken sentences.

        This updates the engine configuration so future speech uses the
        chosen pause length.

        Args:
            value (float):
                Absolute pause duration, in seconds, to insert after
                each sentence, where larger values create longer
                silences.
        """
        set_sentence_silence(
            tts_engine=tts_engine,
            tts_piper=tts_piper,
            update_app_config_key=_update_app_config_key,
            value=value,
        )

    def dispatch_tts_highlight() -> None:
        """
        Dispatch the latest text-to-speech highlight into the web view.

        This synchronizes the on-screen text highlight with the most
        recent TTS chunk produced by the engine.

        It performs a side effect by executing JavaScript in the web
        view when a new TTS highlight is available.
        """
        drained: list[Any] = []
        while True:
            try:
                drained.append(tts_events.get_nowait())
            except queue.Empty:
                break
        if not drained:
            return
        saw_done = False
        last_text_safe: str | None = None
        for ev in drained:
            if isinstance(ev, dict) and str(ev.get("type") or "") == "done":
                saw_done = True
                continue
            cand: str | None = extract_tts_sync_text(event=ev)
            if cand:
                last_text_safe = cand
        if last_text_safe:
            _run_javascript_on_view(
                view, build_tts_sync_script(text=last_text_safe)
            )
        if saw_done:
            _run_javascript_on_view(
                view,
                "window.mdViewerTtsClear && window.mdViewerTtsClear();",
            )

    return TtsControlBindings(
        tts_set_piper_voice=set_voice,
        tts_get_speed=get_speed,
        tts_adjust_speed=adjust_speed_value,
        tts_set_speed=set_speed_value,
        tts_get_sentence_silence=get_sentence_silence_value,
        tts_set_sentence_silence=set_sentence_silence_value,
        dispatch_tts_highlight=dispatch_tts_highlight,
    )


def _create_tts_runtime(
    *,
    window: QMainWindow,
    view: QWebEngineView,
    qt_timer_cls: type[QTimer],
    get_online_panel: Callable[[], OnlinePanelRefs | None],
) -> TtsRuntimeState:
    """
    Create and initialize the text-to-speech runtime for the desktop
    app.

    This assembles engine controllers, orchestrator actions, and UI
    control bindings into a single state object ready for use by the
    viewer.

    Args:
        window (QMainWindow):
            Main application window used for status reporting, lifecycle
            management, and integration with global TTS actions.
        view (QWebEngineView):
            Web view that renders the current document and receives TTS
            highlight synchronization updates.
        qt_timer_cls (type[QTimer]):
            Qt timer class used to marshal callbacks back onto the UI
            thread for safe interaction with Qt widgets.
        get_online_panel (Callable[[], OnlinePanelRefs | None]):
            Callable that resolves the current online panel references,
            or None when no panel is active, enabling chat-related TTS
            actions.

    Returns:
        TtsRuntimeState:
            Aggregated runtime state containing offline TTS controllers,
            the active engine name, the action orchestrator, and control
            bindings used by menus, bridges, and timers.
    """
    tts_espeak = DesktopOfflineTtsController(split_for_tts=_split_for_tts)
    tts_piper: PiperTtsController = DesktopPiperTtsController(
        repo_root=REPO_ROOT, split_for_tts=_split_for_tts
    )
    tts_events: queue.Queue[dict[str, str]] = queue.Queue()

    def push_playback_done() -> None:
        try:
            tts_events.put_nowait({"type": "done", "text": ""})
        except Exception:
            return

    tts_espeak.on_playback_finished = push_playback_done
    tts_piper.on_playback_finished = push_playback_done
    tts_engine: str = apply_tts_ini_settings(
        settings_path=SETTINGS_PATH,
        repo_root=REPO_ROOT,
        tts_espeak=tts_espeak,
        tts_piper=tts_piper,
    )

    def active_tts() -> object:
        """
        Return the currently active text-to-speech controller instance.

        This chooses between available engines so callers can issue
        playback commands without knowing which backend is selected.

        Returns:
            object:
                The active TTS controller object, either the Piper
                controller when the engine is set to "piper" or the
                eSpeak controller otherwise.
        """
        return tts_piper if tts_engine == "piper" else tts_espeak

    def on_tts_chunk(chunk: str) -> None:
        """
        Handle a newly generated text-to-speech chunk from the engine.

        This filters and normalizes the chunk before queuing it for
        highlight synchronization in the UI.

        Args:
            chunk (str):
                Raw text fragment produced by the TTS engine, which may
                include control prefixes or whitespace that should be
                ignored.
        """
        if not chunk:
            return
        clean_chunk: str = chunk.strip()
        if clean_chunk.startswith("[tts]") or clean_chunk.startswith(
            "[piper]"
        ):
            return
        tts_events.put({"type": "chunk", "text": clean_chunk[:800]})

    tts_espeak.on_chunk = on_tts_chunk
    tts_piper.on_chunk = on_tts_chunk
    tts_piper.on_progress = None
    orchestrator, show_status = _build_tts_actions_state(
        inputs=TtsActionStateInputs(
            window=window,
            view=view,
            qt_timer_cls=qt_timer_cls,
            tts_engine=tts_engine,
            tts_piper=tts_piper,
            get_active_tts=active_tts,
            get_online_panel=get_online_panel,
        )
    )
    controls: TtsControlBindings = _build_tts_control_state(
        view=view,
        tts_engine=tts_engine,
        tts_piper=tts_piper,
        tts_events=tts_events,
        show_status=show_status,
    )
    return TtsRuntimeState(
        tts_espeak=tts_espeak,
        tts_piper=tts_piper,
        tts_engine=tts_engine,
        orchestrator=orchestrator,
        controls=controls,
    )


def main() -> None:
    """
    Launch the legacy desktop viewer application.

    This bootstraps the HTTP runtime, Qt shell, menus, text-to-speech
    runtime, and online panel integration, then enters the Qt event
    loop.

    This function does not return a value. It runs the main application
    loop until the user closes the window, at which point registered
    cleanup callbacks are executed.
    """
    _ensure_runtime_dependencies()
    (
        qt_application_cls,
        qt_main_window_cls,
        qt_menu_bar_cls,
        qt_timer_cls,
        qt_web_engine_view_cls,
        qt_web_profile_cls,
    ) = _import_qt_bindings()
    _warn_if_default_plans_dir_missing()
    running_server: RunningServer = _start_http_runtime()
    server: _ShutdownCapable = cast(_ShutdownCapable, running_server.server)
    base_url: str = running_server.base_url
    shell: DesktopShell = _create_desktop_shell(
        qt_application_cls=qt_application_cls,
        qt_main_window_cls=qt_main_window_cls,
        qt_web_profile_cls=qt_web_profile_cls,
        qt_web_engine_view_cls=qt_web_engine_view_cls,
    )
    app: QApplication = shell.app
    window: QMainWindow = shell.window
    profile: QWebEngineProfile = shell.profile
    view: QWebEngineView = shell.view
    current_path: list[str] = shell.current_path
    menu_bar: QMenuBar = qt_menu_bar_cls()
    _install_main_file_menu(
        window=window,
        menu_bar=menu_bar,
        view=view,
        base_url=base_url,
        current_path=current_path,
    )
    online_panel_ref: list[OnlinePanelRefs | None] = [None]
    view_acts: dict[str, QAction] = _install_main_view_menu_and_connect(
        window=window,
        menu_bar=menu_bar,
        view=view,
    )
    sync_external_panels: Callable[[], None] = _sync_external_panels_factory(
        online_panel_ref
    )
    sync_menu_from_page: Callable[..., None] = (
        _main_sync_menu_from_page_factory(
            view=view,
            view_acts=view_acts,
            sync_external_panels=sync_external_panels,
        )
    )
    install_settings_menu(
        window=window,
        menu_bar=menu_bar,
        open_settings_ui=_main_open_app_settings_ui(view),
    )
    tts_runtime: TtsRuntimeState = _create_tts_runtime(
        window=window,
        view=view,
        qt_timer_cls=qt_timer_cls,
        get_online_panel=lambda: online_panel_ref[0],
    )
    _attach_online_panel(
        window=window,
        menu_bar=menu_bar,
        view=view,
        profile=profile,
        external_urls=shell.external_urls,
        view_acts=view_acts,
        online_panel_ref=online_panel_ref,
        tts_runtime=tts_runtime,
        apply_qt_theme=shell.apply_qt_theme,
    )
    install_main_view_runtime(
        view=view,
        base_url=base_url,
        sync_menu_from_page=sync_menu_from_page,
    )
    _tts_timer: QTimer = install_tts_highlight_timer(
        dispatch_tts_highlight=tts_runtime.controls.dispatch_tts_highlight
    )
    cleanup: Callable[[], None] = _main_desktop_cleanup_factory(
        tts_runtime=tts_runtime,
        server=server,
    )
    install_about_to_quit_cleanup(
        app=app, cleanup=cleanup  # pyright: ignore[reportArgumentType]
    )
    window.show()
    app.exec()
