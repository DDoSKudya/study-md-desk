"""
This module builds and manages the "online" side panel in a PyQt-based
desktop viewer, providing chat, translation, sandbox, and Codewars web
tabs.

It defines two dataclasses, OnlinePanelRefs and OnlinePanelDependencies,
that bundle together the widgets, configuration, and callbacks needed
to construct and operate the panel.

It includes helper functions to copy chat prompts to the clipboard and
focus the chat view, update web view URLs only when they change, and
synchronize the URLs of the chat, sandbox, and translation tabs with
external configuration.

It provides builders for generic web tabs and a specialized Codewars tab
that includes a URL input field and "Open" button wired into a
QWebEngineView.

It connects a menu or toolbar action to control the visibility of the
online panel, initializing and persisting that visibility state via
JSON-backed settings.

It wires Codewars UI controls to the web view and state storage,
normalizing user-entered URLs and saving the latest visited Codewars
URL through a small nested state patch.

The main build_online_panel function assembles the splitter layout, sets
up the ShellWebHost and chat bridge via QWebChannel, adds all tabs,
hooks up stateful behaviors, and returns an OnlinePanelRefs object so
the rest of the application can control and interact with the online
panel.
"""

from __future__ import annotations

from contextlib import suppress

from dataclasses import dataclass
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QAction, QClipboard
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from typing import Callable, Mapping, TypeAlias

from viewer_app.desktop.desktop_bridge import BridgeActions, ChatBridge
from viewer_app.desktop.desktop_doc_loading_overlay import ShellWebHost
from viewer_app.desktop.desktop_runtime import ExternalUrls
from viewer_app.desktop.desktop_web_helpers import (
    normalize_external_url,
    resolve_online_panel_visible,
)


@dataclass(frozen=True)
class OnlinePanelRefs:
    """
    Holds references to key widgets and bridge objects for the online
    panel.

    This data class provides a convenient bundle of handles needed to
    control and interact with the panel after it has been constructed.

    The references allow other parts of the application to update URLs,
    toggle visibility, and communicate with the chat web view via the
    Qt WebChannel.

    Attributes:
        splitter (QSplitter):
            The main splitter that arranges the document shell and the
            right-side tab panel.
        shell_web_host (ShellWebHost):
            The wrapper hosting the primary document web view and its
            loading overlay.
        right_tabs (QTabWidget):
            The tab widget containing chat, translation, sandbox, and
            Codewars views.
        chat_view (QWebEngineView):
            The web view that displays the external chat interface.
        translate_view (QWebEngineView):
            The web view used for the translation service.
        sandbox_view (QWebEngineView):
            The web view hosting the external code sandbox or compiler.
        codewars_view (QWebEngineView):
            The web view that loads Codewars pages or kata URLs.
        codewars_url_input (QLineEdit):
            The text input field where the user can enter or edit the
            current Codewars URL.
        chat_bridge (ChatBridge):
            The bridge object exposing actions and callbacks between the
            shell web view and the chat panel.
        web_channel (QWebChannel):
            The Qt WebChannel instance that registers and transports the
            chat bridge to the web content.
    """

    splitter: QSplitter
    shell_web_host: ShellWebHost
    right_tabs: QTabWidget
    chat_view: QWebEngineView
    translate_view: QWebEngineView
    sandbox_view: QWebEngineView
    codewars_view: QWebEngineView
    codewars_url_input: QLineEdit
    chat_bridge: ChatBridge
    web_channel: QWebChannel


@dataclass(frozen=True)
class OnlinePanelDependencies:
    """
    Collects all objects required to construct and manage the online
    panel.

    This data class bundles UI elements, configuration helpers, and
    bridge callbacks into a single dependency container.

    It is passed into the panel builder so that window wiring, state
    loading, and JavaScript integration can be controlled from the
    outside.

    Attributes:
        window (QMainWindow):
            The main application window that will host the splitter and
            online panel as its central widget.
        menu_bar (QMenuBar):
            The menu bar to attach to the main window, providing access
            to view actions such as toggling the online panel.
        view (QWebEngineView):
            The primary web view used by the shell document host that
            sits alongside the online panel.
        profile (QWebEngineProfile):
            The shared web profile used to create all web views in the
            panel so they share cookies and other web state.
        external_urls (ExternalUrls):
            The configuration
            object that supplies initial URLs for chat, translation,
            sandbox, and Codewars tabs.
        view_acts (dict[str, QAction]):
            The mapping of named view actions, including the action that
            controls online panel visibility.
        load_state_json (Callable[[], dict[str, object]]):
            A callable that returns the persisted UI state, used to
            restore panel visibility and Codewars URL.
        update_state_nested_dict (
            Callable[[str, dict[str, object]], None]
        ):
            A function used to patch nested sections of the persisted
            state, such as the "qtPanels" or "codewars" entries.
        run_view_js (Callable[[str], None]):
            A helper that executes JavaScript in the primary document
            view, enabling interaction between the shell and the chat
            panel.
        bridge_actions (BridgeActions):
            An object describing callable actions that the chat bridge
            can expose to web content.
        initial_qt_theme (str):
            The initial Qt theme name (for example, "light" or "dark")
            used to style the document loading overlay.
    """

    window: QMainWindow
    menu_bar: QMenuBar
    view: QWebEngineView
    profile: QWebEngineProfile
    external_urls: ExternalUrls
    view_acts: dict[str, QAction]
    load_state_json: Callable[[], dict[str, object]]
    update_state_nested_dict: Callable[[str, dict[str, object]], None]
    run_view_js: Callable[[str], None]
    bridge_actions: BridgeActions
    initial_qt_theme: str = "light"


StateLoader: TypeAlias = Callable[[], dict[str, object]]
StateUpdater: TypeAlias = Callable[[str, dict[str, object]], None]


def focus_chat_and_copy_prompt(chat_view: QWebEngineView, prompt: str) -> None:
    """
    Copies a chat prompt to the clipboard and focuses the chat view.

    This helper streamlines the workflow of sending a prepared prompt to
    the external chat panel.

    The function trims the prompt, writes it to the system clipboard
    when non-empty, and then brings the chat web view to the foreground
    for immediate interaction.

    Args:
        chat_view (QWebEngineView):
            The chat web view that should receive focus after the prompt
            is placed on the clipboard.
        prompt (str):
            The text that will be trimmed and copied to the clipboard
            before focusing the chat view.
    """
    value: str = (prompt or "").strip()
    if not value:
        return
    with suppress(RuntimeError):
        clipboard: QClipboard | None = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(value)
    with suppress(RuntimeError):
        chat_view.setFocus()
        chat_view.activateWindow()


def _set_view_url_if_changed(web_view: QWebEngineView, url: str) -> None:
    """
    Updates a web views URL only when it has actually changed.

    This avoids unnecessary reloads and preserves state when the
    requested location matches the current one.

    The function compares the views current URL string with the target
    value and sets a new QUrl only if they differ.

    Args:
        web_view (QWebEngineView):
            The web view whose URL should be conditionally updated.
        url (str):
            The desired URL to display in the web view, expressed as a
            string.
    """
    if web_view.url().toString() != url:
        web_view.setUrl(QUrl(url))


def sync_external_panel_urls(
    refs: OnlinePanelRefs,
    *,
    chat_url: str,
    sandbox_url: str,
    translate_url: str,
) -> None:
    """
    Synchronizes the URLs of the external panels web views with new
    values.

    This helper updates each tab only when its target URL has changed to
    avoid unnecessary reloads.

    It keeps the chat, sandbox, and translation views aligned with the
    latest configuration or state provided by the caller.

    Args:
        refs (OnlinePanelRefs):
            The collection of web views and widgets that make up the
            online panel, including the chat, sandbox, and translate
            views.
        chat_url (str):
            The desired URL for the chat tab, typically pointing to an
            external chat service.
        sandbox_url (str):
            The desired URL for the sandbox tab, usually an online
            compiler or code runner.
        translate_url (str):
            The desired URL for the translation tab, targeting an
            external translation service.
    """
    _set_view_url_if_changed(web_view=refs.chat_view, url=chat_url)
    _set_view_url_if_changed(web_view=refs.sandbox_view, url=sandbox_url)
    _set_view_url_if_changed(web_view=refs.translate_view, url=translate_url)


def _build_tab(
    *, profile: QWebEngineProfile, url: str
) -> tuple[QWebEngineView, QWidget]:
    """
    Constructs a simple web-based tab using a shared web profile.

    This helper creates a QWebEngineView, loads an initial URL, and
    wraps it in a QWidget with a vertical layout.

    The returned pair can be added directly to a QTabWidget as a new
    browser-like tab.

    Args:
        profile (QWebEngineProfile):
            The web profile instance that will be used to create the web
            view so it shares cookies and other web state with related
            views.
        url (str):
            The initial URL to load into the newly created web view.

    Returns:
        tuple[QWebEngineView, QWidget]:
            A tuple containing the configured web view and its wrapper
            widget suitable for insertion into a tab bar or layout.
    """
    web_view: QWebEngineView = QWebEngineView(profile)
    web_view.setUrl(QUrl(url))
    tab: QWidget = QWidget()
    layout: QVBoxLayout = QVBoxLayout(tab)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(web_view)
    return web_view, tab


def _build_codewars_tab(
    *, profile: QWebEngineProfile, external_urls: ExternalUrls
) -> tuple[QWebEngineView, QWidget, QLineEdit, QPushButton]:
    """
    Builds the Codewars tab UI, including URL controls and web view.

    This helper wires an input field, button, and browser area into a
    vertical layout for navigating Codewars content.

    The function initializes the web view with the configured Codewars
    URL, pre-fills the input, and returns all key widgets so callers
    can attach behavior and state handling.

    Args:
        profile (QWebEngineProfile):
            The shared web profile used to construct the Codewars web
            view so it reuses cookies and other browser state.
        external_urls (ExternalUrls):
            The configuration object that supplies the initial Codewars
            dashboard or kata URL.

    Returns:
        tuple[QWebEngineView, QWidget, QLineEdit, QPushButton]:
            A tuple containing the Codewars web view, its wrapper
            widget, the URL input line edit, and the "Open" button.
    """
    codewars_view: QWebEngineView = QWebEngineView(profile)
    codewars_view.setUrl(QUrl(external_urls.codewars_url))
    codewars_wrap: QWidget = QWidget()
    codewars_layout: QVBoxLayout = QVBoxLayout(codewars_wrap)
    codewars_layout.setContentsMargins(10, 10, 10, 10)
    codewars_layout.setSpacing(8)
    codewars_top: QHBoxLayout = QHBoxLayout()
    codewars_top.setContentsMargins(0, 0, 0, 0)
    codewars_top.setSpacing(8)
    codewars_url_input: QLineEdit = QLineEdit()
    codewars_url_input.setPlaceholderText("Kata URL or Codewars page URL...")
    codewars_url_input.setText(external_urls.codewars_url)
    codewars_go: QPushButton = QPushButton("Open")
    codewars_top.addWidget(QLabel("Codewars"))
    codewars_top.addWidget(codewars_url_input, 1)
    codewars_top.addWidget(codewars_go)
    codewars_layout.addLayout(codewars_top)
    codewars_layout.addWidget(codewars_view, 1)
    return codewars_view, codewars_wrap, codewars_url_input, codewars_go


def _connect_online_action(
    *,
    right_tabs: QTabWidget,
    view_actions: Mapping[str, QAction],
    load_state_json: StateLoader,
    update_state_nested_dict: StateUpdater,
) -> None:
    """
    Connects the online panel toggle action to the right-side tab
    widget.

    This helper wires the menu or toolbar action so that it reflects and
    controls whether the online panel is visible.

    The function initializes the checked state from persisted UI state
    and updates that state whenever the action is toggled.

    Args:
        right_tabs (QTabWidget):
            The tab widget representing the online panel whose
            visibility is controlled by the action.
        view_actions (Mapping[str, QAction]):
            A mapping of named view actions from which the "online"
            action is retrieved and connected.
        load_state_json (StateLoader):
            A callable that returns the persisted UI state used to
            decide the initial visibility of the online panel.
        update_state_nested_dict (StateUpdater):
            A function that updates the nested "qtPanels" state whenever
            the online visibility changes.
    """
    online_act: QAction | None = view_actions.get("online")
    if online_act is None:
        return
    online_checked: bool = resolve_online_panel_visible(
        state=load_state_json()
    )
    online_act.setChecked(online_checked)
    right_tabs.setVisible(online_checked)

    def toggle_online(checked: bool) -> None:
        """
        Builds and wires the entire online side panel for the desktop
        viewer.

        This function constructs chat, translation, sandbox, and
        Codewars tabs, sets up the splitter layout, and connects
        stateful behaviors.

        It returns a collection of references that higher-level code can
        use to control visibility, update URLs, and interact with the
        chat bridge.

        Args:
            deps (OnlinePanelDependencies):
                The bundle of UI objects, configuration, and callbacks
                required to create the panel, including the main
                window, web profile, external URLs, state helpers, and
                bridge actions.

        Returns:
            OnlinePanelRefs:
                A structured set of references to the splitter, tabs,
                web views, Codewars controls, and chat bridge created
                for the online panel.
        """
        right_tabs.setVisible(checked)
        update_state_nested_dict("qtPanels", {"online": checked})

    online_act.triggered.connect(toggle_online)


def _connect_codewars_controls(
    *,
    codewars_view: QWebEngineView,
    codewars_url_input: QLineEdit,
    codewars_go: QPushButton,
    save_codewars_state: Callable[..., None],
) -> None:
    """
    Wires the Codewars input controls to the web view and state storage.

    This helper keeps the displayed Codewars page and the persisted
    "last URL" value in sync as the user navigates.

    The function normalizes user-entered URLs before loading them,
    updates the input field, and records navigation changes via the
    provided state callback.

    Args:
        codewars_view (QWebEngineView):
            The web view that displays Codewars pages and emits URL
            change signals as the user navigates.
        codewars_url_input (QLineEdit):
            The line edit where the user types or edits the target
            Codewars URL.
        codewars_go (QPushButton):
            The button that, when clicked, normalizes and loads the URL
            from the input field into the web view.
        save_codewars_state (Callable[..., None]):
            A callback used to persist the most recent Codewars URL
            whenever it is opened explicitly or changed by navigation.
    """

    def open_codewars_url() -> None:
        """
        Builds and wires the entire online side panel for the desktop
        viewer.

        This function constructs chat, translation, sandbox, and
        Codewars tabs, sets up the splitter layout, and connects
        stateful behaviors.

        It returns a collection of references that higher-level code can
        use to control visibility, update URLs, and interact with the
        chat bridge.

        Args:
            deps (OnlinePanelDependencies):
                The bundle of UI objects, configuration, and callbacks
                required to create the panel, including the main
                window, web profile, external URLs, state helpers, and
                bridge actions.

        Returns:
            OnlinePanelRefs:
                A structured set of references to the splitter, tabs,
                web views, Codewars controls, and chat bridge created
                for the online panel.
        """
        normalized: str = normalize_external_url(url=codewars_url_input.text())
        if not normalized:
            return
        codewars_url_input.setText(normalized)
        codewars_view.setUrl(QUrl(normalized))
        save_codewars_state(last_url=normalized)

    def on_codewars_url_changed(url: QUrl) -> None:
        """
        Persists the latest Codewars URL whenever the web view
        navigates.

        This callback keeps the stored "last URL" value aligned with the
        page currently displayed in the Codewars tab.

        The function converts the QUrl to a string and forwards it to
        the provided state-saving callback.

        Args:
            url (QUrl):
                The new Codewars page URL emitted by the web view when
                navigation occurs.
        """
        save_codewars_state(last_url=url.toString())

    codewars_go.clicked.connect(open_codewars_url)
    with suppress(RuntimeError):
        codewars_view.urlChanged.connect(on_codewars_url_changed)


def build_online_panel(*, deps: OnlinePanelDependencies) -> OnlinePanelRefs:
    """
    Builds and wires the entire online side panel for the desktop
    viewer.

    This function constructs chat, translation, sandbox, and Codewars
    tabs, sets up the splitter layout, and connects stateful behaviors.

    It returns a collection of references that higher-level code can use
    to control visibility, update URLs, and interact with the chat
    bridge.

    Args:
        deps (OnlinePanelDependencies):
            The bundle of UI objects, configuration, and callbacks
            required to create the panel, including the main window,
            web profile, external URLs, state helpers, and bridge
            actions.

    Returns:
        OnlinePanelRefs:
            A structured set of references to the splitter, tabs, web
            views, Codewars controls, and chat bridge created for the
            online panel.
    """
    deps.window.setMenuBar(deps.menu_bar)
    shell_web_host: ShellWebHost = ShellWebHost(web_view=deps.view)
    shell_web_host.apply_doc_overlay_theme(deps.initial_qt_theme)
    splitter: QSplitter = QSplitter()
    splitter.setOrientation(Qt.Orientation.Horizontal)
    splitter.addWidget(shell_web_host)
    right_tabs: QTabWidget = QTabWidget()
    right_tabs.setMinimumWidth(420)
    right_tabs.setMaximumWidth(720)
    chat_view, chat_wrap = _build_tab(
        profile=deps.profile,
        url=deps.external_urls.chat_url,
    )
    chat_bridge: ChatBridge = ChatBridge(
        run_view_js=deps.run_view_js,
        get_chat_view=lambda: chat_view,
        actions=deps.bridge_actions,
        on_doc_content_loading=shell_web_host.set_doc_loading_visible,
        on_shell_chrome_theme=shell_web_host.apply_doc_overlay_theme,
    )
    web_channel: QWebChannel = QWebChannel()
    web_channel.registerObject("chatBridge", chat_bridge)
    _shell_page: QWebEnginePage | None = shell_web_host.web_view.page()
    if _shell_page is not None:
        _shell_page.setWebChannel(web_channel)
    right_tabs.addTab(chat_wrap, "Chat")
    translate_view, translate_wrap = _build_tab(
        profile=deps.profile,
        url=deps.external_urls.translate_url,
    )
    right_tabs.addTab(translate_wrap, "Translate")
    sandbox_view, sandbox_wrap = _build_tab(
        profile=deps.profile,
        url=deps.external_urls.sandbox_url,
    )
    right_tabs.addTab(sandbox_wrap, "Sandbox")
    codewars_view, codewars_wrap, codewars_url_input, codewars_go = (
        _build_codewars_tab(
            profile=deps.profile,
            external_urls=deps.external_urls,
        )
    )
    right_tabs.addTab(codewars_wrap, "Codewars")
    splitter.addWidget(right_tabs)
    splitter.setStretchFactor(0, 3)
    splitter.setStretchFactor(1, 2)
    deps.window.setCentralWidget(splitter)

    def save_codewars_state(*, last_url: str | None = None) -> None:
        """
        Updates the persisted Codewars state with the latest visited
        URL.

        This helper prepares a minimal patch dictionary and forwards it
        to the general nested-state updater used by the application.

        The function ignores calls that do not supply a URL, ensuring
        that only meaningful navigation changes are written to the
        stored state.

        Args:
            last_url (str | None):
                The most recently visited Codewars URL, or None when no
                update should be applied.
        """
        patch: dict[str, object] = {}
        if last_url is not None:
            patch["lastUrl"] = str(last_url)
        if patch:
            deps.update_state_nested_dict("codewars", patch)

    _connect_codewars_controls(
        codewars_view=codewars_view,
        codewars_url_input=codewars_url_input,
        codewars_go=codewars_go,
        save_codewars_state=save_codewars_state,
    )

    if right_tabs.isTabEnabled(0):
        right_tabs.setCurrentIndex(0)

    _connect_online_action(
        right_tabs=right_tabs,
        view_actions=deps.view_acts,
        load_state_json=deps.load_state_json,
        update_state_nested_dict=deps.update_state_nested_dict,
    )

    return OnlinePanelRefs(
        splitter=splitter,
        shell_web_host=shell_web_host,
        right_tabs=right_tabs,
        chat_view=chat_view,
        translate_view=translate_view,
        sandbox_view=sandbox_view,
        codewars_view=codewars_view,
        codewars_url_input=codewars_url_input,
        chat_bridge=chat_bridge,
        web_channel=web_channel,
    )
