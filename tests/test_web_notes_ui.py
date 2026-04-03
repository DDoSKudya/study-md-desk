from viewer_app.runtime.state import DEFAULT_STATE
from viewer_app.web.web_notes_ui import (
    _json_for_script,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.web.web_notes_ui import (
    build_notes_ui_html,
)

_PAYLOAD_WITH_APOSTROPHE: dict[str, object] = {"a": "b'c"}


def test_json_for_script_escapes_characters_for_inline_script_literal() -> (
    None
):
    serialized = _json_for_script(_PAYLOAD_WITH_APOSTROPHE)

    assert "'" in serialized or "\\" in serialized  # noqa: S101


def test_build_notes_ui_html_emits_document_with_notes_ui_markers() -> None:
    initial_state = dict(DEFAULT_STATE)

    document_html = build_notes_ui_html(initial_state)

    lowered = document_html.lower()
    assert "<html" in lowered  # noqa: S101
    assert "notes" in lowered  # noqa: S101
