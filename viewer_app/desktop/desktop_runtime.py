"""
This module centralizes configuration handling for a desktop app,
especially external URLs, web profiles, and text-to-speech settings.

It defines constants for default service URLs and a set of valid TTS
engine names that are used as fallbacks.

It provides an ExternalUrls dataclass that groups the URLs for chat,
sandbox, translation, and Codewars into an immutable configuration
object.

It declares protocol classes for espeak, Piper, and web profile objects,
specifying the attributes and methods they must support so
configuration code can work against abstract interfaces.

Helper functions handle safe INI file reading, extraction of the [app]
section, trimming and normalizing values, clamping numeric
configuration values, and resolving relative file paths.

Higher-level functions read individual app settings, build an
ExternalUrls instance by combining defaults, INI overrides, and
persisted JSON state, and configure a web profiles storage, cache, and
cookie policy.

The apply_tts_ini_settings function loads TTS-related options from the
INI file, validates and clamps them, applies them to espeak and Piper
objects, chooses which TTS engine to use, and returns its name.

Overall, this module acts as the configuration layer that bridges
persistent settings and runtime components in the desktop application.
"""

from __future__ import annotations

import configparser
from configparser import ConfigParser, SectionProxy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, Protocol

DEFAULT_CHAT_URL = "https://chat.qwen.ai/"
DEFAULT_SANDBOX_URL = "https://www.programiz.com/r/online-compiler/"
DEFAULT_TRANSLATE_URL = "https://translate.yandex.ru/"
DEFAULT_CODEWARS_URL = "https://www.codewars.com/dashboard"
VALID_TTS_ENGINES: Final[set[str]] = {"piper", "espeak"}


@dataclass(frozen=True)
class ExternalUrls:
    """
    Represents external URLs used by the desktop application.

    This data class stores endpoints for chat, sandbox, translation, and
    Codewars services.

    The URLs can be customized via configuration, but default to
    predefined values when not explicitly set.

    Attributes:
        chat_url (str):
            The URL for the external chat service.
        sandbox_url (str):
            The URL for the external code sandbox or online compiler.
        translate_url (str):
            The URL for the external translation service.
        codewars_url (str):
            The URL for the external Codewars dashboard or practice
            page.
    """

    chat_url: str = DEFAULT_CHAT_URL
    sandbox_url: str = DEFAULT_SANDBOX_URL
    translate_url: str = DEFAULT_TRANSLATE_URL
    codewars_url: str = DEFAULT_CODEWARS_URL


class _TtsEspeakProtocol(Protocol):
    """
    Defines the expected interface for an espeak-based TTS engine.

    This protocol specifies the minimal properties required to configure
    speech output.

    Implementations are expected to expose mutable attributes that
    control voice selection and speaking rate.

    Attributes:
        voice (str):
            The identifier of the voice to be used for speech synthesis,
            such as a language or accent code.
        rate (int):
            The speaking rate in words per minute or an engine-specific
            unit, controlling how fast text is spoken.
    """

    voice: str
    rate: int


class _TtsPiperProtocol(Protocol):
    """
    Defines the expected interface for a Piper-based TTS backend.

    This protocol specifies configuration attributes required to control
    Piper synthesis behavior.

    Implementations should expose mutable settings that influence pause
    duration, playback speed, and model selection.

    Attributes:
        sentence_silence (float):
            The duration of silence, in seconds, to insert between
            sentences in synthesized speech.
        speed (float):
            The relative playback speed multiplier used to render audio,
            where values greater than 1.0 speed up speech and values
            less than 1.0 slow it down.
        model_path (str):
            The file system path to the Piper model used for generating
            speech audio.
        config_path (str):
            The file system path to the Piper configuration file that
            defines model-specific settings.
    """

    sentence_silence: float
    speed: float
    model_path: str
    config_path: str


class _WebProfileProtocol(Protocol):
    """
    Defines the minimal interface required for a web profile object.

    This protocol specifies the methods and attributes used to configure
    persistent storage, caching, and cookie behavior for a web profile.

    Attributes:
        PersistentCookiesPolicy (Any):
            The enumeration or object that defines available persistent
            cookie policies used when configuring the profile.

    # Methods:

        setPersistentStoragePath(path: str) -> None:
            Sets the file system path where persistent web profile data,
            such as local storage or databases, will be stored.

        setCachePath(path: str) -> None:
            Sets the file system path where the web engine should store
            its cache data for this profile.

        setPersistentCookiesPolicy(policy: Any) -> None:
            Applies a persistent cookies policy that determines how
            cookies are stored and retained for the profile.
    """

    PersistentCookiesPolicy: Any

    def setPersistentStoragePath(self, path: str) -> None: ...  # noqa: N802
    def setCachePath(self, path: str) -> None: ...  # noqa: N802
    def setPersistentCookiesPolicy(  # noqa: N802
        self, policy: Any
    ) -> None: ...


def _safe_read_ini(settings_path: Path) -> configparser.ConfigParser:
    """
    Safely reads an INI configuration file from the given path. If the
    file is missing or cannot be read, an empty parser with no sections
    is returned.

    This helper isolates filesystem access for configuration loading and
    ensures callers always receive a usable ConfigParser instance.

    Args:
        settings_path (Path):
            The path to the INI file that should be read into the
            parser.

    Returns:
        ConfigParser:
            A configuration parser populated with values from the INI
            file if it exists, or an empty parser when the file is
            absent.
    """
    parser: ConfigParser = configparser.ConfigParser()
    if not settings_path.exists():
        return parser
    parser.read(filenames=settings_path, encoding="utf-8")
    return parser


def _app_section(
    parser: configparser.ConfigParser,
) -> configparser.SectionProxy | None:
    """
    Retrieves the application settings section from a configuration
    parser.

    If the section is missing, this function returns None instead of
    raising an error.

    This helper centralizes access to the [app] section and makes it
    easy for callers to handle absent configuration gracefully.

    Args:
        parser (ConfigParser):
            The configuration parser instance to inspect for the app
            section.

    Returns:
        SectionProxy | None:
            A proxy object exposing the keys and values in the app
            section when it exists, or None if the section is not
            present.
    """
    return parser["app"] if parser.has_section(section="app") else None


def _strip_value(value: object) -> str:
    """
    Normalizes an arbitrary value into a trimmed string.

    This provides a safe, uniform representation for configuration
    values and other loose inputs.

    The function converts None and falsy values to an empty string, then
    strips leading and trailing whitespace from the result.

    Args:
        value (object):
            The input value to normalize, which may be None, a string,
            or any other object that can be converted to text.

    Returns:
        str:
            A trimmed string representation of the input value, or an
            empty string when the value is falsy.
    """
    return str(value or "").strip()


def _parse_float_clamped(
    value: str, *, low: float, high: float
) -> float | None:
    """
    Parses a string into an integer constrained to a numeric range.

    This helper rejects invalid values and ensures the result never
    falls outside the specified bounds.

    If the input is empty or cannot be interpreted as a number, the
    function returns None instead of raising an exception.

    Args:
        value (str):
            The raw numeric string to parse and clamp to the target
            range.
        low (int):
            The minimum allowed integer value, used as the lower clamp
            bound.
        high (int):
            The maximum allowed integer value, used as the upper clamp
            bound.

    Returns:
        int | None:
            The parsed integer constrained between low and high, or None
            when the input is empty or invalid.
    """
    if not value:
        return None
    try:
        parsed: float = float(value)
    except ValueError:
        return None
    return max(low, min(high, parsed))


def _parse_int_clamped(value: str, *, low: int, high: int) -> int | None:
    """
    Parses a string into an integer constrained to a numeric range.

    This helper rejects invalid values and ensures the result never
    falls outside the specified bounds.

    If the input is empty or cannot be interpreted as a number, the
    function returns None instead of raising an exception.

    Args:
        value (str):
            The raw numeric string to parse and clamp to the target
            range.
        low (int):
            The minimum allowed integer value, used as the lower clamp
            bound.
        high (int):
            The maximum allowed integer value, used as the upper clamp
            bound.

    Returns:
        int | None:
            The parsed integer constrained between low and high, or None
            when the input is empty or invalid.
    """
    if not value:
        return None
    try:
        parsed: int = int(float(value))
    except ValueError:
        return None
    return max(low, min(high, parsed))


def _resolve_app_path(repo_root: Path, raw_path: str) -> str | None:
    """
    Resolves a possibly relative application path to an absolute string
    path.

    This helper interprets configuration paths in the context of the
    repository root and returns a normalized location.

    If the input is empty, the function returns None to signal that no
    usable path was provided.

    Args:
        repo_root (Path):
            The root directory of the repository used as the base for
            resolving relative paths.
        raw_path (str):
            The original path string from configuration, which may be
            absolute, relative, or empty.

    Returns:
        str | None:
            An absolute path string pointing to the resolved location,
            or None when no path was supplied.
    """
    if not raw_path:
        return None
    maybe_path: Path = Path(raw_path)
    if maybe_path.is_absolute():
        return str(maybe_path)
    return str((repo_root / maybe_path).resolve())


def read_app_setting(settings_path: Path, key: str, default: str) -> str:
    """
    Reads a single application setting from the INI configuration file.

    This helper falls back to a default value whenever the setting or
    its section is missing or unreadable.

    The function abstracts away error handling and normalization so
    callers always receive a usable string value.

    Args:
        settings_path (Path):
            The path to the INI file that should be consulted for the
            setting.
        key (str):
            The name of the option within the [app] section to look up.
        default (str):
            The fallback value to return when the configuration or key
            is not available.

    Returns:
        str:
            The trimmed setting value from the configuration, or the
            provided default when the key cannot be resolved.
    """
    try:
        parser: ConfigParser = _safe_read_ini(settings_path)
    except (OSError, configparser.Error):
        return default
    section: SectionProxy | None = _app_section(parser)
    if section is None:
        return default
    value: str = _strip_value(value=section.get(key, default))
    return value or default


def load_external_urls(
    settings_path: Path, load_state_json: Callable[[], dict[str, Any]]
) -> ExternalUrls:
    """
    Loads external service URLs from configuration and saved state.

    This function combines INI settings with persisted runtime state to
    produce a complete set of endpoints for external integrations.

    Configuration values override defaults, and the last-used Codewars
    dashboard URL is restored when available in the state file.

    Args:
        settings_path (Path):
            The path to the INI configuration file that may override
            default service URLs.
        load_state_json (Callable[[], dict[str, Any]]):
            A callable that returns the persisted state dictionary used
            to recover the most recent Codewars URL.

    Returns:
        ExternalUrls:
            A data object containing resolved URLs for chat, sandbox,
            translation, and Codewars services.
    """
    codewars_url = DEFAULT_CODEWARS_URL
    try:
        state: dict[str, Any] = load_state_json()
    except (OSError, RuntimeError, ValueError, TypeError):
        state = {}
    codewars: Any | None = state.get("codewars")
    if isinstance(codewars, dict):
        codewars_url: str = (
            _strip_value(value=codewars.get("lastUrl", DEFAULT_CODEWARS_URL))
            or DEFAULT_CODEWARS_URL
        )
    return ExternalUrls(
        chat_url=read_app_setting(
            settings_path, key="chatUrl", default=DEFAULT_CHAT_URL
        ),
        sandbox_url=read_app_setting(
            settings_path, key="sandboxUrl", default=DEFAULT_SANDBOX_URL
        ),
        translate_url=read_app_setting(
            settings_path, key="translateUrl", default=DEFAULT_TRANSLATE_URL
        ),
        codewars_url=codewars_url,
    )


def configure_web_profile(
    profile: _WebProfileProtocol, app_root: Path
) -> None:
    """
    Configures a web profile with persistent storage, caching, and
    cookie behavior.

    This helper centralizes the default paths and cookie policy used by
    the desktop application.

    The function ensures that profile data is stored under the
    application root and that persistent cookies are allowed for
    smoother user experience.

    Args:
        profile (_WebProfileProtocol):
            The web profile instance that will be configured with paths
            and cookie policy.
        app_root (Path):
            The root directory of the application, used as the base for
            storage and cache locations.
    """
    profile.setPersistentStoragePath(str(app_root / "web_profile_storage"))
    profile.setCachePath(str(app_root / "web_profile_cache"))
    profile.setPersistentCookiesPolicy(
        profile.PersistentCookiesPolicy.AllowPersistentCookies
    )


def apply_tts_ini_settings(  # noqa: C901
    settings_path: Path,
    repo_root: Path,
    tts_espeak: _TtsEspeakProtocol,
    tts_piper: _TtsPiperProtocol,
) -> str:
    """
    Applies text-to-speech settings from an INI file to TTS engines.

    This function reads configuration values, validates them, and
    updates both espeak and Piper backends in a safe, clamped manner.

    The function also selects which TTS engine should be used, falling
    back to a sensible default when configuration is missing or
    invalid.

    Args:
        settings_path (Path):
            The path to the INI configuration file that may contain TTS
            options.
        repo_root (Path):
            The root directory of the repository, used to resolve any
            relative Piper model or config paths.
        tts_espeak (_TtsEspeakProtocol):
            The espeak-compatible TTS object whose voice and rate may be
            updated from configuration.
        tts_piper (_TtsPiperProtocol):
            The Piper-compatible TTS object whose timing, speed, and
            model paths may be updated from configuration.

    Returns:
        str:
            The name of the TTS engine that should be used after
            applying configuration, such as "piper" or "espeak".
    """
    engine: str = "espeak"
    try:
        parser: ConfigParser = _safe_read_ini(settings_path)
    except (OSError, configparser.Error):
        return engine
    app: SectionProxy | None = _app_section(parser)
    if app is None:
        return engine
    requested_engine: str = (
        _strip_value(value=app.get("ttsEngine", engine)).lower() or engine
    )
    engine = (
        requested_engine if requested_engine in VALID_TTS_ENGINES else "piper"
    )
    voice: str = _strip_value(value=app.get("ttsVoice", ""))
    rate: int | None = _parse_int_clamped(
        value=_strip_value(value=app.get("ttsRate", "")), low=80, high=320
    )
    sentence_silence: float | None = _parse_float_clamped(
        value=_strip_value(value=app.get("piperSentenceSilence", "")),
        low=0.0,
        high=1.2,
    )
    speed: float | None = _parse_float_clamped(
        value=_strip_value(value=app.get("ttsSpeed", "")),
        low=0.6,
        high=1.8,
    )
    raw_model_ini: str = _strip_value(value=app.get("piperModelPath", ""))
    raw_config_ini: str = _strip_value(value=app.get("piperConfigPath", ""))
    model_path: str | None = _resolve_app_path(
        repo_root,
        raw_path=raw_model_ini,
    )
    config_path: str | None = _resolve_app_path(
        repo_root,
        raw_path=raw_config_ini,
    )
    piper_voice_name: str = _strip_value(value=app.get("piperVoiceName", ""))
    if not raw_model_ini and not raw_config_ini and piper_voice_name:
        voice_dir: Path = repo_root / "tts_models" / piper_voice_name
        voice_model: Path = voice_dir / "model.onnx"
        voice_cfg: Path = voice_dir / "model.onnx.json"
        if voice_model.is_file() and voice_cfg.is_file():
            model_path = str(voice_model.resolve())
            config_path = str(voice_cfg.resolve())
    if voice:
        tts_espeak.voice = voice
    if rate is not None:
        tts_espeak.rate = rate
    if sentence_silence is not None:
        tts_piper.sentence_silence = sentence_silence
    if speed is not None:
        tts_piper.speed = speed
    if model_path is not None:
        tts_piper.model_path = model_path
    if config_path is not None:
        tts_piper.config_path = config_path
    return engine
