"""
Study MD Desk — desktop entry script.

This repository root module is the **user-facing launcher** for Study MD
Desk: a local **PyQt6** desktop application for reading and studying
**Markdown** courses.

Run it with ``python study_md_desk.py`` (or ``python -m`` if the project
root is on ``PYTHONPATH``).

All application logic lives under ``viewer_app/``; this file only
delegates to ``viewer_app.app.main.main``.

**What the app provides**

- A single window with a file tree, heading outline (**Contents**), and
  a rendered Markdown **reader** (typography, code highlighting,
  callouts, stepwise blocks, and related extensions).
- **Projects**: multiple course roots (folders), recent and pinned
  roots, and navigation between files inside the active project.
- **Notes**: attach short notes to text selections; edit or remove them
  from the same context menu when the selection matches saved metadata.
- **Text-to-speech**: listen to the document or a selection using
  **Piper** (offline, with ONNX voices under ``tts_models/`` and
  optional binaries under ``bin/``) or **eSpeak**; optional read-along
  **highlighting** and controls for speed and pauses.
  Text is normalized via ``tts_rules.json`` before speech.
- **Sandboxed Python**: a small **Interpreter** panel runs restricted
  snippets beside the reader (for exercises and quick experiments).
- **Settings**: appearance, TTS engine and voice, optional URLs for
  online chat, translator, and playground tabs; **explain** prompt
  templates come from ``prompts.json`` (the app copies a prepared prompt
  to the clipboard—it does not send it to the network by itself).
- **Optional online side tabs**: embedded browser tabs reach the network
  only when you open them; core reading and Piper TTS work **offline**
  once dependencies and models are installed.

**Runtime layout**

- Configuration and UI state default next to the application root:
  ``study_md_desk.ini``, ``study_md_desk_state.json``, plus WebEngine
  profile directories.
  Set the environment variable ``MD_VIEWER_HOME`` to use a different
  directory for those files (and related caches).
  A sample INI is ``study_md_desk.ini.example``.
- Bundled resources (``bin/``, ``tts_models/``, ``prompts.json``,
  ``tts_rules.json``) are resolved from the **repository / install
  root**, not from ``MD_VIEWER_HOME``.

**Local HTTP**

The embedded viewer loads a **local** shell page served on ``127.0.0.1``
on the first free port in the range **8765-8774** (see
``viewer_app.runtime``).
This is not a public server; it exists to unify the desktop shell and
the in-app browser.

**Documentation and stack**

End-user and install notes: **README.md**. Architecture, HTTP routes,
WebChannel, and extension points: **docs/DEVELOPERS.md**.
Dependencies: **requirements.txt** (runtime) and
**requirements-dev.txt** (tests and linters). **Python 3.12+** is the
supported baseline.

**License**

MIT — see ``LICENSE`` in the repository root.
"""

from viewer_app.app.main import main

if __name__ == "__main__":
    main()
