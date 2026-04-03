"""
This module defines and discovers filesystem locations used by the
viewer applications runtime and bundled resources.

It centralizes how the app finds its root directory, runtime home,
configuration files, state files, prompts, TTS data, and various
resource subdirectories.

It declares constants for environment variables, directory names, and
key file names that describe the runtime layout.

The _default_runtime_home function chooses a runtime home directory
based on an environment variable override or the discovered application
root.

The _is_repository_root and _discover_app_root helpers walk up from a
given anchor path to find the repository root by looking for the viewer
application directory.

The _prefer_existing_file helper resolves a file by preferring a
user-editable copy under the runtime home and falling back to a bundled
version under the resources root.

The AppPaths frozen dataclass stores paths for app_root, runtime_home,
resources_root, and cache/profile directories, and its discover
classmethod constructs an instance using the helper functions.

AppPaths exposes properties that compute paths for settings, state,
prompts, TTS rules, plans, binaries, and TTS models, and an
ensure_runtime_dirs method that creates required runtime directories so
the rest of the system can persist data safely.
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from pathlib import Path

RUNTIME_HOME_ENV_VAR: str = "MD_VIEWER_HOME"
VIEWER_APP_DIRECTORY_NAME: str = "viewer_app"
WEB_PROFILE_CACHE_DIRECTORY: str = "web_profile_cache"
WEB_PROFILE_STORAGE_DIRECTORY: str = "web_profile_storage"
SETTINGS_FILE_NAME: str = "study_md_desk.ini"
STATE_FILE_NAME: str = "study_md_desk_state.json"
PROMPTS_FILE_NAME: str = "prompts.json"
TTS_RULES_FILE_NAME: str = "tts_rules.json"
PLANS_DIRECTORY_NAME: str = "plans"
BIN_DIRECTORY_NAME: str = "bin"
TTS_MODELS_DIRECTORY_NAME: str = "tts_models"


def _default_runtime_home(app_root: Path) -> Path:
    """
    Determine the default runtime home directory for the application.

    This helper chooses between an environment-variable override and the
    supplied application root path.

    Args:
        app_root (Path):
            Resolved application root path to use as the runtime home
            when no explicit override is provided via the environment.

    Returns:
        Path:
            The runtime home directory path, either derived from the
            RUNTIME_HOME_ENV_VAR environment variable or falling back
            to the given application root.
    """
    override: str = (os.environ.get(RUNTIME_HOME_ENV_VAR) or "").strip()
    return Path(override).expanduser().resolve() if override else app_root


def _is_repository_root(candidate: Path) -> bool:
    """
    Check whether a filesystem path looks like the repository root.

    This helper inspects the given directory for a child folder that
    matches the expected viewer application directory name.

    Args:
        candidate (Path):
            Directory path to test as a potential repository root, which
            should contain a subdirectory named after the viewer
            application.

    Returns:
        bool:
            True if the candidate directory contains the expected viewer
            application subdirectory and thus appears to be the
            repository root, otherwise False.
    """
    viewer_app: Path = candidate / VIEWER_APP_DIRECTORY_NAME
    return viewer_app.is_dir()


def _discover_app_root(anchor: Path) -> Path:
    """
    Discover the root directory of the viewer application repository.

    This helper walks up from an anchor path to find either the viewer
    app package directory or a directory that looks like the repository
    root.

    Args:
        anchor (Path):
            Filesystem path used as the starting point for discovery,
            such as the current file location or a path within the
            repository.

    Returns:
        Path:
            The resolved application root directory, derived by scanning
            the anchor directory and its parents for the viewer app
            folder or a suitable repository root. If no match is found,
            the parent of the starting directory is returned as a
            fallback.
    """
    resolved: Path = anchor.resolve()
    start: Path = resolved if resolved.is_dir() else resolved.parent
    for candidate in (start, *start.parents):
        if candidate.name == VIEWER_APP_DIRECTORY_NAME:
            return candidate.parent
        if _is_repository_root(candidate):
            return candidate
    return start.parent


def _prefer_existing_file(
    runtime_home: Path, resources_root: Path, file_name: str
) -> Path:
    """
    Choose between runtime and bundled locations when resolving a file.

    This helper prefers a user-modifiable file in the runtime home but
    falls back to a bundled resource when the runtime copy is absent.

    Args:
        runtime_home (Path):
            Base directory where user-specific or runtime-generated
            files are stored and checked first for the requested file
            name.
        resources_root (Path):
            Base directory containing bundled application resources used
            as a fallback when no runtime file exists.
        file_name (str):
            Name of the file to resolve, relative to both the runtime
            home and resources root directories. resolve, relative to
            both the runtime home and resources root directories.

    Returns:
        Path:
            The path to the preferred file location, pointing to the
            runtime-home copy when it exists, or otherwise to the
            bundled resource under the resources root.
    """
    runtime_candidate: Path = runtime_home / file_name
    bundled_candidate: Path = resources_root / file_name
    return (
        runtime_candidate if runtime_candidate.exists() else bundled_candidate
    )


@dataclass(frozen=True)
class AppPaths:
    """
    Represent core filesystem locations used by the viewer runtime.

    This dataclass captures the application root, runtime home, resource
    root, and directories for cache and browser profiles.

    Attributes:
        app_root (Path):
            Resolved root directory of the application or repository,
            used as the base for bundled  resources.
        runtime_home (Path):
            Directory where user-specific runtime data such as settings,
            state, and overrides are stored.
        resources_root (Path):
            Directory that contains bundled, read-only
            application resources, typically aligned with app_root.
        cache_root (Path):
            Directory used for transient cache data such as web profile
            such as web profile cache files.
        profile_root (Path):
            Directory used to store persistent browser or web engine
            profile data.
    """

    app_root: Path
    runtime_home: Path
    resources_root: Path
    cache_root: Path
    profile_root: Path

    @classmethod
    def discover(cls, anchor: Path | None = None) -> AppPaths:
        """
        Discover and construct an AppPaths instance for the viewer
        runtime.

        This factory locates the application root, selects a runtime
        home, and derives related cache and profile directories.

        Args:
            anchor (Path | None):
                Optional filesystem path used as the starting point for
                app root discovery; when omitted, the path of this
                module file is used as the anchor.

        Returns:
            AppPaths:
                An initialized AppPaths object whose app_root points at
                the discovered repository root, whose runtime_home
                reflects either an environment override or the app root,
                and whose cache_root and profile_root are nested under
                the runtime home.
        """
        anchor_path: Path = anchor if anchor is not None else Path(__file__)
        app_root: Path = _discover_app_root(anchor=anchor_path)
        runtime_home: Path = _default_runtime_home(app_root)
        return cls(
            app_root=app_root,
            runtime_home=runtime_home,
            resources_root=app_root,
            cache_root=runtime_home / WEB_PROFILE_CACHE_DIRECTORY,
            profile_root=runtime_home / WEB_PROFILE_STORAGE_DIRECTORY,
        )

    @property
    def settings_path(self) -> Path:
        """
        Return the filesystem path for the runtime settings file.

        This property locates the main configuration file within the
        active runtime home directory.

        Returns:
            Path:
                Absolute path pointing to the settings file inside the
                runtime_home directory, using the configured settings
                file name.
        """
        return self.runtime_home / SETTINGS_FILE_NAME

    @property
    def state_path(self) -> Path:
        """
        Return the filesystem path for the runtime state file.

        This property locates the JSON state snapshot file within the
        active runtime home directory.

        Returns:
            Path:
                Absolute path pointing to the state file inside the
                runtime_home directory, using the configured state file
                name.
        """
        return self.runtime_home / STATE_FILE_NAME

    @property
    def prompts_path(self) -> Path:
        """
        Return the filesystem path for the prompts configuration file.

        This property prefers a runtime-specific prompts file and falls
        back to the bundled default prompts resource when needed.

        Returns:
            Path:
                Absolute path to the prompts file, pointing either to
                the runtime_home copy if it exists or to the bundled
                prompts.json under the resources_root directory.
        """
        return _prefer_existing_file(
            self.runtime_home,
            self.resources_root,
            file_name=PROMPTS_FILE_NAME,
        )

    @property
    def tts_rules_path(self) -> Path:
        """
        Return the filesystem path for the text-to-speech rules file.

        This property prefers a runtime-specific rules file and falls
        back to the bundled default rules resource when needed.

        Returns:
            Path:
                Absolute path to the TTS rules file, pointing either to
                the runtime_home copy if it exists or to the bundled
                tts_rules.json under the resources_root directory.
        """
        return _prefer_existing_file(
            self.runtime_home,
            self.resources_root,
            file_name=TTS_RULES_FILE_NAME,
        )

    @property
    def plans_dir(self) -> Path:
        """
        Return the directory that contains bundled plan definitions.

        This property points at the configured plans folder when it
        exists and falls back to the resources root otherwise.

        Returns:
            Path:
                Absolute path to the plans directory, resolving to
                resources_root / PLANS_DIRECTORY_NAME when that
                directory is present, or to resources_root if no
                dedicated plans folder exists.
        """
        default_plans: Path = self.resources_root / PLANS_DIRECTORY_NAME
        return default_plans if default_plans.exists() else self.resources_root

    @property
    def bundled_bin_root(self) -> Path:
        """
        Return the root directory for bundled executable binaries.

        This property points to the folder under resources_root that
        holds packaged helper binaries or scripts.

        Returns:
            Path:
                Absolute path to the bundled bin directory, resolved as
                resources_root / BIN_DIRECTORY_NAME.
        """
        return self.resources_root / BIN_DIRECTORY_NAME

    @property
    def tts_models_root(self) -> Path:
        """
        Return the root directory that contains bundled TTS models.

        This property points to the folder under resources_root where
        text-to-speech model files are stored.

        Returns:
            Path:
                Absolute path to the TTS models directory, resolved as
                resources_root / TTS_MODELS_DIRECTORY_NAME.
        """
        return self.resources_root / TTS_MODELS_DIRECTORY_NAME

    def ensure_runtime_dirs(self) -> None:
        """
        Ensure that all runtime-related directories exist on disk.

        This method creates the runtime home, cache, and profile
        directories if they are missing so that the application can
        read and write data safely.
        """
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.profile_root.mkdir(parents=True, exist_ok=True)
