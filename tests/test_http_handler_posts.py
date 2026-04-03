import requests
from http import HTTPStatus
from typing import Any

_POST_TIMEOUT_S: int = 10
_GET_TIMEOUT_S: int = 60

_GET_SMOKE_ROUTES: tuple[str, ...] = (
    "/projects",
    "/piper-voices",
    "/notes?path=x.md",
    "/notes-ui",
    "/course-parts?root=/",
)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int = _POST_TIMEOUT_S,
) -> requests.Response:
    return requests.post(url, json=payload, timeout=timeout)


def test_post_settings_returns_no_content(http_server: str) -> None:
    response = _post_json(
        f"{http_server}/settings",
        {"readerPrefs": {"fontSize": 16}},
    )

    assert response.status_code == HTTPStatus.NO_CONTENT  # noqa: S101
    assert response.content == b""  # noqa: S101


def test_post_projects_set_active_returns_no_content(http_server: str) -> None:
    response = _post_json(
        f"{http_server}/projects",
        {"action": "setActive", "root": "/"},
    )

    assert response.status_code == HTTPStatus.NO_CONTENT  # noqa: S101
    assert response.content == b""  # noqa: S101


def test_get_routes_return_ok_for_projects_notes_and_related(
    http_server: str,
) -> None:
    for path in _GET_SMOKE_ROUTES:
        url = f"{http_server}{path}"
        response = requests.get(url, timeout=_GET_TIMEOUT_S)
        assert response.status_code == HTTPStatus.OK  # noqa: S101


def test_post_notes_with_empty_paths_returns_no_content(
    http_server: str,
) -> None:
    response = _post_json(
        f"{http_server}/notes",
        {"root": "", "path": "", "text": "x"},
    )

    assert response.status_code == HTTPStatus.NO_CONTENT  # noqa: S101
    assert response.content == b""  # noqa: S101
