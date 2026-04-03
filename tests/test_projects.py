import pytest
from pathlib import Path
from typing import Any, cast

from viewer_app.runtime.paths import AppPaths
from viewer_app.runtime.projects import (
    _parse_projects_state_from_snapshot,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.projects import (
    _to_project_item,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.projects import (
    ProjectsService,
    normalize_project_root,
)
from viewer_app.runtime.state import StateStore

_COURSE_DISPLAY_NAME: str = "MyCourse"


def _app_paths_with_isolated_runtime(base_dir: Path) -> AppPaths:
    return AppPaths(
        app_root=base_dir,
        runtime_home=base_dir / "runtime_home",
        resources_root=base_dir,
        cache_root=base_dir / "cache",
        profile_root=base_dir / "profile",
    )


@pytest.mark.parametrize("blank_input", ["", "  "])
def test_normalize_project_root_maps_blank_string_to_empty(
    blank_input: str,
) -> None:
    assert normalize_project_root(blank_input) == ""  # noqa: S101


def test_normalize_project_root_resolves_existing_directory(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    normalized = normalize_project_root(str(project_dir))

    assert normalized == str(project_dir.resolve())  # noqa: S101


@pytest.mark.parametrize("invalid_payload", [None, {"root": ""}])
def test_to_project_item_returns_none_for_invalid_payload(
    invalid_payload: Any,
) -> None:
    assert _to_project_item(invalid_payload) is None  # noqa: S101


def test_parse_projects_state_from_snapshot_defaults_empty_lists() -> None:
    parsed = _parse_projects_state_from_snapshot({})

    assert parsed["pinned"] == []  # noqa: S101
    assert parsed["recent"] == []  # noqa: S101


def test_touch_recent_records_project_without_setting_active_root(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    paths.ensure_runtime_dirs()
    state_store = StateStore(paths)
    projects_service = ProjectsService(state_store)
    plans_root = str(paths.plans_dir.resolve())

    projects_service.touch_recent(plans_root, name=_COURSE_DISPLAY_NAME)

    state = state_store.load()
    assert state["activeProjectRoot"] == ""  # noqa: S101
    projects_block = cast(dict[str, Any], state["projects"])
    recent_entries = cast(list[Any], projects_block["recent"])
    assert any(  # noqa: S101
        entry.get("root") == plans_root for entry in recent_entries
    )

    assert projects_service.toggle_pin(plans_root) is True  # noqa: S101
    assert projects_service.toggle_pin(plans_root) is False  # noqa: S101


def test_save_course_parts_persists_project_metadata(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    (project_root / "l.md").write_text("# L", encoding="utf-8")
    app_paths = _app_paths_with_isolated_runtime(tmp_path)
    app_paths.runtime_home.mkdir(parents=True, exist_ok=True)
    state_store = StateStore(app_paths)
    projects_service = ProjectsService(state_store)

    projects_service.save_course_parts(str(project_root))

    state = state_store.load()
    meta_by_root = state.get("projectMetaByRoot") or {}

    assert meta_by_root  # noqa: S101


def test_index_course_parts_discovers_parts_or_root_key(
    tmp_path: Path,
) -> None:
    course_root = tmp_path / "proj"
    (course_root / "partA").mkdir(parents=True)
    (course_root / "partB").mkdir(parents=True)
    (course_root / "root.md").write_text("# r", encoding="utf-8")
    (course_root / "partA" / "a.md").write_text("# a", encoding="utf-8")
    (course_root / "partB" / "b.md").write_text("# b", encoding="utf-8")
    app_paths = _app_paths_with_isolated_runtime(tmp_path)
    app_paths.runtime_home.mkdir(parents=True, exist_ok=True)
    state_store = StateStore(app_paths)
    projects_service = ProjectsService(state_store)

    parts = projects_service.index_course_parts(str(course_root))
    part_keys = {part["key"] for part in parts}

    assert "" in part_keys or "partA" in part_keys  # noqa: S101
