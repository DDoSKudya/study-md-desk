import json

import pytest

from viewer_app.runtime.config import (
    _normalize_prompt_templates,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.config import (
    INI_KEY_APP_SUBTITLE,
    INI_KEY_APP_TITLE,
    INI_KEY_ROOT_DIR,
    effective_explain_prompt_key,
    get_app_config_dict,
    load_app_config,
    load_prompt_templates,
    save_app_config,
    update_app_config_key,
)
from viewer_app.runtime.paths import PROMPTS_FILE_NAME

_INI_KEY_EXPLAIN_PROMPT: str = "explainPromptKey"
_PROMPT_TEMPLATE_KEY_EN: str = "explain_en"
_PROMPT_TEMPLATE_KEY_RU: str = "explain_ru"


def test_load_app_config_when_settings_missing_uses_default_plans_dir(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_ini=False)

    config = load_app_config(paths)

    assert config.plans_dir == paths.plans_dir  # noqa: S101


def test_save_and_load_app_config_round_trips_root_and_titles(
    app_paths_factory,
    tmp_path,
) -> None:
    study_root = tmp_path / "study_docs"
    study_root.mkdir()
    expected_title = "My Study App"
    expected_subtitle = "Read and learn"
    paths = app_paths_factory(with_ini=True)

    save_app_config(
        paths,
        {
            INI_KEY_ROOT_DIR: str(study_root),
            INI_KEY_APP_TITLE: expected_title,
            INI_KEY_APP_SUBTITLE: expected_subtitle,
        },
    )
    config = load_app_config(paths)

    assert config.plans_dir == study_root.resolve()  # noqa: S101
    assert config.app_title == expected_title  # noqa: S101
    assert config.app_subtitle == expected_subtitle  # noqa: S101


def test_update_app_config_key_persists_single_field(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_ini=True)
    updated_title = "Updated title"

    update_app_config_key(paths, INI_KEY_APP_TITLE, updated_title)
    config = load_app_config(paths)

    assert config.app_title == updated_title  # noqa: S101


def test_get_app_config_dict_includes_saved_custom_entries(
    app_paths_factory,
) -> None:
    arbitrary_key = "customKey"
    stored_key_lower = arbitrary_key.lower()
    arbitrary_value = "customValue"
    paths = app_paths_factory(with_ini=True)

    save_app_config(paths, {arbitrary_key: arbitrary_value})
    items = get_app_config_dict(paths)["items"]

    assert (stored_key_lower, arbitrary_value) in items  # noqa: S101


def test_effective_explain_prompt_key_accepts_allowed_values_only(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_ini=True)

    save_app_config(paths, {_INI_KEY_EXPLAIN_PROMPT: _PROMPT_TEMPLATE_KEY_EN})
    assert (  # noqa: S101
        effective_explain_prompt_key(paths) == _PROMPT_TEMPLATE_KEY_EN
    )

    save_app_config(paths, {_INI_KEY_EXPLAIN_PROMPT: "unknown_key"})
    assert (  # noqa: S101
        effective_explain_prompt_key(paths) == _PROMPT_TEMPLATE_KEY_RU
    )


def test_load_prompt_templates_when_file_missing_uses_built_in_defaults(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()

    templates = load_prompt_templates(paths)

    assert _PROMPT_TEMPLATE_KEY_RU in templates  # noqa: S101


def test_load_prompt_templates_reads_runtime_prompts_json(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    prompts_file = paths.runtime_home / PROMPTS_FILE_NAME
    expected_ru_body = "Russian template body"
    expected_en_body = "English template body"
    prompts_file.write_text(
        json.dumps(
            {
                _PROMPT_TEMPLATE_KEY_RU: expected_ru_body,
                _PROMPT_TEMPLATE_KEY_EN: expected_en_body,
            }
        ),
        encoding="utf-8",
    )

    templates = load_prompt_templates(paths)

    assert templates[_PROMPT_TEMPLATE_KEY_RU] == expected_ru_body  # noqa: S101


@pytest.mark.parametrize("raw_templates", [None, {}])
def test_normalize_prompt_templates_returns_fallback_when_empty(
    raw_templates: dict[str, str] | None,
) -> None:
    fallback = {"a": "1"}

    result = _normalize_prompt_templates(raw_templates, fallback)

    assert result == fallback  # noqa: S101


def test_normalize_prompt_templates_coerces_values_to_strings() -> None:
    fallback = {"a": "1"}

    result = _normalize_prompt_templates({"k": 1}, fallback)

    assert result == {"k": "1"}  # noqa: S101
