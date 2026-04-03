import io
import json
import sys

from http import HTTPStatus
from pathlib import Path
from typing import Any

from viewer_app.http import http_routes as routes
from viewer_app.runtime.python_runner import handle_run as real_handle_run

_FAKE_PROJECT_ROOT: str = "/project/z"
_RUN_PYTHON_TIMEOUT_RETURNCODE: int = -1


def _normalize_path_string(path_str: str) -> str:
    return str(Path(path_str).resolve())


class _StubHttpResponseHandler:

    def __init__(self) -> None:
        self.wfile: Any = io.BytesIO()
        self.last_status: int | None = None
        self.recorded_headers: list[tuple[str, str]] = []

    def send_response(self, status: int, message: str | None = None) -> None:
        self.last_status = status

    def send_header(self, keyword: str, value: str) -> None:
        self.recorded_headers.append((keyword, value))

    def end_headers(self) -> None:
        pass


class _SimpleContentLengthHeaders:

    def __init__(self, body_byte_length: int) -> None:
        self._body_byte_length = body_byte_length

    def get(self, name: str, default: str | None = None) -> str | None:
        if name.lower() == "content-length":
            return str(self._body_byte_length)
        return default


class _StubJsonRequestHandler(_StubHttpResponseHandler):
    def __init__(self, body: bytes) -> None:
        super().__init__()
        self.headers: Any = _SimpleContentLengthHeaders(len(body))
        self.rfile: Any = io.BytesIO(body)


def test_first_qs_value_returns_first_entry_when_key_present() -> None:
    assert (  # noqa: S101
        routes._first_qs_value(  # pyright: ignore[reportPrivateUsage]
            {"k": ["a"]}, "k"
        )
        == "a"
    )


def test_first_qs_value_returns_empty_string_when_key_missing() -> None:
    assert (  # noqa: S101
        routes._first_qs_value({}, "k")  # pyright: ignore[reportPrivateUsage]
        == ""
    )


def test_send_json_sets_ok_and_writes_payload() -> None:
    handler = _StubHttpResponseHandler()

    routes.send_json(handler, {"a": 1})

    assert handler.last_status == HTTPStatus.OK  # noqa: S101
    assert b'"a"' in handler.wfile.getvalue()  # noqa: S101


def test_parse_json_body_returns_dict_when_body_valid() -> None:
    handler = _StubJsonRequestHandler(b'{"x":1}')

    parsed = routes.parse_json_body(handler, "bad json")

    assert parsed == {"x": 1}  # noqa: S101


def test_parse_json_body_returns_none_when_body_invalid() -> None:
    handler = _StubJsonRequestHandler(b"not-json")

    assert routes.parse_json_body(handler, "bad json") is None  # noqa: S101


def test_run_python_payload_maps_timeout_to_error_returncode() -> None:
    def run_heavy_raises_timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError()

    result = routes.run_python_payload(
        {"code": "1"},
        run_heavy=run_heavy_raises_timeout,
        handle_run=lambda c, p: {"stdout": "", "stderr": "", "returncode": 0},
    )

    assert result["returncode"] == _RUN_PYTHON_TIMEOUT_RETURNCODE  # noqa: S101


def test_run_python_payload_runs_snippet_with_real_runner() -> None:
    def fake_run_heavy(fn: Any, code: str, py: str, timeout: int = 15) -> Any:
        return fn(code, py)

    result = routes.run_python_payload(
        {"code": "print(1)", "python": sys.executable},
        run_heavy=fake_run_heavy,
        handle_run=real_handle_run,
    )

    assert result["returncode"] == 0  # noqa: S101


def test_build_projects_get_payload_includes_active_root_and_projects() -> (
    None
):
    def load_state() -> Any:
        return {
            "activeProjectRoot": "/x",
            "projects": {"pinned": [], "recent": []},
        }

    def get_projects_state(_state: Any) -> Any:
        return {"pinned": [], "recent": []}

    payload = routes.build_projects_get_payload(
        load_state_json=load_state,
        get_projects_state=get_projects_state,
    )

    assert payload["activeProjectRoot"] == "/x"  # noqa: S101
    assert "projects" in payload  # noqa: S101


def test_apply_project_action_set_active_invokes_callback() -> None:
    active_roots: list[str] = []

    def set_active_project(root: str) -> None:
        active_roots.append(root)

    routes.apply_project_action(
        {"action": routes.ACTION_SET_ACTIVE, "root": _FAKE_PROJECT_ROOT},
        normalize_project_root=lambda x: x,
        set_active_project=set_active_project,
        touch_project_recent=lambda *a, **k: None,
        toggle_pin_project=lambda r: False,
    )

    assert active_roots  # noqa: S101


def test_save_notes_payload_persists_anchor_text(tmp_path: Path) -> None:
    project_root = tmp_path / "notes_root"
    project_root.mkdir()

    def load_state() -> Any:
        return {"activeProjectRoot": str(project_root.resolve())}

    routes.save_notes_payload(
        {
            "root": str(project_root.resolve()),
            "path": "doc.md",
            "anchor": "h1",
            "text": "hello",
        },
        load_state_json=load_state,
        normalize_project_root=_normalize_path_string,
    )
    note_path = project_root / "notes" / "doc.json"
    stored = json.loads(note_path.read_text(encoding="utf-8"))

    assert note_path.is_file()  # noqa: S101
    assert stored.get("byAnchor", {}).get("h1") == "hello"  # noqa: S101
