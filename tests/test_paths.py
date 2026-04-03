import pytest
from pathlib import Path

from viewer_app.runtime.paths import (
    _default_runtime_home,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.paths import (
    _discover_app_root,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.paths import (
    _is_repository_root,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.paths import (
    _prefer_existing_file,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.paths import (
    BIN_DIRECTORY_NAME,
    PLANS_DIRECTORY_NAME,
    RUNTIME_HOME_ENV_VAR,
    TTS_MODELS_DIRECTORY_NAME,
    VIEWER_APP_DIRECTORY_NAME,
)

_OVERLAY_FILE_NAME: str = "f.txt"


def test_default_runtime_home_matches_app_root_when_env_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_HOME_ENV_VAR, raising=False)

    resolved = _default_runtime_home(tmp_path)

    assert resolved == tmp_path  # noqa: S101


def test_default_runtime_home_respects_runtime_home_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_home = tmp_path / "runtime_home"
    runtime_home.mkdir()
    monkeypatch.setenv(RUNTIME_HOME_ENV_VAR, str(runtime_home))

    resolved = _default_runtime_home(tmp_path)

    assert resolved == runtime_home.resolve()  # noqa: S101


def test_is_repository_root_true_when_viewer_app_directory_present(
    tmp_path: Path,
) -> None:
    (tmp_path / VIEWER_APP_DIRECTORY_NAME).mkdir()

    assert _is_repository_root(tmp_path) is True  # noqa: S101


def test_is_repository_root_false_without_viewer_app_directory(
    tmp_path: Path,
) -> None:
    assert _is_repository_root(tmp_path) is False  # noqa: S101


def test_discover_app_root_resolves_repo_from_file_under_viewer_app(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repo"
    viewer_app_dir = repository_root / VIEWER_APP_DIRECTORY_NAME
    viewer_app_dir.mkdir(parents=True)
    source_file = viewer_app_dir / "x.py"

    discovered = _discover_app_root(source_file)

    assert discovered == repository_root.resolve()  # noqa: S101


def test_discover_app_root_returns_absolute_path_for_orphan_file(
    tmp_path: Path,
) -> None:
    orphan_file = tmp_path / "orphan.py"
    orphan_file.write_text("# x", encoding="utf-8")

    discovered = _discover_app_root(orphan_file)

    assert discovered.is_absolute()  # noqa: S101


def test_discover_app_root_returns_repo_when_anchor_is_repository_root(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repo"
    (repository_root / VIEWER_APP_DIRECTORY_NAME).mkdir(
        parents=True,
        exist_ok=True,
    )

    discovered = _discover_app_root(repository_root)

    assert discovered == repository_root.resolve()  # noqa: S101


def test_prefer_existing_file_prefers_runtime_copy_when_both_exist(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime_home"
    resources_root = tmp_path / "resources_root"
    runtime_home.mkdir()
    resources_root.mkdir()
    (runtime_home / _OVERLAY_FILE_NAME).write_text("a", encoding="utf-8")
    (resources_root / _OVERLAY_FILE_NAME).write_text("b", encoding="utf-8")

    chosen = _prefer_existing_file(
        runtime_home,
        resources_root,
        _OVERLAY_FILE_NAME,
    )

    assert chosen == runtime_home / _OVERLAY_FILE_NAME  # noqa: S101


def test_prefer_existing_file_falls_back_to_resources_when_runtime_missing(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime_home"
    resources_root = tmp_path / "resources_root"
    runtime_home.mkdir()
    resources_root.mkdir()
    (resources_root / _OVERLAY_FILE_NAME).write_text("b", encoding="utf-8")

    chosen = _prefer_existing_file(
        runtime_home,
        resources_root,
        _OVERLAY_FILE_NAME,
    )

    assert chosen == resources_root / _OVERLAY_FILE_NAME  # noqa: S101


def test_app_paths_ensure_runtime_dirs_and_plans_fallback_without_plans_folder(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_plans=False, with_ini=False)

    paths.ensure_runtime_dirs()

    assert paths.settings_path.parent.is_dir()  # noqa: S101
    assert (  # noqa: S101
        paths.bundled_bin_root == paths.resources_root / BIN_DIRECTORY_NAME
    )
    assert paths.tts_models_root == (  # noqa: S101
        paths.resources_root / TTS_MODELS_DIRECTORY_NAME
    )
    assert paths.plans_dir == paths.resources_root  # noqa: S101


def test_app_paths_plans_dir_points_at_plans_under_resources_when_present(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_plans=True)

    assert (paths.resources_root / PLANS_DIRECTORY_NAME).is_dir()  # noqa: S101
    assert (  # noqa: S101
        paths.plans_dir == paths.resources_root / PLANS_DIRECTORY_NAME
    )
