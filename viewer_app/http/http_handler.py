"""
This module implements an HTTP request handler for the viewer app,
serving both HTML pages and JSON APIs.

It acts as the server-side entry point for routing and responding to
browser and API calls.

It defines constants for URL paths, cache directives, and HTTP status
codes to standardize responses.

It declares a PiperVoiceMeta typed dictionary describing metadata for
text-to-speech voice models.

It initializes global application context objects for paths, state
storage, and project services used throughout request handling.

It provides helper functions to normalize arbitrary Python objects into
JSON-safe values, dictionaries, and lists.

It includes utility functions for reading and shaping persisted
application state, such as extracting project-related information.

It parses URL query strings into a mapping of parameter names to lists,
handling invalid input safely.

It discovers available Piper voice models from a configured models
directory and returns structured metadata about them.

The Handler class subclasses SimpleHTTPRequestHandler and customizes
initialization to serve from the application root directory.

Handler methods _send_file, _send_json, and _send_html wrap low-level
HTTP response logic, adding content type, cache, and CORS headers.

Convenience methods _send_json_uncached, _send_html_uncached, and
_send_no_content standardize common response patterns for APIs.

The handler can parse JSON request bodies into dictionaries, returning a
400 error when payloads are invalid.

It defines a family of handle_get* methods for routes like notes, app
config, piper voices, projects, course parts, notes UI, shell, TOC,
static assets, and dynamic views.

Each GET handler checks whether the request path matches its route,
builds the appropriate payload or HTML, and sends it back.

Dynamic view handling can render HTML or serve a related view asset
depending on whether a template is found.

The handler also defines handle_post* methods for executing Python code,
updating app config, updating UI settings, saving notes, and applying
project actions.

Each POST handler validates its route path, parses the JSON body,
updates state or runs actions, and replies with JSON or no-content.

The do_GET method parses the request URL, decomposes path and query
string, and dispatches to the sequence of GET handlers, falling back to
a 404 response if none handle the request.

The do_POST method parses the path and dispatches to the sequence of
POST handlers in priority order, returning 404 when no handler matches.

Overall, this file provides the HTTP interface layer of the viewer
application, bridging URLs to runtime services, state, and HTML
rendering.
"""

from __future__ import annotations

import json
import socket
import urllib.parse
from urllib.parse import ParseResult

from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import BaseServer
from typing import TypedDict

from viewer_app.app.context import AppContext, get_app_context
from viewer_app.http.http_pages import (
    build_shell_html,
    build_toc_html,
    build_view_html,
    guess_content_type,
    resolve_asset_path,
    resolve_view_asset,
)
from viewer_app.http.http_routes import (
    JsonDict,
    JsonValue,
    QueryParams,
    apply_project_action,
    build_course_parts_payload,
    build_notes_get_payload,
    build_projects_get_payload,
    run_python_payload,
    save_notes_payload,
)
from viewer_app.runtime.config import (
    get_app_config_dict,
    load_app_config,
    load_prompt_templates,
    save_app_config,
)
from viewer_app.runtime.paths import AppPaths
from viewer_app.runtime.projects import (
    CoursePart,
    ProjectsService,
    normalize_project_root,
)
from viewer_app.runtime.python_runner import handle_run, run_heavy
from viewer_app.runtime.state import StateStore
from viewer_app.web.web_notes_ui import build_notes_ui_html

__all__ = ["Handler"]

_CACHE_NO_STORE: str = "no-cache, no-store, must-revalidate"
_CACHE_VIEW_ASSET: str = "public, max-age=3600"
_PATH_ASSETS_PREFIX: str = "/assets/"
_PATH_VIEW_PREFIX: str = "/view/"
_PATH_NOTES: str = "/notes"
_PATH_APP_CONFIG: str = "/app-config"
_PATH_PIPER_VOICES: str = "/piper-voices"
_PATH_PROJECTS: str = "/projects"
_PATH_COURSE_PARTS: str = "/course-parts"
_PATH_NOTES_UI: str = "/notes-ui"
_PATH_TOC: str = "/toc"
_STATUS_OK: int = 200
_STATUS_BAD_REQUEST: int = 400
_STATUS_NO_CONTENT: int = 204


class PiperVoiceMeta(TypedDict):
    """
    PiperVoiceMeta stores metadata for a single Piper voice
    configuration.

    It captures identifiers and file relationships needed to load voice
    models.

    Attributes:
        id (str):
            Unique identifier for the Piper voice.
        name (str):
            Human-readable name for the Piper voice.
        modelRel (str):
            Relative filesystem path to the ONNX model file.
        configRel (str):
            Relative filesystem path to the model configuration file.
    """

    id: str
    name: str
    modelRel: str
    configRel: str


_APP_CONTEXT: AppContext = get_app_context()
_APP_ROOT: Path = _APP_CONTEXT.paths.app_root
_APP_PATHS: AppPaths = _APP_CONTEXT.paths
_STATE_STORE: StateStore = _APP_CONTEXT.state
_PROJECTS_SERVICE: ProjectsService = _APP_CONTEXT.projects


def _to_json_value(value: object) -> JsonValue:
    """
    Convert a Python object into a JSON-serializable value.

    This normalizes nested containers while preserving basic scalar
    types.

    Args:
        value (object):
            Input value to normalize into a JSON-compatible
            representation.

    Returns:
        JsonValue:
            Normalized JSON-compatible value, using strings for
            unsupported types.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_json_value(value=item) for item in value]
    if isinstance(value, dict):
        out: JsonDict = {
            key: _to_json_value(value=item)
            for key, item in value.items()
            if isinstance(key, str)
        }
        return out
    return str(value)


def _to_json_dict(value: object) -> JsonDict:
    """
    Normalize a Python object into a JSON dictionary.

    This ensures the result is always a mapping with string keys.

    Args:
        value (object):
            Input value to normalize into a dictionary-shaped JSON
            value.

    Returns:
        JsonDict:
            Normalized JSON dictionary, or an empty dictionary if the
            value cannot be represented as a mapping.
    """
    normalized: JsonValue = _to_json_value(value)
    return normalized if isinstance(normalized, dict) else {}


def _index_course_parts(root: str) -> list[JsonDict]:
    """
    Index course parts for a project root and normalize them as JSON.

    This converts each discovered course part into a JSON-safe
    dictionary.

    Args:
        root (str):
            Project root directory to scan for course parts.

    Returns:
        list[JsonDict]:
            List of normalized JSON dictionaries, one for each indexed
            course part.
    """
    parts_raw: list[CoursePart] = _PROJECTS_SERVICE.index_course_parts(
        project_root=root
    )
    return [_to_json_dict(value=item) for item in parts_raw]


def _load_state_json() -> JsonDict:
    """
    Load the persisted application state as JSON-compatible data.

    This provides a normalized snapshot suitable for serialization.

    Returns:
        JsonDict:
            Canonical JSON dictionary representing the current
            application state, or an empty dictionary if no state is
            available.
    """
    return _to_json_dict(value=_STATE_STORE.load())


def _json_dict_to_object_dict(payload: JsonDict) -> dict[str, object]:
    """
    Convert a JSON dictionary into a plain object dictionary.

    This retypes JSON-compatible values for use in application state.

    Args:
        payload (JsonDict):
            JSON-compatible mapping to convert into a dictionary of
            Python objects.

    Returns:
        dict[str, object]:
            New dictionary containing the same keys and values as the
            input payload, typed as generic Python objects.
    """
    return dict[str, object](payload.items())


def _coerce_json_list(value: object) -> list[JsonValue]:
    """
    Coerce an arbitrary value into a JSON list.

    This guarantees a list result even for non-list inputs.

    Args:
        value (object):
            Input value to normalize into a JSON list representation.

    Returns:
        list[JsonValue]:
            List of JSON-compatible values if the input can be
            interpreted as a list, or an empty list otherwise.
    """
    normalized: JsonValue = _to_json_value(value)
    return normalized if isinstance(normalized, list) else []


def _get_projects_state(state: JsonDict | None) -> JsonDict:
    """
    Extract the projects portion of the persisted state.

    This shapes project information into pinned and recent project
    lists.

    Args:
        state (JsonDict | None):
            Optional full application state snapshot previously loaded
            from storage.

    Returns:
        JsonDict:
            JSON dictionary with "pinned" and "recent" keys, each
            containing a list of project identifiers, defaulting to
            empty lists when unavailable.
    """
    raw: JsonDict = state if isinstance(state, dict) else {}
    projects: JsonValue = raw.get("projects")
    projects = projects if isinstance(projects, dict) else {}
    return {
        "pinned": _coerce_json_list(value=projects.get("pinned")),
        "recent": _coerce_json_list(value=projects.get("recent")),
    }


def _parse_query_string(query: str) -> QueryParams:
    """
    Parse a URL query string into a mapping of parameter names to
    values.
    This safely handles invalid input by returning an empty mapping.

    Args:
        query (str):
            Raw query string portion of a URL, without the leading
            question mark.

    Returns:
        QueryParams:
            Dictionary mapping parameter names to lists of string
            values, or an empty dictionary if the query string is
            invalid or empty.
    """
    try:
        return urllib.parse.parse_qs(qs=query or "")
    except (TypeError, ValueError):
        return {}


def _list_piper_voices() -> list[PiperVoiceMeta]:
    """
    List all available Piper voices discovered in the models directory.

    This filters out incomplete voice folders and returns structured
    metadata.

    Returns:
        list[PiperVoiceMeta]:
            List of Piper voice metadata dictionaries, each describing a
            single voice with identifiers and relative model/config
            paths.
    """
    base: Path = _APP_PATHS.tts_models_root
    out: list[PiperVoiceMeta] = []
    try:
        if not base.exists():
            return out
        for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir():
                continue
            model: Path = d / "model.onnx"
            cfg: Path = d / "model.onnx.json"
            if not (model.exists() and cfg.exists()):
                continue
            voice_id: str = d.name
            meta: PiperVoiceMeta = {
                "id": voice_id,
                "name": voice_id,
                "modelRel": str(model.relative_to(_APP_ROOT)).replace(
                    "\\", "/"
                ),
                "configRel": str(cfg.relative_to(_APP_ROOT)).replace(
                    "\\", "/"
                ),
            }
            out.append(meta)
    except OSError:
        return []
    return out


class Handler(SimpleHTTPRequestHandler):

    def __init__(
        self,
        request: socket.socket,
        client_address: tuple[str, int] | str,
        server: BaseServer,
        *,
        directory: str | None = None,
        **kwargs: object,
    ) -> None:
        del directory
        super().__init__(
            request,
            client_address,
            server,
            directory=str(_APP_ROOT),
            **kwargs,
        )

    def _send_file(self, path: Path, *, cache_control: str) -> None:
        """
        Send a static file response for the given path.

        This sets appropriate headers and streams the file bytes to the
        client.

        Args:
            path (Path):
                Filesystem path of the file to be sent in the response.
            cache_control (str):
                Cache-Control header value indicating desired caching
                behaviour for the response.
        """
        try:
            self.send_response(code=200)
            self.send_header(
                keyword="Content-type", value=guess_content_type(path)
            )
            self.send_header(keyword="Cache-Control", value=cache_control)
            self.end_headers()
            self.wfile.write(path.read_bytes())
        except OSError:
            pass

    def _send_json(
        self,
        payload: object,
        *,
        cache_control: str | None = None,
        cors: bool = False,
        status: int = _STATUS_OK,
    ) -> None:
        """
        Send a JSON response with optional caching and CORS headers.

        This serializes the given payload into JSON and writes it to the
        client.

        Args:
            payload (object):
                Python object to serialize into a JSON response body.
            cache_control (str | None):
                Optional Cache-Control header value controlling client
                and intermediary caching behaviour.
            cors (bool):
                Whether to include a permissive CORS header allowing
                access from any origin.
            status (int):
                HTTP status code to send with the response, such as 200
                for success or 204 for no content.
        """
        try:
            self.send_response(code=status)
            self.send_header(
                keyword="Content-type", value="application/json; charset=utf-8"
            )
            if cache_control:
                self.send_header(keyword="Cache-Control", value=cache_control)
            if cors:
                self.send_header(
                    keyword="Access-Control-Allow-Origin", value="*"
                )
            self.end_headers()
            if status != _STATUS_NO_CONTENT:
                payload_json: JsonValue = _to_json_value(value=payload)
                body: bytes = json.dumps(
                    obj=payload_json, ensure_ascii=False
                ).encode(encoding="utf-8")
                self.wfile.write(body)
        except OSError:
            pass

    def _send_html(
        self,
        page: str,
        *,
        cache_control: str | None = None,
        status: int = _STATUS_OK,
    ) -> None:
        """
        Send an HTML response with optional caching headers.

        This writes the provided HTML page content to the client.

        Args:
            page (str):
                Fully rendered HTML content to send in the response
                body.
            cache_control (str | None):
                Optional Cache-Control header value describing how
                clients and intermediaries may cache the page.
            status (int):
                HTTP status code to send with the response, such as 200
                for success.
        """
        try:
            self.send_response(code=status)
            self.send_header(
                keyword="Content-type", value="text/html; charset=utf-8"
            )
            if cache_control:
                self.send_header(keyword="Cache-Control", value=cache_control)
            self.end_headers()
            self.wfile.write(page.encode(encoding="utf-8"))
        except OSError:
            pass

    def _send_json_uncached(
        self, payload: object, *, cors: bool = False
    ) -> None:
        """
        Send a JSON response with no caching and optional CORS headers.

        This is a convenience wrapper that always disables client
        caching.

        Args:
            payload (object):
                Python object to serialize into a JSON response body.
            cors (bool):
                Whether to include a permissive CORS header allowing
                access from any origin.
        """
        self._send_json(
            payload,
            cache_control=_CACHE_NO_STORE,
            cors=cors,
            status=_STATUS_OK,
        )

    def _send_html_uncached(self, page: str) -> None:
        """
        Send an HTML response with caching disabled.

        This is a convenience wrapper that always returns a fresh page.

        Args:
            page (str):
                Fully rendered HTML content to send in the response
                body.
        """
        self._send_html(page, cache_control=_CACHE_NO_STORE, status=_STATUS_OK)

    def _send_no_content(self, *, cors: bool = False) -> None:
        """
        Send an empty JSON response with a no-content status.

        This is used for successful POST requests that have no body
        payload.

        Args:
            cors (bool):
                Whether to include a permissive CORS header allowing
                access from any origin.
        """
        self._send_json(payload={}, status=_STATUS_NO_CONTENT, cors=cors)

    def _parse_json_body(self, invalid_message: str) -> JsonDict | None:
        """
        Parse the JSON body of the current request into a dictionary.

        This validates the payload and sends an error response if
        parsing fails.

        Args:
            invalid_message (str):
                Error message to include in the JSON response when the
                request body cannot be parsed as valid JSON.

        Returns:
            JsonDict | None:
                Parsed JSON dictionary representing the request body, or
                None if the payload is invalid and an error response
                has already been sent.
        """
        try:
            length: int = int(self.headers.get("Content-Length") or "0")
            raw_body: bytes = self.rfile.read(length) if length else b""
            data: object = json.loads(
                raw_body.decode(encoding="utf-8", errors="replace")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json(
                payload={"error": invalid_message},
                status=_STATUS_BAD_REQUEST,
                cors=True,
                cache_control=_CACHE_NO_STORE,
            )
            return None
        return _to_json_dict(value=data)

    def _handle_get_notes(self, qs: QueryParams) -> bool:
        """
        Handle a GET request for the notes JSON API.

        This resolves the correct route and returns the notes payload.

        Args:
            qs (QueryParams):
                Parsed query string parameters used to determine which
                notes data to load and return.

        Returns:
            bool:
                True if the request path matched the notes endpoint and
                a response was sent, or False if the handler did not
                process the request.
        """
        if (
            urllib.parse.unquote(string=urllib.parse.urlparse(self.path).path)
            != _PATH_NOTES
        ):
            return False
        self._send_json_uncached(
            payload=build_notes_get_payload(
                qs=qs,
                load_state_json=_load_state_json,
                normalize_project_root=normalize_project_root,
            ),
            cors=True,
        )
        return True

    def _handle_get_app_config(self, path: str) -> bool:
        """
        Handle a GET request for the app configuration JSON API.

        This returns the current application configuration when the
        route matches.

        Args:
            path (str):
                Normalized request path to check against the app
                configuration endpoint.

        Returns:
            bool:
                True if the path matched and a configuration response
                was sent, or False if this handler did not process the
                request.
        """
        if path != _PATH_APP_CONFIG:
            return False
        self._send_json_uncached(
            payload=get_app_config_dict(paths=_APP_PATHS), cors=True
        )
        return True

    def _handle_get_piper_voices(self, path: str) -> bool:
        """
        Handle a GET request for the Piper voices JSON API.

        This returns the list of discovered Piper voice configurations
        when the path matches.

        Args:
            path (str):
                Normalized request path to check against the Piper
                voices endpoint.

        Returns:
            bool:
                True if the path matched and a voices response was sent,
                or False if this handler did not process the request.
        """
        if path != _PATH_PIPER_VOICES:
            return False
        self._send_json_uncached(
            payload={"voices": _list_piper_voices()}, cors=True
        )
        return True

    def _handle_get_projects(self, path: str) -> bool:
        """
        Handle a GET request for the projects JSON API.

        This returns the current projects view derived from persisted
        state.

        Args:
            path (str):
                Normalized request path to check against the projects
                endpoint.

        Returns:
            bool:
                True if the path matched and a projects response was
                sent, or False if this handler did not process the
                request.
        """
        if path != _PATH_PROJECTS:
            return False
        self._send_json_uncached(
            payload=build_projects_get_payload(
                load_state_json=_load_state_json,
                get_projects_state=_get_projects_state,
            ),
            cors=True,
        )
        return True

    def _handle_get_course_parts(self, path: str, qs: QueryParams) -> bool:
        """
        Handle a GET request for the course parts JSON API.

        This returns indexed course parts for the selected project when
        matched.

        Args:
            path (str):
                Normalized request path to check against the course
                parts endpoint.
            qs (QueryParams):
                Parsed query string parameters used to determine the
                target project or filtering options.

        Returns:
            bool:
                True if the path matched and a course parts response was
                sent, or False if this handler did not process the
                request.
        """
        if path != _PATH_COURSE_PARTS:
            return False
        self._send_json_uncached(
            payload=build_course_parts_payload(
                qs=qs,
                load_state_json=_load_state_json,
                normalize_project_root=normalize_project_root,
                index_course_parts=_index_course_parts,
            ),
            cors=True,
        )
        return True

    def _handle_get_notes_ui(self, path: str) -> bool:
        """
        Handle a GET request for the notes UI HTML page.

        This returns the rendered notes user interface when the path
        matches.

        Args:
            path (str):
                Normalized request path to check against the notes UI
                endpoint.

        Returns:
            bool:
                True if the path matched and the notes UI page was sent,
                or False if this handler did not process the request.
        """
        if path != _PATH_NOTES_UI:
            return False
        self._send_html_uncached(
            page=build_notes_ui_html(initial_state=_STATE_STORE.load())
        )
        return True

    def _handle_get_shell(self, path: str, query: str) -> bool:
        """
        Handle a GET request for the main shell HTML page.

        This returns the application shell populated with the current
        state.

        Args:
            path (str):
                Normalized request path to check against the shell
                endpoint, typically the root path.
            query (str):
                Raw query string used to initialize routing or view
                state in the shell.

        Returns:
            bool:
                True if the path matched and the shell page was sent, or
                False if this handler did not process the request.
        """
        if path not in ("", "/"):
            return False
        self._send_html_uncached(
            page=build_shell_html(
                paths=_APP_PATHS, state=_load_state_json(), query=query
            )
        )
        return True

    def _handle_get_toc(self, path: str, query: str) -> bool:
        """
        Handle a GET request for the table-of-contents HTML page.

        This renders the TOC view based on the requested query
        parameters.

        Args:
            path (str):
                Normalized request path to check against the TOC
                endpoint.
            query (str):
                Raw query string used to determine which plan or section
                the TOC should represent.

        Returns:
            bool:
                True if the path matched and the TOC page was sent, or
                False if this handler did not process the request.
        """
        if path != _PATH_TOC:
            return False
        toc: str = build_toc_html(
            query=query, plans_dir=load_app_config(paths=_APP_PATHS).plans_dir
        )
        self._send_html(page=toc)
        return True

    def _handle_get_assets(self, path: str) -> bool:
        """
        Handle a GET request for static asset files.

        This resolves asset paths and serves them with appropriate
        headers.

        Args:
            path (str):
                Normalized request path expected to start with the
                assets prefix.

        Returns:
            bool:
                True if the path referenced a known asset and a response
                was sent, or False if the handler did not process the
                request.
        """
        if not path.startswith(_PATH_ASSETS_PREFIX):
            return False
        asset_name: str = path[len(_PATH_ASSETS_PREFIX) :]
        asset_path: Path | None = resolve_asset_path(asset_name)
        if asset_path is None:
            self.send_error(code=404)
            return True
        self._send_file(path=asset_path, cache_control=_CACHE_NO_STORE)
        return True

    def _handle_get_view(self, path: str, query: str) -> bool:
        """
        Handle a GET request for a dynamic view or view asset.

        This serves either a rendered view HTML page or a related static
        file.

        Args:
            path (str):
                Normalized request path to check against the view prefix
                and locate the view resource.
            query (str):
                Raw query string used to determine which view or
                resource should be rendered or resolved.

        Returns:
            bool:
                True if a view page or asset was successfully resolved
                and sent, or False if this handler did not process the
                request.
        """
        if not path.startswith(_PATH_VIEW_PREFIX):
            return False
        prompts: dict[str, str] = load_prompt_templates(paths=_APP_PATHS)
        view_html: str | None = build_view_html(
            paths=_APP_PATHS,
            query=query,
            request_path=path,
            prompts=prompts,
        )
        if view_html is not None:
            self._send_html(page=view_html)
            return True
        plans_dir: Path = load_app_config(paths=_APP_PATHS).plans_dir
        asset_path: Path | None = resolve_view_asset(
            plans_dir=plans_dir, query=query, request_path=path
        )
        if asset_path is not None:
            self._send_file(path=asset_path, cache_control=_CACHE_VIEW_ASSET)
            return True
        return False

    def _handle_post_run(self, post_path: str) -> bool:
        """
        Handle a POST request to execute Python code.

        This parses the request payload, runs the code, and returns the
        result.

        Args:
            post_path (str):
                Normalized request path that must match the run endpoint
                in order for the handler to process the request.

        Returns:
            bool:
                True if the path matched and a run response was sent, or
                False if this handler did not process the request.
        """
        if post_path != "/run":
            return False
        data: JsonDict | None = self._parse_json_body(
            invalid_message="Invalid request"
        )
        if data is None:
            return True
        self._send_json_uncached(
            payload=run_python_payload(
                data=data, run_heavy=run_heavy, handle_run=handle_run
            ),
            cors=True,
        )
        return True

    def _handle_post_app_settings(self, post_path: str) -> bool:
        """
        Handle a POST request to update application settings.

        This saves the new configuration and reloads it into the running
        app.

        Args:
            post_path (str):
                Normalized request path that must match the app-settings
                endpoint in order for the handler to process the
                request.

        Returns:
            bool:
                True if the path matched and the settings were applied
                and acknowledged, or False if this handler did not
                process the request.
        """
        if post_path != "/app-settings":
            return False
        body: JsonDict | None = self._parse_json_body(
            invalid_message="Invalid app settings payload"
        )
        if body is None:
            return True
        save_app_config(paths=_APP_PATHS, payload=body)
        _APP_CONTEXT.reload_config()
        self._send_no_content(cors=True)
        return True

    def _handle_post_settings(self, post_path: str) -> bool:
        """
        Handle a POST request to update persisted UI settings.

        This merges the incoming settings payload into the stored
        application state.

        Args:
            post_path (str):
                Normalized request path that must match the settings
                endpoint in order for the handler to process the
                request.

        Returns:
            bool:
                True if the path matched and the settings were updated
                and acknowledged, or False if this handler did not
                process the request.
        """
        if post_path != "/settings":
            return False
        body: JsonDict | None = self._parse_json_body(
            invalid_message="Invalid settings payload"
        )
        if body is None:
            return True
        _STATE_STORE.update(patch=_json_dict_to_object_dict(payload=body))
        self._send_no_content(cors=True)
        return True

    def _handle_post_notes(self, post_path: str) -> bool:
        """
        Handle a POST request to save notes for the active project.

        This updates persisted notes state based on the incoming
        payload.

        Args:
            post_path (str):
                Normalized request path that must match the notes
                endpoint in order for the handler to process the
                request.

        Returns:
            bool:
                True if the path matched and the notes were saved and
                acknowledged, or False if this handler did not process
                the request.
        """
        if post_path != _PATH_NOTES:
            return False
        body: JsonDict | None = self._parse_json_body(
            invalid_message="Invalid notes payload"
        )
        if body is None:
            return True
        save_notes_payload(
            data=body,
            load_state_json=_load_state_json,
            normalize_project_root=normalize_project_root,
        )
        self._send_no_content(cors=True)
        return True

    def _handle_post_projects(self, post_path: str) -> bool:
        """
        Handle a POST request to apply a project action.

        This updates project selection or pinning state based on the
        payload.

        Args:
            post_path (str):
                Normalized request path that must match the projects
                endpoint in order for the handler to process the
                request.

        Returns:
            bool:
                True if the path matched and the project action was
                applied and acknowledged, or False if this handler did
                not process the request.
        """
        if post_path != _PATH_PROJECTS:
            return False
        body: JsonDict | None = self._parse_json_body(
            invalid_message="Invalid projects payload"
        )
        if body is None:
            return True
        apply_project_action(
            data=body,
            normalize_project_root=normalize_project_root,
            set_active_project=_PROJECTS_SERVICE.set_active,
            touch_project_recent=_PROJECTS_SERVICE.touch_recent,
            toggle_pin_project=_PROJECTS_SERVICE.toggle_pin,
        )
        self._send_no_content(cors=True)
        return True

    def do_GET(self) -> None:  # noqa: N802
        """
        Dispatch an incoming GET request to the appropriate handler.

        This routes API and HTML requests and returns 404 for unknown
        paths.

        Returns:
            None:
                This method does not return a value. It writes the HTTP
                response directly to the client or delegates to helper
                handlers that do so.
        """
        parsed: ParseResult = urllib.parse.urlparse(self.path)
        path: str = urllib.parse.unquote(string=parsed.path).split(sep="?")[0]
        query: str = parsed.query or ""
        qs: QueryParams = _parse_query_string(query=query)
        handled: bool = (
            self._handle_get_notes(qs)
            or self._handle_get_app_config(path)
            or self._handle_get_piper_voices(path)
            or self._handle_get_projects(path)
            or self._handle_get_course_parts(path, qs)
            or self._handle_get_notes_ui(path)
            or self._handle_get_shell(path, query)
            or self._handle_get_toc(path, query)
            or self._handle_get_assets(path)
            or self._handle_get_view(path, query)
        )
        if handled:
            return
        self.send_error(code=404)

    def do_POST(self) -> None:  # noqa: N802
        """
        Dispatch an incoming POST request to the appropriate handler.

        This routes JSON API write operations and returns 404 for
        unknown paths.

        Returns:
            None:
                This method does not return a value. It writes the HTTP
                response directly to the client or delegates to helper
                handlers that do so.
        """
        parsed: ParseResult = urllib.parse.urlparse(self.path)
        post_path: str = parsed.path
        if self._handle_post_run(post_path):
            return
        if self._handle_post_app_settings(post_path):
            return
        if self._handle_post_settings(post_path):
            return
        if self._handle_post_notes(post_path):
            return
        if self._handle_post_projects(post_path):
            return
        self.send_error(code=404)
