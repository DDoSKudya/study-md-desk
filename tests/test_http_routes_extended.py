import io
import json

from http import HTTPStatus
from pathlib import Path
from typing import Any, cast

from viewer_app.http import http_routes as routes
from viewer_app.http.http_routes import (
    _note_text_for_anchor,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.runtime.projects import normalize_project_root

_FAKE_PROJECT_ROOT: str = "/project/demo"


def _normalize_path_string(path_str: str) -> str:
    return str(Path(path_str).resolve())


class _RecordingHttpHandler:

    def __init__(self) -> None:
        self.wfile: Any = io.BytesIO()
        self.last_status: int | None = None

    def send_response(self, status: int, message: str | None = None) -> None:
        self.last_status = status

    def send_header(self, keyword: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass


def test_note_text_for_anchor_prefers_by_anchor_when_key_matches() -> None:
    assert (  # noqa: S101
        _note_text_for_anchor(
            cast(Any, {"byAnchor": {"x": "y"}, "text": "d"}),
            "x",
        )
        == "y"
    )


def test_note_text_for_anchor_falls_back_to_plain_text_when_anchor_empty() -> (
    None
):
    assert (  # noqa: S101
        _note_text_for_anchor(cast(Any, {"text": "plain"}), "") == "plain"
    )


def test_send_html_sets_ok_status() -> None:
    handler = _RecordingHttpHandler()

    routes.send_html(handler, "<p>x</p>")

    assert handler.last_status == HTTPStatus.OK  # noqa: S101


def test_send_no_content_sets_204_with_cors() -> None:
    handler = _RecordingHttpHandler()

    routes.send_no_content(handler, cors=True)

    assert handler.last_status == HTTPStatus.NO_CONTENT  # noqa: S101


def test_build_notes_get_payload_reads_anchor_and_clips_from_disk(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    notes_dir = project_root / "notes"
    notes_dir.mkdir()
    stored_note = {
        "text": "def",
        "byAnchor": {"a1": "anchored"},
        "clips": [{"quote": "q", "text": "t"}],
    }
    (notes_dir / "lesson.json").write_text(
        json.dumps(stored_note),
        encoding="utf-8",
    )

    def load_state() -> Any:
        return {"activeProjectRoot": str(project_root)}

    query = {
        routes.QS_ROOT: [str(project_root)],
        routes.QS_PATH: ["lesson.md"],
        routes.QS_ANCHOR: ["a1"],
        routes.QS_INCLUDE_CLIPS: ["1"],
    }
    payload = routes.build_notes_get_payload(
        qs=query,
        load_state_json=load_state,
        normalize_project_root=normalize_project_root,
    )

    assert payload["text"] == "anchored"  # noqa: S101
    assert payload["clips"]  # noqa: S101


def test_build_course_parts_payload_uses_cached_meta_when_present(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "p"
    project_root.mkdir()
    (project_root / "a.md").write_text("# A", encoding="utf-8")
    normalized_root = normalize_project_root(str(project_root))

    def load_state() -> Any:
        return {
            "activeProjectRoot": normalized_root,
            "projectMetaByRoot": {
                normalized_root: {
                    "courseParts": [
                        {"key": "", "title": "M", "docs": []},
                    ]
                }
            },
        }

    payload = routes.build_course_parts_payload(
        qs={routes.QS_ROOT: [str(project_root)]},
        load_state_json=load_state,
        normalize_project_root=normalize_project_root,
        index_course_parts=lambda r: [],
    )

    assert payload["root"] == normalized_root  # noqa: S101
    assert payload["parts"]  # noqa: S101


def test_apply_project_action_toggle_pin_invokes_toggle() -> None:
    pin_calls: list[bool] = []

    routes.apply_project_action(
        {"action": routes.ACTION_TOGGLE_PIN, "root": _FAKE_PROJECT_ROOT},
        normalize_project_root=lambda x: x,
        set_active_project=lambda r: None,
        touch_project_recent=lambda *a, **k: None,
        toggle_pin_project=lambda r: pin_calls.append(True) or True,
    )

    assert pin_calls  # noqa: S101


def test_apply_project_action_rename_trims_display_name() -> None:
    rename_events: list[tuple[str, str | None]] = []

    routes.apply_project_action(
        {
            "action": routes.ACTION_RENAME,
            "root": _FAKE_PROJECT_ROOT,
            "name": "  NewName  ",
        },
        normalize_project_root=lambda x: x,
        set_active_project=lambda r: None,
        touch_project_recent=lambda r, name=None, limit=18: rename_events.append(
            (r, name)
        ),
        toggle_pin_project=lambda r: False,
    )

    assert rename_events  # noqa: S101
    assert rename_events[0][1] == "NewName"  # noqa: S101


def test_save_notes_payload_appends_new_clip(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    def load_state() -> Any:
        return {"activeProjectRoot": str(project_root.resolve())}

    routes.save_notes_payload(
        {
            "root": str(project_root),
            "path": "d.md",
            "anchor": "",
            "clip": {"quote": "sel", "note": "note", "headingId": "h"},
        },
        load_state_json=load_state,
        normalize_project_root=_normalize_path_string,
    )
    note_file = project_root / "notes" / "d.json"
    saved = json.loads(note_file.read_text(encoding="utf-8"))

    assert saved["clips"]  # noqa: S101


def test_save_notes_payload_updates_clip_matching_range(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "proj2"
    project_root.mkdir()
    notes_dir = project_root / "notes"
    notes_dir.mkdir()
    initial_note = {
        "clips": [
            {
                "quote": "same",
                "note": "old",
                "range": {"startGlobal": 1, "endGlobal": 2},
            }
        ],
        "byAnchor": {},
        "text": "",
    }
    (notes_dir / "x.json").write_text(
        json.dumps(initial_note), encoding="utf-8"
    )

    def load_state() -> Any:
        return {"activeProjectRoot": str(project_root.resolve())}

    routes.save_notes_payload(
        {
            "root": str(project_root),
            "path": "x.md",
            "clipUpdate": {
                "note": "newtext",
                "range": {"startGlobal": 1, "endGlobal": 2},
            },
        },
        load_state_json=load_state,
        normalize_project_root=_normalize_path_string,
    )
    after_update = json.loads(
        (notes_dir / "x.json").read_text(encoding="utf-8")
    )

    assert after_update["clips"][0]["note"] == "newtext"  # noqa: S101


def test_save_notes_payload_deletes_clip_matching_quote_and_range(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "proj2"
    project_root.mkdir()
    notes_dir = project_root / "notes"
    notes_dir.mkdir()
    initial_note = {
        "clips": [
            {
                "quote": "same",
                "note": "old",
                "range": {"startGlobal": 1, "endGlobal": 2},
            }
        ],
        "byAnchor": {},
        "text": "",
    }
    (notes_dir / "x.json").write_text(
        json.dumps(initial_note), encoding="utf-8"
    )

    def load_state() -> Any:
        return {"activeProjectRoot": str(project_root.resolve())}

    routes.save_notes_payload(
        {
            "root": str(project_root),
            "path": "x.md",
            "clipDelete": {
                "quote": "same",
                "headingId": "",
                "range": {"startGlobal": 1, "endGlobal": 2},
            },
        },
        load_state_json=load_state,
        normalize_project_root=_normalize_path_string,
    )
    after_delete = json.loads(
        (notes_dir / "x.json").read_text(encoding="utf-8")
    )

    assert after_delete["clips"] == []  # noqa: S101
