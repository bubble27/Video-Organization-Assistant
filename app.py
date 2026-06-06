#!/usr/bin/env python3
"""
Clip Organizer — a local web app to sort .MOV clips into "lines".

Backend: pure Python standard library (no pip installs needed).
External dependency: ffmpeg + ffprobe must be on PATH.

Run:  python3 app.py
It starts a local server and opens your browser.
"""

import os
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
        if not os.path.isdir(ldir) or line == UNUSED_NAME:
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
        if not os.path.isdir(ldir) or line == UNUSED_NAME:
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
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "p = filedialog.askdirectory(title='Choose the folder of clips to organize')\n"
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

    # -- routing ----------------------------------------------------------
    def do_GET(self):
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
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        body = self._json_body()
        try:
            if p == "/api/choose-folder":
                return self._send(200, {"path": pick_folder()})

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
