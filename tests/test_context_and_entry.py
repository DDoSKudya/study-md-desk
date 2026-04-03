import study_md_desk
from viewer_app.app import main as viewer_app_main
from viewer_app.app.context import AppContext, get_app_context
from viewer_app.runtime.config import load_app_config
from viewer_app.runtime.projects import ProjectsService
from viewer_app.runtime.state import StateStore


def test_study_md_desk_package_exposes_callable_main() -> None:
    assert callable(study_md_desk.main)  # noqa: S101


def test_get_app_context_returns_same_instance_on_repeated_calls() -> None:
    get_app_context.cache_clear()
    try:
        first_call = get_app_context()
        second_call = get_app_context()

        assert isinstance(first_call, AppContext)  # noqa: S101
        assert first_call is second_call  # noqa: S101
    finally:
        get_app_context.cache_clear()


def test_app_context_reload_config_keeps_plans_dir_consistent(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_ini=True)
    paths.ensure_runtime_dirs()
    state = StateStore(paths)
    context = AppContext(
        paths=paths,
        config=load_app_config(paths),
        state=state,
        projects=ProjectsService(state),
    )

    reloaded_config = context.reload_config()

    assert reloaded_config.plans_dir == context.config.plans_dir  # noqa: S101


def test_viewer_app_main_starts_desktop_after_resolving_context(
    mocker,
) -> None:
    mocker.patch("viewer_app.app.main.get_app_context")
    run_desktop = mocker.patch("viewer_app.app.main.run_desktop_app")

    viewer_app_main.main()

    run_desktop.assert_called_once()
