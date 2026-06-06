#!/usr/bin/env python3
"""
Clip Organizer — a local web app to sort .MOV clips into "lines".

Backend: pure Python standard library (no pip installs needed).
External dependency: ffmpeg + ffprobe must be on PATH.

Run:  python3 app.py
It starts a local server and opens your browser.
"""

import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import threading
import subprocess
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
CACHE_DIR = os.path.join(HERE, ".thumbcache")     # kept OUT of the video folder
os.makedirs(CACHE_DIR, exist_ok=True)

UNUSED_NAME = "Unused"
ASSETS_DIR = "Assets"            # holds Thumbnails/ and Icons/, never a line
ASSET_KINDS = {"thumbnails": "Thumbnails", "icons": "Icons"}
RESERVED_DIRS = {UNUSED_NAME, ASSETS_DIR}
N_FRAMES = 10           # frames per filmstrip
FRAME_W, FRAME_H = 160, 120
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
HOST, PORT = "127.0.0.1", 8765

# Shared scan job state (single user, so one global job is fine)
JOB = {"running": False, "total": 0, "done": 0, "model": None, "error": None, "root": None}
JOB_LOCK = threading.Lock()

# Packaging job state
PKG = {"running": False, "total": 0, "done": 0, "zip": None,
       "error": None, "bytes": 0, "count": 0}
PKG_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ----------------------------------------------------------------------------

def _run(cmd):
    """Run a command silently. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except FileNotFoundError as e:
        return 127, "", str(e)


def have_tools():
    return _run(["ffprobe", "-version"])[0] == 0 and _run(["ffmpeg", "-version"])[0] == 0


def probe_duration(path):
    rc, out, _ = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ])
    try:
        return float(out.strip())
    except (ValueError, AttributeError):
        return 0.0


def clip_key(path):
    """Stable cache key independent of folder location (survives moves)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    raw = f"{os.path.basename(path)}|{size}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:16]


def sprite_path(key):
    return os.path.join(CACHE_DIR, key + ".jpg")


def meta_path(key):
    return os.path.join(CACHE_DIR, key + ".json")


def ensure_sprite(path):
    """Generate (if missing) a filmstrip sprite + meta json for a clip. Returns meta dict."""
    key = clip_key(path)
    sp, mp = sprite_path(key), meta_path(key)
    if os.path.exists(sp) and os.path.exists(mp):
        try:
            with open(mp, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass

    duration = probe_duration(path)
    tmpdir = os.path.join(CACHE_DIR, "_tmp_" + key)
    os.makedirs(tmpdir, exist_ok=True)
    vf = (f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
          f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:black")

    made = 0
    for i in range(N_FRAMES):
        t = (duration * i / (N_FRAMES - 1)) if duration > 0 and N_FRAMES > 1 else 0.0
        out = os.path.join(tmpdir, f"{i}.jpg")
        rc, _, _ = _run([
            "ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", path,
            "-frames:v", "1", "-vf", vf, "-q:v", "5", out,
        ])
        if rc == 0 and os.path.exists(out):
            made += 1
        else:
            # fall back to a black frame so the sprite stays uniform
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                  f"color=c=black:s={FRAME_W}x{FRAME_H}", "-frames:v", "1", out])

    # tile frames horizontally into one sprite
    _run([
        "ffmpeg", "-y", "-framerate", "1", "-start_number", "0",
        "-i", os.path.join(tmpdir, "%d.jpg"),
        "-frames:v", "1", "-vf", f"tile={N_FRAMES}x1", "-q:v", "5", sp,
    ])
    shutil.rmtree(tmpdir, ignore_errors=True)

    meta = {"key": key, "duration": duration, "n": N_FRAMES,
            "frameW": FRAME_W, "frameH": FRAME_H}
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


# A low-res H.264 preview used when the browser can't play the raw HEVC .MOV
# (Chrome / Windows). Generated lazily and cached next to the thumbnails.
_PREVIEW_LOCKS = {}
_PREVIEW_GUARD = threading.Lock()


def preview_path(key):
    return os.path.join(CACHE_DIR, key + ".preview.mp4")


def ensure_preview(src):
    """Transcode `src` to a small, broadly-playable mp4 (cached). Returns path or None."""
    key = clip_key(src)
    out = preview_path(key)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    with _PREVIEW_GUARD:
        lock = _PREVIEW_LOCKS.setdefault(key, threading.Lock())
    with lock:
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        tmp = out + ".tmp.mp4"
        rc, _, _ = _run([
            "ffmpeg", "-y", "-i", src,
            "-vf", "scale=-2:480",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart", "-threads", "0", tmp,
        ])
        if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            return out
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return None


# ----------------------------------------------------------------------------
# Folder scanning / model
# ----------------------------------------------------------------------------

def is_junk(name):
    return name.startswith("._") or name == ".DS_Store" or name.startswith(".")


def is_video(name):
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS and not is_junk(name)


def line_label(name):
    """Turn folder name like 'L1' or 'Line 2' into a display label 'Line N'."""
    digits = "".join(c for c in name if c.isdigit())
    if digits and any(c.isalpha() for c in name):
        return f"Line {int(digits)}"
    return name


# Filename markers the user applies to clips. The mark is encoded in the name
# so it survives reloads and is recovered on scan.
MARK_BASE = {"main": "U USED", "sub": "U I USED", "outro": "U O USED"}
_BASE_TO_MARK = {v: k for k, v in MARK_BASE.items()}


def mark_of(name):
    """Return 'main' | 'sub' | 'outro' | None based on a clip's filename."""
    stem = os.path.splitext(name)[0].strip()
    # strip a trailing collision number, e.g. "U USED 2" -> "U USED"
    head, _, tail = stem.rpartition(" ")
    if head and tail.isdigit():
        stem = head
    return _BASE_TO_MARK.get(stem)


def list_clips_in(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(n for n in os.listdir(folder)
                  if is_video(n) and os.path.isfile(os.path.join(folder, n)))


def gather_all_clip_paths(root):
    """Every clip path (active + unused) under every line, for pre-generation."""
    paths = []
    for line in sorted(os.listdir(root)):
        ldir = os.path.join(root, line)
        if not os.path.isdir(ldir) or line in RESERVED_DIRS:
            continue
        for n in list_clips_in(ldir):
            paths.append(os.path.join(ldir, n))
        udir = os.path.join(ldir, UNUSED_NAME)
        for n in list_clips_in(udir):
            paths.append(os.path.join(udir, n))
    return paths


def build_model(root):
    """Read disk state and build the JSON model the frontend renders."""
    lines = []
    total_active = 0.0
    for line in sorted(os.listdir(root)):
        ldir = os.path.join(root, line)
        if not os.path.isdir(ldir) or line in RESERVED_DIRS:
            continue

        clips = []
        active_dur = 0.0

        def add(name, folder, active):
            nonlocal active_dur
            path = os.path.join(folder, name)
            meta = ensure_sprite(path)
            dur = meta.get("duration", 0.0)
            clips.append({
                "name": name, "key": meta["key"], "line": line,
                "active": active, "duration": dur, "n": meta["n"],
                "mark": mark_of(name),
            })
            if active:
                active_dur += dur

        for n in list_clips_in(ldir):
            add(n, ldir, True)
        for n in list_clips_in(os.path.join(ldir, UNUSED_NAME)):
            add(n, os.path.join(ldir, UNUSED_NAME), False)

        total_active += active_dur
        lines.append({
            "name": line, "label": line_label(line),
            "clips": clips, "activeDuration": active_dur,
        })

    return {"root": root, "lines": lines, "totalActive": total_active}


def clip_disk_path(root, line, name, active):
    base = os.path.join(root, line)
    return os.path.join(base, name) if active else os.path.join(base, UNUSED_NAME, name)


# ----------------------------------------------------------------------------
# Background scan (generate all thumbnails, with progress)
# ----------------------------------------------------------------------------

def start_scan(root):
    with JOB_LOCK:
        if JOB["running"]:
            return False
        paths = gather_all_clip_paths(root)
        JOB.update(running=True, total=len(paths), done=0,
                   model=None, error=None, root=root)

    def worker():
        try:
            def one(p):
                ensure_sprite(p)
                with JOB_LOCK:
                    JOB["done"] += 1
            workers = max(2, (os.cpu_count() or 4))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(one, paths))
            model = build_model(root)
            with JOB_LOCK:
                JOB["model"] = model
                JOB["running"] = False
        except Exception as e:  # noqa: BLE001
            with JOB_LOCK:
                JOB["error"] = str(e)
                JOB["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


# ----------------------------------------------------------------------------
# Native folder picker (separate process to avoid Tk/threading issues)
# ----------------------------------------------------------------------------

def pick_folder():
    prompt = "Choose the folder of clips to organize"
    # macOS: use a native AppleScript dialog (no Tk dependency, always available)
    if sys.platform == "darwin":
        script = (
            'try\n'
            f'  POSIX path of (choose folder with prompt "{prompt}")\n'
            'on error\n'
            '  return ""\n'
            'end try\n'
        )
        rc, out, _ = _run(["osascript", "-e", script])
        return out.strip().rstrip("/") if rc == 0 else ""
    # Windows / Linux: Tk file dialog in a subprocess
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        f"p = filedialog.askdirectory(title='{prompt}')\n"
        "print(p or '')\n"
    )
    rc, out, _ = _run([sys.executable, "-c", code])
    return out.strip() if rc == 0 else ""


# ----------------------------------------------------------------------------
# Packaging
# ----------------------------------------------------------------------------

def _package_file_list(root):
    """All files to include, excluding macOS junk."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_junk(d)]
        for fn in filenames:
            if fn.startswith("._") or fn == ".DS_Store":
                continue
            files.append(os.path.join(dirpath, fn))
    return files


def start_package(root):
    """Zip the folder in a background thread, with progress. Uses ZIP_STORED
    (video is already compressed) and writes atomically to a .partial file
    that is renamed only after the archive is written and verified."""
    root = os.path.abspath(root)
    with PKG_LOCK:
        if PKG["running"]:
            return False
        files = _package_file_list(root)
        PKG.update(running=True, total=len(files), done=0,
                   zip=None, error=None, bytes=0, count=0)

    def worker():
        base = os.path.basename(root)
        final = os.path.join(os.path.dirname(root), base + ".zip")
        partial = final + ".partial"
        try:
            if os.path.exists(partial):
                os.remove(partial)
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for full in files:
                    try:
                        arc = os.path.join(base, os.path.relpath(full, root))
                        zf.write(full, arc)
                    except (OSError, ValueError):
                        pass  # a file vanished mid-package; skip it
                    with PKG_LOCK:
                        PKG["done"] += 1
            # verify the central directory is readable
            with zipfile.ZipFile(partial) as zf:
                count = len(zf.infolist())
            if os.path.exists(final):
                os.remove(final)
            os.replace(partial, final)
            with PKG_LOCK:
                PKG.update(running=False, zip=final,
                           bytes=os.path.getsize(final), count=count)
        except Exception as e:  # noqa: BLE001
            with PKG_LOCK:
                PKG.update(running=False, error=str(e))
            try:
                if os.path.exists(partial):
                    os.remove(partial)
            except OSError:
                pass

    threading.Thread(target=worker, daemon=True).start()
    return True


def open_in_viewer(path):
    if not os.path.exists(path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------------
# Stage 1 (triage) + assets
# ----------------------------------------------------------------------------

CONTENT_TYPES = {
    ".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/x-m4v",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".bmp": "image/bmp", ".heic": "image/heic", ".tiff": "image/tiff",
}


def content_type_for(name):
    return CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


def safe_join(root, rel):
    """Join rel onto root, refusing anything that escapes root."""
    root = os.path.abspath(root)
    full = os.path.abspath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path escapes root")
    return full


def loose_clips(root):
    """Video files sitting directly in the root (not yet sorted into a line)."""
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root)
                  if is_video(n) and os.path.isfile(os.path.join(root, n)))


def line_numbers(root):
    """Existing L<n> line-folder numbers in the root, sorted ascending."""
    nums = set()
    if os.path.isdir(root):
        for n in os.listdir(root):
            if os.path.isdir(os.path.join(root, n)):
                m = re.fullmatch(r"[Ll]\s*0*(\d+)", n.strip())
                if m:
                    nums.add(int(m.group(1)))
    return sorted(nums)


def asset_dir(root, kind):
    return os.path.join(root, ASSETS_DIR, ASSET_KINDS[kind])


def list_assets(root):
    out = {}
    for kind in ASSET_KINDS:
        d = asset_dir(root, kind)
        out[kind] = (sorted(n for n in os.listdir(d)
                            if os.path.isfile(os.path.join(d, n)) and not is_junk(n))
                     if os.path.isdir(d) else [])
    return out


def folder_state(root):
    """Phase 1 if there are loose clips to sort, else phase 2."""
    root = os.path.abspath(root)
    loose = loose_clips(root)
    return {
        "root": root,
        "phase": 1 if loose else 2,
        "loose": loose,
        "lines": line_numbers(root),
        "assets": list_assets(root),
    }


def assign_clip(root, name, line_num, unused, mark):
    """Move a loose root clip into L<n>/ (or L<n>/Unused/), applying a mark rename."""
    src = safe_join(root, name)
    if os.path.dirname(src) != os.path.abspath(root) or not os.path.isfile(src):
        raise FileNotFoundError("clip not found in root")
    dest_dir = os.path.join(root, f"L{int(line_num)}")
    if unused:
        dest_dir = os.path.join(dest_dir, UNUSED_NAME)
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(name)[1]
    base = MARK_BASE[mark] if mark in MARK_BASE else os.path.splitext(name)[0]
    cand = os.path.join(dest_dir, base + ext)
    i = 2
    while os.path.exists(cand) and os.path.abspath(cand) != os.path.abspath(src):
        cand = os.path.join(dest_dir, f"{base} {i}{ext}")
        i += 1
    shutil.move(src, cand)
    return os.path.basename(cand)


def save_asset(root, kind, filename, data):
    if kind not in ASSET_KINDS:
        raise ValueError("unknown asset kind")
    name = os.path.basename(filename.replace("\\", "/"))
    if not name or name.startswith("."):
        raise ValueError("invalid filename")
    d = asset_dir(root, kind)
    os.makedirs(d, exist_ok=True)
    stem, ext = os.path.splitext(name)
    dest = os.path.join(d, name)
    i = 2
    while os.path.exists(dest):
        dest = os.path.join(d, f"{stem} {i}{ext}")
        i += 1
    with open(dest, "wb") as f:
        f.write(data)
    return os.path.basename(dest)


def remove_asset(root, kind, name):
    if kind not in ASSET_KINDS:
        raise ValueError("unknown asset kind")
    p = os.path.join(asset_dir(root, kind), os.path.basename(name))
    if os.path.isfile(p):
        os.remove(p)


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    # -- helpers ----------------------------------------------------------
    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _serve_file(self, path, ctype):
        if not os.path.isfile(path):
            self._send(404, {"error": "not found"})
            return
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, ctype)

    def _query(self):
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    def _serve_range(self, path, ctype):
        """Serve a file with HTTP Range support (needed for <video> seeking).
        Tolerant of files that vanish or live on flaky external drives
        (e.g. a USB drive that drops out mid-read → OSError)."""
        try:
            size = os.path.getsize(path)
            fh = open(path, "rb")
        except OSError:
            # missing, moved, or the drive went away before we sent any headers
            return self._send(404, {"error": "not available"})

        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[6:].partition("-")
                start = int(s) if s else 0
                end = int(e) if e else size - 1
                start, end = max(0, start), min(end, size - 1)
                status = 206 if start <= end else 200
                if status != 206:
                    start, end = 0, size - 1
            except ValueError:
                start, end, status = 0, size - 1, 200
        length = end - start + 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            fh.seek(start)
            remaining = length
            while remaining > 0:
                try:
                    buf = fh.read(min(262144, remaining))
                except OSError:
                    break  # drive hiccup mid-stream — stop quietly
                if not buf:
                    break
                self.wfile.write(buf)
                remaining -= len(buf)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client went away or socket error — nothing more to do
        finally:
            fh.close()

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client aborted (e.g. <video> cancelled a range request)
        except Exception as e:  # noqa: BLE001 — never crash a request thread
            try:
                self._send(500, {"error": str(e)})
            except Exception:
                pass

    def _get(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._serve_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if p.startswith("/static/"):
            fp = os.path.join(STATIC_DIR, os.path.basename(p))
            ctype = ("text/css" if fp.endswith(".css")
                     else "application/javascript" if fp.endswith(".js")
                     else "application/octet-stream")
            return self._serve_file(fp, ctype)
        if p.startswith("/thumb/"):
            key = os.path.basename(p).split(".")[0]
            return self._serve_file(sprite_path(key), "image/jpeg")
        if p == "/api/scan-status":
            with JOB_LOCK:
                resp = {"running": JOB["running"], "total": JOB["total"],
                        "done": JOB["done"], "error": JOB["error"],
                        "model": JOB["model"] if not JOB["running"] else None}
            return self._send(200, resp)
        if p == "/api/package-status":
            with PKG_LOCK:
                resp = dict(PKG)
            return self._send(200, resp)
        if p == "/api/health":
            return self._send(200, {"ffmpeg": have_tools()})
        if p == "/api/clip":
            q = self._query()
            try:
                full = safe_join(q["root"], q.get("path", ""))
            except (KeyError, ValueError):
                return self._send(400, {"error": "bad path"})
            return self._serve_range(full, content_type_for(full))
        if p == "/api/preview":
            q = self._query()
            try:
                full = safe_join(q["root"], q.get("path", ""))
            except (KeyError, ValueError):
                return self._send(400, {"error": "bad path"})
            out = ensure_preview(full)
            if not out:
                return self._send(500, {"error": "preview generation failed"})
            return self._serve_range(out, "video/mp4")
        if p == "/api/asset-file":
            q = self._query()
            try:
                full = os.path.join(asset_dir(q["root"], q["kind"]), os.path.basename(q["name"]))
            except (KeyError, ValueError):
                return self._send(400, {"error": "bad asset"})
            return self._serve_range(full, content_type_for(full))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path

        # asset upload sends raw file bytes, not JSON — handle before reading body
        if p == "/api/asset-add":
            return self._handle_asset_add()

        body = self._json_body()
        try:
            if p == "/api/choose-folder":
                return self._send(200, {"path": pick_folder()})

            if p == "/api/folder-state":
                root = body.get("path", "").strip()
                if not root or not os.path.isdir(root):
                    return self._send(400, {"error": "Folder does not exist."})
                return self._send(200, folder_state(os.path.abspath(root)))

            if p == "/api/assign":
                root = body["root"]
                new = assign_clip(root, body["name"], body["line"],
                                  bool(body.get("unused")), body.get("mark"))
                return self._send(200, {"assigned": new, "state": folder_state(root)})

            if p == "/api/asset-remove":
                remove_asset(body["root"], body["kind"], body["name"])
                return self._send(200, {"assets": list_assets(body["root"])})

            if p == "/api/scan":
                root = body.get("path", "").strip()
                if not root or not os.path.isdir(root):
                    return self._send(400, {"error": "Folder does not exist."})
                if not have_tools():
                    return self._send(400, {"error": "ffmpeg/ffprobe not found on PATH."})
                start_scan(os.path.abspath(root))
                return self._send(200, {"started": True})

            if p == "/api/refresh":
                root = body.get("path", "").strip()
                if not os.path.isdir(root):
                    return self._send(400, {"error": "Folder does not exist."})
                return self._send(200, {"model": build_model(os.path.abspath(root))})

            if p == "/api/toggle":
                return self._mutate_toggle(body)

            if p == "/api/move":
                return self._mutate_move(body)

            if p == "/api/rename":
                return self._mutate_rename(body)

            if p == "/api/open":
                root = body["root"]; line = body["line"]
                name = body["name"]; active = bool(body["active"])
                ok = open_in_viewer(clip_disk_path(root, line, name, active))
                return self._send(200, {"ok": ok})

            if p == "/api/package":
                root = body.get("path", "").strip()
                if not os.path.isdir(root):
                    return self._send(400, {"error": "Folder does not exist."})
                start_package(os.path.abspath(root))
                return self._send(200, {"started": True})

        except KeyError as e:
            return self._send(400, {"error": f"missing field {e}"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    # -- mutations --------------------------------------------------------
    def _mutate_toggle(self, body):
        root, line, name = body["root"], body["line"], body["name"]
        new_active = bool(body["active"])
        src = clip_disk_path(root, line, name, not new_active)
        dst = clip_disk_path(root, line, name, new_active)
        if not os.path.exists(src):
            return self._send(400, {"error": "Source clip not found."})
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return self._send(200, {"model": build_model(root)})

    def _mutate_move(self, body):
        root = body["root"]; name = body["name"]
        from_line, to_line = body["fromLine"], body["toLine"]
        active = bool(body["active"])
        src = clip_disk_path(root, from_line, name, active)
        dst = clip_disk_path(root, to_line, name, active)
        if not os.path.exists(src):
            return self._send(400, {"error": "Source clip not found."})
        if os.path.abspath(src) == os.path.abspath(dst):
            return self._send(200, {"model": build_model(root)})
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            return self._send(400, {"error": f"'{name}' already exists in {to_line}."})
        shutil.move(src, dst)
        return self._send(200, {"model": build_model(root)})

    def _mutate_rename(self, body):
        """Rename a clip to a mark base ("U USED" etc.) or to an explicit target
        name (used to restore the original). Returns the resulting name so the
        frontend can track reversibility for the session."""
        root, line, name = body["root"], body["line"], body["name"]
        active = bool(body["active"])
        mark = body.get("mark")
        target = body.get("target")
        src = clip_disk_path(root, line, name, active)
        if not os.path.exists(src):
            return self._send(400, {"error": "Source clip not found."})
        folder = os.path.dirname(src)

        if mark in MARK_BASE:
            base, ext = MARK_BASE[mark], os.path.splitext(name)[1]
        elif target:
            base, ext = os.path.splitext(target)
        else:
            return self._send(400, {"error": "Nothing to rename to."})

        # find a free target name, tolerating collisions ("U USED 2", ...)
        cand = os.path.join(folder, base + ext)
        i = 2
        while os.path.exists(cand) and os.path.abspath(cand) != os.path.abspath(src):
            cand = os.path.join(folder, f"{base} {i}{ext}")
            i += 1

        if os.path.abspath(cand) != os.path.abspath(src):
            old_key = clip_key(src)
            shutil.move(src, cand)
            # carry the cached filmstrip over to the new name (no re-extraction)
            new_key = clip_key(cand)
            if new_key != old_key and os.path.exists(sprite_path(old_key)):
                try:
                    shutil.copyfile(sprite_path(old_key), sprite_path(new_key))
                    if os.path.exists(meta_path(old_key)):
                        with open(meta_path(old_key), "r", encoding="utf-8") as f:
                            mt = json.load(f)
                        mt["key"] = new_key
                        with open(meta_path(new_key), "w", encoding="utf-8") as f:
                            json.dump(mt, f)
                except (OSError, ValueError):
                    pass

        return self._send(200, {
            "model": build_model(root),
            "newName": os.path.basename(cand), "oldName": name,
        })

    # -- asset upload (raw bytes) -----------------------------------------
    def _handle_asset_add(self):
        q = self._query()
        root, kind, name = q.get("root"), q.get("kind"), q.get("name")
        if not root or not os.path.isdir(root) or kind not in ASSET_KINDS or not name:
            return self._send(400, {"error": "bad asset upload"})
        n = int(self.headers.get("Content-Length", 0) or 0)
        data = self.rfile.read(n) if n else b""
        try:
            saved = save_asset(root, kind, name, data)
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        return self._send(200, {"saved": saved, "assets": list_assets(root)})


def main():
    if not have_tools():
        print("WARNING: ffmpeg/ffprobe not found on PATH. Previews and durations will not work.")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Clip Organizer running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
