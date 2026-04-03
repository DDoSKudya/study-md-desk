# ADR 0003: Embedded web UI with local HTTP, shell/viewer split, and dual IPC

## Status

Accepted

## Context

The application is a **desktop-first** PyQt program that nevertheless presents its main workspace as a **web UI** inside `QWebEngineView` (Chromium). Several concerns must be decided explicitly:

1. **How the UI is delivered** — `file://` URLs, bundled `qrc` resources, or a **local HTTP** server.
2. **Separation of “chrome” vs document** — toolbars, file tree, TOC, TTS controls, and interpreter live around a **document** that must scroll and run **viewer scripts** independently.
3. **How JavaScript talks to Python** — the shell must trigger TTS, clipboard, chat injection, and navigation; the **reader** (nested document) must report scroll position, section focus, and receive TTS highlight overlays.
4. **Where Markdown becomes HTML** — client-only rendering vs **server-side** pipeline shared with navigation and asset safety checks.
5. **Security posture** — the HTTP server binds to **loopback** only; optional **sandboxed** execution for user Python snippets; path traversal prevention for `/view/` and assets.

ADR [0001](adr-0001-modular-monolith-layout.md) covers **paths and monolith packaging**. ADR [0002](adr-0002-hybrid-async-runtime.md) covers **async boundaries and resilience** around blocking I/O. Neither defines the **web shell architecture** or **why two IPC mechanisms** coexist.

## Decision

### 1. Local HTTP as the UI transport (not `file://` for the shell)

- Serve the shell, static assets, JSON APIs, and rendered views from an **in-process** `ThreadedServer` on **`127.0.0.1`**, trying ports **8765–8774** (see `viewer_app.runtime.server_runtime`).
- Load the primary `QWebEngineView` at **`http://127.0.0.1:<port>/`** so the shell uses **normal origin semantics** (`fetch`, relative URLs, cookies if needed) against a single host.
- **Rationale:** avoids `file://` quirks, keeps one consistent base URL for the shell and for `/view/...` documents, and aligns with ADR 0002’s synchronous HTTP boundary.

### 2. Shell page + document iframe

- **`GET /`** (`build_shell_html` in `http_pages.py`) returns the **shell**: sidebar (files, favorites), TOC column, **iframe** pointing at **`/view/<path>`**, interpreter strip, TTS strip, modals.
- **`GET /view/...`** (`build_view_html`) returns a **full HTML document** for one Markdown file: Python-rendered body, `viewer.js` enhancements, Mermaid/MathJax as configured.
- **Rationale:** the iframe isolates **document navigation and `viewer.js`** from shell layout scripts; the parent can swap `iframe.src` without reloading the entire desktop window (`shell.js` `openDoc`).

### 3. Server-side Markdown rendering

- Conversion **MD → HTML** runs in **Python** (`viewer_app.core.markdown_core`, used from `http_pages.build_view_html`).
- **Rationale:** one pipeline for extensions, Pygments, stepwise/callout transforms, link rewriting, and **root-confined** path resolution (`_resolve_markdown_fs_path`, `resolve_view_asset`). The client (`viewer.js`) adds UX (TOC sync, selection menus, TTS highlight targets), not canonical parsing.

### 4. Dual IPC: Qt WebChannel on the shell + `postMessage` for the iframe

| Mechanism | Where | Role |
|-----------|--------|------|
| **Qt WebChannel** | Registered on the **shell** `QWebEnginePage` only (`desktop_online_panel.build_online_panel`): `registerObject("chatBridge", chat_bridge)` | JS on the **shell** calls into Python (`ChatBridge`): TTS control, clipboard, `askInChat`, notes navigation hooks, document loading overlay, theme. |
| **`window.postMessage`** | **Viewer** document → parent `shell.js` `message` listener | Iframe reports `doc-render-ready`, `doc-meta`, `active-section`, `scroll-progress`, `ask-in-chat`, `tts-speak`, `add-note`, etc. Parent can **`postMessage`** into the iframe for TTS highlight and related viewer commands (see `viewer.js` / `shell.js`). |

**Rationale:** WebChannel is attached to the **top-level** page loaded in the main view; the **nested iframe** loads a separate document origin path (`/view/...`). Using **`postMessage`** for iframe ↔ parent avoids registering WebChannel inside every document load and keeps a clear, typed message protocol for document events. Shell-side features that need **Python** use WebChannel from `shell.js` (`window.chatBridge`).

### 5. Online panel (optional web tabs)

- Additional **`QWebEngineView`** instances in a splitter show **external** URLs (chat, translator, sandbox) configured via INI.
- They share the **same** user-facing workflow as the bridge (e.g. `askInChat` from viewer context menu) but are **not** the same origin as `127.0.0.1`; integration goes through **`ChatBridge`** and host callbacks, not direct iframe access to localhost APIs.

### 6. Sandboxed user Python

- **`POST /run`** executes snippets via `viewer_app.runtime.python_runner` with **restricted** semantics appropriate for a study tool (not a general remote shell). This is **orthogonal** to the Qt event loop but shares the same **local HTTP** surface as the UI.

### 7. Loopback binding and documentation assets

- The server is intended for **local** use only; binding policy stays in `server_runtime` / `ThreadedServer`.
- The repository’s **`content/`** tree (screenshots, GIFs for README/docs) is **not** served as part of the app UI; it is **documentation media** only (see `DEVELOPERS.md` §2). This keeps shipped surface area smaller and avoids accidental exposure of arbitrary binary paths.

## Consequences

- **Pros:** Clear separation between shell chrome, rendered lesson, and Python bridge; predictable same-origin behavior; server-side MD stays authoritative; iframe messaging is debuggable in DevTools.
- **Cons:** Two IPC styles must be documented and maintained (`ChatBridge` API + `postMessage` types); any new cross-frame feature must choose the correct channel.
- **Testing / tooling:** E2E tests may drive the HTTP API without Qt; UI tests require WebEngine or focused JS tests.

## Implementation map (for navigation)

| Area | Primary modules |
|------|-----------------|
| Shell HTML, view HTML, TOC | `viewer_app.http.http_pages` |
| GET/POST routing | `viewer_app.http.http_handler`, `http_routes` |
| Markdown pipeline | `viewer_app.core.markdown_core`, `navigation` |
| HTTP server thread | `viewer_app.http.http_server`, `runtime.server_runtime` |
| WebChannel + online panel | `viewer_app.desktop.desktop_online_panel`, `desktop_bridge` |
| Shell / viewer JS | `viewer_app.web.web_assets/shell.js`, `viewer.js` |
| TTS highlight dispatch | `viewer_app.desktop.legacy_app`, `desktop_tts_*`, `viewer.js` |
| Sandboxed run | `viewer_app.runtime.python_runner` |

## Related documents

- [adr-0001-modular-monolith-layout.md](adr-0001-modular-monolith-layout.md) — paths and runtime home.
- [adr-0002-hybrid-async-runtime.md](adr-0002-hybrid-async-runtime.md) — threading, caching, resilience.
- [DEVELOPERS.md](DEVELOPERS.md) — route tables, sequence diagrams, file roles.
