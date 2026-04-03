"""
This module implements a PyQt-based bridge between a web-based viewer UI
and a desktop application for chat and text-to-speech features.

It coordinates theme handling, clipboard operations, document
navigation, and TTS controls between JavaScript running in a web view
and Python logic.

The module defines type aliases for callback signatures and a
_CHAT_THEME_STORAGE_HOSTS constant listing chat sites whose local
storage themes can be modified.

It provides helper functions to normalize theme names, parse hostnames
from URLs, decide whether a host allows theme storage, generate
JavaScript to rewrite localStorage theme keys, and safely run JavaScript
on a QWebEngineView.

The BridgeActions dataclass collects callbacks for sending chat prompts,
controlling TTS playback, querying and setting TTS speed and silence,
and applying Qt themes, allowing the bridge to remain decoupled from
specific backends.

The ChatBridge QObject exposes a set of @pyqtSlot methods that
JavaScript can call to send chat prompts, copy text to the clipboard,
control TTS (speak text, change voice, pause, stop, adjust speed and
silence), navigate or search the markdown document, and notify the host
when document loading starts or stops.

ChatBridge.setChatTheme normalizes a requested theme, applies it to the
Qt shell and optional chrome callback, and, for allowed chat hosts,
injects JavaScript into the chat web view to synchronize the chat sites
theme-related local-storage keys with the chosen mode.

In the broader system, this module forms the glue layer that lets an
embedded web UI drive native desktop behaviors and keeps visual themes
consistent between the viewer shell and supported external chat sites.
"""

# pyright: reportUntypedFunctionDecorator=false

from __future__ import annotations

import json
import urllib.parse

from dataclasses import dataclass
from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtGui import QClipboard
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication
from typing import Callable, Final, TypeAlias

RunViewJsFn: TypeAlias = Callable[[str], None]
GetChatViewFn: TypeAlias = Callable[[], QWebEngineView | None]
CopyToClipboardFn: TypeAlias = Callable[[str], None]
DocLoadingCallback: TypeAlias = Callable[[bool], None]
ShellChromeThemeCallback: TypeAlias = Callable[[str], None]

_CHAT_THEME_STORAGE_HOSTS: Final[frozenset[str]] = frozenset[str](
    ("qwen.ai", "perplexity.ai", "deepseek.com")
)


@dataclass(frozen=True)
class BridgeActions:
    """
    Collects callbacks that implement chat and text-to-speech actions.

    This dataclass groups the callable hooks needed by the chat bridge
    to delegate user-initiated operations into the host application.

    Each field represents a concrete operation, such as sending a
    prompt, controlling text-to-speech playback, or updating the UI
    theme. The bridge uses these callables to keep transport and UI
    logic decoupled from the JavaScript-facing interface.

    Attributes:
        send_prompt_to_chat (Callable[[str], None]):
            Sends a plain-text prompt string to the active chat backend
            for processing or display.
        tts_speak_text (Callable[[str], None]):
            Starts text-to-speech playback for the provided text
            fragment.
        tts_set_piper_voice (Callable[[str], None]):
            Selects the text-to-speech voice implementation by its
            identifier.
        tts_speak_current_doc (Callable[[], None]):
            Initiates text-to-speech playback for the current document
            content.
        tts_toggle_pause (Callable[[], None]):
            Toggles between paused and playing states for the active
            text-to-speech session.
        tts_stop (Callable[[], None]):
            Stops any ongoing text-to-speech playback and resets related
            state.
        tts_get_speed (Callable[[], str]):
            Retrieves the current text-to-speech playback speed as a
            displayable string.
        tts_adjust_speed (Callable[[float], str]):
            Adjusts the playback speed by a relative delta and returns
            the new speed as a string.
        tts_set_speed (Callable[[float], None]):
            Sets the text-to-speech playback speed to an absolute value.
        tts_get_sentence_silence (Callable[[], str]):
            Retrieves the configured sentence-break silence duration as
            a string.
        tts_set_sentence_silence (Callable[[float], None]):
            Updates the sentence-break silence duration to the given
            absolute value.
        apply_qt_theme (Callable[[str], None]):
            Applies a named visual theme to the Qt application shell or
            viewer components.
    """

    send_prompt_to_chat: Callable[[str], None]
    tts_speak_text: Callable[[str], None]
    tts_set_piper_voice: Callable[[str], None]
    tts_speak_current_doc: Callable[[], None]
    tts_toggle_pause: Callable[[], None]
    tts_stop: Callable[[], None]
    tts_get_speed: Callable[[], str]
    tts_adjust_speed: Callable[[float], str]
    tts_set_speed: Callable[[float], None]
    tts_get_sentence_silence: Callable[[], str]
    tts_set_sentence_silence: Callable[[float], None]
    apply_qt_theme: Callable[[str], None]


def _viewer_and_chat_theme_modes(raw: str) -> tuple[str, str]:
    """
    Normalizes a raw theme string into viewer and chat theme modes.

    This helper derives a Qt viewer theme and a simplified chat-storage
    theme from an arbitrary input value.

    The mapping constrains the viewer theme to "dark", "sepia", or
    "light" and then collapses that choice into a "dark" or "light"
    mode for chat local-storage usage.

    Args:
        raw (str):
            The requested theme name, which may be any case or falsy; it
            is lowercased and interpreted as "dark", "sepia", or
            "light" with non-matching values defaulting to "light".

    Returns:
        tuple[str, str]:
            A pair consisting of the normalized Qt viewer theme (one of
            "dark", "sepia", or "light") and the corresponding chat
            storage theme (either "dark" or "light").
    """
    desired: str = (raw or "").lower()
    if desired == "dark":
        qt_mode: str = "dark"
    elif desired == "sepia":
        qt_mode = "sepia"
    else:
        qt_mode = "light"
    chat_storage_mode: str = "dark" if qt_mode == "dark" else "light"
    return qt_mode, chat_storage_mode


def _parse_url_hostname(url: str) -> str | None:
    """
    Extracts the hostname component from a URL string.

    This helper safely parses a URL and returns the lowercase host name
    portion when available.

    If the URL cannot be parsed, it returns None to signal failure, and
    if no hostname is present it returns an empty string so callers can
    distinguish an empty host from a parsing error.

    Args:
        url (str):
            The URL string to inspect for a hostname component.

    Returns:
        str | None:
            The extracted hostname, which may be an empty string when
            the URL parses but does not contain a host, or None if
            parsing raises a ValueError.
    """
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return None


def _host_allows_chat_theme_storage(host_lower: str) -> bool:
    """
    Determines whether a host is allowed to persist chat theme settings.

    This helper checks a lowercased hostname against a predefined set of
    domains that support theme storage.

    It performs a substring match so that both bare domains and their
    subdomains are treated as eligible for local-storage theme updates.

    Args:
        host_lower (str):
            The hostname to test, expected to already be lowercased for
            case-insensitive matching.

    Returns:
        bool:
            True if the host string contains any of the configured chat
            theme storage domains, otherwise False.
    """
    return any(domain in host_lower for domain in _CHAT_THEME_STORAGE_HOSTS)


def _chat_local_storage_theme_js(chat_storage_mode: str) -> str:
    """
    Builds JavaScript to update chat site theme keys in local storage.

    This helper returns a script that, when executed in a browser
    context, forces all theme-related local-storage entries to a single
    value.

    The generated script walks every key in window.localStorage and, for
    keys whose names match the case-insensitive pattern "theme",
    overwrites their value with the provided mode. It is wrapped in
    try/catch blocks to avoid breaking the host page if local storage is
    unavailable or throws.

    Args:
        chat_storage_mode (str):
            The normalized theme mode to persist into all matching
            local-storage theme keys, typically "dark" or "light".

    Returns:
        str:
            A JavaScript snippet that can be injected into a web view to
            synchronize chat site theme-related local-storage entries
            with the requested mode.
    """
    value_json: str = json.dumps(chat_storage_mode)
    return f"""
        (function() {{
          try {{
            var value = {value_json};
            try {{
              var ls = window.localStorage;
              if (ls) {{
                Object.keys(ls).forEach(function(k) {{
                  if (/theme/i.test(k)) ls.setItem(k, value);
                }});
              }}
            }} catch (e) {{}}
          }} catch (e) {{}}
        }})();
        """


def _run_js_on_view(view: QWebEngineView, script: str) -> None:
    """
    Runs a JavaScript snippet in the context of a web view page.

    This helper safely acquires the underlying page and executes the
    given script if a page is present.

    It first checks whether the view currently has an associated
    QWebEnginePage and exits early when it does not, avoiding errors
    from calling into a missing page.

    Args:
        view (QWebEngineView):
            The web view whose active page should evaluate the provided
            JavaScript.
        script (str):
            The JavaScript source code to run within the page context.
    """
    page: QWebEnginePage | None = view.page()
    if page is None:
        return
    page.runJavaScript(script)


class ChatBridge(QObject):
    def __init__(
        self,
        *,
        run_view_js: RunViewJsFn,
        get_chat_view: GetChatViewFn,
        actions: BridgeActions,
        copy_to_clipboard: CopyToClipboardFn | None = None,
        on_doc_content_loading: DocLoadingCallback | None = None,
        on_shell_chrome_theme: ShellChromeThemeCallback | None = None,
    ) -> None:
        super().__init__()
        self._run_view_js: RunViewJsFn = run_view_js
        self._get_chat_view: GetChatViewFn = get_chat_view
        self._actions: BridgeActions = actions
        self._copy_to_clipboard: CopyToClipboardFn = (
            copy_to_clipboard
            if copy_to_clipboard is not None
            else ChatBridge._default_copy_to_clipboard
        )
        self._on_doc_content_loading: DocLoadingCallback | None = (
            on_doc_content_loading
        )
        self._on_shell_chrome_theme: ShellChromeThemeCallback | None = (
            on_shell_chrome_theme
        )

    @staticmethod
    def _default_copy_to_clipboard(text: str) -> None:
        """
        Copies text into the applications global clipboard.

        This helper uses the current Qt application instance to obtain a
        clipboard and write the provided text into it.

        If a clipboard object is available, the text is stored so it can
        be pasted into other widgets or external applications.

        Args:
            text (str):
                The string content to place on the clipboard, replacing
                any existing clipboard text.
        """
        clipboard: QClipboard | None = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    @pyqtSlot(str)
    def askInChat(self, prompt: str) -> None:  # noqa: N802
        """
        Sends a user-supplied prompt from the viewer into the chat
        backend.

        This slot is invoked from JavaScript to initiate a chat query
        using the current bridge actions.

        Empty or whitespace-only prompts are ignored so that only
        meaningful input is forwarded. Non-empty prompts are trimmed
        and dispatched through the configured send_prompt_to_chat
        callback.

        Args:
            prompt (str):
                The raw prompt text provided by the caller, typically
                originating from the web view UI.
        """
        if prompt:
            self._actions.send_prompt_to_chat(prompt.strip())

    @pyqtSlot(str)
    def copyToClipboard(self, text: str) -> None:  # noqa: N802
        """
        Copies text from the web view into the hosts clipboard handler.

        This slot bridges JavaScript-driven copy requests to the
        configured Python-side clipboard implementation.

        The method normalizes the incoming value to a string, treating
        falsy values as empty text, and forwards it to the injected
        copy callback so the host application can decide how and where
        to store it.

        Args:
            text (str):
                The text value supplied by the caller, typically
                originating from the web view UI, which will be
                converted to a string and passed to the clipboard
                handler.
        """
        self._copy_to_clipboard(str(text or ""))

    @pyqtSlot(str)
    def ttsSpeakText(self, text: str) -> None:  # noqa: N802
        """
        Requests text-to-speech playback for an arbitrary text fragment.

        This slot forwards viewer-supplied text to the configured TTS
        engine so it can be spoken aloud.

        The text is normalized by treating falsy values as empty strings
        and trimming surrounding whitespace before dispatch. The
        cleaned value is then passed through to the tts_speak_text
        action provided by the host application.

        Args:
            text (str):
                The raw text to be spoken, typically originating from
                the web view UI, which will be sanitized and sent to
                the TTS backend.
        """
        self._actions.tts_speak_text((text or "").strip())

    @pyqtSlot(str)
    def ttsSetPiperVoice(self, voice_id: str) -> None:  # noqa: N802
        """
        Selects the active text-to-speech voice by identifier.

        This slot forwards a viewer-provided voice name or ID to the
        underlying TTS system so subsequent speech uses the chosen
        voice.

        Args:
            voice_id (str):
                The identifier or name of the voice that should be
                activated for future text-to-speech playback.
        """
        self._actions.tts_set_piper_voice(voice_id)

    @pyqtSlot()
    def ttsSpeakCurrentDoc(self) -> None:  # noqa: N802
        """
        Starts text-to-speech playback for the current document.

        This slot instructs the configured TTS backend to speak whatever
        content is considered the active document.

        The method does not take any text input from the caller;
        instead, it delegates to the host applications
        tts_speak_current_doc action, which is responsible for locating
        and streaming the appropriate content.
        """
        self._actions.tts_speak_current_doc()

    @pyqtSlot()
    def ttsTogglePause(self) -> None:  # noqa: N802
        """
        Toggles the pause state of the active text-to-speech session.

        This slot switches between playing and paused modes for the
        current TTS playback, if any is active.
        """
        self._actions.tts_toggle_pause()

    @pyqtSlot()
    def ttsStop(self) -> None:  # noqa: N802
        self._actions.tts_stop()

    @pyqtSlot(str)
    def notesGoToAnchor(self, anchor: str) -> None:  # noqa: N802
        """
        Scroll the markdown viewer to the given heading anchor id.

        When non-empty, runs injected JavaScript to call
        window.mdViewerScrollToAnchor.
        """
        value: str = (anchor or "").strip()
        if value:
            self._run_view_js(
                f"window.mdViewerScrollToAnchor && window.mdViewerScrollToAnchor({json.dumps(value)});"
            )

    @pyqtSlot(str)
    def notesFindInDoc(self, text: str) -> None:  # noqa: N802
        """
        Highlights matches for a search term within the current
        document.

        This slot relays a viewer-supplied query string into the
        markdown viewer so it can perform an in-document search.

        The text is normalized by treating falsy values as empty strings
        and trimming surrounding whitespace. Only non-empty queries
        trigger a call into the pages mdViewerFindInDoc helper via
        injected JavaScript.

        Args:
            text (str):
                The raw search string provided by the caller, typically
                entered in the web view UI, which will be sanitized
                before use.
        """
        value: str = (text or "").strip()
        if value:
            self._run_view_js(
                f"window.mdViewerFindInDoc && window.mdViewerFindInDoc({json.dumps(value)});"
            )

    @pyqtSlot(str, str)
    def notesOpenDoc(self, rel_path: str, root: str) -> None:  # noqa: N802
        """
        Opens another document in the markdown viewer by relative path.

        This slot asks the web viewer to resolve and display a new
        document based on a path and an optional root hint.

        Both the relative path and root values are trimmed of
        surrounding whitespace, and the operation is only attempted
        when a non-empty path is provided. When valid, a JavaScript call
        to mdViewerOpenByPath is injected so the page can handle the
        navigation.

        Args:
            rel_path (str):
                The relative path to the target document, as supplied by
                the caller; leading and trailing whitespace are
                ignored.
            root (str):
                A root or base location hint used by the viewer to
                resolve the relative path, which may be an empty string
                if not needed.
        """
        path: str = (rel_path or "").strip()
        root_value: str = (root or "").strip()
        if path:
            self._run_view_js(
                f"window.mdViewerOpenByPath && window.mdViewerOpenByPath({json.dumps(path)}, {json.dumps(root_value)});"
            )

    @pyqtSlot(result="QString")
    def ttsGetSpeed(self) -> str:  # noqa: N802
        """
        Retrieves the current text-to-speech playback speed.

        This slot exposes the active TTS speed as a string value
        suitable for display in the web view UI.

        Returns:
            str:
                The current playback speed, formatted as a
                human-readable string such as a multiplier or
                percentage, as provided by the TTS backend.
        """
        return self._actions.tts_get_speed()

    @pyqtSlot(float, result="QString")
    def ttsAdjustSpeed(self, delta: float) -> str:  # noqa: N802
        """
        Adjusts the current text-to-speech playback speed by a delta.

        This slot lets the caller nudge the TTS speed faster or slower
        and returns the resulting value as a display-ready string.

        Args:
            delta (float):
                The relative change to apply to the current playback
                speed, where positive values increase speed and
                negative values decrease it.

        Returns:
            str:
                The updated playback speed, formatted as a
                human-readable string such as a multiplier or
                percentage, as reported by the TTS backend after
                adjustment.
        """
        return self._actions.tts_adjust_speed(delta)

    @pyqtSlot(float)
    def ttsSetSpeed(self, value: float) -> None:  # noqa: N802
        """
        Sets the text-to-speech playback speed to an absolute value.

        This slot lets the caller define an explicit TTS speed rather
        than adjusting it relative to the current value.

        Args:
            value (float):
                The absolute playback speed to apply, where the accepted
                range and interpretation are determined by the TTS
                backend.
        """
        self._actions.tts_set_speed(value)

    @pyqtSlot(result="QString")
    def ttsGetSentenceSilence(self) -> str:  # noqa: N802
        """
        Retrieves the configured sentence-break silence duration.

        This slot exposes the pause length between sentences used by the
        TTS engine as a string for display in the web view UI.

        Returns:
            str:
                The current sentence silence duration, formatted as a
                human-readable string such as a number of seconds or
                milliseconds, as provided by the TTS backend.
        """
        return self._actions.tts_get_sentence_silence()

    @pyqtSlot(float)
    def ttsSetSentenceSilence(self, value: float) -> None:  # noqa: N802
        """
        Sets the sentence-break silence duration for text-to-speech
        playback.

        This slot lets the caller define how long pauses between
        sentences should last in the TTS engine.

        Args:
            value (float):
                The absolute silence duration to apply between
                sentences, whose units and valid range are determined
                by the TTS backend.
        """
        self._actions.tts_set_sentence_silence(value)

    @pyqtSlot(bool)
    def docContentLoading(self, active: bool) -> None:  # noqa: N802
        """
        Notifies the host that document content loading has started or
        finished.

        This slot relays loading state changes from the web view to an
        optional callback so the application can react, for example by
        showing or hiding a loading indicator.

        Args:
            active (bool):
                Whether document content is currently loading (True) or
                has finished loading (False).
        """
        hook: DocLoadingCallback | None = self._on_doc_content_loading
        if hook is not None:
            hook(active)

    @pyqtSlot(str)
    def setChatTheme(self, theme: str) -> None:  # noqa: N802
        """
        Applies a theme to both the Qt viewer and eligible chat sites.

        This slot normalizes a requested theme name and propagates the
        chosen mode to the host shell and, when allowed, to chat-site
        local storage.

        The method first computes viewer and chat theme modes, then
        invokes the Qt theme callback and optional chrome hook. For
        supported chat hosts, it injects JavaScript into the chat web
        view that updates theme-related local-storage keys to match the
        desired mode.

        Args:
            theme (str):
                The raw theme name supplied by the caller, which may be
                any case or partially matching common values like
                "dark" or "sepia"; it is normalized before use.
        """
        qt_desired, chat_desired = _viewer_and_chat_theme_modes(raw=theme)
        self._actions.apply_qt_theme(qt_desired)
        chrome_hook: ShellChromeThemeCallback | None = (
            self._on_shell_chrome_theme
        )
        if chrome_hook is not None:
            chrome_hook(qt_desired)
        chat_view: QWebEngineView | None = self._get_chat_view()
        if chat_view is None:
            return
        url_str: str = chat_view.url().toString()
        host: str | None = _parse_url_hostname(url=url_str)
        if host is None:
            return
        host_lower: str = host.lower()
        if not _host_allows_chat_theme_storage(host_lower):
            return
        _run_js_on_view(
            view=chat_view,
            script=_chat_local_storage_theme_js(
                chat_storage_mode=chat_desired
            ),
        )
