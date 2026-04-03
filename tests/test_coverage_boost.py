import io
from unittest.mock import MagicMock

from viewer_app.core.navigation import rewrite_document_asset_urls
from viewer_app.http import http_routes as routes
from viewer_app.runtime.config import load_app_config

_MINIMAL_PNG_BYTES: bytes = b"\x89PNG\r\n\x1a\n"
_CORRUPT_INI_BYTES: bytes = b"\xff\xff"
_INVALID_JSON_REQUEST_BODY: bytes = b"not json"


def _headers_get_with_fixed_content_length(
    name: str,
    default: str | None = None,
) -> str | int | None:
    if name.lower() == "content-length":
        return len(_INVALID_JSON_REQUEST_BODY)
    return default


def test_send_json_ignores_oserror_when_writing_body() -> None:
    handler = MagicMock()
    handler.wfile.write.side_effect = OSError("broken pipe")

    routes.send_json(handler, {"ok": True})

    handler.send_response.assert_called()


def test_send_html_ignores_oserror_when_writing_body() -> None:
    handler = MagicMock()
    handler.wfile.write.side_effect = OSError("write failed")

    routes.send_html(handler, "<p>x</p>")

    handler.send_response.assert_called()


def test_parse_json_body_returns_none_and_sends_error_when_json_invalid() -> (
    None
):
    handler = MagicMock()
    handler.headers.get = MagicMock(
        side_effect=_headers_get_with_fixed_content_length
    )
    handler.rfile = io.BytesIO(_INVALID_JSON_REQUEST_BODY)

    parsed = routes.parse_json_body(handler, "Invalid JSON payload")

    assert parsed is None  # noqa: S101
    handler.send_response.assert_called()


def test_load_app_config_tolerates_unreadable_ini_file(
    app_paths_factory,
) -> None:
    paths = app_paths_factory(with_ini=True)
    paths.settings_path.write_bytes(_CORRUPT_INI_BYTES)

    config = load_app_config(paths)

    assert config.app_title  # noqa: S101


def test_rewrite_document_asset_urls_points_local_images_at_view_route(
    tmp_path,
) -> None:
    image_path = tmp_path / "z.png"
    image_path.write_bytes(_MINIMAL_PNG_BYTES)
    html_with_relative_img = '<img src="z.png" alt="diagram">'

    rewritten = rewrite_document_asset_urls(
        html_body=html_with_relative_img,
        doc_rel_path="chap/lesson.md",
        root_param=f"root={tmp_path}",
    )

    assert "/view/" in rewritten  # noqa: S101
