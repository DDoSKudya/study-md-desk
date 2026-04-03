# ADR 0001: Modular monolith with explicit path policy

## Status

Accepted

## Context

`study_md_desk` historically mixed entrypoint code, desktop shell, HTTP server, markdown rendering, TTS logic, bundled binaries and runtime user data in one script and one directory-level mental model. After moving code under `viewer_app`, the previous `Path(__file__).parent` semantics would have broken runtime compatibility unless path ownership became explicit.

## Decision

- Keep the application as a modular monolith.
- Introduce `viewer_app.runtime.paths.AppPaths` as the single path policy entry.
- Preserve legacy runtime layout by default, with `study_md_desk/` as runtime home.
- Allow an alternate runtime home through `MD_VIEWER_HOME`.
- Treat `bin/`, `tts_models/`, `prompts.json`, `tts_rules.json` as bundled resources anchored at application root.
- Treat `study_md_desk.ini`, `study_md_desk_state.json`, `web_profile_storage/` as runtime-home data.
- Keep `study_md_desk.py` as a thin compatibility entrypoint that delegates to `viewer_app.app.main`.

## Consequences

- Existing installs continue to work without moving user files.
- New modules can depend on `AppPaths` instead of hidden filesystem assumptions.
- Future migration of runtime data out of the legacy directory can happen behind the same compatibility layer.
- Architectural erosion is reduced because source code and runtime data are now modeled separately even when they still coexist physically in legacy mode.

