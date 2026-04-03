"""
This module builds and manages the File menu for a PyQt-based markdown
viewer, including project selection, pinned and recent projects, and a
recent-document history submenu.

It defines several TypedDicts (ProjectItem, ProjectsState, HistoryItem)
and type aliases that describe the shape of project and history data
loaded from persisted state or returned by JavaScript in the web view.

The _run_js helper safely executes JavaScript in the QWebEngineView and
optionally passes back a string result to Python, handling the case
where no underlying page exists.

The _open_project_in_view function is the central “open project”
operation, normalizing the project root, updating current-project state
via callbacks, and navigating the main web view with a ?root= query
parameter.

Helpers like _project_item_name, _as_project_item_list, and
_normalize_projects_state sanitize raw project data into a consistent
structure with clean roots and optional names, separating pinned and
recent lists.

The _decode_history_items function parses JSON from the page into a list
of normalized HistoryItem dicts, defaulting missing fields to empty
strings and ignoring malformed entries.

_add_project_entries renders pinned or recent project lists into a QMenu
section, creating up to twenty actions that, when triggered, call
_open_project_in_view.

_connect_history_menu hooks the History submenus aboutToShow signal to
a rebuild function that queries the web pages recent list via
JavaScript, clears and repopulates the menu, and wires actions to open
documents back through JS.

_add_empty_projects_action inserts a disabled placeholder row when there
are no projects to show, while _build_rebuild_projects_menu returns a
callable that reloads pinned and recent projects from persisted state
and repopulates the Projects submenu, handling errors by showing the
empty placeholder.

_install_open_project_action adds an “Open project…” action that runs a
folder dialog and then delegates to _open_project_in_view to update
state and load the chosen project.

_install_projects_menu creates the Projects submenu, adds a pin/unpin
action that toggles the current projects pinned state and shows a
status-bar message, and connects aboutToShow to the rebuild function so
the menu always reflects current state.

The top-level install_file_menu function assembles the File menu on the
windows menu bar, installing the open-project action, Projects submenu,
and History submenu, thereby integrating filesystem projects, persisted
state, and web-page history into the desktop UI.
"""

from __future__ import annotations

import json
import urllib.parse

from pathlib import Path
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMenuBar,
    QStatusBar,
)
from typing import Callable, NotRequired, TypeAlias, TypedDict


class ProjectItem(TypedDict, total=False):
    """
    Represents a single project entry in the File menu.

    This typed dictionary describes the minimal information needed to
    identify and label a project.

    It pairs a mandatory root path with an optional display name,
    allowing callers to distinguish between storage location and
    user-facing title.

    Attributes:
        root (str):
            The file system path to the project root directory that
            should be opened in the viewer.
        name (str | None):
            An optional human-friendly label for the project; when
            omitted, callers typically fall back to the root folder
            name.
    """

    root: str
    name: NotRequired[str]


class ProjectsState(TypedDict):
    """
    Represents the persisted collections of pinned and recent projects.

    This typed dictionary groups related ProjectItem lists used to build
    the Projects submenu.

    It separates long-lived "pinned" entries from automatically
    maintained "recent" entries so the UI can display them in distinct
    sections.

    Attributes:
        pinned (list[ProjectItem]):
            The list of user-pinned projects that should appear at the
            top of the Projects menu under a dedicated "Pinned"
            section.
        recent (list[ProjectItem]):
            The list of most recently used projects that are shown below
            pinned entries in the Projects menu.
    """

    pinned: list[ProjectItem]
    recent: list[ProjectItem]


class HistoryItem(TypedDict, total=False):
    """
    Models a single entry in the document history list.

    This typed dictionary captures the minimal metadata needed to reopen
    a recent document from the History menu.

    It stores both the document path and its associated project root,
    plus a title suitable for display in the UI.

    Attributes:
        path (str):
            The file system path to the document that was previously
            opened in the viewer.
        root (str):
            The project root directory associated with the document,
            used to restore context when reopening it.
        title (str):
            The human-readable title shown in the History menu; falls
            back to the path when no specific title is available.
    """

    path: str
    root: str
    title: str


LoadStateJsonFn: TypeAlias = Callable[[], dict[str, object]]
GetProjectsStateFn: TypeAlias = Callable[
    [dict[str, object]], dict[str, object]
]
ProjectActionFn: TypeAlias = Callable[[str], None]


def _run_js(
    view: QWebEngineView,
    script: str,
    callback: Callable[[str | None], None] | None = None,
) -> None:
    """
    Executes a JavaScript snippet in the given web view, optionally
    handling its string result.

    This helper hides the boilerplate of accessing the underlying page
    and dealing with missing pages or callbacks.

    When no page is available it invokes the callback with None if
    provided, and otherwise runs the script with or without a result
    handler as requested.

    Args:
        view (QWebEngineView):
            The web view whose underlying page will execute the
            JavaScript code.
        script (str):
            The JavaScript source string to be evaluated in the context
            of the current page.
        callback (Callable[[str | None], None] | None):
            An optional function that receives the script result
            converted to a string, or None when there is no page or no
            result is produced.
    """
    page: QWebEnginePage | None = view.page()
    if page is None:
        if callback is not None:
            callback(None)
        return
    if callback is None:
        page.runJavaScript(script)
        return
    page.runJavaScript(script, callback)


def _open_project_in_view(
    *,
    root: str,
    base_url: str,
    current_path: list[str],
    set_active_project: ProjectActionFn,
    touch_project_recent: ProjectActionFn,
    save_course_parts: ProjectActionFn,
    view: QWebEngineView,
) -> None:
    """
    Opens a project in the main web view and updates related state.

    This helper centralizes the steps needed when switching the active
    project from the File menu.

    It validates and normalizes the root path, updates the current
    project tracking, records the project as active and recent,
    triggers any course-part saving logic, and navigates the web view to
    the new project root.

    Args:
        root (str):
            The file system path to the project root that should become
            the active project.
        base_url (str):
            The base viewer URL to which the URL-encoded project root
            query parameter will be appended.
        current_path (list[str]):
            A mutable single-element list holding the currently active
            project root, updated in place to the new value.
        set_active_project (ProjectActionFn):
            A callback that records the specified project as the
            currently active one in persistent or in-memory state.
        touch_project_recent (ProjectActionFn):
            A callback that updates the "recent projects" list to
            include or move the given project root.
        save_course_parts (ProjectActionFn):
            A callback that persists any course or document-part
            metadata associated with the selected project root.
        view (QWebEngineView):
            The web view that will be navigated to show the newly
            selected project by loading the base URL plus the encoded
            root query argument.
    """
    project_root: str = (root or "").strip()
    if not project_root:
        return
    current_path[0] = project_root
    set_active_project(project_root)
    touch_project_recent(project_root)
    save_course_parts(project_root)
    view.setUrl(QUrl(base_url + "?root=" + urllib.parse.quote(project_root)))


def _project_item_name(item: ProjectItem, root: str) -> str:
    """
    Derives a user-facing project name from a project item and root
    path.

    This helper prefers an explicit name field but falls back to a
    sensible default when one is not provided.

    If no label is stored in the item, it uses the last path component
    of the root, and finally the raw root string when no folder name
    can be extracted.

    Args:
        item (ProjectItem):
            The project metadata dictionary that may contain an optional
            name key.
        root (str):
            The file system path to the project root used as a fallback
            source for the display name.

    Returns:
        str:
            The resolved project display name, chosen from the item
            name, the root folder name, or the full root path.
    """
    return str(item.get("name") or Path(root).name or root)


def _as_project_item_list(raw: object) -> list[ProjectItem]:
    """
    Normalizes an arbitrary object into a list of well-formed project
    items.

    This helper validates structure and strips whitespace so only usable
    entries are returned.

    It discards invalid elements, enforces the presence of a non-empty
    root path, and preserves an optional cleaned name field.

    Args:
        raw (object):
            The raw value expected to contain a list-like collection of
            project dictionaries, typically loaded from persisted
            state.

    Returns:
        list[ProjectItem]:
            A list of sanitized project items, each with a mandatory
            root key and an optional name key when provided.
    """
    if not isinstance(raw, list):
        return []
    result: list[ProjectItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        root: str = str(item.get("root") or "").strip()
        if not root:
            continue
        name: str = str(item.get("name") or "").strip()
        entry: ProjectItem = {"root": root}
        if name:
            entry["name"] = name
        result.append(entry)
    return result


def _normalize_projects_state(raw: object) -> ProjectsState:
    """
    Normalizes a raw projects payload into a structured projects state.

    This helper guarantees that callers always receive pinned and recent
    lists in a consistent, sanitized format.

    It tolerates missing or malformed input by falling back to empty
    lists and delegates per-item validation to _as_project_item_list.

    Args:
        raw (object):
            The raw projects section loaded from persisted state,
            expected to be a dictionary with optional pinned and recent
            entries.

    Returns:
        ProjectsState:
            A dictionary containing normalized pinned and recent lists
            of project items, with invalid data replaced by empty
            lists.
    """
    if not isinstance(raw, dict):
        return {"pinned": [], "recent": []}
    return {
        "pinned": _as_project_item_list(raw=raw.get("pinned")),
        "recent": _as_project_item_list(raw=raw.get("recent")),
    }


def _decode_history_items(result: str | None) -> list[HistoryItem]:
    """
    Decodes a JSON payload of history items into a normalized list.

    This helper accepts a raw JSON string from the web view and converts
    it into well-typed HistoryItem dictionaries.

    It tolerates missing, malformed, or non-list data by returning an
    empty list and ensures all fields are present as strings,
    defaulting to empty values when absent.

    Args:
        result (str | None):
            The JSON string produced by the page that is expected to
            encode a list of history item objects, or None when no data
            is available.

    Returns:
        list[HistoryItem]:
            A list of history entries, each with path, root, and title
            keys populated from the decoded payload or defaulted to
            empty strings.
    """
    if not result:
        return []
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        {
            "path": str(item.get("path") or ""),
            "root": str(item.get("root") or ""),
            "title": str(item.get("title") or ""),
        }
        for item in payload
        if isinstance(item, dict)
    ]


def _add_project_entries(
    *,
    menu: QMenu,
    window: QMainWindow,
    items: list[ProjectItem],
    title: str,
    base_url: str,
    current_path: list[str],
    set_active_project: ProjectActionFn,
    touch_project_recent: ProjectActionFn,
    save_course_parts: ProjectActionFn,
    view: QWebEngineView,
) -> None:
    """
    Populates a menu with project actions for a given section title.

    This helper renders a header label and up to twenty project entries
    that open the selected project in the main view.

    It skips items with missing roots, derives a user-friendly name for
    each project, and wires actions so selecting a project updates
    state and navigates the viewer.

    Args:
        menu (QMenu):
            The menu that will receive the section header and project
            actions.
        window (QMainWindow):
            The main window used as the parent for the created actions.
        items (list[ProjectItem]):
            The list of project metadata items from which individual
            menu entries are built.
        title (str):
            The non-interactive header text describing this group of
            projects, such as "Pinned" or "Recent".
        base_url (str):
            The base viewer URL used when opening a project, combined
            with the encoded project root.
        current_path (list[str]):
            A mutable single-element list holding the currently active
            project root, updated when a project action is triggered.
        set_active_project (ProjectActionFn):
            A callback that records the chosen project as the active one
            in persistent or in-memory state.
        touch_project_recent (ProjectActionFn):
            A callback that updates the recent-projects list whenever a
            project entry is invoked.
        save_course_parts (ProjectActionFn):
            A callback that persists any course-part metadata associated
            with the selected project root.
        view (QWebEngineView):
            The web view that will be navigated to display the newly
            chosen project when its menu action is triggered.
    """
    if not items:
        return
    header: QAction = QAction(title, window)
    header.setEnabled(False)
    menu.addAction(header)
    for item in items[:20]:
        root: str = str(item.get("root") or "").strip()
        if not root:
            continue
        name: str = _project_item_name(item, root)
        act: QAction = QAction(name, window)
        act.triggered.connect(
            lambda _=False, rr=root: _open_project_in_view(
                root=rr,
                base_url=base_url,
                current_path=current_path,
                set_active_project=set_active_project,
                touch_project_recent=touch_project_recent,
                save_course_parts=save_course_parts,
                view=view,
            )
        )
        menu.addAction(act)


def _connect_history_menu(
    *, history_menu: QMenu, window: QMainWindow, view: QWebEngineView
) -> None:
    """
    Keeps the History submenu in sync with the recent documents list
    exposed by the web page.

    This helper rebuilds the menu on demand by querying the page via
    JavaScript and wiring actions to reopen items.

    It clears any existing entries, shows a placeholder when there is no
    history, and otherwise creates one action per history item that
    calls back into the page to open the selected document.

    Args:
        history_menu (QMenu):
            The menu instance that will be repopulated with recent
            document actions each time it is about to be shown.
        window (QMainWindow):
            The main window used as the parent for dynamically created
            history actions.
        view (QWebEngineView):
            The web view whose page exposes JavaScript helpers for
            fetching and opening recent documents.
    """

    def rebuild_history_menu() -> None:
        """
        Builds a fresh History menu from the pages recent documents
        list.

        This helper queries the web view for recent items, clears
        existing entries, and repopulates the menu with actions that
        reopen those documents.

        When no history is available, it shows a disabled placeholder
        entry, otherwise it generates one action per item that triggers
        a JavaScript call to open the chosen document by path and
        project root.

        Args:
            history_menu (QMenu):
                The menu instance that will be repopulated with recent
                document actions each time it is about to be shown.
            window (QMainWindow):
                The main window used as the parent for dynamically
                created history actions.
            view (QWebEngineView):
                The web view whose page exposes JavaScript helpers for
                fetching and opening recent documents.
        """

        def callback(result: str | None) -> None:
            items: list[HistoryItem] = _decode_history_items(result)
            history_menu.clear()
            if not items:
                empty_act: QAction = QAction("(no recent items yet)", window)
                empty_act.setEnabled(False)
                history_menu.addAction(empty_act)
                return
            for item in items:
                path: str = str(item.get("path") or "")
                root: str = str(item.get("root") or "")
                title: str = str(item.get("title") or path or "Document")
                act: QAction = QAction(title, window)

                def open_doc(
                    doc_path: str = path, doc_root: str = root
                ) -> None:
                    """
                    Installs the File menu, including project and
                    history submenus, on the main window.

                    This helper wires up actions for opening projects,
                    pinning recent workspaces, and accessing the
                    recent-document history.

                    It delegates menu population to helper functions,
                    ensuring the File menu stays in sync with persisted
                    state and the web views notion of recent documents.

                    Args:
                        window (QMainWindow):
                            The main application window that owns the
                            menu bar and provides a parent for created
                            actions.
                        menu_bar (QMenuBar):
                            The menu bar
                            to which the File menu and its submenus will
                            be added.
                        view (QWebEngineView):
                            The primary
                            web view used for opening projects and
                            responding to history menu actions.
                        base_url (str):
                            The base viewer URL used when navigating the
                            web view to a selected project.
                        current_path (list[str]):
                            A mutable
                            single-element list tracking the currently
                            active project root, updated whenever a
                            project is opened.
                        load_state_json (LoadStateJsonFn):
                            A callable that returns the persisted state
                            dictionary from which project metadata is
                            loaded.
                        get_projects_state (GetProjectsStateFn):
                            A function that extracts the projects
                            section from the persisted state structure.
                        set_active_project (ProjectActionFn):
                            A callback that records the chosen project
                            as the active one in persistent or in-memory
                            state.
                        touch_project_recent (ProjectActionFn):
                            A callback that updates the recent-projects
                            list whenever a project is opened from the
                            menu.
                        save_course_parts (ProjectActionFn):
                            A callback that persists any course-part
                            metadata associated with the active project
                            root.
                        toggle_pin_project (Callable[[str], bool]):
                            A function that toggles the pinned state of
                            the current project and returns True when
                            the project becomes pinned.

                    Returns:
                        QMenu | None:
                            The created File menu instance, or None if
                            the menu could not be added to the menu
                            bar.
                    """
                    _run_js(
                        view,
                        f"window.mdViewerOpenByPath && window.mdViewerOpenByPath({json.dumps(doc_path)}, {json.dumps(doc_root)});",
                    )

                act.triggered.connect(open_doc)
                history_menu.addAction(act)

        _run_js(
            view,
            "window.mdViewerGetRecentList && window.mdViewerGetRecentList(12);",
            callback,
        )

    history_menu.aboutToShow.connect(rebuild_history_menu)


def _add_empty_projects_action(
    projects_menu: QMenu, window: QMainWindow
) -> None:
    """
    Adds a disabled placeholder entry to the Projects menu when there
    are no projects to show.

    This helper provides a consistent, non-interactive message that
    informs the user the list is currently empty.

    It creates a grayed-out action labeled "(no projects yet)" and
    inserts it into the given projects menu.

    Args:
        projects_menu (QMenu):
            The Projects submenu that should receive the placeholder
            action when it has no real project entries.
        window (QMainWindow):
            The main window used as the parent for the placeholder menu
            action.
    """
    empty_act: QAction = QAction("(no projects yet)", window)
    empty_act.setEnabled(False)
    projects_menu.addAction(empty_act)


def _build_rebuild_projects_menu(
    *,
    projects_menu: QMenu,
    pin_act: QAction,
    window: QMainWindow,
    base_url: str,
    current_path: list[str],
    view: QWebEngineView,
    load_state_json: LoadStateJsonFn,
    get_projects_state: GetProjectsStateFn,
    set_active_project: ProjectActionFn,
    touch_project_recent: ProjectActionFn,
    save_course_parts: ProjectActionFn,
) -> Callable[[], None]:
    """
    Builds a callable that reconstructs the Projects submenu on demand.

    This helper encapsulates the logic for re-reading persisted project
    state and refreshing the menu contents each time it is shown.

    The returned function clears existing entries, re-adds the pin
    action, populates pinned and recent project groups, and falls back
    to a placeholder entry on error or when no projects exist.

    Args:
        projects_menu (QMenu):
            The Projects submenu that will be cleared and repopulated by
            the rebuilt function.
        pin_act (QAction):
            The action used to pin or unpin the current project, which
            is always kept at the top of the submenu.
        window (QMainWindow):
            The main window used as the parent for dynamically created
            project actions.
        base_url (str):
            The base viewer URL used when opening a project, combined
            with the encoded project root.
        current_path (list[str]):
            A mutable single-element list tracking the currently active
            project root, updated whenever a project entry is invoked.
        view (QWebEngineView):
            The web view navigated to display the selected project when
            a menu action is triggered.
        load_state_json (LoadStateJsonFn):
            A callable that returns the full persisted state dictionary
            from which project metadata is read.
        get_projects_state (GetProjectsStateFn):
            A function that extracts the projects section from the
            persisted state structure.
        set_active_project (ProjectActionFn):
            A callback that records the chosen project as the active one
            in persistent or in-memory state.
        touch_project_recent (ProjectActionFn):
            A callback that updates the recent-projects list whenever a
            project is opened from the menu.
        save_course_parts (ProjectActionFn):
            A callback that persists any course-part metadata associated
            with the selected project root.

    Returns:
        Callable[[], None]:
            A parameterless function that, when called, repopulates the
            Projects submenu based on the latest persisted projects
            state.
    """

    def rebuild_projects_menu() -> None:
        try:
            projects_menu.clear()
            projects_menu.addAction(pin_act)
            projects_menu.addSeparator()
            state_raw: dict[str, object] = load_state_json()
            projects_raw: dict[str, object] = get_projects_state(state_raw)
            projects_state: ProjectsState = _normalize_projects_state(
                raw=projects_raw
            )
            pinned: list[ProjectItem] = projects_state["pinned"]
            recent: list[ProjectItem] = projects_state["recent"]

            _add_project_entries(
                menu=projects_menu,
                window=window,
                items=pinned,
                title="Pinned",
                base_url=base_url,
                current_path=current_path,
                set_active_project=set_active_project,
                touch_project_recent=touch_project_recent,
                save_course_parts=save_course_parts,
                view=view,
            )
            if pinned:
                projects_menu.addSeparator()

            _add_project_entries(
                menu=projects_menu,
                window=window,
                items=recent,
                title="Recent",
                base_url=base_url,
                current_path=current_path,
                set_active_project=set_active_project,
                touch_project_recent=touch_project_recent,
                save_course_parts=save_course_parts,
                view=view,
            )

            if not pinned and not recent:
                _add_empty_projects_action(projects_menu, window)
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ):
            projects_menu.clear()
            projects_menu.addAction(pin_act)
            projects_menu.addSeparator()
            _add_empty_projects_action(projects_menu, window)

    return rebuild_projects_menu


def _install_open_project_action(
    *,
    file_menu: QMenu,
    window: QMainWindow,
    view: QWebEngineView,
    base_url: str,
    current_path: list[str],
    set_active_project: ProjectActionFn,
    touch_project_recent: ProjectActionFn,
    save_course_parts: ProjectActionFn,
) -> None:
    """
    Installs the “Open project…” action on the File menu and wires it to
    a folder picker.

    This helper lets the user choose a project directory and then loads
    it into the main web view.

    When the action is triggered and a folder is selected, it delegates
    to _open_project_in_view so project state, recents, and course
    parts are updated consistently.

    Args:
        file_menu (QMenu):
            The File menu that will receive the “Open project…” action.
        window (QMainWindow):
            The main application window used as the parent for the file
            dialog and the created action.
        view (QWebEngineView):
            The web view that will display the selected project once it
            has been opened.
        base_url (str):
            The base viewer URL used when navigating to the selected
            project, combined with the encoded project root.
        current_path (list[str]):
            A mutable single-element list holding the currently active
            project root, updated when a new project is chosen.
        set_active_project (ProjectActionFn):
            A callback that records the chosen project as the active one
            in persistent or in-memory state.
        touch_project_recent (ProjectActionFn):
            A callback that updates the
            recent-projects list whenever a new project is opened.
        save_course_parts (ProjectActionFn):
            A callback that persists any course-part metadata associated
            with the newly selected project root.
    """
    open_act: QAction = QAction("&Open project...", window)
    open_act.setShortcut("Ctrl+O")

    def choose_folder() -> None:
        path: str = QFileDialog.getExistingDirectory(
            window,
            "Select a folder with .md files",
            current_path[0],
        )
        if path:
            _open_project_in_view(
                root=path,
                base_url=base_url,
                current_path=current_path,
                set_active_project=set_active_project,
                touch_project_recent=touch_project_recent,
                save_course_parts=save_course_parts,
                view=view,
            )

    open_act.triggered.connect(choose_folder)
    file_menu.addAction(open_act)


def _install_projects_menu(
    *,
    file_menu: QMenu,
    window: QMainWindow,
    view: QWebEngineView,
    base_url: str,
    current_path: list[str],
    load_state_json: LoadStateJsonFn,
    get_projects_state: GetProjectsStateFn,
    set_active_project: ProjectActionFn,
    touch_project_recent: ProjectActionFn,
    save_course_parts: ProjectActionFn,
    toggle_pin_project: Callable[[str], bool],
) -> None:
    """
    Installs the Projects submenu under the File menu and wires its
    behavior.

    This helper adds a pin/unpin action, sets up project listing, and
    keeps the submenu synchronized with persisted project state.

    When the menu is about to be shown, it triggers a rebuild that
    reflects current pinned and recent projects, while the pin action
    updates state and shows a brief status message.

    Args:
        file_menu (QMenu):
            The File menu that will host the Projects submenu and its
            actions.
        window (QMainWindow):
            The main window used as the parent for dynamically created
            menu actions and status messages.
        view (QWebEngineView):
            The web view that will be navigated when project entries are
            selected from the submenu.
        base_url (str):
            The base viewer URL used when opening a project, combined
            with the encoded project root.
        current_path (list[str]):
            A mutable single-element list tracking the currently active
            project root, which the pin action uses to determine which
            project to toggle.
        load_state_json (LoadStateJsonFn):
            A callable that returns the full persisted state dictionary
            from which project metadata is read.
        get_projects_state (GetProjectsStateFn):
            A function that extracts the projects section from the
            persisted state structure.
        set_active_project (ProjectActionFn):
            A callback that records a chosen project as the active one
            in persistent or in-memory state.
        touch_project_recent (ProjectActionFn):
            A callback that updates the recent-projects list whenever a
            project is opened from the menu.
        save_course_parts (ProjectActionFn):
            A callback that persists any course-part metadata associated
            metadata associated with the selected project root.
        toggle_pin_project (Callable[[str], bool]):
            A function that toggles the pinned state of the current
            project root and returns True when the project becomes
            pinned.
    """
    projects_menu: QMenu | None = file_menu.addMenu("&Projects")
    if projects_menu is None:
        return
    pin_act: QAction = QAction("Pin / unpin current project", window)

    def pin_current() -> None:
        """
        Installs the File menu, including project and history submenus,
        on the main window.

        This helper wires up actions for opening projects, pinning
        recent workspaces, and accessing the recent-document history.

        It delegates menu population to helper functions, ensuring the
        File menu stays in sync with persisted state and the web views
        notion of recent documents.

        Args:
            window (QMainWindow):
                The main application window that owns the menu bar and
                provides a parent for created actions.
            menu_bar (QMenuBar):
                The menu bar to which the File menu and its submenus
                will be added.
            view (QWebEngineView):
                The primary web view used for opening projects and
                responding to history menu actions.
            base_url (str):
                The base viewer URL used when navigating the web view to
                a selected project.
            current_path (list[str]):
                A mutable single-element list tracking the currently
                active project root, updated whenever a project is
                opened.
            load_state_json (LoadStateJsonFn):
                A callable that returns the persisted state dictionary
                from which project metadata is loaded.
            get_projects_state (GetProjectsStateFn):
                A function that extracts the projects section from the
                persisted state structure.
            set_active_project (ProjectActionFn):
                A callback that records the chosen project as the active
                one in persistent or in-memory state.
            touch_project_recent (ProjectActionFn):
                A callback that updates the recent-projects list
                whenever a project is opened from the menu.
            save_course_parts (ProjectActionFn):
                A callback that persists any course-part metadata
                associated with the active project root.
            toggle_pin_project (Callable[[str], bool]):
                A function that toggles the pinned state of the current
                project and returns True when the project becomes
                pinned.

        Returns:
            QMenu | None:
                The created File menu instance, or None if the menu
                could not be added to the menu bar.
        """
        try:
            pinned: bool = toggle_pin_project(current_path[0])
            status_bar: QStatusBar | None = window.statusBar()
            if status_bar is not None:
                status_bar.showMessage(
                    "Project pinned" if pinned else "Project unpinned"
                )
        except (OSError, RuntimeError, ValueError):
            return

    pin_act.triggered.connect(pin_current)
    projects_menu.addAction(pin_act)
    projects_menu.addSeparator()
    rebuild_projects_menu: Callable[[], None] = _build_rebuild_projects_menu(
        projects_menu=projects_menu,
        pin_act=pin_act,
        window=window,
        base_url=base_url,
        current_path=current_path,
        view=view,
        load_state_json=load_state_json,
        get_projects_state=get_projects_state,
        set_active_project=set_active_project,
        touch_project_recent=touch_project_recent,
        save_course_parts=save_course_parts,
    )
    projects_menu.aboutToShow.connect(rebuild_projects_menu)


def install_file_menu(
    *,
    window: QMainWindow,
    menu_bar: QMenuBar,
    view: QWebEngineView,
    base_url: str,
    current_path: list[str],
    load_state_json: LoadStateJsonFn,
    get_projects_state: GetProjectsStateFn,
    set_active_project: ProjectActionFn,
    touch_project_recent: ProjectActionFn,
    save_course_parts: ProjectActionFn,
    toggle_pin_project: Callable[[str], bool],
) -> QMenu | None:
    file_menu: QMenu | None = menu_bar.addMenu("&File")
    if file_menu is None:
        return None
    _install_open_project_action(
        file_menu=file_menu,
        window=window,
        view=view,
        base_url=base_url,
        current_path=current_path,
        set_active_project=set_active_project,
        touch_project_recent=touch_project_recent,
        save_course_parts=save_course_parts,
    )
    _install_projects_menu(
        file_menu=file_menu,
        window=window,
        view=view,
        base_url=base_url,
        current_path=current_path,
        load_state_json=load_state_json,
        get_projects_state=get_projects_state,
        set_active_project=set_active_project,
        touch_project_recent=touch_project_recent,
        save_course_parts=save_course_parts,
        toggle_pin_project=toggle_pin_project,
    )
    history_menu: QMenu | None = file_menu.addMenu("&History")
    if history_menu is None:
        return file_menu
    _connect_history_menu(history_menu=history_menu, window=window, view=view)
    return file_menu
