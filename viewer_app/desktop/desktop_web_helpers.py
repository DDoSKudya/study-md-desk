"""
This module provides small helper utilities for integrating the desktop
viewer with its web-based UI, focusing on selection prompts and TTS
highlight syncing.

It defines constants for maximum TTS snippet length, pause markers, and
a JavaScript template used to build chat prompts from the current
document and selection.

The build_selection_prompt_script function returns a JavaScript snippet
that runs in the content iframe to gather title, full text, and
selection and produce a prompt string.

The normalize_external_url function cleans user-entered URLs and ensures
they are fully qualified with an HTTP(S) scheme.

The resolve_online_panel_visible function inspects persisted application
state to decide whether the online side panel should be shown.

The drain_latest_tts_event function empties a queue of TTS sync events
and returns only the most recent one for use in highlight
synchronization.

The extract_tts_sync_text function pulls and sanitizes text from a TTS
event, filtering out pause markers and enforcing a maximum length.

The build_tts_sync_script function creates a JavaScript call to a global
window.mdViewerTtsSync hook with JSON-encoded text so the web view can
synchronize highlights with TTS playback.

Within the broader system, this module acts as a bridge layer between
Python-based TTS and state management and the in-page JavaScript APIs
that control prompts, panels, and text highlights.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import queue
from queue import Empty
from typing import Any, Final, TypeAlias

_TTS_SYNC_SNIPPET_MAX_LEN: Final[int] = 800
_PAUSE_MARKERS: Final[frozenset[str]] = frozenset[str](
    {"__TTS_PAUSE_PAR__", "__TTS_PAUSE_SHORT__"}
)

TtsSyncEvent: TypeAlias = dict[str, str]

_SELECTION_PROMPT_SCRIPT: Final[
    str
] = """
        (function() {
          try {
            var frame = document.getElementById('contentFrame');
            if (!frame || !frame.contentWindow || !frame.contentDocument) return '';
            var win = frame.contentWindow;
            var sel = (win.getSelection && win.getSelection().toString()) || '';
            sel = sel.trim();
            var full = (win.document.body && (win.document.body.innerText || win.document.body.textContent) || '').trim();
            if (!full && !sel) return '';
            var h = win.document.querySelector('h1, h2, h3');
            var title = h ? (h.textContent || '').trim() : (win.document.title || '');
            var ptk = (window.EXPLAIN_PROMPT_KEY || 'explain_ru');
            var rawTpl = (window.PROMPT_TEMPLATES && window.PROMPT_TEMPLATES[ptk]) || (window.PROMPT_TEMPLATES && window.PROMPT_TEMPLATES.explain_ru) || '{CONTENT}';
            var tpl = String(rawTpl || '');
            if (tpl.indexOf('{CONTENT}') === -1) tpl = '{CONTENT}';
            var sectionText = full.slice(0, 20000);
            var highlight = sel ? sel.slice(0, 4000) : '';
            var bodyText = 'Full text of the current section:\\n\\n' + sectionText;
            if (highlight) {
              bodyText += '\\n\\n---\\n\\nSelected fragment (place extra emphasis on it):\\n\\n' + highlight;
            }
            var topic = title || '';
            var prompt = tpl.split('{TITLE}').join(topic || 'Untitled').split('{CONTENT}').join(bodyText);
            return prompt;
          } catch (e) {
            return '';
          }
        })();
        """


def build_selection_prompt_script() -> str:
    """
    Return the JavaScript snippet used to build a selection-based
    prompt.

    This script inspects the embedded content frame to assemble a prompt
    string from the current page text and selection.

    Returns:
        str:
            JavaScript source code that, when executed in the viewer
            page, returns a prompt string combining the document title,
            section text, and any highlighted selection.
    """
    return _SELECTION_PROMPT_SCRIPT


def normalize_external_url(url: str) -> str:
    """
    Normalize an external URL string to a fully qualified HTTPS URL.

    This ensures loosely entered or partial URLs are converted into a
    consistent, browser-ready form.

    Args:
        url (str):
            Raw URL or hostname string that may be empty, contain
            leading or trailing whitespace, or omit a scheme prefix.

    Returns:
        str:
            Normalized URL string with whitespace removed and an
            explicit "https://" scheme added when no "http://" or
            "https://" prefix is present, or an empty string when the
            input is blank.
    """
    normalized: str = (url or "").strip()
    if not normalized:
        return ""
    if not normalized.startswith(("http://", "https://")):
        normalized = "https://" + normalized
    return normalized


def resolve_online_panel_visible(state: Mapping[str, object]) -> bool:
    """
    Determine whether the online side panel should be visible.

    This reads the persisted panel state and falls back to showing the
    panel when no explicit setting is stored.

    Args:
        state (Mapping[str, object]):
            Application state mapping that may contain a "qtPanels"
            section with an "online" flag indicating the desired
            visibility.

    Returns:
        bool:
            True if the online panel is considered visible based on the
            stored state, or True by default when the state is missing
            or malformed.
    """
    qt_panels_raw: object = state.get("qtPanels")
    if isinstance(qt_panels_raw, dict):
        online_raw: object = qt_panels_raw.get("online", True)
        return bool(online_raw)
    return True


def drain_latest_tts_event(
    tts_events: queue.Queue[TtsSyncEvent],
) -> TtsSyncEvent | None:
    """
    Drain and return the most recent text-to-speech sync event.

    This consumes any queued events and keeps only the latest one for
    use in highlight synchronization.

    Args:
        tts_events (queue.Queue[TtsSyncEvent]):
            Queue of TTS synchronization events, typically produced by
            the TTS engine callback when new chunks are spoken.

    Returns:
        TtsSyncEvent | None:
            The last available event from the queue if one exists, or
            None when the queue is empty.
    """
    latest: TtsSyncEvent | None = None
    while True:
        try:
            latest = tts_events.get_nowait()
        except Empty:
            return latest


def extract_tts_sync_text(event: object) -> str | None:
    """
    Extract a sanitized text snippet from a TTS synchronization event.

    This filters out pause markers and empty values, returning a trimmed
    string suitable for highlight synchronization.

    Args:
        event (object):
            Raw event object, either a mapping containing a "text" field
            or a direct text-like value produced by the TTS pipeline.

    Returns:
        str | None:
            Cleaned text snippet truncated to the maximum sync length,
            or None when the event carries no usable text or encodes a
            pause marker.
    """
    candidate: Any | object | None = (
        event.get("text") if isinstance(event, dict) else event
    )
    if candidate is None or candidate in _PAUSE_MARKERS:
        return None
    text: str = str(candidate).strip()
    if not text or text in _PAUSE_MARKERS:
        return None
    return text[:_TTS_SYNC_SNIPPET_MAX_LEN]


def build_tts_sync_script(text: str) -> str:
    """
    Build the JavaScript snippet that synchronizes TTS highlights.

    This script calls the viewer's TTS sync hook with a JSON-encoded
    text payload when executed in the page context.

    Args:
        text (str):
            Highlight text that should be synchronized with the viewer,
            which will be truncated to the maximum snippet length
            before being embedded in the script.

    Returns:
        str:
            JavaScript source string that invokes
            "window.mdViewerTtsSync(payload)" if the hook is defined,
            where payload is the JSON-encoded highlight text.
    """
    payload: str = (text or "")[:_TTS_SYNC_SNIPPET_MAX_LEN]
    return f"window.mdViewerTtsSync && window.mdViewerTtsSync({json.dumps(payload)});"
