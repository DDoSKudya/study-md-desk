"""
This module implements offline text-to-speech control for a desktop
application, supporting both eSpeak-based and Piper-based synthesis
backends.

It also provides utilities for splitting text into speakable chunks,
managing pauses, spawning synthesis and audio player processes, and
tracking playback progress.

The code defines constants for special pause tokens and a set of
punctuation pause markers used to represent silent gaps in the TTS
queue.

It includes helper functions for platform and architecture detection,
locating bundled Piper binaries, spawning and terminating subprocesses,
playing WAV files, and computing WAV durations.

There are text-processing helpers that tokenize text into words and
punctuation, collapse adjacent words into two-word progress tokens, and
cap the list to a reasonable size for progress reporting.

Callback helpers (_invoke_chunk) and quiet cleanup helpers
(_unlink_quiet) abstract optional logging callbacks and best-effort
temporary file deletion.

Two NamedTuple types, _PiperWorkerSnap and _EspeakWorkerSnap, represent
immutable snapshots of all parameters needed to synthesize a single
utterance with Piper or eSpeak.

Utility functions compute punctuation-dependent pause durations for both
engines and a generic _try_consume_tts_pause_token function sleeps and
advances the queue when encountering pause tokens.

OfflineTtsController manages an eSpeak/espeak-ng driven TTS pipeline
with its own worker thread, queue, index, pause/stop flags, and
subprocess handle.

It exposes methods to check availability and activity, start speaking
text, pause, resume, stop, toggle pause, and runs a worker loop that
alternates between consuming pause tokens and spawning eSpeak
subprocesses for chunks.

PiperTtsController manages a Piper-based TTS pipeline with similar queue
and worker management, plus Piper-specific configuration such as
piper_path, model_path, config_path, sentence_silence, and speed.

It can speak raw text or pre-split chunks, report a cursor index for
resume support, and provides pause/resume/stop/toggle logic that also
purges Windows sound state when needed.

The Piper controller builds synthesis commands, spawns Piper
subprocesses, drains them while tracking lifecycle, and decides whether
to discard or keep synthesized WAV files based on stop/pause flags.

It then measures WAV size and duration, launches an external audio
player (preferably asynchronously on Unix-like systems), and either
drives word-level progress callbacks or falls back to synchronous CLI
playback.

At the core of Piper playback, the controller emits detailed status
lines, polls the player process to emit per-word progress ticks,
ensures the player terminates cleanly, and advances the chunk index when
playback finishes.

Overall, this module acts as the low-level offline TTS engine for the
application, bridging between high-level text and external TTS binaries
while providing progress reporting, pause handling, and resource cleanup
across platforms.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
import wave
from subprocess import (
    DEVNULL,
    PIPE,
    CompletedProcess,
    Popen,
    TimeoutExpired,
    run,
)

from _thread import LockType
from pathlib import Path
from typing import (
    IO,
    Any,
    Callable,
    Final,
    LiteralString,
    NamedTuple,
    Sequence,
    TypeAlias,
)

_PAUSE_PAR: Final[str] = "__TTS_PAUSE_PAR__"
_PAUSE_SHORT: Final[str] = "__TTS_PAUSE_SHORT__"
_PAUSE_COMMA: Final[str] = "__TTS_PAUSE_COMMA__"
_PAUSE_SEMI: Final[str] = "__TTS_PAUSE_SEMI__"
_PAUSE_COLON: Final[str] = "__TTS_PAUSE_COLON__"
_PAUSE_DOT: Final[str] = "__TTS_PAUSE_DOT__"
_PAUSE_DASH: Final[str] = "__TTS_PAUSE_DASH__"

_PUNCT_PAUSE_CHUNKS: Final[frozenset[str]] = frozenset[str](
    {
        _PAUSE_SHORT,
        _PAUSE_COMMA,
        _PAUSE_SEMI,
        _PAUSE_COLON,
        _PAUSE_DOT,
        _PAUSE_DASH,
    },
)

StrCommand: TypeAlias = list[str]
ChunkCallback: TypeAlias = Callable[[str], None]
ProgressCallback: TypeAlias = Callable[[str], None]
PlaybackFinishedCallback: TypeAlias = Callable[[], None]


def _notify_playback_finished(cb: PlaybackFinishedCallback | None) -> None:
    """
    Invoke an optional playback-finished hook while swallowing errors.

    TTS worker threads use this to signal the UI layer on the main side
    without risking crashes from callback exceptions.
    """

    if cb is None:
        return
    try:
        cb()
    except Exception:
        return


def _platform_tag() -> str:
    """
    Return a normalized platform tag string for locating binaries.

    This helper maps the current sys.platform value into a simplified
    identifier used to choose platform-specific TTS executables and
    resources.

    Returns:
        str:
            Lowercase platform label such as "linux", "windows", or
            "macos", or the raw sys.platform value when no known prefix
            matches.
    """
    plat: LiteralString = sys.platform.lower()
    if plat.startswith("linux"):
        return "linux"
    if plat.startswith("win"):
        return "windows"
    return "macos" if plat.startswith("darwin") else plat


def _arch_tag() -> str:
    """
    Return a normalized CPU architecture tag string for locating
    binaries.

    This helper maps environment and platform-reported architecture
    names into a simplified identifier used to choose
    architecture-specific TTS executables and resources.

    Returns:
        str:
            Lowercase architecture label such as "x86_64" or "arm64"
            when a known alias is detected, or the raw machine string
            (or "unknown") when no mapping applies.
    """
    m: str = (
        os.environ.get("PROCESSOR_ARCHITECTURE") or ""
    ).lower().strip() or (platform.machine() or "").lower().strip()
    if m in {"x86_64", "amd64"}:
        return "x86_64"
    return "arm64" if m in {"aarch64", "arm64"} else m or "unknown"


def _find_bundled_piper_binary(*, repo_root: Path) -> str | None:
    """
    Locate a bundled Piper executable for the current platform and arch.

    This helper constructs a platform- and architecture-specific bin
    directory under the repository root and returns the first existing
    Piper binary path it finds.

    Args:
        repo_root (Path):
            Root directory of the repository from which the function
            derives the 'bin/<platform>-<arch>' folder that may contain
            the Piper executable or a nested 'piper/' subfolder.

    Returns:
        str | None:
            Absolute path to a discovered Piper executable when one of
            the candidate locations exists as a file, or None if no
            suitable binary is found.
    """
    plat: str = _platform_tag()
    arch: str = _arch_tag()
    base: Path = repo_root / "bin" / f"{plat}-{arch}"
    candidates: list[Path] = (
        [base / "piper.exe", base / "piper" / "piper.exe"]
        if plat == "windows"
        else [base / "piper", base / "piper" / "piper"]
    )
    return next((str(p) for p in candidates if p.is_file()), None)


def _terminate_if_running(
    proc: Popen[bytes] | Popen[str] | None,
) -> None:
    """
    Terminate a subprocess if it is still running.

    This helper safely stops a potentially active text-to-speech or
    audio player process, ignoring errors if the process has already
    exited or cannot be terminated.

    Args:
        proc (Popen[bytes] | Popen[str] | None):
            Subprocess handle to check and terminate, which may be None
            or already finished; only a live process will receive a
            terminate signal.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass


def _play_wav_file(path: str) -> tuple[bool, str]:
    """
    Play a WAV audio file using an OS-appropriate mechanism.

    This helper chooses a platform-specific audio backend to
    synchronously play the given WAV file, trying several common
    command-line players on Linux and macOS when needed.

    Args:
        path (str):
            Filesystem path to the WAV file to be played; playback is
            skipped with an error message when the path is empty.

    Returns:
        tuple[bool, str]:
            Two-tuple where the first element indicates whether playback
            succeeded, and the second element is either an empty
            string, the name of the player used, or a short error
            description when playback fails.
    """
    if not path:
        return False, "empty path"
    plat: str = _platform_tag()
    if plat == "windows":
        try:
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME)
        except (ImportError, OSError, RuntimeError) as exc:
            return False, f"winsound failed: {exc}"
        return True, ""
    for cmd in _linux_macos_play_commands(wav_path=path):
        exe: str | None = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            r: CompletedProcess[bytes] = run(  # noqa: S603
                [exe, *cmd[1:]],
                stdout=DEVNULL,
                stderr=DEVNULL,
                check=False,
            )
            if r.returncode == 0:
                return True, cmd[0]
        except OSError:
            continue
    return False, "no player succeeded"


def _linux_macos_play_commands(wav_path: str) -> tuple[StrCommand, ...]:
    """
    Return candidate command-line players for WAV playback on Unix-like
    OSes.

    This helper enumerates a small ordered list of common audio playback
    commands that can be tried to play a WAV file on Linux or macOS.

    Args:
        wav_path (str):
            Filesystem path to the WAV file that each candidate command
            should be invoked with as its final argument.

    Returns:
        tuple[StrCommand, ...]:
            Tuple of argument lists, where each inner list starts with
            the player executable name followed by any flags and the
            WAV file path.
    """
    return (
        ["pw-play", wav_path],
        ["paplay", wav_path],
        ["aplay", wav_path],
        [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            wav_path,
        ],
    )


def _try_spawn_wav_player_subprocess(
    proc_path: str, argv: list[str]
) -> Popen[bytes] | None:
    """
    Start a background subprocess to play a WAV file and validate
    startup.

    This helper tries to launch an external audio player, waits briefly
    to see if it exits with an error, and returns the process handle
    only when startup appears successful.

    Args:
        proc_path (str):
            Filesystem path to the audio player executable that should
            be spawned as a subprocess.
        argv (list[str]):
            List of command-line arguments to pass to the player
            executable, typically including flags and the WAV file path.

    Returns:
        Popen[bytes] | None:
            Subprocess object when the player is successfully started
            and does not immediately exit with a non-zero status, or
            None when process creation fails or the player terminates
            with an error.
    """
    try:
        proc: Popen[bytes] = Popen[bytes](
            [proc_path, *argv],
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    except OSError:
        return None
    time.sleep(0.05)
    rc: int | None = proc.poll()
    return None if rc is not None and rc != 0 else proc


def _launch_wav_player(
    path: str,
) -> tuple[Popen[bytes] | None, str]:
    """
    Start an asynchronous WAV player process and report which backend is
    used.

    This helper searches for an available command-line audio player on
    Unix-like systems, attempts to launch it in the background for the
    given WAV file, and returns both the process handle and a short
    status label.

    Args:
        path (str):
            Filesystem path to the WAV file that should be played; an
            empty path short-circuits with a descriptive status string.

    Returns:
        tuple[Popen[bytes] | None, str]:
            Two-tuple where the first element is the spawned player
            subprocess (or None if no player could be started) and the
            second element is a status string indicating the chosen
            player name, "windows" when delegated to platform-specific
            handling, or an error description.
    """
    if not path:
        return None, "empty path"
    if _platform_tag() == "windows":
        return None, "windows"
    for cmd in _linux_macos_play_commands(wav_path=path):
        proc_path: str | None = shutil.which(cmd[0])
        if not proc_path:
            continue
        proc: Popen[bytes] | None = _try_spawn_wav_player_subprocess(
            proc_path, cmd[1:]
        )
        if proc is not None:
            return proc, cmd[0]
    return None, "no player started"


def _wav_duration_seconds(path: str) -> float:
    """
    Compute the duration of a WAV audio file in seconds.

    This helper inspects the WAV header to derive playback length and
    falls back to zero when the file cannot be read or has invalid
    metadata.

    Args:
        path (str):
            Filesystem path to the WAV file whose duration should be
            measured.

    Returns:
        float:
            Duration of the audio in seconds when the file is readable
            and reports a positive sample rate, or 0.0 on error or
            invalid header data.
    """
    try:
        with wave.open(path, "rb") as wf:
            frames: int = wf.getnframes()
            rate: int = wf.getframerate()
            if rate > 0:
                return frames / float(rate)
    except (OSError, EOFError, wave.Error):
        return 0.0
    return 0.0


def _collapse_adjacent_word_tokens(compact: list[str]) -> list[str]:
    """
    Combine adjacent word tokens into pairs to drive progress updates.

    This helper walks a compact token list and merges consecutive word
    tokens into two-word strings while leaving punctuation and
    standalone tokens untouched.

    Args:
        compact (list[str]):
            Sequence of non-empty tokens, typically produced by
            splitting TTS text into words and punctuation, from which
            adjacent word pairs will be formed.

    Returns:
        list[str]:
            New list where neighboring alphabetic tokens are joined into
            'word word' entries, preserving order and truncating
            nothing except for the merge of such adjacent pairs.
    """
    pairs: list[str] = []
    i = 0
    while i < len(compact):
        a: str = compact[i]
        b: str = compact[i + 1] if i + 1 < len(compact) else ""
        if (
            b
            and re.match(r"^\w+$", a, flags=re.UNICODE)
            and re.match(r"^\w+$", b, flags=re.UNICODE)
        ):
            pairs.append(f"{a} {b}")
            i += 2
        else:
            pairs.append(a)
            i += 1
    return pairs


def _tts_word_pairs(text: str) -> list[str]:
    """
    Derive a bounded list of two-word progress tokens from raw text.

    This helper tokenizes input into words and punctuation, compacts it,
    and returns at most 200 merged word pairs suitable for driving TTS
    progress updates.

    Args:
        text (str):
            Original text string to analyze, which may be empty or None
            and will be treated as an empty string in that case.

    Returns:
        list[str]:
            List of up to 200 tokens where adjacent words are combined
            into 'word word' pairs and punctuation tokens are preserved
            as-is, preserving the original reading order.
    """
    words: list[Any] = re.findall(r"\w+|[^\w\s]", text or "", flags=re.UNICODE)
    compact: list[str] = [w for w in words if w and not w.isspace()]
    return _collapse_adjacent_word_tokens(compact)[:200]


def _invoke_chunk(cb: ChunkCallback | None, message: str) -> None:
    """
    Invoke a chunk callback with a message if one is configured.

    This helper centralizes the null-check for optional callbacks so
    callers can emit progress or status messages without duplicating
    guard logic.

    Args:
        cb (ChunkCallback | None):
            Optional callable that accepts a single string message; when
            None, the message is silently ignored.
        message (str):
            Text payload to deliver to the callback when it is present,
            typically representing a TTS chunk or status line.
    """
    if cb is not None:
        cb(message)


def _unlink_quiet(path: str) -> None:
    """
    Remove a filesystem path while ignoring errors.

    This helper attempts to delete a temporary or auxiliary file and
    silently suppresses any OS-level failures so cleanup does not
    interfere with TTS playback or other processing.

    Args:
        path (str):
            Filesystem path to unlink, which may refer to a file that
            has already been removed or cannot be deleted due to
            permissions or other OS constraints.
    """
    try:
        os.remove(path)
    except OSError:
        pass


class _PiperWorkerSnap(NamedTuple):
    """
    Immutable snapshot of Piper synthesis parameters for worker threads.

    This named tuple bundles all runtime values needed to synthesize a
    single TTS chunk with Piper so that background workers can operate
    without touching shared mutable state.

    Attributes:
        chunk (str):
            Text fragment to be synthesized into speech, which may be a
            sentence, phrase, or punctuation-derived pause token.
        piper_path (str):
            Filesystem path to the Piper executable that will perform
            the synthesis for this chunk.
        model_path (str):
            Filesystem path to the Piper ONNX model to use when
            generating audio for the chunk.
        config_path (str):
            Filesystem path to the Piper JSON configuration that defines
            voice-specific parameters and tuning.
        sentence_silence (float):
            Base silence duration in seconds to insert between
            sentences, used to derive pause timing for punctuation and
            paragraph breaks.
        speed (float):
            Playback speed factor applied when computing Piper
            length-scale parameters, controlling how fast the
            synthesized audio is spoken.
    """

    chunk: str
    piper_path: str
    model_path: str
    config_path: str
    sentence_silence: float
    speed: float


class _EspeakWorkerSnap(NamedTuple):
    """
    Immutable snapshot of eSpeak synthesis parameters for worker
    threads.

    This named tuple captures all the per-utterance settings needed by
    the background eSpeak worker so it can speak a single chunk without
    accessing shared mutable controller state.

    Attributes:
        chunk (str):
            Text fragment to be synthesized into speech, which may be a
            word, short phrase, or pause token.
        engine (str):
            Executable name or full path of the eSpeak-compatible binary
            that should be invoked to render this chunk.
        voice (str):
            eSpeak voice identifier specifying language and timbre
            selection for the utterance (for example, "en" or "ru").
        rate (int):
            Speaking rate in words per minute to pass to eSpeak,
            controlling how fast the chunk is spoken.
    """

    chunk: str
    engine: str
    voice: str
    rate: int


def _espeak_pause_duration_seconds(chunk: str) -> float:
    """
    Compute the pause duration for an eSpeak TTS punctuation token.

    This helper applies a simple set of multipliers to a base pause
    length so that different punctuation marks yield slightly different
    silence durations.

    Args:
        chunk (str):
            Pause token constant representing either a specific
            punctuation mark (such as comma, semicolon, colon, dot, or
            dash) or a generic short pause.

    Returns:
        float:
            Pause duration in seconds derived from the base value and
            the punctuation-specific multiplier for the supplied token.
    """
    base: float = 0.16
    if chunk == _PAUSE_COMMA:
        return base
    if chunk == _PAUSE_SEMI:
        return base * 1.25
    if chunk == _PAUSE_COLON:
        return base * 1.35
    if chunk == _PAUSE_DOT:
        return base * 1.60
    return base * 1.10 if chunk == _PAUSE_DASH else base


def _piper_pause_duration_seconds(
    chunk: str, sentence_silence: float
) -> float:
    """
    Compute the pause duration for a Piper TTS punctuation token.

    This helper scales a clamped base sentence-silence value so that
    different punctuation marks produce distinct but bounded pause
    durations.

    Args:
        chunk (str):
            Pause token constant representing either a specific
            punctuation mark (such as comma, semicolon, colon, dot, or
            dash) or a generic short pause.
        sentence_silence (float):
            User-configurable base silence duration in seconds, which
            will be clamped into a reasonable range before
            punctuation-specific multipliers are applied.

    Returns:
        float:
            Pause duration in seconds derived from the clamped base
            value and the punctuation-specific multiplier for the
            supplied token, further constrained to a minimum and maximum
            bound.
    """
    base: float = max(0.04, min(0.55, sentence_silence))
    dur: float
    if chunk == _PAUSE_COMMA:
        dur = base * 1.00
    elif chunk == _PAUSE_SEMI:
        dur = base * 1.20
    elif chunk == _PAUSE_COLON:
        dur = base * 1.35
    elif chunk == _PAUSE_DOT:
        dur = base * 1.60
    elif chunk == _PAUSE_DASH:
        dur = base * 1.10
    else:
        dur = base
    return max(0.06, min(0.80, dur))


def _purge_windows_sound() -> None:
    """
    Purge any queued or ongoing WinMM sound playback on Windows systems.

    This helper calls the Win32 SND_PURGE operation via winsound so that
    previously started PlaySound calls do not continue or interfere
    with subsequent TTS audio.

    """
    if _platform_tag() != "windows":
        return
    import winsound

    winsound.PlaySound(None, winsound.SND_PURGE)


def _try_consume_tts_pause_token(
    *,
    chunk: str,
    paragraph_sleep_seconds: float,
    punct_sleep_for_chunk: Callable[[str], float],
    advance_chunk_index: Callable[[], None],
) -> bool:
    """
    Consume a TTS pause token by sleeping and advancing the chunk index.

    This helper centralizes handling of paragraph and punctuation pause
    tokens so callers can uniformly apply time-based delays and move
    the playback cursor forward.

    Args:
        chunk (str):
            Current token from the TTS queue, which may represent a
            paragraph pause marker, a punctuation pause marker, or
            normal speech content.
        paragraph_sleep_seconds (float):
            Duration in seconds to sleep when the chunk denotes a
            paragraph pause, typically longer than punctuation pauses.
        punct_sleep_for_chunk (Callable[[str], float]):
            Callback that computes the appropriate punctuation pause
            duration in seconds for the given chunk when it is a
            punctuated pause token.
        advance_chunk_index (Callable[[], None]):
            Callable that advances the internal chunk index so playback
            continues with the next token after a pause is consumed.

    Returns:
        bool:
            True when the chunk was recognized as a pause token, a sleep
            was performed, and the index was advanced; False when the
            token should be treated as normal speech content instead.
    """
    if chunk == _PAUSE_PAR:
        time.sleep(paragraph_sleep_seconds)
        advance_chunk_index()
        return True
    if chunk in _PUNCT_PAUSE_CHUNKS:
        time.sleep(punct_sleep_for_chunk(chunk))
        advance_chunk_index()
        return True
    return False


class OfflineTtsController:

    def __init__(self, *, split_for_tts: Callable[[str], list[str]]) -> None:
        self._split_for_tts: Callable[[str], list[str]] = split_for_tts
        self._lock: LockType = threading.Lock()
        self._worker: threading.Thread | None = None
        self._queue: list[str] = []
        self._idx: int = 0
        self._paused: bool = False
        self._stop: bool = False
        self._proc: Popen[bytes] | None = None
        self.voice: str = "ru"
        self.rate: int = 175
        self.engine: str | None = shutil.which("espeak-ng") or shutil.which(
            "espeak"
        )
        self.on_chunk: ChunkCallback | None = None
        self.on_progress: ProgressCallback | None = None
        self.on_playback_finished: PlaybackFinishedCallback | None = None

    def _advance_chunk_index(self) -> None:
        """
        Advance the current TTS chunk index in a thread-safe manner.

        This helper centralizes mutation of the playback cursor so
        worker threads can move to the next chunk without corrupting
        shared state.

        """
        with self._lock:
            self._idx += 1

    def is_available(self) -> bool:
        """
        Report whether an offline eSpeak-compatible TTS engine is
        available.

        This method checks the resolved engine executable path and
        indicates if the controller can currently accept speech
        requests.

        Returns:
            bool:
                True when an eSpeak or eSpeak-NG binary has been found
                and stored in the engine attribute, or False when no
                compatible TTS engine is configured.
        """
        return bool(self.engine)

    def is_active(self) -> bool:
        """
        Report whether the offline TTS controller currently has an
        active worker.

        This method checks the background synthesis thread under a lock
        and indicates if any speech job is still running.

        Returns:
            bool:
                True when a worker thread exists and is alive, meaning
                speech processing is ongoing; False when no active
                worker is present.
        """
        with self._lock:
            return bool(self._worker and self._worker.is_alive())

    def speak(self, text: str) -> bool:
        """
        Begin speaking a text string using the offline eSpeak-based TTS
        engine.

        This method splits the input text into TTS chunks, resets
        internal playback state under a lock, and starts a background
        worker thread to process the queue.

        Args:
            text (str):
                Full text to be spoken, which will be segmented into
                chunks by the configured split_for_tts function; empty
                or whitespace-only input results in no speech.

        Returns:
            bool:
                True if the text was successfully queued and a worker
                thread started (assuming an engine is configured), or
                False when there is nothing to read or no
                eSpeak-compatible engine is available.
        """
        chunks: list[str] = self._split_for_tts(text)
        if not chunks:
            return False
        if not self.engine:
            return False
        with self._lock:
            self._queue = chunks
            self._idx = 0
            self._paused = False
            self._stop = False
        self._start_worker()
        return True

    def pause(self) -> None:
        """
        Pause offline TTS playback and stop the current eSpeak
        subprocess.

        This method marks the controller as paused under a lock and then
        terminates any active eSpeak process so speech output halts
        promptly.

        """
        with self._lock:
            self._paused = True
            proc: Popen[bytes] | None = self._proc
        _terminate_if_running(proc)

    def resume(self) -> None:
        """
        Resume offline TTS playback if there is queued text to read.

        This method clears the paused flag under a lock when a queue
        exists and restarts the background worker thread to continue
        speaking from the current position.

        """
        with self._lock:
            if not self._queue:
                return
            self._paused = False
        self._start_worker()

    def stop(self) -> None:
        """
        Stop offline TTS playback and terminate any active eSpeak
        subprocess.

        This method sets a stop flag under a lock so the worker loop
        will exit and then terminates the currently running eSpeak
        process, if any, to halt speech immediately.

        """
        with self._lock:
            self._stop = True
            proc: Popen[bytes] | None = self._proc
        _terminate_if_running(proc)

    def toggle_pause(self) -> bool:
        """
        Toggle offline TTS playback between paused and active states.

        This method either pauses the controller and stops current
        speech or resumes playback from the existing queue, returning a
        flag that tells callers whether audio is now paused.

        Returns:
            bool:
                True when playback is switched into the paused state,
                and False when playback is resumed and speech
                continues.
        """
        with self._lock:
            paused: bool = self._paused
        if paused:
            self.resume()
            return False
        self.pause()
        return True

    def _start_worker(self) -> None:
        """
        Start a background worker thread to process the TTS queue.

        This helper ensures that only one worker is active at a time
        and, when idle, spawns a new daemon thread to run the
        controller's main loop.

        """
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def _espeak_lock_snapshot(self) -> _EspeakWorkerSnap | None:
        """
        Capture a thread-safe snapshot of the next eSpeak utterance to
        speak.

        This helper inspects the controller state under a lock and, when
        speech is allowed, returns an immutable description of the next
        chunk along with engine, voice, and rate settings.

        Returns:
            _EspeakWorkerSnap | None:
                Named tuple containing the current queue chunk, engine
                path, voice, and speaking rate when playback is active
                and within bounds, or None when playback is stopped,
                paused, past the end of the queue, or has no configured
                engine.
        """
        with self._lock:
            if self._stop or self._paused:
                return None
            if self._idx >= len(self._queue):
                return None
            engine: str | None = self.engine
            if not engine:
                return None
            return _EspeakWorkerSnap(
                chunk=self._queue[self._idx],
                engine=engine,
                voice=self.voice,
                rate=int(self.rate),
            )

    def _espeak_try_consume_pause_chunk(self, snap: _EspeakWorkerSnap) -> bool:
        """
        Handle an eSpeak pause token by sleeping and advancing the chunk
        index.

        This helper delegates to the generic pause-consumption routine
        using eSpeak-specific timing rules so punctuation and paragraph
        breaks yield appropriate delays.

        Args:
            snap (_EspeakWorkerSnap):
                Snapshot describing the current utterance chunk, whose
                'chunk' field is inspected to decide whether to apply a
                paragraph or punctuation pause and whether to advance
                the playback cursor.

        Returns:
            bool:
                True when the chunk is treated as a pause (a sleep is
                performed and the index advanced), or False when the
                chunk should instead be spoken by eSpeak.
        """
        return _try_consume_tts_pause_token(
            chunk=snap.chunk,
            paragraph_sleep_seconds=0.28,
            punct_sleep_for_chunk=lambda c: max(
                0.06, min(0.55, _espeak_pause_duration_seconds(c))
            ),
            advance_chunk_index=self._advance_chunk_index,
        )

    def _espeak_run_utterance_subprocess(
        self, snap: _EspeakWorkerSnap
    ) -> bool:
        """
        Speak a single eSpeak utterance chunk via a subprocess call.

        This helper notifies listeners about the chunk, invokes the
        eSpeak engine with the configured voice and rate, and tracks
        the subprocess lifecycle for cancellation.

        Args:
            snap (_EspeakWorkerSnap):
                Snapshot describing the current utterance, including the
                text chunk to speak, the eSpeak engine executable path,
                the selected voice identifier, and the speaking rate.

        Returns:
            bool:
                True when the eSpeak subprocess is started successfully
                and runs to completion, or False if process creation
                fails with an OS-level error.
        """
        _invoke_chunk(self.on_chunk, snap.chunk)
        cmd: Sequence[str] = [
            snap.engine,
            "-v",
            snap.voice,
            "-s",
            str(snap.rate),
            snap.chunk,
        ]
        try:
            proc: Popen[bytes] = Popen[bytes](
                cmd,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
        except OSError:
            return False
        with self._lock:
            self._proc = proc
        try:
            proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        return True

    def _run(self) -> None:
        """
        Run the offline eSpeak worker loop until playback completes or
        stops.

        This method repeatedly fetches the next utterance snapshot,
        consumes pause tokens, and speaks chunks via eSpeak until the
        queue is exhausted or playback is halted.

        """
        while True:
            snap: _EspeakWorkerSnap | None = self._espeak_lock_snapshot()
            if snap is None:
                break
            if self._espeak_try_consume_pause_chunk(snap):
                continue
            if not self._espeak_run_utterance_subprocess(snap):
                break
            with self._lock:
                if self._stop or self._paused:
                    break
                self._idx += 1
        with self._lock:
            at_end: bool = self._idx >= len(self._queue)
            finished_clean: bool = (
                at_end and not self._stop and not self._paused
            )
        if finished_clean:
            _notify_playback_finished(self.on_playback_finished)


class PiperTtsController:

    def __init__(
        self,
        *,
        repo_root: Path,
        split_for_tts: Callable[[str], list[str]],
        default_voice: str = "ru_RU-ruslan-medium",
    ) -> None:
        self._split_for_tts: Callable[[str], list[str]] = split_for_tts
        self._lock: LockType = threading.Lock()
        self._worker: threading.Thread | None = None
        self._queue: list[str] = []
        self._idx = 0
        self._paused = False
        self._stop = False
        self._proc: Popen[str] | None = None
        self.piper_path: str | None = _find_bundled_piper_binary(
            repo_root=repo_root
        ) or shutil.which("piper")
        self.model_path: str = str(
            repo_root / "tts_models" / default_voice / "model.onnx"
        )
        self.config_path: str = str(
            repo_root / "tts_models" / default_voice / "model.onnx.json"
        )
        self.on_chunk: ChunkCallback | None = None
        self.on_progress: ProgressCallback | None = None
        self.on_playback_finished: PlaybackFinishedCallback | None = None
        self.sentence_silence: float = 0.22
        self.speed: float = 1.0

    def _advance_chunk_index(self) -> None:
        """
        Advance the current Piper TTS chunk index in a thread-safe
        manner.

        This helper centralizes mutation of the playback cursor so
        worker threads can move to the next Piper chunk without
        corrupting shared state.

        """
        with self._lock:
            self._idx += 1

    def is_available(self) -> bool:
        """
        Report whether the Piper-based offline TTS engine is available.

        This method checks that a Piper executable and the configured
        model file both exist on disk so the controller can accept
        speech requests.

        Returns:
            bool:
                True when a Piper binary path is configured, points to
                an existing file, and the model_path also resolves to a
                file, or False when any of these prerequisites are
                missing.
        """
        pp: str | None = self.piper_path
        return bool(
            pp and Path(pp).is_file() and Path(self.model_path).is_file()
        )

    def is_active(self) -> bool:
        """
        Report whether the Piper TTS controller currently has an active
        worker.

        This method inspects the background synthesis thread under a
        lock and indicates if any Piper speech job is still running.

        Returns:
            bool:
                True when a worker thread exists and is alive, meaning
                Piper- based speech processing is ongoing; False when
                no active worker is present.
        """
        with self._lock:
            return bool(self._worker and self._worker.is_alive())

    def speak(self, text: str) -> bool:
        """
        Begin speaking a text string using the Piper-based TTS engine.

        This convenience method splits the input text into chunks and
        forwards them to speak_chunks, starting playback from the
        beginning.

        Args:
            text (str):
                Full text to be spoken, which will be segmented into
                chunks by the configured split_for_tts function before
                being queued for Piper synthesis.
        """
        return self.speak_chunks(chunks=self._split_for_tts(text), start_idx=0)

    def speak_chunks(self, chunks: list[str], start_idx: int = 0) -> bool:
        """
        Begin speaking a pre-split list of text chunks using the Piper
        engine.

        This method initializes the internal playback queue from the
        provided chunks, normalizes the starting index, and launches a
        background worker to synthesize and play audio.

        Args:
            chunks (list[str]):
                Ordered list of text chunks to be spoken by Piper, which
                may be empty or None and will result in no playback
                when empty after normalization.
            start_idx (int):
                Zero-based index into the chunks list from which
                playback should begin; negative or out-of-range values
                are clamped into a valid starting position.

        Returns:
            bool:
                True when a non-empty chunk list is accepted, Piper is
                available, state is reset, and a worker thread is
                started; False when there is nothing to read or Piper is
                not available.
        """
        chunks = chunks or []
        if not chunks:
            return False
        if not self.is_available():
            return False
        si: int = int(start_idx or 0)
        si = max(si, 0)
        if si >= len(chunks):
            si = 0
        with self._lock:
            self._queue = chunks
            self._idx = si
            self._paused = False
            self._stop = False
        self._start_worker()
        return True

    def cursor_index(self) -> int:
        """
        Return the current zero-based Piper chunk index in a thread-safe
        way.

        This accessor exposes the playback cursor so external callers
        can track or persist the current position within the Piper TTS
        queue.

        Returns:
            int:
                Integer index of the next chunk to be spoken, captured
                under a lock to avoid races with the worker thread.
        """
        with self._lock:
            return int(self._idx)

    def pause(self) -> None:  # sourcery skip: class-extract-method
        """
        Pause Piper-based TTS playback and stop any active audio
        process.

        This method marks the controller as paused under a lock,
        terminates the current Piper synthesis or player subprocess,
        and purges any queued WinMM sound on Windows.

        """
        with self._lock:
            self._paused = True
            proc: Popen[str] | None = self._proc
        _terminate_if_running(proc)
        _purge_windows_sound()

    def resume(self) -> None:
        """
        Resume Piper-based TTS playback if there is queued text to read.

        This method clears the paused flag under a lock when chunks are
        available and restarts the background worker thread to continue
        speaking from the current position.

        """
        with self._lock:
            if not self._queue:
                return
            self._paused = False
        self._start_worker()

    def stop(self) -> None:
        """
        Stop Piper-based TTS playback and terminate any active audio
        process.

        This method sets a stop flag under a lock so the worker loop
        will exit, then terminates the current Piper synthesis or
        player subprocess and purges any queued WinMM sound on Windows
        to halt audio immediately.

        """
        with self._lock:
            self._stop = True
            proc: Popen[str] | None = self._proc
        _terminate_if_running(proc)
        _purge_windows_sound()

    def toggle_pause(self) -> bool:
        """
        Toggle Piper-based TTS playback between paused and active
        states.

        This method either pauses the controller and stops current audio
        or resumes playback from the existing queue, returning a flag
        that tells callers whether audio is now paused.

        Returns:
            bool:
                True when playback is switched into the paused state,
                and False when playback is resumed and speech
                continues.
        """
        with self._lock:
            paused: bool = self._paused
        if paused:
            self.resume()
            return False
        self.pause()
        return True

    def _start_worker(self) -> None:
        """
        Start a background worker thread to process the Piper TTS queue.

        This helper ensures that only one Piper worker is active at a
        time and, when idle, spawns a new daemon thread to run the
        controller's main loop.

        """
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def _piper_lock_snapshot(self) -> _PiperWorkerSnap | None:
        """
        Capture a thread-safe snapshot of the next Piper utterance to
        speak.

        This helper inspects the controller state under a lock and, when
        speech is allowed, returns an immutable description of the next
        chunk together with all Piper synthesis parameters.

        Returns:
            _PiperWorkerSnap | None:
                Named tuple containing the current queue chunk, Piper
                binary path, model and config paths, sentence-silence
                value, and speed when playback is active and within
                bounds, or None when playback is stopped, paused, past
                the end of the queue, or has no usable Piper
                configuration.
        """
        with self._lock:
            if self._stop or self._paused:
                return None
            if self._idx >= len(self._queue):
                return None
            return _PiperWorkerSnap(
                chunk=self._queue[self._idx],
                piper_path=self.piper_path or "",
                model_path=self.model_path,
                config_path=self.config_path,
                sentence_silence=float(self.sentence_silence),
                speed=float(self.speed),
            )

    def _piper_try_consume_pause_chunk(self, snap: _PiperWorkerSnap) -> bool:
        """
        Handle a Piper pause token by sleeping and advancing the chunk
        index.

        This helper delegates to the generic pause-consumption routine
        using Piper-specific timing rules so punctuation and paragraph
        breaks yield appropriate delays.

        Args:
            snap (_PiperWorkerSnap):
                Snapshot describing the current utterance chunk, whose
                'chunk' and 'sentence_silence' fields are used to
                compute pause durations and decide whether to advance
                the playback cursor.

        Returns:
            bool:
                True when the chunk is treated as a pause (a sleep is
                performed and the index advanced), or False when the
                chunk should instead be synthesized and played by Piper.
        """
        return _try_consume_tts_pause_token(
            chunk=snap.chunk,
            paragraph_sleep_seconds=0.32,
            punct_sleep_for_chunk=lambda c: _piper_pause_duration_seconds(
                chunk=c, sentence_silence=snap.sentence_silence
            ),
            advance_chunk_index=self._advance_chunk_index,
        )

    def _piper_mkstemp_wav(self) -> str | None:
        """
        Create a temporary WAV file path for Piper synthesis output.

        This helper allocates a unique temporary filename with a WAV
        suffix and immediately closes the underlying file descriptor so
        Piper can safely overwrite the file.

        Returns:
            str | None:
                Filesystem path to the newly created temporary WAV file
                when successful, or None if the OS-level mkstemp call
                fails and no file could be created.
        """
        try:
            fd, out_wav = tempfile.mkstemp(
                prefix="study_md_desk_tts_",
                suffix=".wav",
            )
            os.close(fd)
        except OSError:
            return None
        return out_wav

    def _piper_build_synthesis_cmd(
        self, snap: _PiperWorkerSnap, out_wav: str
    ) -> list[str]:
        """
        Build the Piper command-line invocation for synthesizing a
        chunk.

        This helper computes a bounded length-scale from the requested
        speed and assembles the full list of arguments needed to
        generate audio into the given WAV file.

        Args:
            snap (_PiperWorkerSnap):
                Snapshot providing Piper paths and synthesis parameters,
                including the executable, model and config paths,
                sentence silence, and speed.
            out_wav (str):
                Filesystem path to the temporary WAV file that Piper
                should write synthesized audio into.

        Returns:
            list[str]:
                Argument list starting with the Piper executable and
                including model, output file, sentence silence,
                length-scale, and an optional config flag when a valid
                config file is present.
        """
        length_scale = max(0.7, min(1.6, 1.0 / max(0.5, snap.speed)))
        cmd: list[str] = [
            snap.piper_path,
            "--model",
            snap.model_path,
            "--output_file",
            out_wav,
            "--sentence_silence",
            str(snap.sentence_silence),
            "--length_scale",
            str(length_scale),
        ]
        cfg = snap.config_path
        if cfg and Path(cfg).is_file():
            cmd += ["--config", cfg]
        return cmd

    def _piper_spawn_synthesis_process(
        self, cmd: list[str], out_wav: str
    ) -> Popen[str] | None:
        """
        Start a Piper synthesis subprocess and clean up on failure.

        This helper launches the Piper binary with the prepared
        command-line, configuring stdin and discarding output streams,
        and removes the temporary WAV file if the process cannot be
        created.

        Args:
            cmd (list[str]):
                Fully constructed Piper command-line argument list,
                starting with the executable path followed by model,
                output, and other synthesis flags.
            out_wav (str):
                Filesystem path to the temporary WAV file that Piper is
                expected to write; this file is deleted if process
                startup fails.

        Returns:
            Popen[str] | None:
                Subprocess handle for the running Piper process when
                creation succeeds, or None if an OS-level error occurs
                during spawn.
        """
        try:
            return Popen[str](
                cmd,
                stdin=PIPE,
                stdout=DEVNULL,
                stderr=DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            _unlink_quiet(out_wav)
            return None

    @staticmethod
    def _piper_write_chunk_to_stdin(stdin: IO[str], chunk: str) -> None:
        """
        Write a Piper text chunk to the process stdin and close the
        stream.

        This helper sends the utterance text followed by a newline to
        Pipers standard input, flushing it and safely handling broken
        pipes.

        Args:
            stdin (IO[str]):
                Text-mode file-like object representing the Piper
                process stdin, which will be written to, flushed, and
                closed.
            chunk (str):
                Text chunk to synthesize, written as a single line so
                Piper can process it as one utterance.
        """
        try:
            stdin.write(chunk)
            stdin.write("\n")
            stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            stdin.close()

    def _piper_drain_synthesis_subprocess(
        self, proc: Popen[str], chunk: str
    ) -> None:
        """
        Drain a Piper synthesis subprocess while tracking its lifecycle.

        This helper records the active Piper process under a lock, feeds
        the text chunk into its stdin, waits for synthesis to complete,
        and then clears the stored handle if it still points to the same
        process.

        Args:
            proc (Popen[str]):
                Subprocess instance representing the running Piper
                synthesis command whose stdin will be written to and
                waited on.
            chunk (str):
                Text chunk to synthesize, which is forwarded to Piper
                via its standard input before the process is allowed to
                exit.
        """
        with self._lock:
            self._proc = proc
        try:
            stdin: IO[str] | None = proc.stdin
            if stdin is not None:
                self._piper_write_chunk_to_stdin(stdin, chunk)
            proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def _piper_exec_synthesis(
        self, cmd: list[str], out_wav: str, chunk: str
    ) -> bool:
        """
        Run a single Piper synthesis job and report whether it
        succeeded.

        This helper spawns the Piper process for one chunk, streams the
        text into it, and returns a flag indicating if synthesis
        completed without startup failure.

        Args:
            cmd (list[str]):
                Fully constructed Piper command-line argument list,
                starting with the executable path followed by model,
                output, and other synthesis flags.
            out_wav (str):
                Filesystem path to the temporary WAV file that Piper is
                expected to write during synthesis.
            chunk (str):
                Text chunk to synthesize, which is forwarded to Piper
                via its standard input once the process is running.

        Returns:
            bool:
                True when the Piper process is created successfully and
                drained for the given chunk, or False if the process
                cannot be started.
        """
        proc: Popen[str] | None = self._piper_spawn_synthesis_process(
            cmd, out_wav
        )
        if proc is None:
            return False
        self._piper_drain_synthesis_subprocess(proc, chunk)
        return True

    def _piper_aborted_after_synthesis(self, out_wav: str) -> bool:
        """
        Decide whether to discard a synthesized WAV file after Piper
        aborts.

        This helper checks the stop and pause flags under a lock and,
        when playback has been cancelled, deletes the synthesized WAV
        file instead of proceeding to playback.

        Args:
            out_wav (str):
                Filesystem path to the synthesized WAV file that should
                be removed when playback has been stopped or paused.

        Returns:
            bool:
                True when playback was aborted and the WAV file has been
                unlinked, or False when playback should continue and
                the file remains in place.
        """
        with self._lock:
            if not (self._stop or self._paused):
                return False
        _unlink_quiet(path=out_wav)
        return True

    @staticmethod
    def _piper_wav_byte_size(path: str) -> int:
        """
        Return the byte size of a Piper-generated WAV file.

        This helper inspects the filesystem metadata for a WAV path and
        falls back to zero when the file cannot be read.

        Args:
            path (str):
                Filesystem path to the WAV file whose size in bytes
                should be queried.

        Returns:
            int:
                Non-negative integer representing the file size in bytes
                when stat succeeds, or 0 if the file does not exist or
                cannot be accessed.
        """
        try:
            return int(Path(path).stat().st_size)
        except OSError:
            return 0

    def _piper_emit_playback_status_line(
        self,
        *,
        duration: float,
        file_size: int,
        player_name: str,
    ) -> None:
        """
        Emit a status message describing the current Piper playback
        details.

        This helper formats a concise line with WAV duration, byte size,
        and player name and forwards it to the chunk callback for
        logging or UI display.

        Args:
            duration (float):
                Length of the synthesized WAV audio in seconds, used to
                populate the human-readable duration field of the
                status line.
            file_size (int):
                Size of the WAV file in bytes, included in the status
                message to give an indication of audio payload size.
            player_name (str):
                Identifier for the playback backend used to play the WAV
                file, such as a command-line player name or a
                platform-specific tag.
        """
        _invoke_chunk(
            cb=self.on_chunk,
            message=f"[tts] wav {duration:.2f}s, {file_size} bytes, player={player_name}",
        )

    def _piper_poll_player_progress_ticks(
        self,
        player: Popen[bytes],
        *,
        duration: float,
        pairs: list[str],
        on_progress: ProgressCallback,
    ) -> None:
        """
        Poll a WAV player process and emit word-level TTS progress
        ticks.

        This helper advances through precomputed word pairs at
        time-based intervals while the player runs, stopping early if
        playback is paused or cancelled.

        Args:
            player (Popen[bytes]):
                Subprocess handle for the external audio player whose
                lifetime governs the polling loop and potential early
                termination.
            duration (float):
                Total WAV duration in seconds used to derive the
                interval between successive word-pair progress
                callbacks.
            pairs (list[str]):
                Ordered list of word or word-pair tokens that will be
                emitted one by one to reflect playback progress.
            on_progress (ProgressCallback):
                Callback invoked with each token from 'pairs' at the
                scheduled times while the player remains active.
        """
        step: float = max(0.06, duration / max(1, len(pairs)))
        next_tick: float = time.monotonic()
        widx = 0
        while player.poll() is None:
            with self._lock:
                if self._stop or self._paused:
                    _terminate_if_running(proc=player)
                    break
            now: float = time.monotonic()
            if widx < len(pairs) and now >= next_tick:
                on_progress(pairs[widx])
                widx += 1
                next_tick = now + step
            time.sleep(0.03)

    @staticmethod
    def _piper_join_player_process(player: Popen[bytes]) -> None:
        """
        Wait for a WAV player process to exit, terminating it on
        timeout.

        This helper joins the player subprocess with a short timeout
        and, if it does not finish in time, forcefully terminates it
        and waits again to ensure it fully exits.

        Args:
            player (Popen[bytes]):
                Subprocess handle for the external audio player that
                should be waited on and, if necessary, terminated when
                it exceeds the allowed shutdown timeout.
        """
        try:
            player.wait(timeout=1.0)
        except TimeoutExpired:
            _terminate_if_running(proc=player)
            player.wait(timeout=2.0)

    def _piper_play_with_word_progress(
        self,
        player: Popen[bytes],
        *,
        duration: float,
        pairs: list[str],
        on_progress: ProgressCallback,
    ) -> None:
        """
        Play a WAV file while emitting word-level progress updates.

        This helper wraps the polling and join logic so callers can
        track per-word progress during playback and ensure the player
        process is cleanly terminated.

        Args:
            player (Popen[bytes]):
                Subprocess handle for the external audio player that
                will play the synthesized WAV file.
            duration (float):
                Total WAV duration in seconds used to drive the
                scheduling of word-level progress callbacks.
            pairs (list[str]):
                Ordered list of word or word-pair tokens that will be
                emitted one by one to reflect playback progress.
            on_progress (ProgressCallback):
                Callback invoked with each token from 'pairs' while the
                player remains active, allowing the UI or logs to
                reflect TTS progress.
        """
        self._piper_poll_player_progress_ticks(
            player,
            duration=duration,
            pairs=pairs,
            on_progress=on_progress,
        )
        self._piper_join_player_process(player)

    def _piper_play_via_subprocess_player(
        self,
        *,
        chunk: str,
        duration: float,
        player: Popen[bytes],
    ) -> None:
        """
        Play a synthesized Piper chunk via a subprocess-based WAV
        player.

        This helper optionally emits word-level progress callbacks while
        the player runs and falls back to a simple blocking wait when
        progress tracking is not applicable.

        Args:
            chunk (str):
                Text chunk that was synthesized into the WAV file and
                whose word pairs may be used to drive progress updates.
            duration (float):
                Total WAV duration in seconds used to schedule
                word-level progress callbacks when enabled.
            player (Popen[bytes]):
                Subprocess handle for the external audio player
                responsible for playing the synthesized WAV file.
        """
        pairs: list[str] = _tts_word_pairs(text=chunk)
        prog: ProgressCallback | None = self.on_progress
        if pairs and prog is not None and duration > 0:
            self._piper_play_with_word_progress(
                player,
                duration=duration,
                pairs=pairs,
                on_progress=prog,
            )
        else:
            player.wait()

    def _piper_play_via_sync_cli(self, out_wav: str) -> None:
        """
        Play a Piper-generated WAV file using a synchronous CLI
        fallback.

        This helper delegates playback to the generic WAV player wrapper
        and, on failure, emits a diagnostic chunk message describing
        why playback could not start.

        Args:
            out_wav (str):
                Filesystem path to the synthesized WAV file that should
                be played via the platform-appropriate command-line
                audio player.
        """
        ok_play, play_info = _play_wav_file(path=out_wav)
        if not ok_play:
            _invoke_chunk(
                cb=self.on_chunk,
                message=f"[piper] play failed: {play_info}",
            )

    def _piper_playback_wav(
        self,
        *,
        out_wav: str,
        chunk: str,
        file_size: int,
        duration: float,
        player: Popen[bytes] | None,
        player_name: str,
    ) -> None:
        """
        Play a synthesized Piper WAV chunk and emit a status line about
        it.

        This helper reports basic playback details, prefers a
        subprocess-based player when available, and falls back to a
        synchronous CLI player otherwise.

        Args:
            out_wav (str):
                Filesystem path to the synthesized WAV file that should
                be played.
            chunk (str):
                Text chunk that was synthesized into the WAV file and
                may be used for progress tracking when a subprocess
                player is used.
            file_size (int):
                Size of the WAV file in
                bytes, included in the emitted status line for
                informational purposes.
            duration (float):
                Total WAV
                duration in seconds, used both for status reporting and
                to drive progress callbacks when applicable.
            player (Popen[bytes] | None):
                Optional subprocess handle for an already-launched audio
                player; when provided, playback and progress tracking
                are delegated to it instead of spawning a new player.
            player_name (str):
                Identifier for the playback backend,  the playback
                backend, included in the status message to indicate
                which mechanism is being used.
        """
        self._piper_emit_playback_status_line(
            duration=duration,
            file_size=file_size,
            player_name=player_name,
        )
        if player is not None:
            self._piper_play_via_subprocess_player(
                chunk=chunk,
                duration=duration,
                player=player,
            )
            return
        self._piper_play_via_sync_cli(out_wav)

    def _piper_handle_speech_chunk(self, snap: _PiperWorkerSnap) -> bool:
        """
        Handle a single Piper speech chunk from synthesis through
        playback.

        This method logs the chunk, synthesizes it into a temporary WAV
        file, plays the audio while respecting stop and pause flags,
        and advances the chunk index when playback completes.

        Args:
            snap (_PiperWorkerSnap):
                Snapshot describing the current utterance, including the
                text chunk to speak and all Piper synthesis parameters
                such as executable path, model path, config path,
                sentence silence, and speed.

        Returns:
            bool:
                True when processing of this chunk should end the worker
                loop (for example, due to synthesis failure, aborted
                playback, or a stop/pause request), or False when
                playback finished normally and the controller should
                proceed to the next chunk.
        """
        _invoke_chunk(cb=self.on_chunk, message=snap.chunk)
        out_wav: str | None = self._piper_mkstemp_wav()
        if out_wav is None:
            return True
        cmd: list[str] = self._piper_build_synthesis_cmd(snap, out_wav)
        if not self._piper_exec_synthesis(cmd, out_wav, snap.chunk):
            return True
        if self._piper_aborted_after_synthesis(out_wav):
            return True
        sz: int = self._piper_wav_byte_size(path=out_wav)
        duration: float = _wav_duration_seconds(path=out_wav)
        player, player_name = _launch_wav_player(path=out_wav)
        self._piper_playback_wav(
            out_wav=out_wav,
            chunk=snap.chunk,
            file_size=sz,
            duration=duration,
            player=player,
            player_name=player_name,
        )
        _unlink_quiet(path=out_wav)
        with self._lock:
            if self._stop or self._paused:
                return True
        self._advance_chunk_index()
        return False

    def _run(self) -> None:
        """
        Run the Piper worker loop until the queue is exhausted or
        playback stops.

        This method repeatedly acquires the next Piper snapshot, skips
        pause tokens, and processes speech chunks until there is no
        more work or a stop condition is reached.

        """
        while True:
            snap: _PiperWorkerSnap | None = self._piper_lock_snapshot()
            if snap is None:
                break
            if not snap.piper_path or not snap.model_path:
                break
            if self._piper_try_consume_pause_chunk(snap):
                continue
            if self._piper_handle_speech_chunk(snap):
                break
        with self._lock:
            at_end_p: bool = self._idx >= len(self._queue)
            finished_clean_p: bool = (
                at_end_p and not self._stop and not self._paused
            )
        if finished_clean_p:
            _notify_playback_finished(self.on_playback_finished)
