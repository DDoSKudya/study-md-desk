"""
This module manages text-to-speech (TTS) state for a markdown-based
document viewer, including extracting readable text and persisting a
playback cursor per document.

It provides helper functions to normalize metadata, build a stable
document identifier, and read the current markdown file as TTS-friendly
text.

It defines type aliases for callbacks that supply the current document
filesystem path, load saved state, and save updated state, as well as a
constant for a self-check heading line that may be stripped from TTS
text.

The _meta_str helper reads and normalizes metadata values from a
document mapping, while _tts_cursor_dict extracts and sanitizes the
persisted ttsCursor structure from a generic state mapping.

The _cursor_idx_positive function validates and normalizes a stored
cursor index into a positive integer or None.

The read_current_md_text function loads the active markdown file from
disk, runs it through a markdown-to-text extractor, and if necessary
retries after removing a fixed "Self-check questions" heading.

The current_doc_id function combines root and path metadata into a
stable <root>::<path> identifier used to associate TTS state with a
specific document.

The load_tts_cursor_for_current_doc function loads persisted state,
checks that any stored ttsCursor belongs to the current document ID,
and returns a validated cursor index if available.

The save_tts_cursor function writes a new ttsCursor entry containing the
document ID and cursor index into the persisted state via the provided
saver callback.

The clear_tts_cursor function resets the stored ttsCursor mapping,
effectively forgetting any previously saved TTS cursor so playback
starts from the beginning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Final, Mapping, TypeAlias

_SELF_CHECK_HEADING_LINE: Final[str] = "#### Self-check questions"

GetCurrentDocFsPathFn: TypeAlias = Callable[
    [],
    tuple[Path | None, Mapping[str, object]],
]
LoadStateJsonFn: TypeAlias = Callable[[], Mapping[str, object]]
SaveStateJsonFn: TypeAlias = Callable[[dict[str, object]], None]


def _meta_str(doc: Mapping[str, object], key: str) -> str:
    """
    Extract a normalized string value from a document-like mapping.

    This helper reads a value by key, converts it to text, and trims
    leading and trailing whitespace so callers receive a clean string.

    Args:
        doc (Mapping[str, object]):
            Mapping that holds metadata values, typically representing
            the current document's attributes.
        key (str):
            Key whose associated value should be fetched and normalized
            into a string.

    Returns:
        str:
            Stripped string representation of the value stored under the
            given key, or an empty string if the key is missing or the
            value is falsy.
    """
    value: object | None = doc.get(key)
    return str(value or "").strip()


def _tts_cursor_dict(state: Mapping[str, object]) -> dict[str, object]:
    """
    Normalize the stored text-to-speech cursor state mapping.

    This helper extracts the ttsCursor entry from a persisted state
    mapping and returns it as a plain dictionary with string keys.

    Args:
        state (Mapping[str, object]):
            State mapping that may contain a "ttsCursor" entry holding
            the last known document identifier and cursor index.

    Returns:
        dict[str, object]:
            Dictionary representation of the ttsCursor mapping when the
            stored value is a dict, with all keys coerced to strings,
            or an empty dict if no valid cursor state is present.
    """
    raw: object = state.get("ttsCursor")
    return {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}


def _cursor_idx_positive(raw_idx: object) -> int | None:
    """
    Convert a raw text-to-speech cursor index into a positive integer.

    This helper normalizes loosely-typed index values and rejects
    missing, non-numeric, or non-positive inputs so callers receive
    only valid 1-based indices.

    Args:
        raw_idx (object):
            Raw cursor index value that may be an int, float, string, or
            other type as loaded from persisted state or external
            sources.

    Returns:
        int | None:
            Positive integer index when the value can be parsed and is
            greater than zero, or None if the input is empty, invalid,
            or non-positive.
    """
    if raw_idx in (None, ""):
        return None
    idx: int
    if isinstance(raw_idx, int):
        idx = raw_idx
    elif isinstance(raw_idx, str):
        stripped = raw_idx.strip()
        if not stripped:
            return None
        try:
            idx = int(stripped)
        except ValueError:
            return None
    elif isinstance(raw_idx, float):
        idx = int(raw_idx)
    else:
        return None
    return idx if idx > 0 else None


def read_current_md_text(
    get_current_doc_fs_path: GetCurrentDocFsPathFn,
    extract_tts_text_from_markdown: Callable[[str], str],
) -> str:
    """
    Read and extract text for text-to-speech from the current markdown
    file.

    This function loads the active markdown document from disk and
    derives a TTS-friendly text representation, optionally stripping a
    fixed self-check heading when needed.

    Args:
        get_current_doc_fs_path (GetCurrentDocFsPathFn):
            Callable that returns the filesystem path of the current
            document along with its metadata mapping; if the path is
            None, no text is available.
        extract_tts_text_from_markdown (Callable[[str], str]):
            Callable that converts raw markdown into plain text suitable
            for text-to-speech playback.

    Returns:
        str:
            Extracted TTS text for the current markdown document, or an
            empty string when no current file exists or the extraction
            yields no content.
    """
    fs_path, _current_doc = get_current_doc_fs_path()
    if fs_path is None:
        return ""
    raw: str = fs_path.read_text(encoding="utf-8", errors="replace")
    text: str = extract_tts_text_from_markdown(raw)
    if not text.strip():
        text = extract_tts_text_from_markdown(
            raw.replace(_SELF_CHECK_HEADING_LINE, "")
        )
    return text


def current_doc_id(get_current_doc_fs_path: GetCurrentDocFsPathFn) -> str:
    """
    Build a stable identifier string for the current document.

    This helper derives a unique ID for the active markdown file by
    combining its root and path metadata into a single namespaced
    value.

    Args:
        get_current_doc_fs_path (GetCurrentDocFsPathFn):
            Callable that returns the filesystem path of the current
            document along with its metadata mapping, from which the
            root and path fields are read.

    Returns:
        str:
            Document identifier in the form "<root>::<path>", where both
            components are normalized metadata strings suitable for
            comparing or storing text-to-speech cursor state.
    """
    _fs_path, current_doc = get_current_doc_fs_path()
    path: str = _meta_str(doc=current_doc, key="path")
    root: str = _meta_str(doc=current_doc, key="root")
    return f"{root}::{path}"


def load_tts_cursor_for_current_doc(
    load_state_json: LoadStateJsonFn,
    current_doc_id_value: str,
) -> int | None:
    """
    Load the saved text-to-speech cursor for the current document.

    This function retrieves persisted TTS cursor state and returns the
    cursor index only when it matches the given document identifier.

    Args:
        load_state_json (LoadStateJsonFn):
            Callable that loads the global persisted state mapping from
            storage, expected to include any previously saved TTS
            cursor information.
        current_doc_id_value (str):
            Identifier of the currently active document, used to ensure
            that any loaded cursor state belongs to this document before
            it is returned.

    Returns:
        int | None:
            Positive cursor index associated with the current document
            when valid saved state exists, or None if no matching
            cursor is stored or the saved index is invalid.
    """
    state: Mapping[str, object] = load_state_json()
    cursor: dict[str, object] = _tts_cursor_dict(state)
    if not cursor:
        return None
    if (str(cursor.get("docId") or "")) != current_doc_id_value:
        return None
    return _cursor_idx_positive(raw_idx=cursor.get("idx"))


def save_tts_cursor(
    save_state_json: SaveStateJsonFn,
    current_doc_id_value: str,
    idx: int,
) -> None:
    """
    Persist the current document's text-to-speech cursor position.

    This helper stores the association between a document identifier and
    a cursor index so that TTS playback can later resume from the same
    point.

    Args:
        save_state_json (SaveStateJsonFn):
            Callable that writes the global persisted state mapping to
            storage, replacing or updating the stored TTS cursor entry.
        current_doc_id_value (str):
            Identifier of the document whose cursor position is being
            saved, typically produced by current_doc_id.
        idx (int):
            Positive cursor index that should be remembered for the
            given document so playback can continue from this position.
    """
    save_state_json({"ttsCursor": {"docId": current_doc_id_value, "idx": idx}})


def clear_tts_cursor(save_state_json: SaveStateJsonFn) -> None:
    """
    Clear any persisted text-to-speech cursor state.

    This helper removes the stored association between documents and
    cursor positions so future TTS sessions start without a remembered
    index.

    Args:
        save_state_json (SaveStateJsonFn):
            Callable that writes the global persisted state mapping to
            storage, used here to overwrite the existing ttsCursor
            entry with an empty mapping.
    """
    save_state_json({"ttsCursor": {}})
