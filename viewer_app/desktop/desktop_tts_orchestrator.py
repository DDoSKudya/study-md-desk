"""
This module defines the text-to-speech orchestration layer for a
Qt-based markdown viewer, including how the UI triggers speech and chat
actions.

It coordinates reading document text aloud, resuming playback, pausing,
stopping, and sending selected text to a chat system.

It declares several Protocols that describe the minimal interfaces for a
generic TTS engine, a Piper-specific chunked TTS engine, a
JavaScript-capable web page, and a web view wrapper.

It defines a TypedDict, TtsActionsDict, which specifies the shape of the
exported action mapping from string keys to callables used by the UI.

It introduces TtsActionDependencies as a frozen dataclass that bundles
all external services needed for TTS orchestration, such as engines,
web view, cursor persistence, background runners, and status reporting.

This dependency object is injected into the orchestrator so the core
logic remains decoupled from concrete implementations of TTS,
threading, and UI.

Helper functions _run_inline and _resolve_background_runner normalize
background execution by allowing code to run either in a worker or
inline through a common callable interface.

The helpers _engine_unavailable_extra and _warn_unavailable_tts build
engine-specific error messages and display a warning dialog when a
selected TTS engine is missing or misconfigured.

The _require_page helper enforces that the web view has a loaded page
before running JavaScript, raising RuntimeError otherwise.

The _js_string_result helper sanitizes JavaScript callback results into
a string, defaulting to an empty string for non-string values.

The TtsActionFactory dataclass encapsulates all higher-level TTS and
chat behaviors that the UI can invoke.

It uses the injected dependencies to implement text reading, selection
reading, background document processing, pause and resume with Piper
cursor persistence, stopping playback, and sending selected text to
chat.

The tts_speak_text method validates input text and engine availability
before starting speech and updates a status indicator with the outcome.

The _start_document_tts method chooses between Piper chunked playback
with optional resume and a generic TTS engine, clearing any stale
resume cursor when needed.

The tts_read_document method runs markdown-to-text extraction and resume
index lookup in a background task, then dispatches back to the UI
thread to initiate reading.

The tts_read_selection_or_document method executes JavaScript to grab
the current text selection, reads it if present, or falls back to
reading the entire document through tts_read_document.

The pause_toggle_and_persist method toggles the engines pause state
and, when using Piper and pausing, saves the current chunk index for
later resumption.

The stop_and_clear method stops the active engine, clears any saved
cursor, invokes a page-side JavaScript hook to clear TTS UI markers,
and reports that TTS has stopped.

The ask_current_selection_in_chat method collects the current selection
via JavaScript and, if non-empty, passes it as a prompt to a configured
chat backend.

The build method constructs and returns the TtsActionsDict mapping of
all these methods so they can be easily registered as UI actions.

The top-level build_tts_actions function is a convenience wrapper that
creates a TtsActionFactory with the provided dependencies and returns
its action dictionary.

Within the broader system, this module acts as the central TTS and chat
control hub, connecting the UIs commands, the web content, and the
underlying TTS engines in a loosely coupled way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol, TypedDict

from viewer_app.desktop.desktop_tts_controls import build_selection_text_script
from viewer_app.desktop.desktop_web_helpers import (
    build_selection_prompt_script,
)

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget


class ActiveTtsEngine(Protocol):
    """
    Define the minimal interface required from an active text-to-speech
    engine.

    This protocol describes the operations the orchestrator expects from
    any concrete TTS backend so that reading, pausing, and stopping
    speech can be controlled uniformly.

    # Methods:

        is_available() -> bool:
            Reports whether the underlying TTS engine is ready to accept
            speech requests, allowing callers to show a warning if it
            is missing or misconfigured.

        speak(
            text: str
        ) -> bool:
            Starts speaking the given text aloud and returns True on a
            successful start, or False if the engine could not begin
            playback.

        toggle_pause() -> bool:
            Toggles between paused and playing states for the current
            utterance, returning True when playback becomes paused and
            False when it resumes.

        stop() -> None:
            Immediately stops any ongoing speech and clears the current
            utterance so subsequent calls must start a new reading
            session.
    """

    def is_available(self) -> bool: ...
    def speak(self, text: str) -> bool: ...
    def toggle_pause(self) -> bool: ...
    def stop(self) -> None: ...


class PiperTtsForOrchestrator(Protocol):
    """
    Describe the Piper-specific text-to-speech interface expected by the
    orchestrator.

    This protocol exposes chunk-based speaking and cursor tracking so
    the orchestrator can resume long readings at a specific chunk
    index.

    # Methods:

        speak_chunks(
            chunks: list[str],
            start_idx: int = 0,
        ) -> bool:
            Starts speaking a sequence of text chunks beginning at the
            given index, returning True when playback is successfully
            initiated and False otherwise.

        cursor_index() -> int:
            Returns the current zero-based chunk index within the active
            reading session, used to persist and later resume playback.
    """

    def speak_chunks(self, chunks: list[str], start_idx: int = 0) -> bool: ...
    def cursor_index(self) -> int: ...


class WebPageProtocol(Protocol):
    """
    Abstract the minimal JavaScript-capable web page interface.

    This protocol describes the Qt-like page operations the
    text-to-speech orchestrator needs in order to execute scripts and
    receive their results asynchronously.

    #Methods:

        runJavaScript(
            script: str,
            callback: Callable[[object], None] | None = None,
        ) -> None:
            Executes the given JavaScript snippet in the context of the
            current web page, optionally invoking the callback with the
            evaluation result once the script finishes.
    """

    def runJavaScript(  # noqa: N802 — Qt API name
        self,
        script: str,
        callback: Callable[[object], None] | None = None,
    ) -> None: ...


class WebEngineViewProtocol(Protocol):
    """
    Describe the minimal web view interface required by the
    orchestrator.

    This protocol abstracts a Qt-like view object that can expose a web
    page capable of running JavaScript for TTS and chat interactions.

    # Methods:

        page() -> WebPageProtocol | None:
            Returns the current web page object that supports JavaScript
            execution, or None if no page is loaded, allowing callers
            to guard against missing content before issuing scripts.
    """

    def page(self) -> WebPageProtocol | None: ...


class TtsActionsDict(TypedDict):
    """
    Dictionary of callable text-to-speech actions exposed to the UI.

    This typed mapping groups together the high-level TTS operations
    that the orchestrator offers so they can be wired into menus,
    buttons, or shortcuts in a type-safe way.

    Keys:
        tts_speak_text (Callable[[str], None]):
            Starts speaking an arbitrary text string immediately, using
            the currently selected TTS engine.
        tts_read_document (Callable[[], None]):
            Initiates reading of the entire current document, optionally
            resuming from a saved cursor position when supported.
        tts_read_selection_or_document (Callable[[], None]):
            Reads the currently selected text if any is present, falling
            back to reading the whole document when no selection
            exists.
        pause_toggle_and_persist (Callable[[], None]):
            Toggles between paused and playing states and, when paused
            with a resumable engine, persists the current cursor index.
        stop_and_clear (Callable[[], None]):
            Stops any ongoing TTS playback, clears stored cursor state,
            and resets related UI state.
        ask_current_selection_in_chat (Callable[[], None]):
            Sends the currently selected text as a prompt to the chat
            system, if any selection is available.
    """

    tts_speak_text: Callable[[str], None]
    tts_read_document: Callable[[], None]
    tts_read_selection_or_document: Callable[[], None]
    pause_toggle_and_persist: Callable[[], None]
    stop_and_clear: Callable[[], None]
    ask_current_selection_in_chat: Callable[[], None]


@dataclass(frozen=True)
class TtsActionDependencies:
    """
    Aggregate all runtime dependencies required by the TTS orchestrator.

    This immutable data container supplies the orchestrator with access
    to the active window, web view, TTS engines, document text,
    persistence helpers, and background execution utilities.

    Attributes:
        window (QWidget | None):
            Top-level application window used for parenting dialogs and
            warnings related to TTS availability.
        view (WebEngineViewProtocol):
            Web view that exposes the current markdown document and
            selection via JavaScript, used for both TTS and chat
            actions.
        get_active_tts (Callable[[], ActiveTtsEngine]):
            Callable that returns the currently active low-level TTS
            engine implementation, such as Piper or a system engine.
        get_tts_engine (Callable[[], str]):
            Callable that reports the name of the currently selected TTS
            engine, used to switch behavior such as resume support.
        tts_piper (PiperTtsForOrchestrator):
            Piper-specific TTS interface that
            supports chunked playback and cursor tracking for resumable
            reading.
        read_current_md_text (Callable[[], str]):
            Callable that extracts TTS-ready text from the current
            markdown document, including any preprocessing needed for
            reading.
        load_tts_cursor_for_current_doc (Callable[[], int | None]):
            Callable that retrieves a previously saved cursor index for
            the active document, or None if no resumable state exists.
        save_tts_cursor (Callable[[int], None]):
            Callable that persists the current cursor index for the
            active document so reading can later resume from that point.
        clear_tts_cursor (Callable[[], None]):
            Callable that clears any stored TTS cursor state associated
            with the current document when reading is stopped or reset.
        split_for_tts (Callable[[str], list[str]]):
            Callable that splits a long text into smaller chunks
            suitable for incremental TTS playback.
        send_prompt_to_chat (Callable[[str], None]):
            Callable that forwards a text prompt, typically the current
            selection, to the chat system for processing.
        show_status (Callable[[str, int], None]):
            Callable used to display short status messages about TTS
            actions to the user, with an optional timeout in
            milliseconds.
        run_in_background (Callable[[Callable[[], None]], None] | None):
            Optional scheduler for running blocking work, such as text
            extraction, off the UI thread; if None, work is run inline.
        dispatch_to_ui (Callable[[Callable[[], None]], None] | None):
            Optional dispatcher for posting callbacks back onto the UI
            thread after background work completes; if None, callbacks
            run inline.
    """

    window: QWidget | None
    view: WebEngineViewProtocol
    get_active_tts: Callable[[], ActiveTtsEngine]
    get_tts_engine: Callable[[], str]
    tts_piper: PiperTtsForOrchestrator
    read_current_md_text: Callable[[], str]
    load_tts_cursor_for_current_doc: Callable[[], int | None]
    save_tts_cursor: Callable[[int], None]
    clear_tts_cursor: Callable[[], None]
    split_for_tts: Callable[[str], list[str]]
    send_prompt_to_chat: Callable[[str], None]
    show_status: Callable[[str, int], None]
    run_in_background: Callable[[Callable[[], None]], None] | None
    dispatch_to_ui: Callable[[Callable[[], None]], None] | None


def _run_inline(task: Callable[[], None]) -> None:
    """
    Execute a task immediately in the current thread.

    This helper acts as a trivial background-runner implementation,
    allowing callers to treat synchronous execution and asynchronous
    scheduling through a common callable interface.

    Args:
        task (Callable[[], None]):
            Zero-argument callable representing the work to perform,
            which will be invoked inline without any threading or
            queuing.
    """
    task()


def _resolve_background_runner(
    fn: Callable[[Callable[[], None]], None] | None,
) -> Callable[[Callable[[], None]], None]:
    """
    Resolve a background task runner, falling back to inline execution.

    This helper returns the provided scheduling callable when available,
    or a no-op scheduler that simply runs tasks synchronously
    otherwise.

    Args:
        fn (Callable[[Callable[[], None]], None] | None):
            Optional background runner that accepts a zero-argument task
            callable and is responsible for executing it, potentially
            on a worker thread.

    Returns:
        Callable[[Callable[[], None]], None]:
            Concrete background runner to use; either the supplied
            implementation or a default that invokes tasks inline in
            the current thread.
    """
    return fn if fn is not None else _run_inline


def _engine_unavailable_extra(engine_name: str) -> str:
    """
    Build a human-readable explanation for an unavailable TTS engine.

    This helper returns engine-specific troubleshooting text that can be
    appended to a generic error message shown in the UI.

    Args:
        engine_name (str):
            Name of the currently selected text-to-speech engine, used
            to choose between Piper-specific guidance and generic
            system TTS installation advice.

    Returns:
        str:
            Multi-line message describing likely configuration problems
            and next steps for installing or configuring the requested
            engine.
    """
    if engine_name == "piper":
        return (
            "\n\nPiper is not found or not configured.\n"
            "- Check that the binary is located at "
            "`study_md_desk/bin/<platform>-<arch>/piper(.exe)`\n"
            "- Check `piperModelPath` and `piperConfigPath` in settings\n"
        )
    return (
        "\n\nInstall `espeak` or `espeak-ng`, or switch to Piper "
        "(ttsEngine=piper)."
    )


def _warn_unavailable_tts(window: QWidget | None, engine_name: str) -> None:
    """
    Show a user-facing warning when the selected TTS engine is
    unavailable.

    This helper builds an engine-specific troubleshooting message and
    displays it in a modal warning dialog so the user understands why
    speech cannot start.

    Args:
        window (QWidget | None):
            Optional parent widget for the warning dialog, typically the
            main application window; if None, the dialog is shown
            unparented.
        engine_name (str):
            Name of the currently selected text-to-speech engine, used
            to tailor the troubleshooting details included in the
            warning.
    """
    from PyQt6.QtWidgets import QMessageBox

    extra: str = _engine_unavailable_extra(engine_name)
    QMessageBox.warning(
        window,
        "Speech unavailable",
        "TTS engine is unavailable." + extra,
    )


def _require_page(view: WebEngineViewProtocol) -> WebPageProtocol:
    """
    Retrieve a JavaScript-capable web page from a view or fail loudly.

    This helper enforces the presence of a loaded page so callers can
    rely on JavaScript execution, raising a runtime error when no page
    is available.

    Args:
        view (WebEngineViewProtocol):
            Web view wrapper expected to provide access to the
            underlying web page via its page() method.

    Returns:
        WebPageProtocol: Web page instance that supports running
        JavaScript in the current viewing context.

    Raises:
        RuntimeError:
            If the view reports that no page is currently loaded (i.e.,
            page() returns None).
    """
    page: WebPageProtocol | None = view.page()
    if page is None:
        msg = "QWebEngineView.page() returned None"
        raise RuntimeError(msg)
    return page


def _js_string_result(result: object) -> str:
    """
    Normalize a JavaScript callback result to a string.

    This helper safely converts loosely-typed JavaScript return values
    into a plain Python string, falling back to an empty string for
    non-string results.

    Args:
        result (object):
            Value returned from a JavaScript evaluation callback, which
            may be a string or any other JSON-serializable type.

    Returns:
        str:
            Original value when it is already a string, or an empty
            string when the result is of any other type.
    """
    return result if isinstance(result, str) else ""


@dataclass
class TtsActionFactory:
    """
    Coordinate construction of all text-to-speech UI actions.

    This factory bundles the dependencies needed for TTS and chat
    features and exposes ready-to-use callables for reading text,
    controlling playback, and sending selections to chat.

    Attributes:
        deps (TtsActionDependencies):
            Immutable collection of services and helpers that power the
            TTS actions, including engines, web view access,
            persistence, and status reporting.

    # Methods:

        tts_speak_text(
            text: str
        ) -> None:
            Starts speaking the given text with the active TTS engine
            after validating that the engine is available and the text
            is non-empty, updating status to reflect success or failure.

        tts_read_document() -> None:
            Extracts TTS-ready text for the current document in the
            background, optionally looks up a saved Piper cursor index,
            and dispatches a UI-thread request to start or resume
            reading.

        tts_read_selection_or_document() -> None:
            Queries the web view for the current text selection via
            JavaScript and reads either the selection or, if empty, the
            whole document.

        pause_toggle_and_persist() -> None:
            Toggles the active engine between paused and playing states
            and, when using Piper and pausing, persists the current
            chunk index for later resumption.

        stop_and_clear() -> None:
            Stops any ongoing speech, clears stored TTS cursor state,
            asks the web page to clear TTS UI markers, and reports that
            reading has stopped.

        ask_current_selection_in_chat() -> None:
            Fetches the current selection as a chat prompt via
            JavaScript and, if non-empty, forwards it to the chat system
            for handling.

        build() -> TtsActionsDict:
            Produces the dictionary of UI-facing TTS and chat actions,
            suitable for wiring into menus, buttons, and keyboard
            shortcuts.
    """

    deps: TtsActionDependencies

    @property
    def _run_in_background(self) -> Callable[[Callable[[], None]], None]:
        """
        Resolve the background execution strategy for TTS work.

        This property chooses a concrete scheduler so document reading
        and other potentially blocking tasks can be run either
        asynchronously or inline through a uniform callable.

        Returns:
            Callable[[Callable[[], None]], None]:
                Background runner that will execute zero-argument tasks,
                using the configured run_in_background dependency when
                provided or falling back to inline execution otherwise.
        """
        return _resolve_background_runner(self.deps.run_in_background)

    @property
    def _dispatch_to_ui(self) -> Callable[[Callable[[], None]], None]:
        """
        Resolve the UI-thread dispatch strategy for TTS callbacks.

        This property chooses a concrete dispatcher so results from
        background work can safely update UI-related state or invoke Qt
        APIs.

        Returns:
            Callable[[Callable[[], None]], None]:
                Dispatcher that will execute zero-argument callbacks on
                the UI thread when a dispatch_to_ui dependency is
                provided, or inline in the current thread otherwise.
        """
        return _resolve_background_runner(self.deps.dispatch_to_ui)

    def tts_speak_text(self, text: str) -> None:
        """
        Start speaking a piece of text using the active TTS engine.

        This method validates the input text and engine availability
        before initiating playback, updating the status area to reflect
        what happens.

        Args:
            text (str):
                Raw text to be spoken by the active text-to-speech
                engine, which will be stripped of leading and trailing
                whitespace before use.
        """
        text_value: str = (text or "").strip()
        if not text_value:
            self.deps.show_status("TTS: nothing to read", 0)
            return
        engine: ActiveTtsEngine = self.deps.get_active_tts()
        if not engine.is_available():
            _warn_unavailable_tts(
                self.deps.window, engine_name=self.deps.get_tts_engine()
            )
            return
        ok: bool = engine.speak(text_value)
        self.deps.show_status(
            "TTS: reading started" if ok else "TTS: failed to start",
            0,
        )

    def _start_document_tts(self, text: str, resume_idx: int | None) -> None:
        """
        Start or resume text-to-speech reading for a document.

        This helper chooses between resuming Piper-based chunked
        playback from a saved cursor index and starting a fresh reading
        with the active TTS engine.

        Args:
            text (str):
                Full document text to be spoken, which may be split into
                chunks when using a Piper engine.
            resume_idx (int | None):
                Previously saved Piper chunk index to resume
                from, or None to ignore any existing cursor state and
                start from the beginning.
        """
        if self.deps.get_tts_engine() == "piper":
            chunks: list[str] = self.deps.split_for_tts(text)
            if resume_idx is not None and 0 < resume_idx < len(chunks):
                ok: bool = self.deps.tts_piper.speak_chunks(
                    chunks,
                    start_idx=resume_idx,
                )
                self.deps.show_status(
                    "TTS: resumed" if ok else "TTS: failed to start",
                    0,
                )
                return
            self.deps.clear_tts_cursor()
        self.tts_speak_text(text)

    def tts_read_document(self) -> None:
        """
        Read the current document aloud using text-to-speech.

        This method runs text extraction and cursor lookup in the
        background, then schedules a UI-thread callback to start or
        resume TTS playback based on the active engine.
        """

        def worker() -> None:
            """
            Read the current document aloud using text-to-speech.

            This method runs text extraction and cursor lookup in the
            background, then schedules a UI-thread callback to start or
            resume TTS playback based on the active engine.
            """
            text: str = self.deps.read_current_md_text()
            resume_idx: int | None = (
                self.deps.load_tts_cursor_for_current_doc()
                if self.deps.get_tts_engine() == "piper"
                else None
            )
            self._dispatch_to_ui(
                lambda: self._start_document_tts(text, resume_idx),
            )

        self._run_in_background(worker)

    def tts_read_selection_or_document(self) -> None:
        """
        Read either the current selection or the full document using
        TTS.

        This method prefers reading the user's current text selection
        and falls back to reading the entire document when no selection
        is available.

        """

        def on_selection_text(result: object) -> None:
            """
            Handle the JavaScript result for the current text selection.

            This helper chooses between reading the selected text aloud
            or falling back to reading the entire document when the
            selection is empty.

            Args:
                result (object):
                    Value returned from the JavaScript selection query,
                    which may be a string containing the selected text
                    or another JSON- serializable type that will be
                    normalized to an empty string.
            """
            sel: str = _js_string_result(result).strip()
            if sel:
                self.tts_speak_text(text=sel)
            else:
                self.tts_read_document()

        _require_page(self.deps.view).runJavaScript(
            build_selection_text_script(),
            on_selection_text,
        )

    def pause_toggle_and_persist(self) -> None:
        """
        Toggle TTS playback and optionally persist the Piper cursor
        index.

        This method switches the active TTS engine between paused and
        playing states and, when pausing with Piper, records the
        current chunk index for future resumption.
        """
        paused: bool = self.deps.get_active_tts().toggle_pause()
        if paused and self.deps.get_tts_engine() == "piper":
            self.deps.save_tts_cursor(self.deps.tts_piper.cursor_index())
        self.deps.show_status(
            "TTS: paused" if paused else "TTS: resumed",
            0,
        )

    def stop_and_clear(self) -> None:
        """
        Stop any ongoing TTS playback and reset related state.

        This method halts the active text-to-speech engine, clears any
        stored cursor position, and updates both the web view and
        status area to show that reading has ended.

        """
        self.deps.get_active_tts().stop()
        self.deps.clear_tts_cursor()
        _require_page(self.deps.view).runJavaScript(
            "window.mdViewerTtsClear && window.mdViewerTtsClear();",
        )
        self.deps.show_status("TTS: stopped", 0)

    def ask_current_selection_in_chat(self) -> None:
        """
        Send the current text selection to the chat system.

        This method extracts the user's current selection via JavaScript
        and, if non-empty, forwards it as a prompt to the configured
        chat backend.
        """

        def on_prompt(result: object) -> None:
            """
            Handle the JavaScript result used as a chat prompt.

            This helper normalizes the selection result into text and
            forwards it to the chat system only when a non-empty prompt
            is available.

            Args:
                result (object):
                    Raw value returned from the JavaScript selection
                    query, which may be a string containing the
                    selected text or another JSON- serializable type
                    that will be treated as empty.
            """
            text: str = _js_string_result(result).strip()
            if not text:
                return
            self.deps.send_prompt_to_chat(text)

        _require_page(self.deps.view).runJavaScript(
            build_selection_prompt_script(),
            on_prompt,
        )

    def build(self) -> TtsActionsDict:
        """
        Construct the dictionary of UI-facing TTS and chat actions.

        This method wires the factory's bound methods into a typed
        mapping so callers can register all related actions with menus,
        toolbars, or other UI components in one step.

        Returns:
            TtsActionsDict:
                Mapping from action names to callables that implement
                text-to- speech operations and chat interactions, ready
                to be connected to UI triggers.
        """
        return TtsActionsDict(
            tts_speak_text=self.tts_speak_text,
            tts_read_document=self.tts_read_document,
            tts_read_selection_or_document=self.tts_read_selection_or_document,
            pause_toggle_and_persist=self.pause_toggle_and_persist,
            stop_and_clear=self.stop_and_clear,
            ask_current_selection_in_chat=self.ask_current_selection_in_chat,
        )


def build_tts_actions(*, deps: TtsActionDependencies) -> TtsActionsDict:
    """
    Create the dictionary of high-level TTS and chat UI actions.

    This convenience function instantiates a factory with the provided
    dependencies and returns its assembled mapping of callable actions.

    Args:
        deps (TtsActionDependencies):
            Preconfigured collection of services and helpers that the
            TTS action factory requires to construct all text-to-speech
            and chat-related UI operations.

    Returns:
        TtsActionsDict:
            Mapping from stable action names to bound callables that
            implement text-to-speech control and chat prompting
            behavior, suitable for wiring into menus, toolbars, or
            shortcuts.
    """
    return TtsActionFactory(deps).build()
