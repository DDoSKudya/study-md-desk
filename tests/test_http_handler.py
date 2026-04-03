import sys

import requests
from http import HTTPStatus

_GET_TIMEOUT_S: int = 5
_POST_RUN_PYTHON_TIMEOUT_S: int = 15


def _http_get(url: str, *, timeout: int = _GET_TIMEOUT_S) -> requests.Response:
    return requests.get(url, timeout=timeout)


def test_get_app_config_returns_json_object(http_server: str) -> None:
    response = _http_get(f"{http_server}/app-config")

    assert response.status_code == HTTPStatus.OK  # noqa: S101
    data = response.json()
    assert isinstance(data, dict)  # noqa: S101


def test_get_assets_shell_js_returns_javascript(http_server: str) -> None:
    response = _http_get(f"{http_server}/assets/shell.js")

    assert response.status_code == HTTPStatus.OK  # noqa: S101
    body = response.content
    assert b"function" in body or b"var" in body  # noqa: S101


def test_get_root_returns_html_shell(http_server: str) -> None:
    response = _http_get(f"{http_server}/")

    assert response.status_code == HTTPStatus.OK  # noqa: S101
    assert b"html" in response.content.lower()  # noqa: S101


def test_post_run_python_returns_stdout_in_json(http_server: str) -> None:
    response = requests.post(
        f"{http_server}/run",
        json={"code": "print('hi')", "python": sys.executable},
        timeout=_POST_RUN_PYTHON_TIMEOUT_S,
    )

    assert response.status_code == HTTPStatus.OK  # noqa: S101
    payload = response.json()
    assert "stdout" in payload  # noqa: S101


def test_get_unknown_route_returns_404(http_server: str) -> None:
    response = _http_get(f"{http_server}/no-such-route")

    assert response.status_code == HTTPStatus.NOT_FOUND  # noqa: S101
