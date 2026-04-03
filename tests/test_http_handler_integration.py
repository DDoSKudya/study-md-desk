import requests
from http import HTTPStatus
from pathlib import Path

_REQUEST_TIMEOUT_S: int = 10
_MINIMAL_PNG_BYTES: bytes = b"\x89PNG\r\n\x1a\n\x00"


def test_http_view_returns_html_for_markdown_with_explicit_root(
    tmp_path: Path,
    http_server: str,
) -> None:
    markdown_path = tmp_path / "lesson.md"
    markdown_path.write_text("# Title\n\nBody.", encoding="utf-8")

    response = requests.get(
        f"{http_server}/view/lesson.md",
        params={"root": str(tmp_path.resolve())},
        timeout=_REQUEST_TIMEOUT_S,
    )

    assert response.status_code == HTTPStatus.OK  # noqa: S101
    assert "html" in response.text.lower()  # noqa: S101


def test_http_view_returns_png_image_with_explicit_root(
    tmp_path: Path,
    http_server: str,
) -> None:
    image_path = tmp_path / "x.png"
    image_path.write_bytes(_MINIMAL_PNG_BYTES)

    response = requests.get(
        f"{http_server}/view/x.png",
        params={"root": str(tmp_path.resolve())},
        timeout=_REQUEST_TIMEOUT_S,
    )

    assert response.status_code == HTTPStatus.OK  # noqa: S101
    content_type = response.headers.get("Content-Type", "")
    assert content_type.startswith("image")  # noqa: S101


def test_http_toc_returns_ok_with_explicit_root_and_document_path(
    tmp_path: Path,
    http_server: str,
) -> None:
    document_path = tmp_path / "doc.md"
    document_path.write_text("# One\n\n## Two\n", encoding="utf-8")

    response = requests.get(
        f"{http_server}/toc",
        params={"path": "doc.md", "root": str(tmp_path.resolve())},
        timeout=_REQUEST_TIMEOUT_S,
    )

    assert response.status_code == HTTPStatus.OK  # noqa: S101
