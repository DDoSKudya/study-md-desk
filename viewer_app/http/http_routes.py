"""
This module implements HTTP route helpers for a Markdown study app,
handling JSON and HTML responses, note storage, project views, and
running Python code.

It centralizes the logic for turning HTTP requests and internal state
into JSON payloads and file changes.

It defines type aliases for JSON values and query parameters, plus
constants for query keys, project actions, HTTP status, and Python
execution timeouts.

It also introduces Protocols such as HttpResponseHandler,
HttpHeadersSource, HttpJsonBodyHandler, and
TouchProjectRecent to describe the minimal interfaces used by the helper
functions.

The functions send_json, send_html, and send_no_content write HTTP
responses with appropriate headers, optional cache control, and
optional CORS, catching write errors silently.

parse_json_body reads the request body based on Content-Length, decodes
and parses JSON, and sends a 400 error response if parsing fails.

Note-related helpers like _read_note_file, _note_text_for_anchor,
_clips_from_payload, _clips_list, _apply_clip_update,
_apply_clip_delete, _apply_clip_add, and _apply_text_update manage JSON
note files on disk, including text and per-anchor “clips”.

build_notes_get_payload uses these helpers plus the persisted state and
query parameters to produce a JSON payload describing notes for a
particular document and anchor.

Project-related helpers include build_projects_get_payload, which
creates a JSON summary of active project and metadata, and
build_course_parts_payload, which returns course parts either from
cached metadata or by calling an indexer.

apply_project_action interprets a JSON “action” payload to set the
active project, toggle pin status, or rename a project, delegating to
injected callbacks and normalizing project names with
_optional_project_name.

run_python_payload runs arbitrary Python code described in a JSON
payload via a pluggable heavy-execution helper, enforcing a fixed
timeout and returning a standard timeout result on failure.

Overall, the module sits between the HTTP layer and the apps state and
filesystem, packaging and applying operations in a testable,
framework-agnostic way.
"""

from __future__ import annotations

import json
import sys
import time
from io import BufferedIOBase

from pathlib import Path
from typing import Callable, Literal, Mapping, Protocol, Sequence, TypeAlias

from viewer_app.runtime.python_runner import RunResult

JsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | Sequence["JsonValue"]
    | Mapping[str, "JsonValue"]
)
JsonDict: TypeAlias = dict[str, JsonValue]
QueryParams: TypeAlias = dict[str, list[str]]

QS_ROOT: str = "root"
QS_PATH: str = "path"
QS_ANCHOR: str = "anchor"
QS_INCLUDE_CLIPS: str = "includeClips"

ACTION_SET_ACTIVE: str = "setActive"
ACTION_TOGGLE_PIN: str = "togglePin"
ACTION_RENAME: str = "rename"

_RUN_PYTHON_TIMEOUT_SEC: int = 15
HTTP_STATUS_NO_CONTENT: int = 204
_TIMEOUT_RUN_RESULT: RunResult = {
    "stdout": "",
    "stderr": "Execution time limit exceeded",
    "returncode": -1,
}


class _WritableBytes(Protocol):
    """
    Describes the minimal byte-oriented write capability required for
    HTTP response output streams.

    It allows abstractions to accept raw byte payloads while returning
    an implementation-defined result value.

    # Methods:

        write(
            data: bytes
        ) -> object:
            Writes the given bytes payload to the underlying stream or
            buffer and returns an arbitrary result object determined by
            the concrete implementation.
    """

    def write(self, data: bytes, /) -> object: ...


class HttpResponseHandler(Protocol):
    """
    Protocol describing the minimal interface for sending HTTP responses
    from a request handler.

    It abstracts status line, header, and body writing so helpers can
    operate on any compatible handler type.

    Attributes:
        wfile (_WritableBytes):
            Byte-oriented output stream used to write the HTTP response
            body payload.

    # Methods:

        send_response(
            status: int, message: str | None = None
        ) -> None:
            Starts an HTTP response by sending the status line and
            optional reason phrase to the client.

        send_header(
            keyword: str, value: str
        ) -> None:
            Queues a single HTTP header field to be sent as part of the
            current response.

        end_headers() -> None:
            Finalizes the HTTP header section, ensuring that subsequent
            writes go to the response body.
    """

    wfile: _WritableBytes

    def send_response(
        self, status: int, message: str | None = None
    ) -> None: ...

    def send_header(self, keyword: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


class HttpHeadersSource(Protocol):
    """
    Protocol representing a minimal HTTP header lookup source.

    It abstracts access to incoming request headers without tying
    callers to a specific framework or handler implementation.

    # Methods:

        get(
            name: str, default: str | None = None
        ) -> str | None:
            Returns the value of the specified header name if present,
            otherwise returns the provided default value or None.
    """

    def get(self, name: str, default: str | None = None) -> str | None: ...


class HttpJsonBodyHandler(HttpResponseHandler, Protocol):
    """
    Protocol for HTTP handlers that both send responses and read JSON
    request bodies.

    It combines response-writing capabilities with access to headers and
    a raw request body stream.

    Attributes:
        headers (HttpHeadersSource):
            Source of incoming HTTP request headers used to determine
            the body length and other metadata when parsing JSON
            payloads.
        rfile (BufferedIOBase):
            Buffered binary input stream from which the raw HTTP request
            body is read before decoding and JSON parsing.
    """

    headers: HttpHeadersSource
    rfile: BufferedIOBase


def _as_json_dict(value: object) -> JsonDict:
    """
    Converts an arbitrary value into a JSON-compatible dictionary.

    It preserves mapping objects that are already dictionaries and
    safely normalizes everything else to an empty mapping.

    Args:
        value (object):
            Any object that may or may not be a dictionary containing
            JSON-serializable data.

    Returns:
        JsonDict:
            The input value cast as a JSON dictionary when it is an
            instance of dict, or an empty dictionary when the value is
            of any other type.
    """
    return value if isinstance(value, dict) else {}


def _first_qs_value(qs: QueryParams, key: str) -> str:
    """
    Returns the first value for a query-string key, or an empty string.

    It normalizes missing keys or empty value lists to a safe string
    default.

    Args:
        qs (QueryParams):
            Mapping of query parameter names to lists of string values,
            as typically parsed from a URL query string.
        key (str):
            Name of the query parameter whose first value should be
            retrieved.

    Returns:
        str:
            The first string value associated with the given key when
            present and non-empty, or an empty string when the key is
            missing or has no values.
    """
    values: list[str] | None = qs.get(key)
    return values[0] if values else ""


def send_json(
    handler: HttpResponseHandler,
    payload: JsonValue,
    status: int = 200,
    cache_control: str | None = None,
    cors: bool = False,
) -> None:
    """
    Sends a JSON payload as an HTTP response using the given handler.

    It sets appropriate headers, supports optional caching and CORS
    controls, and skips the body for no-content responses.

    Args:
        handler (HttpResponseHandler):
            HTTP response handler responsible for writing the status
            line, headers, and body to the client connection.
         payload (JsonValue):
            JSON-serializable value to be encoded and sent as the
            response body when the status is not HTTP_STATUS_NO_CONTENT.
        status (int):
            HTTP status code to send with the response, defaulting to
            200 (OK).
        cache_control (str | None):
            Optional Cache-Control header value to include, or None to
            omit explicit caching directives.
        cors (bool):
            When True, adds a permissive Access-Control-Allow-Origin
            header to enable cross-origin access.
    """
    try:
        handler.send_response(status)
        handler.send_header(
            keyword="Content-type", value="application/json; charset=utf-8"
        )
        if cache_control:
            handler.send_header(keyword="Cache-Control", value=cache_control)
        if cors:
            handler.send_header(
                keyword="Access-Control-Allow-Origin", value="*"
            )
        handler.end_headers()
        if status != HTTP_STATUS_NO_CONTENT:
            handler.wfile.write(
                json.dumps(payload, ensure_ascii=False).encode(
                    encoding="utf-8"
                )
            )
    except OSError:
        pass


def send_html(
    handler: HttpResponseHandler,
    payload: str,
    status: int = 200,
    cache_control: str | None = None,
) -> None:
    """
    Sends an HTML payload as an HTTP response using the given handler.

    It sets the appropriate content type, applies optional cache
    control, and writes the encoded HTML body.

    Args:
        handler (HttpResponseHandler):
            HTTP response handler responsible for sending the status
            line, headers, and body over the client connection.
        payload (str):
            HTML text to be encoded as UTF-8 and written to the response
            body.
        status (int):
            HTTP status code to send with the response, defaulting to
            200 (OK).
        cache_control (str | None):
            Optional Cache-Control header value to include, or None to
            omit explicit caching directives.
    """
    try:
        handler.send_response(status)
        handler.send_header(
            keyword="Content-type", value="text/html; charset=utf-8"
        )
        if cache_control:
            handler.send_header(keyword="Cache-Control", value=cache_control)
        handler.end_headers()
        handler.wfile.write(payload.encode(encoding="utf-8"))
    except OSError:
        pass


def send_no_content(handler: HttpResponseHandler, cors: bool = False) -> None:
    """
    Sends an HTTP 204 No Content response using the given handler.

    It optionally adds a permissive CORS header and then finalizes the
    response without a body.

    Args:
        handler (HttpResponseHandler):
            HTTP response handler used to write the status line and
            headers for the no-content response.
        cors (bool):
            When True, adds an Access-Control-Allow-Origin header with a
            wildcard origin to enable cross-origin callers.
    """
    try:
        handler.send_response(status=HTTP_STATUS_NO_CONTENT)
        if cors:
            handler.send_header(
                keyword="Access-Control-Allow-Origin", value="*"
            )
        handler.end_headers()
    except OSError:
        pass


def parse_json_body(
    handler: HttpJsonBodyHandler, invalid_message: str
) -> JsonDict | None:
    """
    Parses a JSON request body from an HTTP handler and returns it as a
    dictionary.

    It validates the Content-Length header, safely decodes the body, and
    reports a client error response when parsing fails.

    Args:
        handler (HttpJsonBodyHandler):
            HTTP handler providing access to request headers, the raw
            input stream, and response-sending methods used for error
            reporting.
        invalid_message (str):
            Human-readable error message to include in the JSON error
            payload when the request body cannot be parsed as valid
            JSON.

    Returns:
        JsonDict | None:
            A JSON dictionary derived from the parsed request body on
            success, or None after sending a 400 error response when
            the body is invalid or cannot be decoded.
    """
    try:
        length: int = int(
            handler.headers.get(name="Content-Length", default="0") or "0"
        )
        body: str = (
            handler.rfile.read(length).decode(
                encoding="utf-8", errors="replace"
            )
            if length
            else ""
        )
        data = json.loads(body or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        send_json(
            handler, payload={"error": invalid_message}, status=400, cors=True
        )
        return None
    return _as_json_dict(value=data)


def _read_note_file(note_path: Path) -> JsonDict:
    """
    Loads a note JSON file from disk and returns its normalized
    dictionary payload.

    It tolerates missing or invalid files by returning an empty mapping
    instead of propagating errors.

    Args:
        note_path (Path):
            Filesystem path pointing to the JSON note file that should
            be opened and parsed.

    Returns:
        JsonDict:
            A dictionary representation of the note content when the
            file exists and can be parsed as JSON, or an empty
            dictionary if the file is missing, unreadable, or contains
            invalid data.
    """
    if not note_path.is_file():
        return {}
    try:
        with note_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {}
    return _as_json_dict(value=raw)


def _note_text_for_anchor(payload: JsonDict, anchor: str) -> str:
    """
    Returns the note text associated with a specific anchor or the
    default note text.

    It prioritizes anchor-specific entries when an anchor is provided,
    falling back to the generic text field otherwise.

    Args:
        payload (JsonDict):
            Dictionary containing note data, including optional
            "byAnchor" and "text" entries that store anchor-specific
            and default note text respectively.
        anchor (str):
            Anchor identifier whose associated note text should be
            returned, or an empty string to select the generic note
            text.

    Returns:
        str:
            The note text string linked to the given anchor when
            present, or the value of the top-level "text" field when no
            anchor is specified or no anchor-specific entry exists.
    """
    if anchor:
        by_anchor: JsonDict = _as_json_dict(value=payload.get("byAnchor"))
        return str(by_anchor.get(anchor) or "")
    return str(payload.get("text") or "")


def _clips_from_payload(payload: JsonDict) -> list[JsonDict]:
    """
    Extracts a normalized list of clip dictionaries from a note payload.

    It filters out non-list values and non-dictionary items to ensure a
    clean, homogeneous collection.

    Args:
        payload (JsonDict):
            Dictionary representing note data that may contain a "clips"
            entry holding a list of clip objects.

    Returns:
        list[JsonDict]:
            A list of clip dictionaries taken from the "clips" field
            when it is a list, or an empty list when the field is
            missing, malformed, or contains non-dictionary items.
    """
    clips: JsonValue = payload.get("clips")
    if not isinstance(clips, list):
        return []
    return [item for item in clips if isinstance(item, dict)]


def build_notes_get_payload(
    qs: QueryParams,
    load_state_json: Callable[[], JsonDict],
    normalize_project_root: Callable[[str], str],
) -> JsonDict:
    """
    Builds a JSON payload describing notes for a specific document and
    anchor.

    It resolves the project root, loads any existing note file, and
    optionally includes associated clips.

    Args:
        qs (QueryParams):
            Mapping of query-string keys to lists of values used to
            extract the project root, document path, anchor, and
            include-clips flag.
        load_state_json (Callable[[], JsonDict]):
            Callable that returns the current persisted application
            state, used to determine the active project root when none
            is provided in the query string.
        normalize_project_root (Callable[[str], str]):
            Function that normalizes or canonicalizes the project root
            path string before it is used to locate note files on disk.

    Returns:
        JsonDict:
            A dictionary containing the resolved project root, document
            path, anchor, and note text, plus a "clips" list when clips
            have been requested and are available for the note.
    """
    root: str = _first_qs_value(qs, key=QS_ROOT)
    doc_path: str = _first_qs_value(qs, key=QS_PATH)
    anchor: str = _first_qs_value(qs, key=QS_ANCHOR)
    include_clips: bool = bool(_first_qs_value(qs, key=QS_INCLUDE_CLIPS))
    state: JsonDict = load_state_json()
    if not root:
        root = str(state.get("activeProjectRoot") or "")
    root_n: str = normalize_project_root(root)
    text_val = ""
    clips_val: list[JsonDict] = []
    if root_n and doc_path:
        note_path: Path = (
            Path(root_n) / "notes" / Path(doc_path).with_suffix(suffix=".json")
        )
        payload: JsonDict = _read_note_file(note_path)
        if payload:
            text_val: str = _note_text_for_anchor(payload, anchor)
            if include_clips:
                clips_val = _clips_from_payload(payload)
    result: JsonDict = {
        "root": root_n,
        "path": doc_path,
        "anchor": anchor,
        "text": text_val,
    }
    if include_clips:
        result["clips"] = clips_val
    return result


def build_projects_get_payload(
    load_state_json: Callable[[], JsonDict],
    get_projects_state: Callable[[JsonDict | None], JsonDict],
) -> JsonDict:
    """
    Builds a JSON payload describing the current projects view state.

    It combines persisted application state with derived project listing
    data for use by the client.

    Args:
        load_state_json (Callable[[], JsonDict]):
            Callable that returns the full persisted application state,
            including the active project root and any project metadata.
        get_projects_state (Callable[[JsonDict | None], JsonDict]):
            Function that converts the raw application state into a
            structured projects listing dictionary for the response.

    Returns:
        JsonDict:
            A dictionary containing the active project root path, a
            "projects" structure generated by get_projects_state, and a
            "projectMetaByRoot" mapping when present and well-formed in
            the persisted state.
    """
    state: JsonDict = load_state_json()
    meta: (
        str
        | dict[str, JsonValue]
        | int
        | float
        | Sequence[JsonValue]
        | Mapping[str, JsonValue]
        | Literal[True]
    ) = (state.get("projectMetaByRoot") or {})
    return {
        "activeProjectRoot": str(state.get("activeProjectRoot") or ""),
        "projects": get_projects_state(state),
        "projectMetaByRoot": meta if isinstance(meta, dict) else {},
    }


def build_course_parts_payload(
    qs: QueryParams,
    load_state_json: Callable[[], JsonDict],
    normalize_project_root: Callable[[str], str],
    index_course_parts: Callable[[str], list[JsonDict]],
) -> JsonDict:
    """
    Builds a JSON payload describing the course parts for a project
    root.

    It resolves the effective project root, reads any cached metadata,
    and falls back to indexing when needed.

    Args:
        qs (QueryParams):
            Mapping of query-string keys to lists of values used to
            extract the requested project root identifier.
        load_state_json (Callable[[None], JsonDict]):
            Callable that returns the current persisted application
            state, used to determine the active project root and its
            metadata when no root is present in the query string.
        normalize_project_root (Callable[[str], str]):
            Function that normalizes or canonicalizes the project root
            path string before it is used to look up metadata or index
            course parts.
        index_course_parts (Callable[[str], list[JsonDict]]):
            Callback that computes a fresh list of course parts for a
            given normalized root when no cached courseParts metadata is
            available.

    Returns:
        JsonDict:
            A dictionary containing the normalized project root under
            "root" and a "parts" list drawn from cached metadata when
            present, or from index_course_parts when no valid metadata
            is available.
    """
    root: str = _first_qs_value(qs, key=QS_ROOT)
    state: JsonDict = load_state_json()
    if not root:
        root = str(state.get("activeProjectRoot") or "")
    root_n: str = normalize_project_root(root)
    meta: JsonDict = _as_json_dict(value=state.get("projectMetaByRoot"))
    item: JsonDict = _as_json_dict(value=meta.get(root_n))
    parts: JsonValue = item.get("courseParts")
    if not isinstance(parts, list):
        parts = index_course_parts(root_n)
    return {"root": root_n, "parts": parts}


def run_python_payload(
    data: JsonDict,
    run_heavy: Callable[..., RunResult],
    handle_run: Callable[[str, str], RunResult],
) -> RunResult:
    """
    Runs a Python code snippet using a heavy-execution helper with a
    timeout safeguard.

    It selects the Python executable to use, delegates execution, and
    normalizes timeout failures into a fixed result.

    Args:
        data (JsonDict):
            JSON dictionary that may contain a "code" string with the
            Python snippet to run and an optional "python" value
            specifying the interpreter path.
        run_heavy (Callable[..., RunResult]):
            Callable responsible for executing potentially long-running
            operations with support for a timeout keyword argument.
        handle_run (Callable[[str, str], RunResult]):
            Function that actually runs the Python code given the code
            text and Python executable path, used as a target for
            run_heavy.

    Returns:
        RunResult:
            A mapping containing stdout, stderr, and returncode either
            from the successful execution of the code or a standardized
            timeout result when the execution exceeds the allowed time
            limit.
    """
    code: str = str(data.get("code", ""))
    python_raw: JsonValue = data.get("python")
    python_path: str = (
        str(python_raw) if python_raw is not None else sys.executable
    )
    try:
        return run_heavy(
            handle_run, code, python_path, timeout=_RUN_PYTHON_TIMEOUT_SEC
        )
    except TimeoutError:
        return _TIMEOUT_RUN_RESULT


def _clips_list(existing: JsonDict) -> list[JsonDict]:
    """
    Extracts a cleaned list of clip dictionaries from an existing note
    payload.

    It ensures that only dictionary entries from the "clips" field are
    returned, normalizing all other values to an empty list.

    Args:
        existing (JsonDict):
            Note payload dictionary that may contain a "clips" entry
            holding a list of clip-like objects.

    Returns:
        list[JsonDict]:
            A list of clip dictionaries filtered from the "clips" value
            when it is a list, or an empty list when "clips" is
            missing, not a list, or contains non-dictionary elements.
    """
    clips: JsonValue = existing.get("clips")
    if isinstance(clips, list):
        return [x for x in clips if isinstance(x, dict)]
    return []


def _apply_clip_update(existing: JsonDict, clip_update: JsonDict) -> None:
    """
    Updates the note text for an existing clip entry matched by range.

    It searches the current clips list for a clip with the same range
    and replaces its note content when found.

    Args:
        existing (JsonDict):
            Note payload dictionary whose "clips" list should be
            searched and updated in place when a matching clip is
            located.
        clip_update (JsonDict):
            Dictionary describing the clip update, expected to contain a
            "range" object identifying the target clip and a "note"
            string with the new note text.
    """
    clips: list[JsonDict] = _clips_list(existing)
    rng: JsonValue = clip_update.get("range")
    new_note: str = str(clip_update.get("note") or "")
    if not isinstance(rng, dict):
        return
    for clip_item in clips:
        current_range: JsonValue = clip_item.get("range")
        if isinstance(current_range, dict) and current_range == rng:
            clip_item["note"] = new_note
            existing["clips"] = clips
            return


def _apply_clip_delete(existing: JsonDict, clip_delete: JsonDict) -> None:
    """
    Removes a single clip entry from an existing note payload based on
    its range, or quote and heading identifiers.

    It constructs a new clips list without the first matching entry and
    writes it back to the note.

    Args:
        existing (JsonDict):
            Note payload dictionary whose "clips" list will be filtered
            to remove a matching clip entry, if any is found.
        clip_delete (JsonDict):
            Dictionary describing the clip to delete, expected to
            contain a "range" object and/or a combination of "quote" and
            "headingId" fields used to identify the target clip.
    """
    clips: list[JsonDict] = _clips_list(existing)
    quote_delete: str = str(clip_delete.get("quote") or "")
    heading_delete: str = str(clip_delete.get("headingId") or "")
    range_delete: JsonValue = clip_delete.get("range")
    new_clips: list[JsonDict] = []
    removed = False
    for clip_item in clips:
        if removed:
            new_clips.append(clip_item)
            continue
        current_range: JsonValue = clip_item.get("range")
        if (
            isinstance(range_delete, dict)
            and isinstance(current_range, dict)
            and current_range == range_delete
        ):
            removed = True
            continue
        if (
            quote_delete
            and str(clip_item.get("quote") or "") == quote_delete
            and str(clip_item.get("headingId") or "") == heading_delete
        ):
            removed = True
            continue
        new_clips.append(clip_item)
    existing["clips"] = new_clips


def _apply_clip_add(existing: JsonDict, clip: JsonDict) -> None:
    """
    Adds a new clip entry to an existing note payload if it has
    meaningful content.

    It constructs a normalized clip dictionary, appends it to the clips
    list, and ensures related note structures are initialized.

    Args:
        existing (JsonDict):
            Note payload dictionary that will receive the new clip and
            may be updated with default "byAnchor" and "text" fields
            when they are missing.
        clip (JsonDict):
            Dictionary describing the clip to add, expected to contain
            "quote", "note", optional heading fields, and an optional
            "range" object identifying the selected text region.
    """
    clips: list[JsonDict] = _clips_list(existing)
    quote: str = str(clip.get("quote") or "")
    note_text: str = str(clip.get("note") or "")
    if quote.strip() or note_text.strip():
        range_val: JsonValue = clip.get("range")
        clips.append(
            {
                "quote": quote,
                "note": note_text,
                "headingId": str(clip.get("headingId") or ""),
                "headingTitle": str(clip.get("headingTitle") or ""),
                "range": range_val if isinstance(range_val, dict) else None,
                "createdAt": int(time.time() * 1000),
            }
        )
    existing["clips"] = clips
    existing.setdefault("byAnchor", {})
    existing.setdefault("text", "")


def _apply_text_update(existing: JsonDict, anchor: str, text: str) -> None:
    """
    Updates the note text in a note payload, either for a specific
    anchor or as the default text.

    It ensures that the complementary text store ("text" or "byAnchor")
    is initialized when needed.

    Args:
        existing (JsonDict):
            Note payload dictionary whose "text" and "byAnchor" fields
            will be updated in place to reflect the new note content.
        anchor (str):
            Anchor identifier to associate with the note text; when
            empty, the text is stored as the top-level default note.
            text; when empty, the text is stored as the top-level
            default note.
        text (str):
            New note text to store under the given anchor or as the
            top-level "text" value.
    """
    if anchor:
        by_anchor: JsonDict = _as_json_dict(value=existing.get("byAnchor"))
        by_anchor[anchor] = text
        existing["byAnchor"] = by_anchor
        existing.setdefault("text", "")
        return
    existing["text"] = text
    existing.setdefault("byAnchor", {})


class TouchProjectRecent(Protocol):
    """
    Protocol for marking a project as recently used and optionally
    updating its display name and history size.

    It abstracts the operation that touches or reorders a project in a
    recent-projects list.

    # Methods:

        __call__(
            root: str,
            name: str | None = None,
            limit: int = 18
        ) -> None:
            Records that the project identified by root was recently
            used, optionally updating its human-readable name and
            enforcing a maximum recent-projects list length given by
            limit.
    """

    def __call__(
        self, root: str, /, *, name: str | None = None, limit: int = 18
    ) -> None: ...


def save_notes_payload(
    data: JsonDict,
    load_state_json: Callable[[], JsonDict],
    normalize_project_root: Callable[[str], str],
) -> None:
    """
    Persists note content and clips for a specific document and anchor
    to disk.

    It resolves the effective project root and note file path, then
    applies either text or clip mutations before writing the updated
    JSON.

    Args:
        data (JsonDict):
            JSON payload describing the note update, including "root",
            "path", "anchor", "text", and optional "clip",
            "clipDelete", or "clipUpdate" objects that select the type
            of change to apply.
        load_state_json (Callable[[], JsonDict]):
            Callable that returns the current persisted application
            state, used to infer the active project root when no root is
            provided in the payload.
        normalize_project_root (Callable[[str], str]):
            Function that normalizes or canonicalizes the project root
            path string before using it to construct the on-disk notes
            directory.
    """
    root: str = normalize_project_root(str(data.get("root") or "").strip())
    path: str = str(data.get("path") or "").strip()
    anchor: str = str(data.get("anchor") or "").strip()
    text: str = str(data.get("text") or "")
    clip: JsonValue = data.get("clip")
    clip_delete: JsonValue = data.get("clipDelete")
    clip_update: JsonValue = data.get("clipUpdate")
    state: JsonDict = load_state_json()
    if not root:
        root = normalize_project_root(
            str(state.get("activeProjectRoot") or "")
        )
    try:
        if not root or not path:
            return
        note_path: Path = (
            Path(root) / "notes" / Path(path).with_suffix(suffix=".json")
        )
        note_path.parent.mkdir(parents=True, exist_ok=True)
        existing: JsonDict = _read_note_file(note_path)
        if isinstance(clip_update, dict):
            _apply_clip_update(existing, clip_update)
        elif isinstance(clip_delete, dict):
            _apply_clip_delete(existing, clip_delete)
        elif isinstance(clip, dict):
            _apply_clip_add(existing, clip)
        else:
            _apply_text_update(existing, anchor, text)

        with note_path.open("w", encoding="utf-8") as fh:
            json.dump(existing, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def apply_project_action(
    data: JsonDict,
    normalize_project_root: Callable[[str], str],
    set_active_project: Callable[[str], None],
    touch_project_recent: TouchProjectRecent,
    toggle_pin_project: Callable[[str], bool],
) -> None:
    """
    Applies a project-related action described by a JSON payload to the
    current application state.

    It can set the active project, toggle its pinned status, or rename
    it while updating recent-projects metadata.

    Args:
        data (JsonDict):
            JSON payload specifying the action to perform under
            "action", the project root under "root", and an optional
            "name" for rename or recent-projects updates.
        normalize_project_root (Callable[[str], str]):
            Function that normalizes or canonicalizes the project root
            path string before it is passed to downstream state
            handlers.
        set_active_project (Callable[[str], None]):
            Callback that marks the given normalized project root as the
            active project in the application state.
        touch_project_recent (TouchProjectRecent):
            Callable that records the project as recently used and can
            update its display name and ordering in the recent-projects
            list.
        toggle_pin_project (Callable[[str], bool]):
            Callback that toggles the pinned status of the given project
            root, typically returning the new pinned state.
    """
    action: str = str(data.get("action") or "").strip()
    root: str = normalize_project_root(str(data.get("root") or "").strip())
    if action == ACTION_SET_ACTIVE and root:
        set_active_project(root)
        touch_project_recent(
            root, name=_optional_project_name(value=data.get("name"))
        )
    elif action == ACTION_TOGGLE_PIN and root:
        toggle_pin_project(root)
    elif action == ACTION_RENAME and root:
        name: str = str(data.get("name") or "").strip()
        if name:
            touch_project_recent(root, name=name)


def _optional_project_name(value: JsonValue) -> str | None:
    """
    Normalizes an optional project name value to a non-empty string or
    None.

    It trims whitespace and rejects non-string and blank values so
    callers can distinguish between unset and meaningful names.

    Args:
        value (JsonValue):
            Raw value that may represent a project name, typically taken
            from user input or a JSON payload and possibly of any JSON
            type.

    Returns:
        str | None:
            The stripped project name string when value is a non-empty
            string after trimming, or None when the value is missing,
            not a string, or becomes empty after stripping whitespace.
    """
    if value is None or not isinstance(value, str):
        return None
    stripped: str = value.strip()
    return stripped or None
