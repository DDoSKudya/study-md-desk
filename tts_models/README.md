## Piper voice models

This directory holds **voice models** (ONNX) for the Piper TTS engine. You also need the **Piper binary itself** — see [`bin/README.md`](../bin/README.md).

The `tts_models/` directory is expected at the **application (repository) root**, next to `viewer_app/` and `bin/`, not under `MD_VIEWER_HOME` if you set that variable.

---

### Where to download voices

The official voice collection is on Hugging Face:

**[https://huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)**

Repository layout: **language** → **locale** → **speaker** → **quality** (often `low` / `medium` / `high`). In the leaf folder you need two files that share the **same name prefix**, for example:

- `ru_RU-irina-medium.onnx`
- `ru_RU-irina-medium.onnx.json`

Download **both** files (without the `.json` pair the voice usually will not work).

A summary table of voices, locales, and links is also in the Piper repo:

**[VOICES.md](https://github.com/rhasspy/piper/blob/master/VOICES.md)** (in the [rhasspy/piper](https://github.com/rhasspy/piper) root)

---

### What to put in `tts_models/`

In this app each voice is its **own subfolder** under `tts_models/` and **must** contain two files with **fixed names**:

- `model.onnx`
- `model.onnx.json`

That is how the voice list works in the UI (`/piper-voices`) and how `study_md_desk.ini` examples are written.

**Steps:**

1. Pick a voice on Hugging Face (or use `VOICES.md`).
2. Download the `*.onnx` and matching `*.onnx.json` pair.
3. Create a directory named with the **voice id** — the same as the file prefix before `.onnx`, e.g. `ru_RU-irina-medium`:

   `study_md_desk/tts_models/ru_RU-irina-medium/`

4. Put the files there and **rename** them to `model.onnx` and `model.onnx.json` (do not change file contents).

Example:

```text
study_md_desk/
  tts_models/
    ru_RU-irina-medium/
      model.onnx
      model.onnx.json
    ru_RU-ruslan-medium/
      model.onnx
      model.onnx.json
```

Multiple voices mean multiple such folders.

---

### Settings in `study_md_desk.ini`

In `study_md_desk.ini`, `[app]` section:

- `ttsengine = piper`
- `pipervoicename = ru_RU-irina-medium` — the **folder** name for the voice (as under `tts_models/`)
- if needed, explicit paths (relative to the project root or absolute):

  - `pipermodelpath = tts_models/ru_RU-irina-medium/model.onnx`
  - `piperconfigpath = tts_models/ru_RU-irina-medium/model.onnx.json`

If you only set `pipervoicename`, the app expects `model.onnx` next to `model.onnx.json` in that subfolder.

---

### Downloading from Hugging Face (optional)

- Web UI: open the file in the repo → **Download** menu.
- [Git LFS](https://git-lfs.com/) or the Hugging Face CLI (`huggingface-cli download …`) if you want automation.

You do not need to copy `samples`-style folders from the voices repo into `tts_models/` — Piper only needs the `.onnx` and `.onnx.json` pair.

---

### Quick checks

If a voice does not show up in the list or TTS is silent:

- the voice folder has **both** `model.onnx` **and** `model.onnx.json`;
- file names are exactly **`model.onnx`** / **`model.onnx.json`** (unless you override paths in the INI);
- `pipervoicename` matches the folder name;
- files are readable by the current user;
- the Piper binary is available (see [`bin/README.md`](../bin/README.md)).
