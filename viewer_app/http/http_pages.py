"""
This module builds HTML pages and resolves file paths for a
markdown-based viewer and interactive shell interface.

It acts as the HTTP-side rendering and asset-resolution layer around
markdown content, configuration, and runtime state.

It defines constants for web asset locations, URL prefixes, default
markdown filenames, and feature flags like dark-mode tokens.

It introduces JSON type aliases and two TypedDicts, ShellBootstrap and
ViewBootstrap, that describe the JSON payloads sent to the client for
initializing the shell and viewer.

The json_for_script_tag function serializes mappings to JSON and escapes
closing script tags so the JSON can be safely embedded in HTML.

The get_root_from_query function parses the query string to select a
content root directory from a root parameter, falling back to a default
when invalid.

The resolve_asset_path function maps a requested asset name to a file
under the web assets directory while preventing directory traversal
outside that root.

The guess_content_type function infers an HTTP Content-Type for a given
file path, with explicit handling for JavaScript, CSS, and WebAssembly.

The _effective_plans_dir helper chooses between a configured plans
directory and the application root based on existence.

The _shell_content_root function decides which directory the shell
should use as its content root, preferring an explicit query root, then
remembered state, then the default plans directory.

The _python_select_options function discovers available Python
interpreters and returns HTML option tags for a selection dropdown.

The _default_view_iframe_src function picks a default iframe URL by
preferring a known index markdown file or the first markdown file under
the root.

The build_shell_html function loads configuration and prompt templates,
determines the content root, builds a navigation tree, constructs
bootstrap JSON, and returns a full HTML document string for the
interactive shell UI.

This HTML includes toolbars, file navigation, table-of-contents panel, a
document iframe, a Python interpreter panel, TTS controls, and wiring
for client-side scripts.

The build_toc_html function reads a markdown file identified by the
query parameters, extracts its generated table of contents, and
rewrites anchors into full view URLs targeting the iframe.

The _view_rel_from_request function strips the /view/ prefix from a
request path and URL-decodes it to get a relative document path.

The _resolve_markdown_fs_path function ensures a requested path resolves
to an existing .md file under the view root, with a fallback to a
configured index markdown.

The _doc_nav_block function computes previous and next document paths in
the tree and returns an HTML navigation block linking to them.

The _reader_dark_mode function inspects query parameters to decide
whether the reader should use a dark theme.

The build_view_html function loads configuration, resolves the view root
and markdown file from the request, converts markdown to HTML, rewrites
asset and internal links, adds navigation and optional MathJax, and
returns a complete HTML document for the reader view.

It also injects prompt templates and dark-mode styling and includes
mermaid support scripts.

The resolve_view_asset function resolves arbitrary view assets beneath
the effective view root and returns a filesystem path only if the asset
exists as a file and does not escape the root directory.

Overall, this module mediates between HTTP-level requests and on-disk
markdown or asset files, producing safe, fully composed HTML responses
for both shell and reader experiences.
"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.parse
from collections.abc import Mapping, Sequence

import html
from pathlib import Path
from typing import TypeAlias, TypedDict

from viewer_app.core.markdown_core import (
    MATHJAX_SCRIPT,
    get_pygments_css,
    md_to_html,
)
from viewer_app.core.navigation import (
    build_tree,
    get_prev_next,
    rewrite_document_asset_urls,
    rewrite_document_markdown_links,
)
from viewer_app.runtime.config import (
    AppConfig,
    AppConfigItems,
    effective_explain_prompt_key,
    get_app_config_dict,
    load_app_config,
    load_prompt_templates,
)
from viewer_app.runtime.paths import AppPaths
from viewer_app.runtime.python_runner import (
    PythonVersion,
    scan_python_versions,
)

_WEB_ASSET_ROOT: Path = (
    Path(__file__).resolve().parent.parent / "web" / "web_assets"
)
_VIEW_PATH_PREFIX: str = "/view/"
_INDEX_MARKDOWN_NAME: str = "python3_mastery_plan.md"
_DEFAULT_PYTHON_VERSIONS: list[PythonVersion] = [
    {"path": "python3", "version": "Python"},
]
_INLINE_MATH_DELIMS: re.Pattern[str] = re.compile(r"\\\(|\\\)")
_MERMAID_BODY_SCRIPTS: str = """<script src="/assets/mermaid.min.js" id="md-viewer-mermaid-js"
  onerror="this.onerror=null;this.src='https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js'"></script>"""
_DARK_READER_FLAGS: frozenset[str] = frozenset[str](
    {"dark", "1", "true", "yes"}
)

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


class ShellBootstrap(TypedDict):
    """
    Defines the JSON bootstrap payload used to initialize the shell view
    in the client.

    It bundles the persisted UI state, application settings, and prompt
    templates into a single structured object.

    Attributes:
        initialState (JsonDict):
            Snapshot of the current client-facing application state used
            to hydrate the shell UI on load.
        appConfig (AppConfigItems):
            Raw application configuration key-value items that describe
            settings such as plans directory and titles.
        promptTemplates (dict[str, str]):
            Mapping of prompt template identifiers to their string
            bodies used by the integrated assistant features.
        explainPromptKey (str):
            Active ``prompts.json`` key for explain-selection prompts
            (``explain_ru`` or ``explain_en``).
    """

    initialState: JsonDict
    appConfig: AppConfigItems
    promptTemplates: dict[str, str]
    explainPromptKey: str


class ViewBootstrap(TypedDict):
    """
    Defines the minimal JSON bootstrap payload required for the document
    view page.

    It currently exposes only prompt templates used by the view.

    Attributes:
        promptTemplates (dict[str, str]):
            Mapping of prompt template identifiers to their string
            bodies made available to the in-view assistant tools.
        explainPromptKey (str):
            Same as shell bootstrap; which template to use in the reader.
    """

    promptTemplates: dict[str, str]
    explainPromptKey: str


def json_for_script_tag(payload: Mapping[str, object]) -> str:
    """
    Serializes a mapping to JSON suitable for embedding in an HTML
    script tag.

    It escapes closing script delimiters so the JSON cannot prematurely
    terminate the surrounding script element.

    Args:
        payload (Mapping[str, object]):
            Mapping of keys to values that should be encoded as a JSON
            object for client-side consumption.

    Returns:
        str:
            A JSON string with </ sequences escaped as <\\/ to keep the
            content safe when inserted inside an HTML <script> tag.
    """
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def get_root_from_query(query: str, default_root: Path) -> Path:
    """
    Resolves a content root directory from a URL query string, falling
    back to a default path when necessary.

    It validates the requested root to ensure it points to an existing
    directory before using it.

    Args:
        query (str):
            Raw query-string portion of the URL, typically in the form
            "root=/some/path&other=...".
        default_root (Path):
            Filesystem directory to return when the query does not
            specify a valid root or when the resolved path is not an
            existing directory.

    Returns:
        Path:
            The resolved and expanded root directory derived from the
            "root" query parameter when valid, or the default_root
            value when the parameter is missing, empty, or does not
            refer to an existing directory.
    """
    params: dict[str, list[str]] = urllib.parse.parse_qs(qs=query)
    roots: list[str] = params.get("root", [])
    if roots:
        root: Path = Path(roots[0].strip()).expanduser()
        if root.is_dir():
            return root.resolve()
    return default_root


def resolve_asset_path(asset_name: str) -> Path | None:
    """
    Resolves an HTTP asset name to a safe filesystem path under the
    applications web assets directory.

    It prevents path traversal by ensuring the resolved path stays
    within the configured asset root.

    Args:
        asset_name (str):
            Relative asset path requested by the client, such as
            "viewer.css" or "images/logo.png".

    Returns:
        Path | None:
            The resolved filesystem path when the asset is located
            inside the web assets root and exists as a file, or None
            when the path escapes the root or the file does not exist.
    """
    root_resolved: Path = _WEB_ASSET_ROOT.resolve()
    candidate: Path = (root_resolved / asset_name).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def guess_content_type(path: Path) -> str:
    """
    Infers an appropriate HTTP Content-Type header value for a file
    based on its path.

    It adds sensible defaults for common web assets when the standard
    MIME type lookup is insufficient.

    Args:
        path (Path):
            Filesystem path of the asset whose content type should be
            determined, typically including an extension such as .js or
            .css.

    Returns:
        str:
            A MIME type string with charset where appropriate,
            preferring explicit types for JavaScript, CSS, and
            WebAssembly, and falling back to the system guess or
            application/octet-stream when no type can be determined.
    """
    content_type, _ = mimetypes.guess_type(url=str(path))
    suffix: str = path.suffix.lower()
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".wasm":
        return "application/wasm"
    return content_type or "application/octet-stream"


def _effective_plans_dir(cfg: AppConfig, paths: AppPaths) -> Path:
    """
    Resolves the effective plans directory based on application settings
    and fallback paths.

    It prefers the configured plans directory when it exists, otherwise
    it falls back to the application root directory.

    Args:
        cfg (AppConfig):
            Application configuration object providing the primary plans
            directory path to consider.
        paths (AppPaths):
            Collection of important application filesystem paths used to
            determine a suitable fallback directory.

    Returns:
        Path:
            The existing plans directory from the configuration when
            available, or the application root directory when the
            configured path is missing or invalid.
    """
    return cfg.plans_dir if cfg.plans_dir.exists() else paths.app_root


def _shell_content_root(
    *, cfg: AppConfig, paths: AppPaths, state: JsonDict, query: str
) -> Path:
    """
    Determines the content root directory to use for the shell interface
    based on configuration, saved state, and the current request.

    It prefers an explicit root in the query string, otherwise it falls
    back to the last remembered root or the default plans directory.

    Args:
        cfg (AppConfig):
            Application configuration providing the default plans
            directory used when no valid root override is found.
        paths (AppPaths):
            Collection of application filesystem paths used indirectly
            to derive the effective plans directory.
        state (JsonDict):
            Persisted shell state that may contain a "currentDoc" entry
            with a previously selected root path.
        query (str):
            Raw query-string portion of the URL whose "root" parameter,
            "root" parameter, when present and valid, overrides other
            root sources.

    Returns:
        Path:
            Directory that should act as the shell content root, chosen
            from the query parameter, remembered state, or the
            configured plans directory, in that order of preference.
    """
    plans_dir: Path = _effective_plans_dir(cfg, paths)
    current_doc: JsonValue = state.get("currentDoc")
    current_doc = current_doc if isinstance(current_doc, dict) else {}
    remembered_root: JsonValue = current_doc.get("root")
    if "root=" in (query or ""):
        return get_root_from_query(query, default_root=plans_dir)
    root: Path = (
        Path(str(remembered_root)).expanduser().resolve()
        if isinstance(remembered_root, str) and remembered_root.strip()
        else plans_dir
    )
    if not root.exists() or not root.is_dir():
        return get_root_from_query(query, default_root=plans_dir)
    return root


def _python_select_options() -> str:
    """
    Builds HTML option tags for the available Python interpreter
    choices.

    It converts discovered interpreter metadata into escaped <option>
    elements ready for insertion into a select control.

    Returns:
        str:
            Concatenated HTML string containing one <option> element per
            discovered Python interpreter, suitable for embedding
            inside a <select> element.
    """
    versions: list[PythonVersion] = (
        scan_python_versions() or _DEFAULT_PYTHON_VERSIONS
    )
    return "".join(
        f'<option value="{html.escape(item["path"])}">{html.escape(item["version"])}</option>'
        for item in versions
    )


def _default_view_iframe_src(root: Path, root_param: str) -> str:
    """
    Selects an appropriate default iframe source URL for the view panel
    given a content root directory and root query parameter.

    It prefers a well-known index markdown document when available and
    otherwise falls back to the first markdown file found under the
    root.

    Args:
        root (Path):
            Filesystem directory that serves as the logical content root
            from which markdown documents are discovered.
        root_param (str):
            Preformatted query-string fragment that encodes the selected
            root directory and is appended to any returned URL.

    Returns:
        str:
            URL path suitable for use as an iframe src attribute,
            pointing at either the index markdown file, the first
            discovered markdown file, or an empty string when no
            markdown documents exist under the root.
    """
    index_path: Path = root / _INDEX_MARKDOWN_NAME
    if index_path.exists():
        rel: str = _INDEX_MARKDOWN_NAME
        return f"/view/{urllib.parse.quote(rel, safe='/')}?{root_param}"
    first_md: Path | None = next(root.rglob(pattern="*.md"), None)
    if first_md is not None:
        rel = str(first_md.relative_to(root)).replace("\\", "/")
        return f"/view/{urllib.parse.quote(rel, safe='/')}?{root_param}"
    return ""


def build_shell_html(*, paths: AppPaths, state: JsonDict, query: str) -> str:
    """
    Builds the complete HTML document for the interactive shell view
    page.

    It assembles layout markup, bootstrap JSON, and configuration-driven
    content into a ready-to-serve response string.

    Args:
        paths (AppPaths):
            Collection of filesystem locations used to load
            configuration, prompt templates, and plan documents for the
            shell.
        state (JsonDict):
            Persisted UI state snapshot that is injected into the
            bootstrap payload to restore the shell on load.
        query (str):
            Raw query-string portion of the shell URL used to select the
            current content root and other runtime options.

    Returns:
        str:
            Fully rendered HTML5 document string for the shell
            interface, including navigation tree, initial iframe
            source, and embedded bootstrap data.
    """
    app_config: AppConfig = load_app_config(paths)
    prompts: dict[str, str] = load_prompt_templates(paths)
    root: Path = _shell_content_root(
        cfg=app_config, paths=paths, state=state, query=query
    )
    root_param: str = f"root={urllib.parse.quote(str(root))}"
    tree: str = build_tree(folder=root, base="", root_param=root_param)
    default_src: str = _default_view_iframe_src(
        root=root, root_param=root_param
    )
    explain_key: str = effective_explain_prompt_key(paths)
    bootstrap: ShellBootstrap = {
        "initialState": dict[str, JsonValue](state),
        "appConfig": get_app_config_dict(paths),
        "promptTemplates": prompts,
        "explainPromptKey": explain_key,
    }
    bootstrap_json: str = json_for_script_tag(payload=bootstrap)
    py_options: str = _python_select_options()
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><title>{html.escape(app_config.app_title)}</title>
<script id="mdViewerShellBootstrap" type="application/json">{bootstrap_json}</script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/monokai.min.css" rel="stylesheet">
<link href="/assets/shell.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/python/python.min.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
</head>
<body>
<div class="layout">
  <div class="top-bar">
      <div class="brand">
        <div class="brand-badge">MD</div>
        <div>
          <div class="brand-title">{html.escape(app_config.app_title)}</div>
          <div class="brand-subtitle">{html.escape(app_config.app_subtitle)}</div>
        </div>
      </div>
    <div class="top-actions">
      <input type="search" id="libraryFilter" class="toolbar-input" placeholder="Filter files and folders...">
      <button type="button" class="toolbar-btn" id="readerSettingsToggle" title="Show toolbar">Tools</button>
      <button type="button" class="toolbar-btn" id="ttsPanelToggle" title="Open speech panel">Speech</button>
    </div>
    <span class="ctrl-group">
      <label><input type="checkbox" id="toggleFiles" checked> Files</label>
      <label><input type="checkbox" id="toggleToc" checked> Contents</label>
      <label><input type="checkbox" id="toggleContent" checked> Document</label>
      <label><input type="checkbox" id="toggleInterpreter" checked> Interpreter</label>
    </span>
    <div class="search-wrap"></div>
  </div>
  <div class="meta-strip">
    <div class="meta-pill"><span class="dot"></span><strong id="metaDocTitle">No document open</strong></div>
    <div class="meta-pill" id="metaSectionPill">Section: <strong id="metaSection">—</strong></div>
    <div class="meta-pill" id="metaProgressPill">Progress: <strong id="metaProgress">0%</strong></div>
    <div class="meta-pill" id="metaReadingPill">Reading: <strong id="metaReadingTime">—</strong></div>
    <div class="meta-progress-bar"><span id="progressFill"></span></div>
  </div>
  <div class="reader-settings-bar" id="readerSettingsBar">
    <div class="reader-settings-group">
      <button type="button" class="toolbar-btn" id="toggleFocusBtn" title="Hide distractions and keep reading view only">Focus</button>
      <button type="button" class="toolbar-btn" id="readerFontDown" title="Decrease text size">A-</button>
      <button type="button" class="toolbar-btn" id="readerFontReset" title="Default text size">A</button>
      <button type="button" class="toolbar-btn" id="readerFontUp" title="Increase text size">A+</button>
      <button type="button" class="toolbar-btn" id="readerWidthBtn" title="Toggle text width">Width</button>
      <button type="button" class="toolbar-btn" id="favoriteBtn" title="Add to favorites">★</button>
      <button type="button" class="toolbar-btn" id="completeBtn" title="Mark as completed">✓</button>
    </div>
    <div class="reader-settings-group reader-settings-toggles">
      <label><input type="checkbox" id="toggleMetaSection" checked> Section</label>
      <label><input type="checkbox" id="toggleMetaProgress" checked> Progress</label>
      <label><input type="checkbox" id="toggleMetaReading" checked> Reading time</label>
      <label><input type="checkbox" id="toggleSepia"> Sepia</label>
    </div>
  </div>
  <div class="tts-panel" id="ttsPanel">
    <button type="button" class="toolbar-btn" id="ttsSpeakCmd" title="Speak (selection or document)">▶</button>
    <button type="button" class="toolbar-btn" id="ttsPauseCmd" title="Pause / resume">⏸</button>
    <button type="button" class="toolbar-btn" id="ttsStopCmd" title="Stop">⏹</button>
    <label style="display:inline-flex;align-items:center;gap:8px;font-size:12px;color:#64748b;user-select:none;">
      <input type="checkbox" id="ttsFollow" checked>
      Follow
    </label>
    <span style="display:inline-flex;align-items:center;gap:8px;font-size:12px;color:#64748b;">
      Speed
      <input type="range" id="ttsSpeedRange" min="0.60" max="1.80" step="0.05" value="1.00" style="width:180px;">
      <span id="ttsSpeedLabel" style="min-width:54px;text-align:right;">x1.00</span>
    </span>
    <span style="display:inline-flex;align-items:center;gap:8px;font-size:12px;color:#64748b;">
      Pause
      <input type="range" id="ttsSentenceSilenceRange" min="0.00" max="0.80" step="0.05" value="0.25" style="width:160px;">
      <span id="ttsSentenceSilenceLabel" style="min-width:54px;text-align:right;">0.25s</span>
    </span>
  </div>
  <div class="tts-now" id="ttsNow">
    <div class="tts-now-title">Now reading</div>
    <div class="tts-now-text" id="ttsNowText">—</div>
  </div>
  <div class="modal-backdrop" id="appSettingsModal">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="appSettingsTitle">
      <div class="modal-header">
        <div class="modal-title" id="appSettingsTitle">Application settings</div>
        <button type="button" class="modal-close" id="appSettingsClose">Close</button>
      </div>
      <div class="modal-body" id="appSettingsForm"></div>
      <div class="modal-footer">
        <button type="button" class="btn ghost" id="appSettingsCancel">Cancel</button>
        <button type="button" class="btn primary" id="appSettingsSave">Save</button>
      </div>
    </div>
  </div>
  <div class="modal-backdrop" id="notesModal">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="notesModalTitle">
      <div class="modal-header">
        <div class="modal-title" id="notesModalTitle">Note</div>
        <button type="button" class="modal-close" id="notesModalClose">Close</button>
      </div>
      <div class="modal-body">
        <div class="form-hint" id="notesModalMeta" style="margin-bottom:10px;">—</div>
        <div class="notes-modal-label">Excerpt</div>
        <div id="notesModalQuote"></div>
        <div class="notes-modal-label">Note</div>
        <textarea id="notesModalText"></textarea>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn ghost" id="notesModalCancel">Cancel</button>
        <button type="button" class="btn primary" id="notesModalSave">Save</button>
      </div>
    </div>
  </div>
  <div class="layout-row">
  <nav class="sidebar" id="sidebar">
    <div class="sidebar-section" id="favoritesSection">
      <div class="sidebar-title">Favorites</div>
      <div id="favoritesList" class="sidebar-list"><div class="sidebar-empty">Empty for now.</div></div>
    </div>
    <div class="sidebar-section" id="filesSection">
      <div class="sidebar-title">Files</div>
      <div id="fileTreeWrap">{tree}</div>
    </div>
  </nav>
  <aside class="toc-panel" id="tocPanel"><div class="sidebar-title">Contents</div><div id="tocContent"></div></aside>
  <main class="content" id="contentMain"><div class="content-inner">
    <iframe name="content" id="contentFrame" src="{html.escape(default_src)}"></iframe>
    <div class="content-loading" id="contentLoading" aria-hidden="true">
      <span class="content-loading-spinner" role="status" aria-label="Loading document"></span>
    </div>
  </div></main>
  <aside class="interpreter" id="interpreter">
    <div class="interpreter-header">
      <select id="pythonSelect">{py_options}</select>
      <button class="interpreter-btn" id="replRun" title="Run">▶</button>
    </div>
    <div class="interpreter-body">
      <div class="interpreter-editor-wrap">
        <div class="interpreter-section-label">Code</div>
        <textarea class="interpreter-editor" id="replCode" placeholder="print('Hello')"></textarea>
        <div id="replCodeMirror" style="flex:1;min-height:0;display:none;"></div>
      </div>
      <div class="interpreter-output-wrap">
        <div class="interpreter-section-label">Output</div>
        <pre class="interpreter-output" id="replOut"></pre>
      </div>
    </div>
  </aside>
  </div>
</div>
<script src="/assets/shell.js"></script>
</body></html>"""


def build_toc_html(*, query: str, plans_dir: Path) -> str:
    """
    Builds an HTML fragment representing the table of contents for a
    markdown document.

    It reads the requested markdown file, extracts its generated TOC
    markup, and rewrites anchors into full view URLs.

    Args:
        query (str):
            Raw query-string containing at least a "path" parameter that
            identifies the markdown file relative to the view root and
            an optional "root" parameter that overrides the default
            plans directory.
        plans_dir (Path):
            Default directory containing plan markdown files, used as
            the view root when no explicit "root" parameter is provided.

    Returns:
        str:
            HTML snippet for the table of contents with each anchor
            pointing at the corresponding view URL and targeting the
            content frame, or an empty string when the TOC cannot be
            built.
    """
    params: dict[str, list[str]] = urllib.parse.parse_qs(qs=query)
    rel_path: str = (params.get("path", [""])[0] or "").strip()
    roots: list[str] = params.get("root", [])
    view_root: Path = (
        Path(roots[0].strip()).resolve()
        if roots and str(roots[0]).strip()
        else plans_dir
    )
    fs_path: Path = (view_root / rel_path).resolve()
    view_prefix: str = str(view_root)
    if (
        not rel_path
        or not fs_path.exists()
        or not str(fs_path).startswith(view_prefix)
    ):
        return ""
    try:
        text: str = fs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _, toc = md_to_html(text)
    root_param: str = urllib.parse.quote(str(view_root))
    base_url: str = f"/view/{urllib.parse.quote(rel_path)}?root={root_param}"
    return re.sub(
        r'href="#([^"]+)"',
        lambda match: f'href="{base_url}#{match.group(1)}" target="content"',
        toc,
    )


def _view_rel_from_request(request_path: str) -> str:
    """
    Normalizes a view request path into a relative document path.

    It strips the fixed view prefix and decodes any URL-encoded
    characters.

    Args:
        request_path (str):
            Raw HTTP request path beginning with the view prefix,
            typically including a URL-encoded relative markdown file
            location.

    Returns:
        str:
            Clean, decoded relative path to the requested markdown
            document within the current view root, without any leading
            slashes or view prefix.
    """
    rel: str = request_path.removeprefix(_VIEW_PATH_PREFIX).lstrip("/")
    return urllib.parse.unquote(string=rel)


def _resolve_markdown_fs_path(view_root: Path, rel_path: str) -> Path | None:
    """
    Resolves a markdown filesystem path for a requested document within
    a view root.

    It ensures the path refers to an existing markdown file and
    optionally falls back to the configured index document.

    Args:
        view_root (Path):
            Base directory that acts as the root for markdown documents
            in the current view.
        rel_path (str):
            Relative path from the view root to the requested markdown
            document, typically derived from the request URL.

    Returns:
        Path | None:
            Filesystem path to the resolved markdown document when it
            exists and has a .md extension, or None if no suitable file
            can be found.
    """
    fs_path: Path = view_root / rel_path
    if not fs_path.exists() and fs_path.name == _INDEX_MARKDOWN_NAME:
        fs_path = view_root / _INDEX_MARKDOWN_NAME
    if not fs_path.exists() or fs_path.suffix.lower() != ".md":
        return None
    return fs_path


def _doc_nav_block(
    *,
    view_root: Path,
    rel_path_str: str,
    root_param: str,
) -> str:
    """
    Builds a navigation block linking to the previous and next documents
    in a view.

    It converts neighbor relationships into labeled HTML anchors for
    inline placement below the current document.

    Args:
        view_root (Path):
            Root directory of the document tree used to compute previous
            and next neighbors relative to the current document.
        rel_path_str (str):
            Path to the current document relative to the view root, used
            to locate its position in the navigation ordering.
        root_param (str):
            Preformatted query-string fragment encoding the active view
            root, appended to generated navigation URLs.

    Returns:
        str:
            HTML snippet containing a navigation bar with "Previous" and
            "Next" links when at least one neighbor exists, or an empty
            string when no adjacent documents are available.
    """
    prev_path, next_path = get_prev_next(root=view_root, rel_path=rel_path_str)
    prev_name: str = Path(prev_path).name if prev_path else ""
    next_name: str = Path(next_path).name if next_path else ""
    if not prev_path and not next_path:
        return ""
    prev_link: str = (
        f'<a href="/view/{urllib.parse.quote(prev_path, safe="/")}?{root_param}">← Previous: {html.escape(prev_name)}</a>'
        if prev_path
        else "<span></span>"
    )
    next_link: str = (
        f'<a href="/view/{urllib.parse.quote(next_path, safe="/")}?{root_param}">Next: {html.escape(next_name)} →</a>'
        if next_path
        else "<span></span>"
    )
    return f'<nav class="doc-nav"><span class="prev">{prev_link}</span><span class="next">{next_link}</span></nav>'


def _reader_dark_mode(params: Mapping[str, list[str]]) -> bool:
    """
    Determines whether the reader view should render in dark mode based
    on URL parameters.

    It interprets a compact runtime flag and maps common truthy values
    to a boolean dark-mode indicator.

    Args:
        params (Mapping[str, list[str]]):
            Parsed query-string parameters where the "rt" key, when
            present with a truthy value such as "dark" or "1",
            activates dark mode.

    Returns:
        bool:
            True if dark reader mode is explicitly requested by the "rt"
            parameter, otherwise False.
    """
    for raw in params.get("rt", []):
        v: str = str(raw).lower().strip()
        if v in _DARK_READER_FLAGS:
            return True
    return False


def build_view_html(
    *, paths: AppPaths, query: str, request_path: str, prompts: dict[str, str]
) -> str | None:
    """
    Builds the full HTML document for rendering a single markdown view
    request.

    It transforms a resolved markdown file into styled, navigable HTML
    with optional math and assistant tooling.

    Args:
        paths (AppPaths):
            Application paths used to locate configuration files and the
            default plans directory when resolving the view root.
        query (str):
            Raw query-string portion of the request that controls
            the view root, theme flags, and other reader options.
        request_path (str):
            HTTP request path that encodes the relative markdown
            document location beneath the view prefix.
        prompts (dict[str, str]):
            Mapping of prompt identifiers to prompt text made available
            to client-side assistant integrations.

    Returns:
        str | None:
            Complete HTML document string for the requested markdown
            view when the markdown file can be located and read, or
            None when resolution or file I/O fails.
    """
    app_config: AppConfig = load_app_config(paths)
    view_root: Path = get_root_from_query(
        query=query,
        default_root=_effective_plans_dir(cfg=app_config, paths=paths),
    )
    rel_path: str = _view_rel_from_request(request_path=request_path)
    fs_path: Path | None = _resolve_markdown_fs_path(
        view_root=view_root, rel_path=rel_path
    )
    if fs_path is None:
        return None
    try:
        text: str = fs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    has_math: bool = bool(_INLINE_MATH_DELIMS.search(text))
    body, _ = md_to_html(text)
    root_param: str = f"root={urllib.parse.quote(str(view_root))}"
    rel_path_str: str = str(fs_path.relative_to(view_root)).replace("\\", "/")
    body: str = rewrite_document_asset_urls(
        html_body=body, doc_rel_path=rel_path_str, root_param=root_param
    )
    body = rewrite_document_markdown_links(
        html_body=body, doc_rel_path=rel_path_str, root_param=root_param
    )
    body = f'<article class="md-doc" role="main">{body}</article>'
    nav_block: str = _doc_nav_block(
        view_root=view_root, rel_path_str=rel_path_str, root_param=root_param
    )
    pygments_css: str = get_pygments_css()
    mathjax: str = MATHJAX_SCRIPT if has_math else ""
    explain_key: str = effective_explain_prompt_key(paths)
    view_boot: ViewBootstrap = {
        "promptTemplates": prompts,
        "explainPromptKey": explain_key,
    }
    bootstrap_json: str = json_for_script_tag(payload=view_boot)
    reader_dark: bool = _reader_dark_mode(
        params=urllib.parse.parse_qs(qs=query)
    )
    html_classes: list[str] = ["md-doc-root"]
    if reader_dark:
        html_classes.insert(0, "theme-dark")
    html_class_attr: str = f' class="{" ".join(html_classes)}"'
    critical_reader: str = (
        "<style>html.theme-dark,html.theme-dark body,html.theme-dark .md-doc-scroll{background:#0b1220;color:#e2e8f0}</style>"
        if reader_dark
        else "<style>html,body,html.md-doc-root .md-doc-scroll{background:#ffffff}</style>"
    )
    doc_scroll_wrap: str = (
        f'<div class="md-doc-scroll">{body}{nav_block}</div>'
    )
    return f"""<!DOCTYPE html>
<html{html_class_attr}><head><meta charset="utf-8">
{critical_reader}
<script id="mdViewerViewBootstrap" type="application/json">{bootstrap_json}</script>
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Source+Sans+3:wght@400;600;700&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
<link href="/assets/viewer.css" rel="stylesheet">
<style>{pygments_css}</style>{mathjax}</head>
<body>{doc_scroll_wrap}
{_MERMAID_BODY_SCRIPTS}
<script src="/assets/viewer.js"></script>
</body></html>"""


def resolve_view_asset(
    *, plans_dir: Path, query: str, request_path: str
) -> Path | None:
    """
    Resolves a requested view asset into a safe filesystem path beneath
    the effective view root.

    It enforces that the asset exists, is a file, and does not escape
    the configured root directory.

    Args:
        plans_dir (Path):
            Default plans directory used as the view root when the query
            does not specify a valid alternative root path.
        query (str):
            Raw query-string portion of the request that may override
            the default view root via the "root" parameter.
        request_path (str): HTTP request path pointing at the asset
            within the view, typically beginning with the view prefix.

    Returns:
        Path | None:
            Filesystem path to the requested asset when it is a regular
            file safely contained within the resolved view root, or
            None when the asset is missing, not a file, or attempts to
            traverse outside the root.
    """
    view_root: Path = get_root_from_query(query=query, default_root=plans_dir)
    rel_path: str = _view_rel_from_request(request_path=request_path)
    fs_path: Path = (view_root / rel_path).resolve()
    if not fs_path.is_file():
        return None
    try:
        fs_path.relative_to(view_root.resolve())
    except ValueError:
        return None
    return fs_path
