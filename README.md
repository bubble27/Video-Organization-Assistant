# Video Organization Assistant

A local app for sorting and trimming folders of video clips before you edit or
deliver them. Point it at a folder whose subfolders are "lines" (e.g. `L1`, `L2`,
`L3`), preview every clip, drop the ones you don't want, mark the keepers, and
package the result into a single archive — all from a clean browser UI driven by
a tiny Python backend.

Originally built for organizing metro/transit b-roll (Line 1, Line 2, …), but it
works for any folder-of-folders of clips.

![lines and a duration timeline at the bottom](https://img.shields.io/badge/UI-local%20browser%20app-4ea1ff) ![python](https://img.shields.io/badge/python-3.8%2B-blue) ![ffmpeg](https://img.shields.io/badge/requires-ffmpeg-orange)

---

## Features

- **Auto-loads on folder pick** — choose a folder and it scans immediately. Each
  subfolder becomes a **Line** (`L1` → "Line 1", etc.).
- **Filmstrip previews** — every clip shows its first frame; hover and move the
  mouse left↔right to scrub through the clip. Works for iPhone HEVC `.MOV` that
  browsers can't normally play.
- **Double-click to play** a clip in your system's default video player.
- **Click to remove / restore** — removed clips are physically moved into an
  `Unused/` subfolder inside their line and stop counting toward durations.
- **Drag clips between lines** (their active/unused state is preserved).
- **Duration bar** — the bottom bar shows the total runtime of all active clips,
  split into segments sized by each clip's share, colored per line. Segments
  smoothly resize as you add/remove clips.
- **Mark clips** via the ⋯ menu on each clip:
  - **Main clip** → renames to `U USED`, shows a gold ★
  - **Sub clip** → renames to `U I USED`, shows a red **SUB** tag
  - **Outro** → renames to `U O USED`, shows an **OUTRO** tag
  - Marks are stored in the filename so they survive reloads, and a **Restore
    name** option reverts to the original name during the session.
- **Package** — bundles the folder (keeping `Unused/` folders and all files) into
  one `.zip`, **excluding macOS junk** (`._*` AppleDouble files and `.DS_Store`).
  Uses no compression (video is already compressed), runs in the background with
  a progress bar, and writes atomically.

Nothing is ever deleted — removing a clip only moves it to `Unused/`.

---

## Requirements

- **macOS** (or Windows/Linux — it's cross-platform)
- **Python 3.8+**
- **ffmpeg** (provides `ffmpeg` + `ffprobe`)

No Python packages to install — the backend is pure standard library.

---

## Install on macOS (easiest)

1. Download **`Auto-Video-Organizer-macOS.zip`** from the
   [latest release](https://github.com/bubble27/Video-Organization-Assistant/releases/latest)
   and unzip it anywhere (e.g. your Applications or Desktop).
2. If you don't already have ffmpeg, double-click **`Install ffmpeg.command`**
   once. (It uses [Homebrew](https://brew.sh); if you don't have Homebrew the
   script tells you the one line to paste into Terminal first.)
3. Double-click **`Clip Organizer.command`** to launch. Your browser opens to the
   app automatically.

> **First launch / Gatekeeper:** because the launcher is downloaded from the
> internet, macOS may say it "cannot verify the developer." If so, **right-click**
> the `.command` file → **Open** → **Open**. You only need to do this once.

To quit, close the small Terminal window that opened.

---

## Run from source (any OS)

```bash
git clone https://github.com/bubble27/Video-Organization-Assistant.git
cd Video-Organization-Assistant
python3 app.py        # Windows: python app.py
```

Install ffmpeg if needed:

- **macOS:** `brew install ffmpeg`
- **Windows:** `winget install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`

The app serves at `http://127.0.0.1:8765/` and opens your browser.

---

## Usage

1. **Choose Folder…** (or paste a path and press Enter). The folder's subfolders
   become Lines. Previews generate once with a progress bar, then are cached.
2. **Hover** a clip to scrub its filmstrip; the tooltip shows its duration.
3. **Click** a clip to remove it (→ `Unused/`); click it again under the
   **Unused** section to restore it.
4. **Drag** a clip onto another line to move it there.
5. **Double-click** a clip to open it in your default player.
6. Use the **⋯ menu** to mark a clip as main / sub / outro, or restore its name.
7. Click **📦 Package Video** to produce `<FolderName>.zip` next to the folder.

### Folder layout it expects

Pick a **parent folder**. Each **immediate subfolder** inside it becomes one
**Line**. The clips go directly inside those subfolders.

```
My Project/              ← the folder you choose in the app
├── L1/                  → shown as "Line 1"
│   ├── IMG_0001.MOV
│   ├── IMG_0002.MOV
│   └── Unused/          (created automatically for removed clips)
├── L2/                  → shown as "Line 2"
│   └── ...
└── L3/                  → shown as "Line 3"
    └── ...
```

### Naming rules

**Line folders** — name them so the app can number them:

- A folder name with **letters + a number** is shown as **"Line N"**, where N is
  the number. So `L1`, `L 1`, `Line 1`, and `Set 1` all display as **"Line 1"**.
- A folder name with **no number** (e.g. `Intro`, `B-Roll`) is shown **verbatim**,
  using the folder name as-is.
- Numbers control only the *label*, not the order — folders are listed
  alphabetically. Use zero-padding (`L01`, `L02`, … `L10`) if you have 10+ lines
  and want them in the right order.

**Reserved folder name** — `Unused` (case-sensitive). Inside any line, a
subfolder literally named `Unused` holds removed clips and is **not** treated as
its own line. You normally don't create this yourself; the app makes it when you
remove a clip.

**Clip files** — must be a video: `.mov`, `.mp4`, `.m4v`, `.avi`, or `.mkv`
(case-insensitive). The app **ignores**:

- macOS junk: `._*` (AppleDouble) files and `.DS_Store`
- anything starting with a dot (`.`)
- non-video files (audio, images, sidecars, etc.)

**Reserved clip names** — the ⋯ marking feature renames files to `U USED` (main),
`U I USED` (sub), and `U O USED` (outro). Avoid naming your own clips these unless
you mean to mark them, since the app reads the mark back from the filename.

---

## Notes

- **Previews use filmstrips, not video playback**, because iPhone `.MOV` is
  HEVC/H.265 which most browsers can't decode. ffmpeg extracts ~10 frames per
  clip into a sprite; hovering scrubs through them.
- **Thumbnail cache** lives in a hidden `.thumbcache/` next to `app.py`, keyed by
  filename + size (so moving/marking a clip doesn't regenerate it). Safe to
  delete anytime.
- **Large packages on Windows:** a zip over 4 GB needs ZIP64, which Windows'
  built-in "Compressed Folders" viewer can't open ("access is denied"). Use
  7-Zip or PowerShell `Expand-Archive`. macOS opens them fine.
- Single-user local tool — one folder at a time.

---

## Building a native macOS `.app` (optional)

The release ships a double-click launcher rather than a compiled binary because
a native `.app` must be built **on a Mac** (PyInstaller/py2app can't cross-compile
from Windows). To build one yourself on a Mac:

```bash
python3 -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name "Clip Organizer" \
  --add-data "static:static" app.py
```

The launcher approach needs no build and works on any machine with Python 3 +
ffmpeg, which is why it's the default distribution.

---

## License

MIT
