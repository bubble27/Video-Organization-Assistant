# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Video Organization Assistant (aka "Clip Organizer") — a local single-user web app for sorting and
trimming folders of video clips before editing/delivery. Originally built for organizing metro/transit
b-roll (Line 1, Line 2, …) but works for any folder of clips.

- **Backend:** `app.py`, pure Python 3 standard library (`http.server`), zero pip dependencies.
- **Frontend:** vanilla JS/HTML/CSS in `static/` (no build step, no framework, no bundler).
- **External dependency:** `ffmpeg`/`ffprobe` on PATH (for thumbnails, filmstrips, durations, transcoded
  previews).
- The server binds `127.0.0.1:8765`, serves the frontend, and opens the browser itself.

The app has two stages, auto-detected from folder contents (see `build_model`/`folder_state` in
`app.py`):

- **Stage 1 (sort):** folder has loose clips → review one at a time, file into line folders (`L1`, `L2`,
  …) via keyboard/buttons.
- **Stage 2 (organize):** folder is already sorted into line folders → preview (filmstrip scrubbing),
  trim/mark/remove clips, drag between lines, and package into a zip.

Read `README.md` for the full user-facing feature list, keyboard shortcuts, folder-layout/naming rules,
and Windows/Linux notes — it's kept accurate and detailed; don't duplicate it here.

## Running it

```bash
python3 app.py       # from repo root; opens http://127.0.0.1:8765/ in the browser
```

No install/build step for the backend or frontend — edit and reload.

Useful env vars (see top of `app.py`):
- `VOA_DEBUG=1` — timing/diagnostic logging to the terminal; also disables the auto-quit-on-browser-close
  watchdog (so a dev session can freely close/reload tabs without killing the server).
- `VOA_JOBS=N` — concurrent preview-generation workers (default: `max(2, min(6, cpu_count))`).

There is no test suite, linter, or build/CI pipeline in this repo — there's nothing to run beyond
starting the app and exercising it manually (or via `curl` against the JSON API).

## Architecture

### Backend (`app.py`, single file)

- **Config block** at the top: paths (`STATIC_DIR`, `CACHE_DIR` = `.thumbcache/`, kept out of the user's
  video folder), reserved names (`Unused`, `Assets/Thumbnails`, `Assets/Icons`), video extensions, and
  the env-var-driven tuning knobs above.
- **Filesystem is the source of truth.** There's no database — line membership, active/unused state, and
  marks (main/sub/outro) are all derived by scanning the folder tree and reading filenames/paths (see
  `build_model`, `folder_state`, `mark_of`, `parse_line`, `line_label`). Mutating state means moving
  files on disk (`assign_clip`, `_mutate_toggle`, `_mutate_move`, `rename_line`), not writing to a store.
- **Marks are encoded in filenames**, not metadata: renaming a clip to `U USED` / `U I USED` / `U O USED`
  marks it main/sub/outro; this survives reloads because it's read back from the name (`mark_of`). Avoid
  naming real clips these unless you mean to mark them.
- **Previews are cached, not stored with the source video**, in `.thumbcache/` next to `app.py`, keyed by
  filename + size (`clip_key`, `sprite_path`, `meta_path`, `preview_path`). Stage 2 uses ffmpeg-extracted
  filmstrip sprites (`ensure_sprite`) because iPhone `.MOV` is HEVC and most browsers can't play it
  inline; Stage 1 plays the raw file where supported and falls back to an on-demand low-res H.264
  transcode (`ensure_preview`) elsewhere. The cache is safe to delete anytime.
- **Background work uses simple global job-state dicts** guarded by locks (`JOB`/`JOB_LOCK` for
  scanning, `PKG`/`PKG_LOCK` for packaging), polled by the frontend via `/api/scan-status` and
  `/api/package-status` rather than pushed (no websockets/SSE). `start_scan`/`start_package` spin up a
  worker thread; `ThreadPoolExecutor` fans out per-clip preview generation within a scan.
- **`Handler(BaseHTTPRequestHandler)`** is the entire HTTP layer: routing is a chain of `if p == "..."`
  checks in `_get`/`do_POST`, not a router/framework. `/api/*` returns JSON; `/api/clip`, `/api/preview`,
  `/api/asset-file` stream video via HTTP range requests (`_serve_range`) so the `<video>` element can
  seek. `safe_join` guards against path traversal for anything built from a client-supplied relative path.
- **Auto-quit watchdog:** the frontend pings `/api/heartbeat`; if the server stops hearing heartbeats for
  `HEARTBEAT_TIMEOUT`, it assumes the browser tab closed and shuts itself down (`main()`'s `watchdog`
  thread). Disabled under `VOA_DEBUG`.
- **`already_running()`** lets a second launch (e.g. double-clicking the Dock icon again) detect the
  live instance on port 8765 and just open a browser tab instead of crashing on the port clash.

### Frontend (`static/`)

- `index.html` — shell; `style.css` — all styling.
- `app.js` — Stage 2 (the organizer): rendering lines/clips/timeline, drag-and-drop between lines,
  filmstrip hover-scrub, the in-app side video player, packaging with a progress bar, asset
  upload/removal. Talks to the backend only through the small `api()` fetch wrapper at the top.
- `stage1.js` — Stage 1 (the one-at-a-time sorter): keyboard shortcuts, filing into lines, flag/mark,
  the in-app preview player.
- State lives in module-level JS variables (`MODEL`, `ROOT`, `ASSETS`, etc.) refreshed by polling
  `/api/scan-status`, not a frontend framework/store.

### macOS app bundle (`Video Organizer.app/`)

Ships as a **source-controlled launcher**, not a compiled binary — `Contents/MacOS/run` is a bash script
(the app's `CFBundleExecutable`), and `Contents/Info.plist` declares the bundle id
`com.bubble27.video-organizer`. This exists so users get a double-clickable, Dock-able app with zero
build step and no PyInstaller/py2app cross-compilation requirement (see README's "Building a native
macOS .app" section for why a truly compiled binary is a manual, Mac-only opt-in instead of the default).

`run` is deliberately defensive because a GUI double-click gives you no terminal to see errors in — it
logs everything to `/tmp/video-organizer-launch.log` and shows an `osascript` alert for every failure
mode it knows about, rather than silently flashing and closing. Read the comments in that file before
touching it; the ordering encodes hard-won fixes:

- **App Translocation detection** — an unsigned/quarantined `.app` opened from a random read-only mount
  can't find `app.py` next to it; detected via `$0` containing `AppTranslocation`.
- **Python interpreter selection is load-bearing, not cosmetic.** macOS's file-privacy system (TCC) hard
  denies Full Disk Access / Documents Folder grants to *any* Apple-signed binary (`/bin/bash`,
  `/usr/bin/python3` from Xcode Command Line Tools) — `Denied (Service Policy)`, and no System Settings
  toggle can override it. Homebrew's `python3` is not Apple-signed, but its `bin/python3` stub itself
  gets a synthetic, ungrantable TCC identity for a startup file check and dies with EPERM *before*
  reaching its own `Resources/Python.app` helper — which is the process that actually runs `app.py`, and
  the one a user's Full Disk Access grant needs to target. The script resolves the real interpreter path
  and invokes that nested `Resources/Python.app/Contents/MacOS/Python` binary directly, skipping the
  stub. If you ever see this app fail with `Operation not permitted` again, this TCC/identity chain is
  almost certainly why — the script's own EPERM handler (below) parses the failing binary out of the log
  and tells the user exactly what path to grant Full Disk Access to.
- **Auto-update on every launch:** fetches `origin/<branch>` (8s timeout, never blocks startup on a slow
  or absent network), fast-forwards only (`git merge --ff-only` — never a real merge, so it can never
  conflict), and only if the working tree is clean. If it pulls, it re-execs itself (`VOA_UPDATED=1 exec
  "$0" "$@"`) rather than continuing inline, because a running bash script's file being rewritten out
  from under it corrupts the in-flight read. Diverged history or local edits silently skip the update and
  launch whatever's on disk — this never discards local work. Skip a single launch's check with
  `VOA_SKIP_UPDATE=1`.
- **EPERM-after-launch handling:** if `python3 app.py` exits non-zero and the log contains
  "Operation not permitted", it's the TCC issue above — parses the actual failing binary path out of the
  log and alerts the user with the exact `.app` path to add in System Settings → Privacy & Security →
  Full Disk Access.

`Clip Organizer.command` and `Install ffmpeg.command` are simpler, older double-click alternatives (a
plain Terminal-window launcher, and a Homebrew-based ffmpeg installer) — they don't have the TCC/app-
bundle complexity above because a `.command` file always runs in a visible Terminal window with normal
shell permissions.

`.gitattributes` forces LF line endings on `.command`, `.sh`, `.py`, and the extensionless `run` script —
CRLF breaks the shebang on macOS, so don't let an editor silently convert these.
