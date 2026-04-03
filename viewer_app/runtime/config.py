"""
This module manages application configuration and prompt templates for a
Markdown study viewer, with caching to minimize disk and parse
operations.

It provides a central place to read, write, and cache INI-based app
settings and JSON-based prompt templates.

It defines constants for INI section and keys, default app titles, and
default prompt templates.

It introduces type aliases and TypedDict/AppConfig dataclasses to
describe structured configuration data.

It uses _path_stamp to compute a tuple representing file modification,
creation time, and size used as a cheap change detector.

_read_parser safely loads a ConfigParser from a settings file, returning
an empty parser on errors.

_app_config_from_section builds an AppConfig from the INI content,
resolving an optional root directory and overriding title/subtitle if
present.

_section_to_items exposes the raw app INI section as a list of key-value
string pairs inside an AppConfigItems wrapper.

_normalize_prompt_templates validates and cleans arbitrary JSON data
into a mapping of non-empty string keys to string values, falling back
to defaults if invalid.

_AppSettingsCache and _PromptTemplatesCache dataclasses hold in-memory
cached data plus associated path and stamp/raw content for change
detection.

load_app_config returns the current AppConfig, using a lock-protected
cache and the path stamp to avoid re-reading unchanged files.

update_app_config_key updates a single setting in the INI file, writes
it to disk, and invalidates affected caches.

save_app_config merges keys from the payload into the app settings
section (preserving other options), then invalidates the cache.

get_app_config_dict returns a cached or freshly read AppConfigItems
snapshot of the settings section, cloning the list on each call.

load_prompt_templates reads a JSON prompts file, caches normalized
templates keyed by path and raw content, and falls back to default
templates on errors.

_invalidate_config_cache clears cached config or prompt data associated
with a given path so subsequent calls will reload from disk.

Within the broader system, this module acts as the runtime configuration
layer for the viewer, abstracting file I/O and normalization behind
simple function calls.

Other parts of the application use these functions to obtain consistent
configuration and prompt data without worrying about caching, error
handling, or file formats.
"""

import json
import threading

from _thread import LockType
from configparser import ConfigParser, Error, SectionProxy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, TypeAlias, TypedDict

from viewer_app.runtime.paths import AppPaths

INI_SECTION_APP: str = "app"
INI_KEY_ROOT_DIR: str = "rootDir"
INI_KEY_APP_TITLE: str = "appTitle"
INI_KEY_APP_SUBTITLE: str = "appSubtitle"

_EXPLAIN_PROMPT_ALLOWED: frozenset[str] = frozenset(("explain_ru", "explain_en"))

_DEFAULT_APP_TITLE: str = "Study MD Desk"
_DEFAULT_APP_SUBTITLE: str = (
    "Comfortable reading and learning from Markdown materials"
)

_DEFAULT_PROMPT_TEMPLATES: dict[str, str] = {
    "explain_ru": (
        "Ты мой ИИ‑репетитор "  # noqa: RUF001
        "по программированию и математике. "
        "Объясни следующий фрагмент учебного текста подробно, "
        "простым и естественным русским языком. "
        "Сначала дай краткое резюме (3–5 предложений), "  # noqa: RUF001
        "затем развёрнутое объяснение с примерами, "  # noqa: RUF001
        "и в конце — список главных выводов и типичных ошибок.\n\n"
        "Тема: {TITLE}\n\n"
        "Фрагмент текста:\n\n"
        "{CONTENT}"
    )
}

PathStamp: TypeAlias = tuple[int, int, int]


class AppConfigItems(TypedDict):
    """
    Defines the configuration items for the application settings
    section.

    This typed dictionary represents key-value pairs stored in the app
    configuration, preserving them as an ordered list of string tuples.

    Attributes:
        items (list[tuple[str, str]]):
            A list of (key, value) string pairs representing
            configuration entries as read from or written to the
            configuration file.
    """

    items: list[tuple[str, str]]


@dataclass
class AppConfig:
    """
    Represents high-level application configuration values for the
    viewer.

    This data class groups user-facing settings that control how the app
    locates study plans and displays its title information.

    Attributes:
        plans_dir (Path):
            Filesystem directory where study plans and related Markdown
            materials are stored and looked up by the application.
        app_title (str):
            Main title text displayed in the application UI, typically
            shown in window chrome or header areas. app_subtitle (str):
            Supplementary subtitle text providing additional context or
            a descriptive tagline for the application.
    """

    plans_dir: Path
    app_title: str = _DEFAULT_APP_TITLE
    app_subtitle: str = _DEFAULT_APP_SUBTITLE


@dataclass
class _AppSettingsCache:
    """
    Caches derived application settings for a single configuration file.

    It provides a lightweight container used to detect changes and reuse
    parsed configuration data across calls.

    Attributes:
        path (str | None):
            String path of the configuration file whose settings are
            cached, or None when no configuration has been loaded yet.
        stamp (PathStamp | None):
            Cached filesystem stamp tuple used to determine whether the
            underlying configuration file has changed since it was last
            read.
        config (AppConfig | None):
            Last computed high-level application configuration instance,
            or None if configuration has not been parsed or has been
            invalidated.
        config_dict (AppConfigItems | None):
            Cached low-level configuration key-value items as read from
            the configuration parser, or None when no dictionary
            snapshot is available.
    """

    path: str | None = None
    stamp: PathStamp | None = None
    config: AppConfig | None = None
    config_dict: AppConfigItems | None = None


@dataclass
class _PromptTemplatesCache:
    """
    Caches raw and normalized prompt template data for a single prompts
    file.

    It helps avoid unnecessary disk reads and JSON parsing by reusing
    previously processed template payloads.

    Attributes:
        path (str | None):
            Path of the prompts configuration file whose templates are
            cached, or None when no prompts file has been associated
            with the cache yet.
        raw (str | None):
            Last read raw textual content of the prompts configuration
            file, or None if the file has not been read or the cache
            has been invalidated.
        payload (dict[str, str] | None):
            Last computed mapping of prompt template identifiers to
            their string templates, or None when parsing has not yet
            occurred or was cleared.
    """

    path: str | None = None
    raw: str | None = None
    payload: dict[str, str] | None = None


_CONFIG_CACHE_LOCK: LockType = threading.Lock()
_APP_SETTINGS_CACHE: _AppSettingsCache = _AppSettingsCache()
_PROMPT_TEMPLATES_CACHE: _PromptTemplatesCache = _PromptTemplatesCache()


def _path_stamp(path: Path) -> PathStamp:
    """
    Computes a compact stamp describing the state of a filesystem path.

    It returns modification, creation, and size metadata used to detect
    when a file or directory has changed.

    Args:
        path (Path):
            Filesystem path for which the metadata stamp should be
            calculated.

    Returns:
        PathStamp:
            A three-element tuple containing modification time in
            nanoseconds, creation time in nanoseconds, and file size in
            bytes. If the path cannot be accessed, all elements are set
            to -1.
    """
    try:
        stat_result = path.stat()
        return (
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
            stat_result.st_size,
        )
    except OSError:
        return (-1, -1, -1)


def _read_parser(path: Path) -> ConfigParser:
    """
    Loads a configuration parser from the given settings file path.

    It returns a parser populated with INI data when possible, or an
    empty parser if the file is missing or unreadable.

    Args:
        path (Path):
            Filesystem path pointing to the configuration file to be
            read.

    Returns:
        ConfigParser:
            A configuration parser instance that may contain sections
            and options loaded from the target file, or be empty if the
            file does not exist, cannot be read, or contains invalid
            data.
    """
    parser: ConfigParser = ConfigParser()
    if not path.exists():
        return parser
    try:
        parser.read(filenames=path, encoding="utf-8")
    except (OSError, UnicodeDecodeError, Error):
        return ConfigParser()
    return parser


def _app_config_from_section(
    paths: AppPaths, parser: ConfigParser
) -> AppConfig:
    """
    Builds a high-level application configuration from an INI section.

    It merges default paths and titles with any overrides provided in
    the application settings section.

    Args:
        paths (AppPaths):
            Collection of resolved filesystem paths used by the
            application, including the default plans directory.
        parser (ConfigParser):
            Configuration parser that may contain the application
            settings section and its key-value options.

    Returns:
        AppConfig:
            An application configuration instance populated with the
            effective plans directory, title, and subtitle after
            applying any valid overrides from the configuration file.
    """
    base: AppConfig = AppConfig(plans_dir=paths.plans_dir)
    if not parser.has_section(section=INI_SECTION_APP):
        return base
    section: SectionProxy = parser[INI_SECTION_APP]
    plans_dir: Path = base.plans_dir
    root_dir: str = section.get(INI_KEY_ROOT_DIR, "").strip()
    if root_dir:
        candidate: Path = Path(root_dir).expanduser()
        if candidate.is_dir():
            plans_dir = candidate.resolve()
    title: str = (
        section.get(INI_KEY_APP_TITLE, base.app_title).strip()
        or base.app_title
    )
    subtitle: str = (
        section.get(INI_KEY_APP_SUBTITLE, base.app_subtitle).strip()
        or base.app_subtitle
    )
    return AppConfig(
        plans_dir=plans_dir, app_title=title, app_subtitle=subtitle
    )


def _section_to_items(parser: ConfigParser) -> AppConfigItems:
    """
    Extracts raw key-value pairs from the application settings section.

    It converts the section into a structured list of configuration
    items suitable for caching or external inspection.

    Args:
        parser (ConfigParser):
            Configuration parser instance that may contain the
            application settings section and its options.

    Returns:
        AppConfigItems:
            A typed dictionary wrapping the list of (key, value) string
            pairs from the application settings section, or an empty
            list when the section is not present.
    """
    if not parser.has_section(section=INI_SECTION_APP):
        return AppConfigItems(items=[])
    app: SectionProxy = parser[INI_SECTION_APP]
    return AppConfigItems(items=list[tuple[str, str]](app.items()))


def _normalize_prompt_templates(
    data: object, fallback: dict[str, str]
) -> dict[str, str]:
    """
    Normalizes raw prompt template payloads into a clean string mapping.

    It ensures only string keys with non-empty names are kept and that
    all values are represented as strings.

    Args:
        data (object):
            Arbitrary decoded payload, typically loaded from JSON, that
            may or may not be a dictionary of template entries.
        fallback (dict[str, str]):
            Default mapping of prompt templates to return when the
            payload is invalid, empty, or yields no usable entries after
            normalization.

    Returns:
        dict[str, str]:
            A dictionary mapping sanitized string keys to string
            template values, or the provided fallback mapping when
            normalization cannot produce a non-empty result.
    """
    if not isinstance(data, dict) or not data:
        return fallback
    out: dict[str, str] = {
        k: v if isinstance(v, str) else str(v)
        for k, v in data.items()
        if isinstance(k, str) and k.strip()
    }
    return out or fallback


def load_app_config(paths: AppPaths) -> AppConfig:
    """
    Loads the current application configuration using cached settings
    when possible.

    It reads the settings file only when the on-disk state has changed,
    returning an up-to-date configuration object.

    Args:
        paths (AppPaths):
            Collection of resolved filesystem paths, including the
            location of the application settings file to be consulted.

    Returns:
        AppConfig:
            An application configuration instance reflecting the
            effective plans directory and UI titles, cloned from cache
            if the backing file is unchanged, or rebuilt from disk
            otherwise.
    """
    path: Path = paths.settings_path
    stamp: PathStamp = _path_stamp(path)
    with _CONFIG_CACHE_LOCK:
        cached_config: AppConfig | None = _APP_SETTINGS_CACHE.config
        if (
            _APP_SETTINGS_CACHE.path == str(path)
            and _APP_SETTINGS_CACHE.stamp == stamp
            and cached_config is not None
        ):
            return replace(cached_config)

    parser: ConfigParser = _read_parser(path)
    cfg: AppConfig = _app_config_from_section(paths, parser)
    with _CONFIG_CACHE_LOCK:
        _APP_SETTINGS_CACHE.path = str(path)
        _APP_SETTINGS_CACHE.stamp = stamp
        _APP_SETTINGS_CACHE.config = cfg
        _APP_SETTINGS_CACHE.config_dict = None
    return cfg


def update_app_config_key(paths: AppPaths, key: str, value: str) -> None:
    """
    Updates a single key in the application settings file and refreshes
    related cached configuration.

    It writes the new value to the INI section, creating the section if
    necessary.

    Args:
        paths (AppPaths):
            Collection of resolved filesystem paths, including the
            application settings file to be modified.
        key (str):
            Name of the configuration option to insert or update within
            the application settings section.
        value (str):
            String value to assign to the specified configuration option
            in the settings file.
    """
    parser: ConfigParser = _read_parser(path=paths.settings_path)
    if INI_SECTION_APP not in parser:
        parser[INI_SECTION_APP] = {}
    parser[INI_SECTION_APP][key] = value
    try:
        with paths.settings_path.open("w", encoding="utf-8") as fh:
            parser.write(fp=fh)
    except OSError:
        return
    _invalidate_config_cache(path=paths.settings_path)


def save_app_config(
    paths: AppPaths, payload: Mapping[str, object] | None = None
) -> None:
    """
    Merges values into the application ``[app]`` section and writes the
    INI file.

    Only keys present in ``payload`` are updated; existing options are
    kept so the web settings form does not drop hidden TTS fields
    (``piperModelPath``, ``ttsSpeed``, etc.) that have no visible
    inputs.

    Args:
        paths (AppPaths):
            Resolved paths, including the settings file to update.
        payload (Mapping[str, object] | None):
            Options to merge into ``[app]``, or ``None`` to remove every
            option in that section.
    """
    parser: ConfigParser = _read_parser(path=paths.settings_path)
    if payload is None:
        parser[INI_SECTION_APP] = {}
    else:
        if INI_SECTION_APP not in parser:
            parser[INI_SECTION_APP] = {}
        section: SectionProxy = parser[INI_SECTION_APP]
        for key, value in payload.items():
            stripped: str | Any = key.strip()
            if not stripped:
                continue
            section[stripped] = str(value)
    try:
        with paths.settings_path.open("w", encoding="utf-8") as fh:
            parser.write(fp=fh)
    except OSError:
        return
    _invalidate_config_cache(path=paths.settings_path)


def effective_explain_prompt_key(paths: AppPaths) -> str:
    """
    Return which ``prompts.json`` template key to use for “explain
    selection” chat prompts (``explain_ru`` or ``explain_en``).

    Reads ``explainPromptKey`` from the ``[app]`` INI section
    (ConfigParser normalizes option names to lowercase). Invalid or
    missing values default to ``explain_ru``.
    """
    items: list[tuple[str, str]] = list[tuple[str, str]](
        get_app_config_dict(paths)["items"]
    )
    for raw_k, raw_v in items:
        if str(raw_k).strip().lower() != "explainpromptkey":
            continue
        val: str = str(raw_v).strip()
        if val in _EXPLAIN_PROMPT_ALLOWED:
            return val
        break
    return "explain_ru"


def get_app_config_dict(paths: AppPaths) -> AppConfigItems:
    """
    Retrieves the raw key-value items for the application settings
    section, using cached data when available.

    It exposes the underlying configuration entries as a typed,
    copy-on-read structure.

    Args:
        paths (AppPaths):
            Collection of resolved filesystem paths, including the
            application settings file whose contents should be
            inspected.

    Returns:
        AppConfigItems:
            A typed dictionary containing a list of (key, value) string
            pairs representing the current state of the application
            settings section, cloned either from cache or from the
            configuration file on disk.
    """
    path: Path = paths.settings_path
    stamp: PathStamp = _path_stamp(path)
    with _CONFIG_CACHE_LOCK:
        cached_dict: AppConfigItems | None = _APP_SETTINGS_CACHE.config_dict
        if (
            _APP_SETTINGS_CACHE.path == str(path)
            and _APP_SETTINGS_CACHE.stamp == stamp
            and cached_dict is not None
        ):
            return AppConfigItems(
                items=list[tuple[str, str]](cached_dict["items"])
            )
    parser: ConfigParser = _read_parser(path)
    payload: AppConfigItems = _section_to_items(parser)
    with _CONFIG_CACHE_LOCK:
        _APP_SETTINGS_CACHE.path = str(path)
        _APP_SETTINGS_CACHE.stamp = stamp
        _APP_SETTINGS_CACHE.config_dict = payload
    return AppConfigItems(items=list[tuple[str, str]](payload["items"]))


def load_prompt_templates(paths: AppPaths) -> dict[str, str]:
    """
    Loads prompt templates from the configured prompts file, falling
    back to built-in defaults when needed.

    It returns a normalized mapping of template identifiers to their
    string bodies, using an internal cache to avoid unnecessary disk
    and JSON work.

    Args:
        paths (AppPaths):
            Collection of resolved filesystem paths, including the
            prompts configuration file that should be read and
            interpreted.

    Returns:
        dict[str, str]:
            A dictionary mapping prompt template names to template
            strings, either loaded and normalized from the prompts file
            or cloned from the built-in default templates when the file
            is missing, unreadable, or contains invalid data.
    """
    path: Path = paths.prompts_path
    if not path.exists():
        return dict[str, str](_DEFAULT_PROMPT_TEMPLATES)
    try:
        raw: str = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return dict[str, str](_DEFAULT_PROMPT_TEMPLATES)
    with _CONFIG_CACHE_LOCK:
        cached_payload: dict[str, str] | None = _PROMPT_TEMPLATES_CACHE.payload
        if (
            _PROMPT_TEMPLATES_CACHE.path == str(path)
            and _PROMPT_TEMPLATES_CACHE.raw == raw
            and cached_payload is not None
        ):
            return dict[str, str](cached_payload)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return dict[str, str](_DEFAULT_PROMPT_TEMPLATES)
    payload: dict[str, str] = _normalize_prompt_templates(
        data, fallback=_DEFAULT_PROMPT_TEMPLATES
    )
    with _CONFIG_CACHE_LOCK:
        _PROMPT_TEMPLATES_CACHE.path = str(path)
        _PROMPT_TEMPLATES_CACHE.raw = raw
        _PROMPT_TEMPLATES_CACHE.payload = payload
    return dict[str, str](payload)


def _invalidate_config_cache(path: Path) -> None:
    """
    Invalidates cached configuration and prompt template data for a
    given path.

    It clears any in-memory entries associated with the path so that
    subsequent reads are forced to reload from disk.

    Args:
        path (Path):
            Filesystem path whose associated configuration and prompt
            template cache entries should be invalidated.
    """
    path_key: str = str(path)
    with _CONFIG_CACHE_LOCK:
        if _APP_SETTINGS_CACHE.path == path_key:
            _APP_SETTINGS_CACHE.stamp = None
            _APP_SETTINGS_CACHE.config = None
            _APP_SETTINGS_CACHE.config_dict = None
        if _PROMPT_TEMPLATES_CACHE.path == path_key:
            _PROMPT_TEMPLATES_CACHE.raw = None
            _PROMPT_TEMPLATES_CACHE.payload = None
