import types

import pytest
from typing import Any

from viewer_app.web import web_notes_ui as notes_ui

_FALLBACK_INLINE_JSON: str = "{}"
_SAMPLE_STATE_PAYLOAD: dict[str, object] = {"a": 1}


def _json_dumps_that_raises_type_error(
    *_args: Any,
    **_kwargs: Any,
) -> str:
    raise TypeError("simulated json.dumps failure")


def test_json_for_script_returns_empty_object_when_dumps_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_json_module = types.SimpleNamespace(
        dumps=_json_dumps_that_raises_type_error,
    )
    monkeypatch.setattr(notes_ui, "json", stub_json_module)

    serialized = (
        notes_ui._json_for_script(  # pyright: ignore[reportPrivateUsage]
            _SAMPLE_STATE_PAYLOAD
        )
    )

    assert serialized == _FALLBACK_INLINE_JSON  # noqa: S101
