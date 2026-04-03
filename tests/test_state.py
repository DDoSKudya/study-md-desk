from __future__ import annotations

import json

from viewer_app.runtime.state import DEFAULT_STATE, StateStore

_INVALID_JSON_FILE_BODY: str = "not json"
_JSON_NON_OBJECT_SNAPSHOT: str = "[1,2]"
_ROUNDTRIP_CURRENT_DOC_VALUE: str = "x"
_UPDATE_PATCH_CURRENT_DOC: str = "doc1"


def test_state_store_load_returns_default_when_file_missing(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    state_store = StateStore(paths)

    loaded = state_store.load()

    assert loaded == DEFAULT_STATE  # noqa: S101


def test_state_store_save_and_load_round_trips_current_doc(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    paths.ensure_runtime_dirs()
    state_store = StateStore(paths)
    modified_state = dict(DEFAULT_STATE)
    modified_state["currentDoc"] = _ROUNDTRIP_CURRENT_DOC_VALUE

    state_store.save(modified_state)
    roundtripped = state_store.load()

    assert (  # noqa: S101
        roundtripped["currentDoc"] == _ROUNDTRIP_CURRENT_DOC_VALUE
    )


def test_state_store_load_returns_default_when_file_is_not_json(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    paths.ensure_runtime_dirs()
    paths.state_path.write_text(_INVALID_JSON_FILE_BODY, encoding="utf-8")
    state_store = StateStore(paths)

    loaded = state_store.load()

    assert loaded == DEFAULT_STATE  # noqa: S101


def test_state_store_load_returns_default_when_json_root_is_not_object(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    paths.ensure_runtime_dirs()
    paths.state_path.write_text(_JSON_NON_OBJECT_SNAPSHOT, encoding="utf-8")
    state_store = StateStore(paths)

    loaded = state_store.load()

    assert loaded == DEFAULT_STATE  # noqa: S101


def test_state_store_update_merges_patch_and_persists_to_disk(
    app_paths_factory,
) -> None:
    paths = app_paths_factory()
    paths.ensure_runtime_dirs()
    state_store = StateStore(paths)

    merged = state_store.update({"currentDoc": _UPDATE_PATCH_CURRENT_DOC})

    assert merged["currentDoc"] == _UPDATE_PATCH_CURRENT_DOC  # noqa: S101
    persisted = json.loads(paths.state_path.read_text(encoding="utf-8"))
    assert persisted["currentDoc"] == _UPDATE_PATCH_CURRENT_DOC  # noqa: S101
