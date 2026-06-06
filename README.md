# Video Organization Assistant

A local app for sorting and trimming folders of video clips before you edit or
deliver them — from a clean browser UI driven by a tiny Python backend.

It has **two stages** and picks the right one automatically when you open a folder:

- **Stage 1 — Sort into lines:** when a folder has loose clips sitting in it, you
  review them one at a time and file each into a line (`L1`, `L2`, …) with a key
  press or a button. Then it collects thumbnails and icons.
- **Stage 2 — Organize:** once everything is sorted into line folders, you preview,
  trim, mark, and package — the duration timeline, drag-between-lines, etc.

Originally built for organizing metro/transit b-roll (Line 1, Line 2, …), but it
works for any folder of clips.

![lines and a duration timeline at the bottom](https://img.shields.io/badge/UI-local%20browser%20app-4ea1ff) ![python](https://img.shields.io/badge/python-3.8%2B-blue) ![ffmpeg](https://img.shields.io/badge/requires-ffmpeg-orange)

---

## Features

### Stage 1 — sorting loose clips into lines

- **One-by-one review** — each loose clip in the root plays in a large preview
  (the raw `.MOV`, so it works best in Safari / on macOS where HEVC decodes).
- **File into a line** — press **1–9** or click a line button to move the clip
  into `L1`, `L2`, … A **+** adds more lines (and is how you reach lines past 9).
- **Flag as unused** (the ⚑ button / **F**) files the clip into that line's
  `Unused/` subfolder instead.
- **Mark on the way in** — **M** main, **B** sub, **O** outro (same `U USED` /
  `U I USED` / `U O USED` naming as Stage 2).
- **Skip** (**S**) sends a clip to the back of the queue to decide later.
- When the last clip is filed, it prompts you to add **thumbnails** then **icons**,
  then transitions to Stage 2 automatically.

### Stage 2 — organizing sorted lines

- **Assets bar at the top** — your thumbnails and icons, each removable, with a
  **+** to add more at any time (drag-drop or file picker).
- **Auto-loads on folder pick** — each line subfolder becomes a **Line**
  (`L1` → "Line 1", etc.).
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

**Choose Folder…** (or paste a path and press Enter). The app detects the stage:

### If the folder has loose clips → Stage 1 (sort)

1. Each clip plays in the big preview. Decide which line it belongs to.
2. Optionally **⚑ flag** it unused, and/or mark it **main / sub / outro**.
3. Press a **number (1–9)** or click a **line button** to file it; the next clip
   loads automatically. Use **+** to add more lines (including 10+).
4. **Skip** (S) defers a clip; **Open ↗** opens it in your default player.
5. After the last clip, add **thumbnails** and **icons** when prompted (or Skip).
   The app then moves into Stage 2.

   | Key | Action | Key | Action |
   |-----|--------|-----|--------|
   | `1`–`9` | file into Line N | `F` | flag unused |
   | `+` | add a line | `S` | skip |
   | `M` | mark main | `B` | mark sub |
   | `O` | mark outro | | |

### If the folder is already sorted → Stage 2 (organize)

1. Previews generate once with a progress bar, then are cached.
2. The **assets bar** at the top shows thumbnails/icons; use **+** to add or **×**
   to remove them.
3. **Hover** a clip to scrub its filmstrip; the tooltip shows its duration.
4. **Click** a clip to remove it (→ `Unused/`); click it again to restore it.
5. **Drag** a clip onto another line to move it there.
6. **Double-click** a clip to open it in your default player.
7. Use the **⋯ menu** to mark a clip as main / sub / outro, or restore its name.
8. Click **📦 Package Video** to produce `<FolderName>.zip` next to the folder.

### Folder layout

**Stage 1 — before sorting:** loose clips sit directly in the folder. The app
sees those and starts the sorter. (Existing `L#` folders, if any, become buttons.)

```
My Project/              ← the folder you choose in the app
├── IMG_0001.MOV         (loose clips → reviewed one by one)
├── IMG_0002.MOV
└── IMG_0003.MOV
```

**Stage 2 — after sorting:** every clip lives in a line folder. No loose clips, so
the app opens the organizer. Sorting and the asset prompts produce this:

```
My Project/
├── L1/                  → shown as "Line 1"
│   ├── IMG_0001.MOV
│   ├── U USED.MOV       (a clip you marked "main")
│   └── Unused/          (clips flagged unused / removed)
├── L2/                  → shown as "Line 2"
│   └── ...
└── Assets/
    ├── Thumbnails/      (whatever you added in the thumbnail step)
    └── Icons/           (whatever you added in the icon step)
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
