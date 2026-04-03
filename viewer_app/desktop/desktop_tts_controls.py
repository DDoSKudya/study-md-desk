"""
This module implements helper logic for configuring and controlling
Piper-based text-to-speech settings in a desktop viewer application.

It centralizes how the UI reads and writes Piper parameters like voice,
speed, and sentence silence, and how these values are persisted to the
app configuration.

The file defines a PiperTtsForControls Protocol that describes the
minimal configuration surface the UI controls interact with (speed,
sentence_silence, model_path, config_path).

It declares several constants for allowed speed and sentence-silence
ranges, along with default string labels for these values when Piper is
not active.

The build_selection_text_script function returns a JavaScript snippet
used in a web view to extract the current text selection from an
embedded iframe.

Private helpers _clamp_speed and _clamp_sentence_silence enforce numeric
bounds, while _format_two_decimals converts floating values into
two-decimal strings for display.

The set_piper_voice function validates a requested voice ID against
on-disk Piper model and config files, updates the Piper configuration
paths, and persists them via an injected config updater.

It also uses a status callback to notify the user which Piper voice has
been selected.

The get_tts_speed, adjust_tts_speed, and set_tts_speed functions expose
and update the Piper playback speed, but only when the active TTS
engine is "piper", otherwise they use a default label or no-op behavior.

They clamp speed values into the configured range, persist them to
configuration, and return display-ready string labels for UI controls.

The get_sentence_silence and set_sentence_silence functions mirror this
behavior for the pause duration between spoken sentences, again only
acting when Piper is the active engine.

Overall, the module acts as the bridge between high-level UI controls
and Piper TTS configuration, ensuring values stay within safe ranges and
remain consistent across sessions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Final, Protocol, TypeAlias

UpdateAppConfigKeyFn: TypeAlias = Callable[[str, str], None]
ShowStatusFn: TypeAlias = Callable[[str, int], None]

_SPEED_MIN: Final[float] = 0.6
_SPEED_MAX: Final[float] = 1.8
_SENTENCE_SILENCE_MIN: Final[float] = 0.0
_SENTENCE_SILENCE_MAX: Final[float] = 1.2

_DEFAULT_SPEED_LABEL: Final[str] = "1.00"
_DEFAULT_SILENCE_LABEL: Final[str] = "0.25"


class PiperTtsForControls(Protocol):
    """
    Define the minimal Piper TTS configuration surface used by controls.

    This protocol specifies the attributes the UI control helpers expect
    to read and mutate when configuring Piper-based text-to-speech
    behavior.

    Attributes:
        speed (float):
            Playback rate factor applied by the Piper engine, typically
            clamped within a UI-defined range around the default speed.
        sentence_silence (float):
            Duration of silence in seconds to insert between sentences,
            constrained to a small range to keep speech natural and
            responsive.
        model_path (str):
            Filesystem path pointing to the active Piper ONNX model file
            that will be loaded for synthesis.
        config_path (str):
            Filesystem path to the JSON configuration associated with
            the active Piper model, controlling voice-specific
            parameters.
    """

    speed: float
    sentence_silence: float
    model_path: str
    config_path: str


def build_selection_text_script() -> str:
    """
    Build a JavaScript snippet that returns the current text selection.

    This helper produces a self-contained script that safely reads the
    selection from the embedded content frame and normalizes it into a
    trimmed string.

    Returns:
        str:
            JavaScript code that, when executed in the page, inspects
            the 'contentFrame' iframe, extracts the current selection
            text if available, and returns a trimmed string or an empty
            string on error.
    """
    return """
        (function() {
          try {
            var frame = document.getElementById('contentFrame');
            if (!frame || !frame.contentWindow || !frame.contentDocument) return '';
            var win = frame.contentWindow;
            var sel = (win.getSelection && win.getSelection().toString()) || '';
            return (sel || '').trim();
          } catch (e) { return ''; }
        })();
        """


def _clamp_speed(value: float) -> float:
    """
    Clamp a raw TTS speed value into the supported range.

    This helper enforces minimum and maximum bounds so that Piper
    playback speed stays within a UI-approved interval.

    Args:
        value (float):
            Requested playback speed factor, which may fall outside the
            allowed range and will be adjusted as needed.

    Returns:
        float:
            Speed value constrained to lie between the configured
            minimum and maximum limits for TTS playback.
    """
    return max(_SPEED_MIN, min(_SPEED_MAX, value))


def _clamp_sentence_silence(value: float) -> float:
    """
    Clamp a raw sentence-silence value into the supported range.

    This helper enforces minimum and maximum bounds so that the pause
    between spoken sentences stays within a UI-approved interval.

    Args:
        value (float):
            Requested silence duration in seconds, which may fall
            outside the allowed range and will be adjusted as needed.

    Returns:
        float:
            Silence duration constrained to lie between the configured
            minimum and maximum limits for sentence pauses.
    """
    return max(_SENTENCE_SILENCE_MIN, min(_SENTENCE_SILENCE_MAX, value))


def _format_two_decimals(value: float) -> str:
    """
    Format a floating-point value with two decimal places.

    This helper converts a numeric value into a fixed-width string
    suitable for displaying TTS settings such as speed or silence.

    Args:
        value (float):
            Numeric value to be formatted, typically a TTS speed factor
            or sentence silence duration.

    Returns:
        str:
            String representation of the value rounded to two decimal
            places.
    """
    return f"{value:.2f}"


def set_piper_voice(
    *,
    repo_root: Path,
    tts_piper: PiperTtsForControls,
    update_app_config_key: UpdateAppConfigKeyFn,
    show_status: ShowStatusFn,
    voice_id: str,
) -> None:
    """
    Select and apply a Piper voice based on a configured model
    directory.

    This function validates the requested voice ID against on-disk Piper
    model files, updates the active Piper configuration, and persists
    the new voice settings to the application configuration.

    Args:
        repo_root (Path):
            Root directory of the repository used as the base for
            locating the 'tts_models' folder that contains Piper voice
            subfolders.
        tts_piper (PiperTtsForControls):
            Piper
            configuration object whose model_path and config_path will
            be updated when a valid voice is found.
        update_app_config_key (UpdateAppConfigKeyFn):
            Callback used
            to persist the selected voice name and model paths into the
            application configuration store.
        show_status (ShowStatusFn):
            Callback used to show a brief status message to the user
            after successfully changing the Piper voice.
        voice_id (str):
            Logical voice identifier corresponding to a subdirectory
            within 'tts_models' that should contain the Piper ONNX model
            and JSON config files.
    """
    voice: str = (voice_id or "").strip()
    if not voice:
        return
    base: Path = repo_root / "tts_models" / voice
    model: Path = base / "model.onnx"
    config: Path = base / "model.onnx.json"
    if not (model.is_file() and config.is_file()):
        return
    tts_piper.model_path = str(model.resolve())
    tts_piper.config_path = str(config.resolve())
    update_app_config_key("piperVoiceName", voice)
    update_app_config_key("piperModelPath", f"tts_models/{voice}/model.onnx")
    update_app_config_key(
        "piperConfigPath",
        f"tts_models/{voice}/model.onnx.json",
    )
    show_status(f"Piper voice: {voice}", 2500)


def get_tts_speed(*, tts_engine: str, tts_piper: PiperTtsForControls) -> str:
    """
    Return the current TTS speed label for the active engine.

    This helper exposes the Piper playback speed as a formatted string
    and falls back to a fixed default label when Piper is not the
    active engine.

    Args:
        tts_engine (str):
            Name of the currently selected text-to-speech engine, used
            to decide whether to read Piper-specific settings or return
            a default label.
        tts_piper (PiperTtsForControls):
            Piper configuration object from which the current speed
            value is read when Piper is the active engine.

    Returns:
        str:
            Two-decimal string representing the current Piper speed when
            'tts_engine' is 'piper', or the default speed label
            otherwise.
    """
    if tts_engine != "piper":
        return _DEFAULT_SPEED_LABEL
    return _format_two_decimals(value=tts_piper.speed)


def adjust_tts_speed(
    *,
    tts_engine: str,
    tts_piper: PiperTtsForControls,
    update_app_config_key: UpdateAppConfigKeyFn,
    delta: float,
) -> str:
    """
    Adjust the current TTS speed incrementally for the active engine.

    This helper nudges the Piper playback speed by a delta, clamps it
    into the supported range, persists the new value, and returns a
    display-ready label.

    Args:
        tts_engine (str):
            Name of the currently selected text-to-speech engine, used
            to decide whether the speed adjustment should be applied to
            Piper or ignored with a default label.
        tts_piper (PiperTtsForControls):
            Piper configuration object whose speed attribute will be
            updated when Piper is the active engine.
        update_app_config_key (UpdateAppConfigKeyFn):
            Callback used to persist the updated speed value in the
            application configuration after clamping.
        delta (float):
            Increment to apply to the current speed, which may be
            positive or negative before clamping to the allowed range.

    Returns:
        str:
            Two-decimal string representing the effective Piper speed
            after adjustment when 'tts_engine' is 'piper', or the
            default speed label when a different engine is active.
    """
    if tts_engine != "piper":
        return _DEFAULT_SPEED_LABEL
    tts_piper.speed = _clamp_speed(tts_piper.speed + delta)
    update_app_config_key("ttsSpeed", _format_two_decimals(tts_piper.speed))
    return _format_two_decimals(tts_piper.speed)


def set_tts_speed(
    *,
    tts_engine: str,
    tts_piper: PiperTtsForControls,
    update_app_config_key: UpdateAppConfigKeyFn,
    value: float,
) -> None:
    """
    Set the absolute TTS speed for the active engine when using Piper.

    This helper applies a specific speed value to the Piper
    configuration, clamps it into the supported range, and persists the
    result in the application configuration.

    Args:
        tts_engine (str):
            Name of the currently selected text-to-speech engine, used
            to decide whether the speed setting should be applied to
            Piper or ignored.
        tts_piper (PiperTtsForControls):
            Piper configuration object whose speed attribute will be
            updated when Piper is the active engine.
        update_app_config_key (UpdateAppConfigKeyFn):
            Callback used to persist the clamped speed value in the
            application configuration after it is applied.
        value (float):
            Desired playback speed factor, which may fall outside the
            allowed range and will be clamped before storage.
    """
    if tts_engine != "piper":
        return
    tts_piper.speed = _clamp_speed(value)
    update_app_config_key(
        "ttsSpeed", _format_two_decimals(value=tts_piper.speed)
    )


def get_sentence_silence(
    *, tts_engine: str, tts_piper: PiperTtsForControls
) -> str:
    """
    Return the current sentence-silence label for the active engine.

    This helper exposes the Piper sentence pause duration as a formatted
    string and falls back to a fixed default label when Piper is not
    the active engine.

    Args:
        tts_engine (str):
            Name of the currently selected text-to-speech engine, used
            to decide whether to read Piper-specific silence settings
            or return a default label.
        tts_piper (PiperTtsForControls):
            Piper configuration object from which the current sentence
            silence value is read when Piper is the active engine.

    Returns:
        str:
            Two-decimal string representing the current Piper sentence
            silence when 'tts_engine' is 'piper', or the default
            silence label otherwise.
    """
    if tts_engine != "piper":
        return _DEFAULT_SILENCE_LABEL
    return _format_two_decimals(value=tts_piper.sentence_silence)


def set_sentence_silence(
    *,
    tts_engine: str,
    tts_piper: PiperTtsForControls,
    update_app_config_key: UpdateAppConfigKeyFn,
    value: float,
) -> None:
    """
    Set the absolute sentence-silence duration when using the Piper
    engine.

    This helper applies a specific silence value between sentences to
    the Piper configuration, clamps it into the supported range, and
    persists the result in the application configuration.

    Args:
        tts_engine (str):
            Name of the currently selected text-to-speech engine, used
            to decide whether the silence setting should be applied to
            Piper or ignored.
        tts_piper (PiperTtsForControls):
            Piper configuration object whose sentence_silence attribute
            will be updated when Piper is the active engine.
        update_app_config_key (UpdateAppConfigKeyFn):
            Callback used to persist the clamped silence value in the
            application configuration after it is applied.
        value (float):
            Desired sentence pause duration in seconds, which may fall
            outside the allowed range and will be clamped before
            storage.
    """
    if tts_engine != "piper":
        return
    tts_piper.sentence_silence = _clamp_sentence_silence(value)
    update_app_config_key(
        "piperSentenceSilence",
        _format_two_decimals(value=tts_piper.sentence_silence),
    )
