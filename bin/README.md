## Local Piper binaries

This folder is for running bundled **Piper** TTS **without** installing Piper system-wide.

The app looks for the binary in this order:

1. `<repository_root>/bin/<platform>-<arch>/`
2. a `piper` executable on `PATH`

If Piper is under `bin`, it takes precedence. Subfolder names match the code: for example `linux-x86_64`, `linux-arm64`, `windows-x86_64`, and on macOS `macos-x86_64` or `macos-arm64`.

---

### Where to download Piper

Official prebuilt binaries are in **Rhasspy Piper** releases:

**[https://github.com/rhasspy/piper/releases](https://github.com/rhasspy/piper/releases)**

Open **Releases**, pick the latest stable release, and download the archive for your OS and CPU:

| Your system | Typical release asset |
|-------------|------------------------|
| Linux x86_64 | `piper_linux_x86_64.tar.gz` |
| Linux AArch64 (ARM64) | `piper_linux_aarch64.tar.gz` |
| Windows x64 | `piper_windows_amd64.zip` |
| macOS Intel | `piper_macos_x64.tar.gz` |
| macOS Apple Silicon | `piper_macos_aarch64.tar.gz` |

Archive contents may change slightly over time; the important part is to use a **prebuilt** `piper_*_*` package, not the source-only tree.

---

### What to put in this folder

1. Extract the archive to a **temporary** directory and inspect it: usually a folder (e.g. `piper`) with the **`piper`** or **`piper.exe`** executable plus **libraries** (`.so` on Linux, `.dll` on Windows), **espeak-ng** data, sometimes **onnxruntime**, etc.

2. **Copy everything** from that folder (not only `piper` / `piper.exe`) into the app’s platform directory:

   - **Linux x86_64:** `study_md_desk/bin/linux-x86_64/`
   - **Linux AArch64 (ARM64):** `study_md_desk/bin/linux-arm64/`
   - **Windows x64:** `study_md_desk/bin/windows-x86_64/`
   - **macOS:** `study_md_desk/bin/macos-x86_64/` or `study_md_desk/bin/macos-arm64/` depending on the CPU.

3. The app checks these executable paths in order:

   - Linux/macOS: `bin/<plat>-<arch>/piper` **or** `bin/<plat>-<arch>/piper/piper`
   - Windows: `bin/<plat>-<arch>/piper.exe` **or** `bin/<plat>-<arch>/piper/piper.exe`

   If the binary lives under `piper/` after unpack, you can **leave it that way** — the second path is for that layout.

Example final layout (Linux):

```text
study_md_desk/
  bin/
    linux-x86_64/
      piper                 # or piper/piper
      libespeak-ng.so*
      libonnxruntime.so*
      ... other files from the Piper archive
```

---

### Important

- Copy the **full file set** from the Piper bundle into the platform folder — without nearby `.so` / `.dll` files the binary often fails to start.
- On Linux: `chmod +x piper` (or `piper/piper`) if the file is not executable.
- **Voice (ONNX) models** do not belong here: configure them in `study_md_desk.ini` / `tts_models/` (see the root README).
- If there is no matching `bin` folder, `piper` from `PATH` is used.

---

### Quick checks

If TTS does not start:

- the folder name is exactly **`linux-x86_64`**, **`windows-x86_64`**, etc. (as above);
- all libraries from the archive sit next to the binary;
- from that folder run `piper --help` (or `.\piper.exe --help` on Windows) — it should succeed with no missing-library errors.
