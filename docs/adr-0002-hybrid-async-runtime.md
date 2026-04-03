# ADR 0002: Hybrid async runtime boundaries

## Status

Accepted

## Context

`study_md_desk` is not a pure HTTP service. It is a desktop-first PyQt application that embeds a local HTTP runtime, filesystem-backed state/config, subprocess-driven TTS, and a Python execution sandbox. A full `asyncio-first` rewrite across the whole stack would create high migration risk around the Qt event loop, TTS subprocess lifecycle, and compatibility-sensitive UI contracts.

At the same time, several runtime paths were still paying synchronous costs on hot flows:

- repeated Python interpreter scanning for shell bootstrap
- repeated config/prompt file reads on request paths
- non-atomic state writes
- synchronous document reads on the desktop TTS path
- noisy `BrokenPipeError` failures when browser requests disconnected mid-response

## Decision

- Keep the desktop shell on the Qt event loop.
- Keep the in-process local HTTP server as a synchronous boundary for now.
- Introduce hybrid async and controlled background execution only at expensive or blocking edges.
- Use shared executors or background threads for subprocess- and file-heavy operations instead of spreading `async` into pure core logic.
- Prefer caching, atomic persistence, and explicit concurrency boundaries before adopting a larger async server rewrite.

## Implemented boundaries

- `viewer_app.runtime.python_runner.run_heavy_async(...)` provides an async entry for heavy executor-backed work.
- `viewer_app.runtime.python_runner.scan_python_versions(...)` now uses short-lived caching to avoid repeated synchronous scans on shell rendering.
- `viewer_app.runtime.config` caches app config by file stamp and reuses parsed prompt payloads when `prompts.json` content is unchanged.
- `viewer_app.runtime.state.StateStore` now serializes access with a re-entrant lock and writes state atomically.
- `viewer_app.desktop.desktop_tts_orchestrator.build_tts_actions(...)` now supports background document loading and UI-thread dispatch.
- `viewer_app.http.http_routes.send_json(...)` and `send_html(...)` tolerate broken client connections instead of surfacing noisy runtime tracebacks.

## Consequences

- The GUI thread is less exposed to blocking document-read work when starting TTS.
- Request-time I/O churn is reduced without forcing a risky rewrite of the local HTTP server.
- State persistence is more resilient under concurrent updates from desktop and HTTP flows.
- Future migration to an async HTTP runtime remains possible because executor-backed boundaries now exist explicitly.
- Pure core modules remain synchronous and deterministic, which keeps tests simple and aligned with clean-code guidance.
