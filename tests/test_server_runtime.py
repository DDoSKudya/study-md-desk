import pytest
import requests
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast

from viewer_app.runtime.server_runtime import RunningServer, start_local_server

_BASE_URL_TEST_PORT: int = 8080
_EXPECTED_LOOPBACK_BASE_URL: str = f"http://127.0.0.1:{_BASE_URL_TEST_PORT}/"
_ECHO_SERVER_FIRST_PORT: int = 40120
_ECHO_SERVER_LAST_EXCLUSIVE_PORT: int = 40150
_HTTP_GET_TIMEOUT_S: int = 3
_FAIL_BIND_FIRST_PORT: int = 40200
_FAIL_BIND_LAST_EXCLUSIVE_PORT: int = 40201
_ECHO_RESPONSE_BODY: bytes = b"ok"


class _EchoGetHandler(BaseHTTPRequestHandler):

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(_ECHO_RESPONSE_BODY)

    def log_message(self, *_args: object, **_kwargs: object) -> None:
        pass


def test_running_server_base_url_formats_loopback_address() -> None:
    running = RunningServer(
        server=cast(Any, None),
        port=_BASE_URL_TEST_PORT,
        thread=cast(Any, None),
    )

    assert running.base_url == _EXPECTED_LOOPBACK_BASE_URL  # noqa: S101


def test_start_local_server_serves_http_get_echo() -> None:
    running = start_local_server(
        HTTPServer,
        _EchoGetHandler,
        _ECHO_SERVER_FIRST_PORT,
        _ECHO_SERVER_LAST_EXCLUSIVE_PORT,
    )
    try:
        response = requests.get(running.base_url, timeout=_HTTP_GET_TIMEOUT_S)

        assert response.status_code == HTTPStatus.OK  # noqa: S101
        assert response.content == _ECHO_RESPONSE_BODY  # noqa: S101
    finally:
        running.server.shutdown()
        running.server.server_close()


def test_start_local_server_raises_when_no_port_can_bind() -> None:
    class _AlwaysBusyServer(HTTPServer):
        def server_bind(self) -> None:
            raise OSError("busy")

    with pytest.raises(OSError, match="busy|Failed"):
        start_local_server(
            _AlwaysBusyServer,
            _EchoGetHandler,
            _FAIL_BIND_FIRST_PORT,
            _FAIL_BIND_LAST_EXCLUSIVE_PORT,
        )
