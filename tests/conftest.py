from collections.abc import Callable, Iterator
from functools import partial

import pytest
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from viewer_app.runtime.paths import AppPaths
    from viewer_app.runtime.server_runtime import RunningServer

_HTTP_TEST_FIRST_BIND_PORT: int = 41200
_HTTP_TEST_BIND_PORT_CEILING: int = 41290

_TEST_APP_ROOT_DIR_NAME: str = "app_root"
_TEST_RUNTIME_DIR_NAME: str = "runtime"

_SAMPLE_PLANS_INDEX_BODY: str = "# Index\n\nHello."


def _stop_threaded_http_server(running_server: RunningServer) -> None:
    running_server.server.shutdown()
    running_server.server.server_close()


def _minimal_app_ini_content(root_dir: str | None) -> str:
    lines = ["[app]"]
    if root_dir:
        lines.append(f"rootDir = {root_dir}")
    return "\n".join(lines) + "\n"


def _write_sample_plans_index(app_root: Path) -> None:
    from viewer_app.runtime.paths import PLANS_DIRECTORY_NAME

    plans_dir = app_root / PLANS_DIRECTORY_NAME
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "index.md").write_text(
        _SAMPLE_PLANS_INDEX_BODY,
        encoding="utf-8",
    )


def build_isolated_app_paths(
    base_dir: Path,
    *,
    with_plans: bool = True,
    with_ini: bool = False,
    root_dir: str | None = None,
) -> AppPaths:
    from viewer_app.runtime.paths import (
        SETTINGS_FILE_NAME,
        WEB_PROFILE_CACHE_DIRECTORY,
        WEB_PROFILE_STORAGE_DIRECTORY,
        AppPaths,
    )

    app_root = base_dir / _TEST_APP_ROOT_DIR_NAME
    app_root.mkdir(parents=True, exist_ok=True)
    runtime_home = base_dir / _TEST_RUNTIME_DIR_NAME
    runtime_home.mkdir(parents=True, exist_ok=True)

    if with_plans:
        _write_sample_plans_index(app_root)
    if with_ini:
        (runtime_home / SETTINGS_FILE_NAME).write_text(
            _minimal_app_ini_content(root_dir),
            encoding="utf-8",
        )

    resolved_app_root = app_root.resolve()
    resolved_runtime = runtime_home.resolve()
    return AppPaths(
        app_root=resolved_app_root,
        runtime_home=resolved_runtime,
        resources_root=resolved_app_root,
        cache_root=(runtime_home / WEB_PROFILE_CACHE_DIRECTORY).resolve(),
        profile_root=(runtime_home / WEB_PROFILE_STORAGE_DIRECTORY).resolve(),
    )


@pytest.fixture(scope="session")
def http_server() -> Iterator[str]:
    from viewer_app.http.http_server import Handler, ThreadedServer
    from viewer_app.runtime.server_runtime import start_local_server

    running = start_local_server(
        ThreadedServer,
        Handler,
        _HTTP_TEST_FIRST_BIND_PORT,
        _HTTP_TEST_BIND_PORT_CEILING,
    )
    try:
        yield running.base_url.rstrip("/")
    finally:
        _stop_threaded_http_server(running)


@pytest.fixture(scope="session", autouse=True)
def _shutdown_shared_python_runner() -> (  # pyright: ignore[reportUnusedFunction]
    Iterator[None]
):
    yield
    from viewer_app.runtime.python_runner import shutdown_runner_executor

    shutdown_runner_executor()


@pytest.fixture
def app_paths_factory(tmp_path: Path) -> Callable[..., AppPaths]:
    return partial(build_isolated_app_paths, tmp_path)
