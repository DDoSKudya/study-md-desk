"""
This module manages project-related state and course structure for a
markdown-based application.

It centralizes logic for tracking recent and pinned projects, active
project selection, and indexing course documents from the filesystem.

It defines several TypedDicts (ProjectItem, ProjectsState, CourseDoc,
CoursePart) that describe the shape of project entries, grouped project
state, course documents, and logical course parts.

It provides helper functions to normalize project root paths, convert
arbitrary data into validated project items, normalize metadata
mappings, and parse project state snapshots from a generic state
dictionary.

The ProjectsService class wraps a StateStore instance and offers methods
to save and load project state, set the active project, update the
recent projects list when a project is opened, and toggle the pinned
status of a project while enforcing limits.

ProjectsService also scans the project directory tree for markdown
files, groups them by top-level directory into course parts, and
persists this structure as part of per-project metadata.

In the broader system, this module acts as the bridge between low-level
persisted application state and higher-level UI features like
recent-project lists and course navigation.
"""

import time

from pathlib import Path
from typing import Generator, TypedDict

from viewer_app.runtime.state import StateStore

DEFAULT_RECENT_PROJECTS_LIMIT: int = 18
MIN_RECENT_PROJECTS_LIMIT: int = 3
MAX_PINNED_PROJECTS: int = 30


class ProjectItem(TypedDict):
    """
    Represent a single project entry in the projects list.

    This structure tracks basic identity, recency, and pin status for a
    project.

    Attributes:
        root (str):
            Absolute, normalized filesystem path to the project root
            directory.
        name (str):
            Human-readable label for the project, typically derived from
            the root directory name.
        lastOpened (int):
            Timestamp in milliseconds since the Unix epoch indicating
            when the project was last opened.
        pinned (bool):
            Flag that marks whether the project is pinned and should be
            kept at the top of project lists.
    """

    root: str
    name: str
    lastOpened: int
    pinned: bool


class ProjectsState(TypedDict):
    """
    Represent the grouped state of pinned and recent projects.

    This structure keeps separate ordered lists for favorited projects
    and those accessed most recently.

    Attributes:
        pinned (list[ProjectItem]):
            Ordered list of project entries that are explicitly pinned
            and should appear first in project selectors.
        recent:
            (list[ProjectItem]):
                Ordered list of non-pinned project entries, sorted by
                most recently opened and subject to a configurable size
                limit.
    """

    pinned: list[ProjectItem]
    recent: list[ProjectItem]


class CourseDoc(TypedDict):
    """
    Represent a single course document within a project.

    This structure captures the file location and a human-friendly title
    for a markdown lesson or resource.

    Attributes:
        path (str):
            Project-relative path to the markdown document, typically
            using POSIX-style separators.
        title (str):
            Human-readable title derived from the document path or
            metadata, suitable for display in course navigation.
    """

    path: str
    title: str


class CoursePart(TypedDict):
    """
    Represent a logical grouping of course documents within a project.

    This structure models a section or part of a course, identified by a
    key and title, and containing a list of markdown documents.

    Attributes:
        key (str):
            Identifier for the course part, often derived from the
            top-level directory name that contains its  documents.
        title (str):
            Human-readable name for the course part, suitable for
            display in navigation or headings.
        docs (list[CourseDoc]):
            Ordered collection of course documents that belong to this
            part, each with its own relative path and display title.
    """

    key: str
    title: str
    docs: list[CourseDoc]


def normalize_project_root(root: str) -> str:
    """
    Normalize a project root path into a canonical string form.

    This helper cleans up empty or whitespace-only inputs and resolves
    valid paths to an absolute location.

    Args:
        root (str):
            Raw project root path, which may be empty, contain
            whitespace, user home shortcuts, or relative segments.

    Returns:
        str:
            A normalized absolute path string when resolution succeeds,
            or a best-effort cleaned version of the input when
            normalization fails or the path is empty.
    """
    normalized: str = (root or "").strip()
    if not normalized:
        return ""
    try:
        return str(Path(normalized).expanduser().resolve())
    except OSError:
        return normalized


def _to_project_item(value: object) -> ProjectItem | None:
    """
    Convert an arbitrary value into a normalized project item mapping.

    This helper validates input structure, normalizes the project root,
    and fills in default metadata when fields are missing or malformed.

    Args:
        value (object):
            Raw value expected to contain project metadata, typically a
            dictionary with "root", "name", "lastOpened", and "pinned"
            keys.

    Returns:
        ProjectItem | None:
            A fully populated ProjectItem dictionary when the input can
            be interpreted as a valid project entry, or None if the
            value is not a suitable mapping or does not contain a usable
            root path.
    """
    if not isinstance(value, dict):
        return None
    root: str = normalize_project_root(root=str(value.get("root") or ""))
    if not root:
        return None
    name: str = str(value.get("name") or Path(root).name or root)
    try:
        last_opened: int = int(value.get("lastOpened") or 0)
    except (TypeError, ValueError):
        last_opened = 0
    pinned: bool = bool(value.get("pinned", False))
    return {
        "root": root,
        "name": name,
        "lastOpened": last_opened,
        "pinned": pinned,
    }


def _to_meta_mapping(value: object) -> dict[str, dict[str, object]]:
    """
    Convert a raw mapping of project metadata into a normalized form.

    This helper cleans up root keys, filters invalid entries, and copies
    nested metadata dictionaries.

    Args:
        value (object):
            Input value expected to be a mapping from project root
            strings to dictionaries containing arbitrary metadata
            fields.

    Returns:
        dict[str, dict[str, object]]:
            A new dictionary keyed by normalized project root paths,
            where each value is a shallow copy of the original metadata
            mapping for that project. If the input is not a suitable
            mapping, an empty dictionary is returned.
    """
    if not isinstance(value, dict):
        return {}
    output: dict[str, dict[str, object]] = {}
    for key, item in value.items():
        root: str = normalize_project_root(root=str(key or ""))
        if not root or not isinstance(item, dict):
            continue
        output[root] = dict(item)
    return output


def _parse_projects_state_from_snapshot(
    state_snapshot: dict[str, object],
) -> ProjectsState:
    """
    Parse a raw state snapshot into a structured projects state mapping.

    This helper normalizes the pinned and recent project lists,
    filtering out invalid or malformed entries.

    Args:
        state_snapshot (dict[str, object]):
            Full application state snapshot that may contain a
            "projects" key with nested "pinned" and "recent" project
            lists.

    Returns:
        ProjectsState:
            A dictionary with "pinned" and "recent" keys, where each
            value is a list of normalized ProjectItem entries derived
            from the snapshot. Any non-list or invalid items in the
            original snapshot are ignored.
    """
    projects_raw = state_snapshot.get("projects")
    projects_raw = projects_raw if isinstance(projects_raw, dict) else {}
    pinned_raw = projects_raw.get("pinned")
    recent_raw = projects_raw.get("recent")
    return {
        "pinned": [
            item
            for item in (
                _to_project_item(value)
                for value in (
                    pinned_raw if isinstance(pinned_raw, list) else []
                )
            )
            if item is not None
        ],
        "recent": [
            item
            for item in (
                _to_project_item(value)
                for value in (
                    recent_raw if isinstance(recent_raw, list) else []
                )
            )
            if item is not None
        ],
    }


class ProjectsService:
    """
    Provide high-level operations for managing projects and course data.

    This service coordinates updates to the shared state store,
    including recent and pinned projects, active selection, and course
    indexing.

    # Methods:

        _save_projects_state(
            projects_state: ProjectsState,
        ) -> None:
            Persist the current projects state into the shared
            application store by writing normalized pinned and recent
            lists.

        _load_meta_by_root(
            state_snapshot: dict[str, object],
        ) -> dict[str, dict[str, object]]:
            Load normalized per-project metadata from a raw state
            snapshot, keyed by canonical project roots.

        _save_meta_by_root(
            meta_by_root: dict[str, dict[str, object]],
        ) -> None:
            Store the provided per-project metadata mapping back into
            the shared application state under a dedicated key.

        set_active(
            root: str,
        ) -> None:
            Mark a project as the active one in the application state
            using a normalized root path.

        touch_recent(
            root: str,
            name: str | None,
            limit: int,
        ) -> None:
            Record that a project was opened, update its metadata, and
            move it to the front of the bounded recent projects list.

        toggle_pin(
            root: str,
        ) -> bool:
            Flip the pinned status of a project, synchronizing pinned
            and recent lists and enforcing the maximum pin limit.

        index_course_parts(
            project_root: str,
        ) -> list[CoursePart]:
            Discover markdown documents under a project root and group
            them into logical course parts with display titles.

        save_course_parts(
            root: str,
        ) -> None:
            Recompute and persist the course parts index for a given
            project, storing the result in its metadata entry.
    """

    def __init__(self, state_store: StateStore) -> None:
        self._state_store: StateStore = state_store

    def _save_projects_state(self, projects_state: ProjectsState) -> None:
        """
        Persist the current projects state into the shared application
        store.

        This helper writes pinned and recent project lists back to the
        state snapshot as plain dictionaries suitable for
        serialization.

        Args:
            projects_state (ProjectsState):
                Structured projects state containing "pinned" and
                "recent" lists, each made up of ProjectItem entries to
                be saved under the "projects" key in the state store.
        """
        self._state_store.update(
            patch={
                "projects": {
                    "pinned": [
                        dict(item) for item in projects_state["pinned"]
                    ],
                    "recent": [
                        dict(item) for item in projects_state["recent"]
                    ],
                }
            }
        )

    def _load_meta_by_root(
        self, state_snapshot: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        """
        Load per-project metadata keyed by normalized project roots.

        This helper extracts the raw metadata mapping from a state
        snapshot and normalizes its keys and values into a consistent
        structure.

        Args:
            state_snapshot (dict[str, object]):
                Full application state snapshot that may contain a
                "projectMetaByRoot" mapping from project roots to
                metadata dictionaries.

        Returns:
            dict[str, dict[str, object]]:
                A dictionary keyed by normalized project root paths,
                where each value is a shallow copy of the corresponding
                metadata mapping. If the snapshot does not contain a
                suitable mapping, an empty dictionary is returned.
        """
        return _to_meta_mapping(value=state_snapshot.get("projectMetaByRoot"))

    def _save_meta_by_root(
        self, meta_by_root: dict[str, dict[str, object]]
    ) -> None:
        """
        Store per-project metadata in the shared application state.

        This helper writes the normalized project metadata mapping back
        into the state store under a dedicated key.

        Args:
            meta_by_root (dict[str, dict[str, object]]):
                Mapping from normalized project root paths to shallow
                metadata dictionaries that should be persisted as the
                latest metadata snapshot.
        """
        self._state_store.update(patch={"projectMetaByRoot": meta_by_root})

    def set_active(self, root: str) -> None:
        """
        Mark a project as the active project in the application state.

        This method normalizes the provided root path and stores it
        under a dedicated key for later retrieval.

        Args:
            root (str):
                Raw filesystem path to the project root that should
                become the currently active project in the shared
                state.
        """
        self._state_store.update(
            patch={"activeProjectRoot": normalize_project_root(root)}
        )

    def touch_recent(
        self,
        root: str,
        name: str | None = None,
        limit: int = DEFAULT_RECENT_PROJECTS_LIMIT,
    ) -> None:
        """
        Update the recent projects list and metadata when a project is
        opened.

        This method records the open time, moves the project to the
        front of the recent list, and maintains per-project metadata
        such as name and timestamps.

        Args:
            root (str):
                Raw filesystem path to the project root that was just
                opened and should be tracked in the recent projects
                list.
            name (str | None):
                Optional human-readable project name to store alongside
                the root; when omitted, the name is derived from the
                root path.
            limit (int):
                Maximum number of recent projects to retain, subject to
                a configured minimum, with older entries dropped when
                the limit is exceeded.
        """
        normalized_root: str = normalize_project_root(root)
        if not normalized_root:
            return
        now_ms: int = int(time.time() * 1000)
        state_snapshot: dict[str, object] = self._state_store.load()
        projects_state: ProjectsState = _parse_projects_state_from_snapshot(
            state_snapshot
        )
        current_project: ProjectItem = {
            "root": normalized_root,
            "name": (name or Path(normalized_root).name or normalized_root),
            "lastOpened": now_ms,
            "pinned": False,
        }
        pinned_roots: set[str] = {
            item["root"] for item in projects_state["pinned"]
        }
        filtered_recent: list[ProjectItem] = []
        for item in projects_state["recent"]:
            current_root: str = item["root"]
            if (
                not current_root
                or current_root == normalized_root
                or current_root in pinned_roots
            ):
                continue
            filtered_recent.append(item)
        filtered_recent.insert(0, current_project)
        recent_limit: int = max(MIN_RECENT_PROJECTS_LIMIT, limit)
        projects_state["recent"] = filtered_recent[:recent_limit]
        self._save_projects_state(projects_state)
        meta_by_root: dict[str, dict[str, object]] = self._load_meta_by_root(
            state_snapshot
        )
        current_meta: dict[str, object] = dict[str, object](
            meta_by_root.get(normalized_root, {})
        )
        if name:
            current_meta["name"] = name
        current_meta["lastOpened"] = now_ms
        current_meta.setdefault("createdAt", now_ms)
        meta_by_root[normalized_root] = current_meta
        self._save_meta_by_root(meta_by_root)

    def toggle_pin(self, root: str) -> bool:
        """
        Toggle the pinned status of a project in the projects state.

        This method either adds a project to the pinned list or removes
        it if it is already pinned, keeping related lists consistent.

        Args:
            root (str):
                Raw filesystem path to the project root whose pinned
                status should be toggled in the shared projects state.

        Returns:
            bool:
                True if the project becomes pinned as a result of this
                call, or False if it was already pinned and is now
                unpinned, or if the root cannot be normalized.
        """
        normalized_root: str = normalize_project_root(root)
        if not normalized_root:
            return False
        now_ms: int = int(time.time() * 1000)
        state_snapshot: dict[str, object] = self._state_store.load()
        projects_state: ProjectsState = _parse_projects_state_from_snapshot(
            state_snapshot
        )
        pinned_projects: list[ProjectItem] = list(projects_state["pinned"])
        for index, item in enumerate(pinned_projects):
            if item["root"] == normalized_root:
                pinned_projects.pop(index)
                projects_state["pinned"] = pinned_projects
                self._save_projects_state(projects_state)
                return False
        pinned_projects.insert(
            0,
            {
                "root": normalized_root,
                "name": Path(normalized_root).name or normalized_root,
                "lastOpened": now_ms,
                "pinned": True,
            },
        )
        projects_state["pinned"] = pinned_projects[:MAX_PINNED_PROJECTS]
        projects_state["recent"] = [
            item
            for item in projects_state["recent"]
            if item["root"] != normalized_root
        ]
        self._save_projects_state(projects_state)
        return True

    def index_course_parts(self, project_root: str) -> list[CoursePart]:
        """
        Index markdown course documents within a project into logical
        parts.

        This method scans the project directory tree, groups markdown
        files by their top-level folder, and returns a structured list
        suitable for navigation.

        Args:
            project_root (str):
                Raw filesystem path to the project root whose markdown
                files should be discovered and organized into course
                parts.

        Returns:
            list[CoursePart]:
                A list of course parts, each containing a key, a display
                title, and an ordered collection of document entries
                with relative paths and human-friendly titles. If the
                root does not exist, is not a directory, or cannot be
                scanned, an empty list is returned.
        """
        normalized_root: str = normalize_project_root(root=project_root)
        root_path: Path = Path(normalized_root)
        if not root_path.exists() or not root_path.is_dir():
            return []
        parts_by_top_dir: dict[str, list[str]] = {}
        try:
            markdown_paths: Generator[Path, None, None] = root_path.rglob(
                pattern="*.md"
            )
            for markdown_path in markdown_paths:
                try:
                    relative_path: str = markdown_path.relative_to(
                        root_path
                    ).as_posix()
                except ValueError:
                    continue
                top_dir: str = (
                    relative_path.split("/", 1)[0]
                    if "/" in relative_path
                    else ""
                )
                parts_by_top_dir.setdefault(top_dir, []).append(relative_path)
        except OSError:
            return []
        course_parts: list[CoursePart] = []
        sorted_top_dirs: list[str] = sorted(
            parts_by_top_dir.keys(),
            key=lambda value: (value == "", value.lower()),
        )
        for top_dir in sorted_top_dirs:
            docs: list[str] = sorted(parts_by_top_dir[top_dir])
            course_parts.append(
                {
                    "key": top_dir,
                    "title": ("Main" if top_dir == "" else top_dir),
                    "docs": [
                        {
                            "path": doc_path,
                            "title": Path(doc_path).stem.replace("_", " "),
                        }
                        for doc_path in docs
                    ],
                }
            )
        return course_parts

    def save_course_parts(self, root: str) -> None:
        """
        Scan a project for course parts and save them into project
        metadata.

        This method refreshes the indexed list of course documents for a
        given project and persists the result under its metadata entry.

        Args:
            root (str):
                Raw filesystem path to the project root whose course
                parts should be discovered, normalized, and stored in
                the shared metadata mapping.
        """
        normalized_root: str = normalize_project_root(root)
        if not normalized_root:
            return
        state_snapshot: dict[str, object] = self._state_store.load()
        meta_by_root: dict[str, dict[str, object]] = self._load_meta_by_root(
            state_snapshot
        )
        current_meta: dict[str, object] = dict(
            meta_by_root.get(normalized_root, {})
        )
        current_meta["courseParts"] = self.index_course_parts(
            project_root=normalized_root
        )
        meta_by_root[normalized_root] = current_meta
        self._save_meta_by_root(meta_by_root)
