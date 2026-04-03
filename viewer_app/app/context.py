"""
This module centralizes creation and access to the applications core
runtime context.

It provides a single place to assemble paths, configuration, state, and
project services and to reuse that assembled context across the app.

It defines an AppContext dataclass that holds paths, config, state, and
projects service instances.

The AppContext.build classmethod discovers runtime paths, ensures
required directories exist, loads configuration, constructs a state
store, and wires a ProjectsService before returning a new context
instance.

The AppContext.reload_config method refreshes the in-memory
configuration by reloading it from disk using the existing paths.

A top-level get_app_context function, decorated with functools.cache,
lazily builds and then returns a cached singleton-like AppContext on
subsequent calls.

Within the broader system, this module acts as a lightweight service
locator or dependency container, giving the UI and background components
a stable entry point to shared runtime services and configuration.
"""

from __future__ import annotations

from functools import cache

from dataclasses import dataclass

from viewer_app.runtime.config import AppConfig, load_app_config
from viewer_app.runtime.paths import AppPaths
from viewer_app.runtime.projects import ProjectsService
from viewer_app.runtime.state import StateStore


@dataclass
class AppContext:
    """
    Bundle core application services and configuration into a single
    context object.

    This dataclass groups paths, configuration, state, and
    project-related services so they can be passed around as one
    cohesive unit.

    Attributes:
        paths (AppPaths):
            Resolved application paths, including runtime directories
            and other filesystem locations used by the app.
        config (AppConfig):
            Loaded application configuration that controls runtime
            behavior and feature flags.
        state (StateStore):
            Persistent state store responsible for saving and restoring
            user or session-specific data. projects (ProjectsService):
            Service that manages project metadata and operations using
            the shared state store and paths. and paths.
        projects (ProjectsService):
            Service that manages project metadata and operations
            using the shared state store and paths.

    # Methods:

        build() -> AppContext:
            Construct a fully initialized application context.

        reload_config() -> AppConfig:
            Reload application configuration from disk.
    """

    paths: AppPaths
    config: AppConfig
    state: StateStore
    projects: ProjectsService

    @classmethod
    def build(cls) -> AppContext:
        """
        Construct a fully initialized application context.

        This class method discovers runtime paths, loads configuration,
        and wires together shared services into a single AppContext
        instance.

        Returns:
            AppContext:
                A new application context whose paths, configuration,
                state store, and projects service are initialized and
                ready for use by the UI or background components.
        """
        paths: AppPaths = AppPaths.discover()
        paths.ensure_runtime_dirs()
        state: StateStore = StateStore(paths)
        return cls(
            paths=paths,
            config=load_app_config(paths),
            state=state,
            projects=ProjectsService(state_store=state),
        )

    def reload_config(self) -> AppConfig:
        """
        Reload application configuration from disk.

        This method refreshes the contexts configuration using the
        current runtime paths so subsequent users see up-to-date
        settings.

        Returns:
            AppConfig:
                The newly loaded application configuration object
                associated with this contexts paths.
        """
        self.config = load_app_config(self.paths)
        return self.config


@cache
def get_app_context() -> AppContext:
    """
    Return a cached application context instance.

    This function builds the global AppContext on first use and then
    returns the  same shared instance for subsequent callers.

    Returns:
        AppContext:
            The singleton-like application context containing paths,
            configuration, state store, and projects service instances.
    """
    return AppContext.build()
