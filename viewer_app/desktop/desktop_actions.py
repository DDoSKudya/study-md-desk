"""
This module defines a small framework for registering global
keyboard-driven actions in a PyQt6 main window.

It primarily wires text-to-speech and chat-related commands to shortcuts
that work across the application.

The _GlobalActionSpec dataclass holds the metadata for a single action,
including its title, shortcut string, and a no-argument handler
function.

The private _register_action function creates a QAction from a
_GlobalActionSpec, sets its shortcut, connects its triggered signal to
the handler, and adds it to the given QMainWindow.

The public install_global_actions function takes a main window and five
handler callables, builds a fixed tuple of _GlobalActionSpec instances
for reading the document, reading a selection, pausing/resuming,
stopping, and generating a chat prompt, and registers them all via
_register_action.

In the broader system, this module centralizes the definition of global
TTS and chat shortcuts so higher-level UI code can easily enable
consistent keyboard control of these features.
"""

from __future__ import annotations

from dataclasses import dataclass
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow
from typing import Callable, TypeAlias

VoidHandler: TypeAlias = Callable[[], None]


@dataclass(frozen=True)
class _GlobalActionSpec:
    """
    Registers a set of global text-to-speech and chat actions on a
    window.

    This function wires up keyboard shortcuts so the user can trigger
    common TTS and prompt-generation operations from anywhere in the
    application.

    It constructs a sequence of global action specifications describing
    the label, shortcut, and handler for each operation, then delegates
    to _internal registration to attach each as a QAction on the
    provided main window.

    Args:
        window (QMainWindow):
            The top-level window that will own the created actions and
            receive their shortcuts.
        tts_read_document (Callable[[], None]):
            Handler invoked when the user requests reading the entire
            current document aloud.
        tts_read_selection_or_document (Callable[[], None]):
            Handler invoked to read the current text selection, or the
            full document when no  selection exists.
        pause_toggle_and_persist (Callable[[], None]):
            Handler that toggles text-to-speech pause or resume while
            persisting any required state.
        stop_and_clear (Callable[[], None]):
            Handler that stops text-to-speech playback and clears
            related state or queues.
        ask_current_selection_in_chat (Callable[[], None]):
            Handler that generates a chat prompt from the current
            selection and typically copies or sends it for further use.
    """

    title: str
    shortcut: str
    handler: VoidHandler


def _register_action(*, window: QMainWindow, spec: _GlobalActionSpec) -> None:
    """
    Adds a single global QAction to a main window from a specification.

    This helper creates a QAction with the given title and shortcut and
    wires it to the provided handler on the target window.

    It constructs the action object, connects its triggered signal to
    the handler, and registers the action with the window so the
    shortcut becomes active.

    Args:
        window (QMainWindow):
            The top-level window that will own the action and receive
            the shortcut events.
        spec (_GlobalActionSpec):
            The specification describing the actions title, shortcut
            string, and handler callback to invoke when triggered.
    """
    action: QAction = QAction(spec.title, window)
    action.setShortcut(spec.shortcut)
    action.triggered.connect(spec.handler)
    window.addAction(action)


def install_global_actions(
    *,
    window: QMainWindow,
    tts_read_document: VoidHandler,
    tts_read_selection_or_document: VoidHandler,
    pause_toggle_and_persist: VoidHandler,
    stop_and_clear: VoidHandler,
    ask_current_selection_in_chat: VoidHandler,
) -> None:
    """
    Registers global text-to-speech and chat actions on a main window.

    This function installs a fixed set of keyboard shortcuts that
    trigger common reading and prompt-generation operations across the
    application.

    It builds several _GlobalActionSpec instances describing each
    actions title, shortcut, and handler and passes them to
    _register_action to attach corresponding QActions to the window.

    Args:
        window (QMainWindow): The top-level window that will own the
        created actions and receive their shortcut events.
    tts_read_document (VoidHandler):
        Handler invoked when the user requests reading the entire
        current document aloud.
    tts_read_selection_or_document (VoidHandler):
        Handler invoked to read the current text selection, or the full
        document when no selection exists.
    pause_toggle_and_persist (VoidHandler):
        Handler that toggles text-to-speech pause or resume while
        persisting any required state.
    stop_and_clear (VoidHandler):
        Handler that stops text-to-speech playback and clears related
        state or queues.
    ask_current_selection_in_chat (VoidHandler):
        Handler that generates a chat prompt from the current selection
        and typically copies or sends it for further use.
    """
    for spec in (
        _GlobalActionSpec(
            title="Read current document",
            shortcut="Ctrl+Alt+R",
            handler=tts_read_document,
        ),
        _GlobalActionSpec(
            title="Read selection (or document)",
            shortcut="Ctrl+Alt+Shift+R",
            handler=tts_read_selection_or_document,
        ),
        _GlobalActionSpec(
            title="Pause / Resume",
            shortcut="Ctrl+Alt+P",
            handler=pause_toggle_and_persist,
        ),
        _GlobalActionSpec(
            title="Stop",
            shortcut="Ctrl+Alt+S",
            handler=stop_and_clear,
        ),
        _GlobalActionSpec(
            title="Generate prompt to clipboard",
            shortcut="Ctrl+Shift+Q",
            handler=ask_current_selection_in_chat,
        ),
    ):
        _register_action(window=window, spec=spec)
