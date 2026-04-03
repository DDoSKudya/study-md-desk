"""
This module manages persistent application state by reading and writing
a JSON state file on disk.

It provides a thread-safe way to load, save, and update state so the
application can restore its last known configuration.

It defines a DEFAULT_STATE dictionary that describes the baseline
structure and default values for the state.

It defines a StateStore class that encapsulates all logic around state
persistence and synchronization.

StateStore.load reads the JSON state file if it exists, falls back to
DEFAULT_STATE on errors, and merges persisted values over the defaults.

StateStore.save serializes a provided state dictionary to JSON and
writes it to the state file, ensuring required directories exist.

StateStore.update loads the current state, applies a partial patch,
persists the result, and returns the updated state snapshot.

Internally, _write_atomic writes JSON to a temporary file and atomically
renames it to the target path to avoid corruption on crashes.

The class uses a reentrant lock to guard all read and write operations,
ensuring thread-safe access to the state file.
"""

import json
import os
import tempfile
import threading
from copy import deepcopy

from _thread import RLock
from pathlib import Path
from typing import Mapping, TypeAlias

from viewer_app.runtime.paths import AppPaths

StateDict: TypeAlias = dict[str, object]

DEFAULT_STATE: StateDict = {
    "currentDoc": "",
    "activeProjectRoot": "",
    "projects": {"recent": [], "pinned": []},
    "readerPrefs": {},
    "ttsCursor": {},
    "qtPanels": {},
    "codewars": {},
}


class StateStore:
    """
    A class that encapsulates logic for handling application state
    persistence.

    It manages loading, saving, and updating state data on disk in a
    thread-safe and crash-resilient way.

    Methods:
        load() -> StateDict:
            Loads persisted state from storage and merges it with
            default values to produce a complete application state
            snapshot.
        save(
            state: StateDict
        ) -> None:
            Persists the provided application state dictionary to
            durable storage for future retrieval.
        update(
            patch: Mapping[str, object]
        ) -> StateDict:
            Applies a partial update to the current state, saves the
            result, and returns the updated state structure.
    """

    def __init__(self, paths: AppPaths) -> None:
        self._paths: AppPaths = paths
        self._lock: RLock = threading.RLock()

    def load(self) -> StateDict:
        """
        Load the persisted application state from disk.

        This method returns a merged state dictionary that combines the
        default state with any values found in the state file, falling
        back to defaults if the file is missing or invalid.

        Returns:
            dict[str, object]:
                A dictionary representing the current application state,
                with defaults applied where persisted data is missing
                or unusable.
        """
        with self._lock:
            path: Path = self._paths.state_path
            if not path.exists():
                return deepcopy(DEFAULT_STATE)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return deepcopy(DEFAULT_STATE)
            if not isinstance(data, dict):
                return deepcopy(DEFAULT_STATE)
            merged: StateDict = deepcopy(DEFAULT_STATE)
            merged.update(data)
            return merged

    def save(self, state: StateDict) -> None:
        """
        Persist the given application state to disk.

        This method serializes the state dictionary to JSON and writes
        it to the configured state file, creating any required runtime
        directories first.

        Args:
            state (dict[str, object]):
                A dictionary representing the application state to be
                saved, which will be encoded as JSON and written to the
                state file.
        """
        with self._lock:
            self._paths.ensure_runtime_dirs()
            payload: str = json.dumps(state, ensure_ascii=False, indent=2)
            self._write_atomic(payload)

    def update(self, patch: Mapping[str, object]) -> StateDict:
        """
        Update the persisted application state with a partial patch.

        This method merges the given patch into the current state, saves
        the result to disk, and returns the updated state snapshot.

        Args:
            patch (dict[str, object]):
                A dictionary containing keys and values to merge into
                the current application state; keys not present in the
                patch remain unchanged.

        Returns:
            dict[str, object]:
                The full application state after applying the patch and
                persisting it to disk.
        """
        with self._lock:
            state: StateDict = self.load()
            state.update(patch or {})
            self.save(state)
            return state

    def _write_atomic(self, payload: str) -> None:
        """
        Write JSON payload to the state file using an atomic rename.

        This method ensures that state writes are crash-safe by writing
        to a temporary file and then atomically replacing the target
        file.

        Args:
            payload (str):
                The JSON-encoded state content to persist to the state
                file.
        """
        state_path: Path = self._paths.state_path
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            dir=str(state_path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temp_path, state_path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass
