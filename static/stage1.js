"use strict";
// Stage 1: review each loose clip and file it into a line.
// Depends on globals from app.js: $, el, api, fmt, ROOT, ASSETS, showStage,
// setPhaseTag, enterStage2, uploadAssets, assetThumb, removeAsset.

const S1 = {
  clips: [], idx: 0, lines: [1], unused: false, mark: null, busy: false,
  preferPreview: false, // set once we learn this browser can't play raw HEVC
  mode: "raw",          // "raw" | "preview" for the clip currently loaded
  rawTimer: null,       // stall watchdog while a raw clip tries to load
};

function rawUrl(name) {
  return `/api/clip?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(name)}`;
}
function previewUrl(name) {
  return `/api/preview?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(name)}`;
}

function startStage1(state) {
  showStage("stage1");
  setPhaseTag(1);
  $("#packageBtn").classList.add("hidden");
  hideStatus();
  S1.preferPreview = false; // start optimistic; we try the raw clip first
  S1.clips = state.loose.slice();
  S1.lines = (state.lines && state.lines.length)
    ? state.lines.map((l) => ({ n: l.n, name: l.name || "" }))
    : [{ n: 1, name: "" }];
  S1.idx = 0;
  S1.unused = false;
  S1.mark = null;
  renderLineButtons();
  showCurrent();
}

function s1Current() { return S1.clips[S1.idx]; }

function setLoading(on) { $("#s1Loading").classList.toggle("hidden", !on); }

function showCurrent() {
  const vid = $("#s1Video");
  const name = s1Current();
  S1.unused = false;
  S1.mark = null;
  syncModifiers();

  if (!name) { return finishStage1(); }

  $("#s1Done").classList.add("hidden");
  vid.classList.remove("hidden");
  $("#s1Count").textContent = `Clip ${S1.idx + 1} of ${S1.clips.length}`;
  $("#s1Name").textContent = name;
  $("#s1Dur").textContent = "";
  clearRawTimer();

  if (S1.preferPreview) {
    loadPreview(name);
  } else {
    // Try the real clip first (plays instantly in Safari / on macOS).
    S1.mode = "raw";
    setLoading(false);
    vid.src = rawUrl(name);
    vid.load();
    playVid(vid);
    // Watchdog: if the raw clip neither plays nor errors, fall back.
    S1.rawTimer = setTimeout(() => {
      if (S1.mode === "raw" && vid.readyState < 2) fallbackToPreview();
    }, 3500);
  }
  prefetchNextPreview();
}

function loadPreview(name) {
  const vid = $("#s1Video");
  S1.mode = "preview";
  clearRawTimer();
  setLoading(true);              // transcode may take a few seconds
  vid.src = previewUrl(name);
  vid.load();
  playVid(vid);
}

function fallbackToPreview() {
  S1.preferPreview = true;       // remember for the rest of this session
  const name = s1Current();
  if (name) loadPreview(name);
  prefetchNextPreview();
}

function playVid(vid) {
  const p = vid.play();
  if (p && p.catch) p.catch(() => {});
}
function clearRawTimer() {
  if (S1.rawTimer) { clearTimeout(S1.rawTimer); S1.rawTimer = null; }
}

// Warm the next clip's transcoded preview so it's ready when we get there.
function prefetchNextPreview() {
  if (!S1.preferPreview) return;
  const next = S1.clips[S1.idx + 1];
  if (!next) return;
  fetch(previewUrl(next), { headers: { Range: "bytes=0-1" } }).catch(() => {});
}

const s1vid = $("#s1Video");
s1vid.addEventListener("loadedmetadata", function () {
  if (isFinite(this.duration)) $("#s1Dur").textContent = fmt(this.duration);
});
s1vid.addEventListener("loadeddata", () => { clearRawTimer(); setLoading(false); });
s1vid.addEventListener("playing", () => { clearRawTimer(); setLoading(false); });
s1vid.addEventListener("error", function () {
  if (S1.mode === "raw") {
    fallbackToPreview();         // raw couldn't decode → transcoded preview
  } else if (S1.mode === "preview") {
    setLoading(false);
    $("#s1Dur").textContent = "(preview unavailable — try Open ↗)";
  }
  // mode "idle": source was cleared on purpose → ignore
});

// ---- modifiers (flag + marks) --------------------------------------------
function syncModifiers() {
  $("#s1Flag").classList.toggle("active", S1.unused);
  document.querySelectorAll("#s1Marks button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mark === S1.mark));
}
function toggleUnused() { S1.unused = !S1.unused; syncModifiers(); }
function setMark(m) { S1.mark = (S1.mark === m) ? null : m; syncModifiers(); }

$("#s1Flag").addEventListener("click", toggleUnused);
document.querySelectorAll("#s1Marks button").forEach((b) =>
  b.addEventListener("click", () => setMark(b.dataset.mark)));

$("#s1Open").addEventListener("click", () => {
  const name = s1Current();
  if (name) openSidePlayer(name, name);  // loose clip lives in the root
});

$("#s1Skip").addEventListener("click", skipCurrent);
function skipCurrent() {
  if (S1.clips.length <= 1 || S1.busy) return;
  const [name] = S1.clips.splice(S1.idx, 1);
  S1.clips.push(name);            // send to the back of the queue
  if (S1.idx >= S1.clips.length) S1.idx = 0;
  showCurrent();
}

// ---- line buttons ---------------------------------------------------------
function s1Line(n) { return S1.lines.find((l) => l.n === n); }
function nextLineNumber() {
  return (S1.lines.length ? Math.max.apply(null, S1.lines.map((l) => l.n)) : 0) + 1;
}
function mergeLines(stateLines) {
  const byN = {};
  S1.lines.forEach((l) => { byN[l.n] = l; });
  (stateLines || []).forEach((li) => { byN[li.n] = { n: li.n, name: li.name || "" }; });
  S1.lines = Object.values(byN).sort((a, b) => a.n - b.n);
}

function renderLineButtons() {
  const wrap = $("#s1Lines");
  wrap.innerHTML = "";
  S1.lines.slice().sort((a, b) => a.n - b.n).forEach((l) => {
    const b = el("button", "s1-line");
    const label = l.name ? `L${l.n} · ${l.name}` : `Line ${l.n}`;
    b.innerHTML = `<span class="s1-line-key">${l.n <= 9 ? l.n : "·"}</span>` +
      `<span class="s1-line-label">${label}</span>` +
      `<span class="s1-line-edit" title="Name this line">✎</span>`;
    b.addEventListener("click", () => assignTo(l.n));
    b.querySelector(".s1-line-edit").addEventListener("click", (e) => {
      e.stopPropagation(); renameLine(l.n);
    });
    wrap.appendChild(b);
  });
  const add = el("button", "s1-line s1-add"); add.textContent = "＋";
  add.title = "Add another line";
  add.addEventListener("click", () => {
    const n = nextLineNumber();
    const name = (window.prompt(`Name for Line ${n} (optional, e.g. "M2"):`, "") || "").trim();
    S1.lines.push({ n, name });
    renderLineButtons();
  });
  wrap.appendChild(add);
}

function renameLine(n) {
  const l = s1Line(n);
  if (!l) return;
  const nn = window.prompt(`Line ${n} name (blank to clear):`, l.name || "");
  if (nn === null) return;              // cancelled
  l.name = nn.trim();
  renderLineButtons();
  // sync the folder on disk if it already exists
  api("/api/rename-line", { root: ROOT, n, name: l.name })
    .then((res) => { mergeLines(res.state.lines); renderLineButtons(); })
    .catch((e) => showStatus("⚠️ " + e.message, "error"));
}

async function assignTo(n) {
  if (S1.busy) return;
  const name = s1Current();
  if (!name) return;
  let entry = s1Line(n);
  if (!entry) { entry = { n, name: "" }; S1.lines.push(entry); renderLineButtons(); }
  S1.busy = true;

  // stop the player so the file isn't being range-requested while it moves
  const vid = $("#s1Video");
  S1.mode = "idle"; clearRawTimer();   // ignore the error fired by clearing src
  vid.pause(); vid.removeAttribute("src"); vid.load();

  try {
    const res = await api("/api/assign",
      { root: ROOT, name, line: n, lineName: entry.name, unused: S1.unused, mark: S1.mark });
    mergeLines(res.state.lines);          // adopt server's folder names
    S1.clips.splice(S1.idx, 1);           // remove the filed clip from the queue
    if (S1.idx >= S1.clips.length) S1.idx = 0;
    renderLineButtons();
    showCurrent();
  } catch (e) {
    showStatus("⚠️ " + e.message, "error");
  } finally {
    S1.busy = false;
  }
}

// ---- keyboard -------------------------------------------------------------
document.addEventListener("keydown", (e) => {
  if ($("#stage1").classList.contains("hidden")) return;
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (e.key >= "1" && e.key <= "9") { e.preventDefault(); assignTo(parseInt(e.key, 10)); }
  else if (k === "f") { e.preventDefault(); toggleUnused(); }
  else if (k === "s") { e.preventDefault(); skipCurrent(); }
  else if (k === "m") { e.preventDefault(); setMark("main"); }
  else if (k === "b") { e.preventDefault(); setMark("sub"); }
  else if (k === "o") { e.preventDefault(); setMark("outro"); }
  else if (k === "+" || k === "=") {
    e.preventDefault();
    S1.lines.push({ n: nextLineNumber(), name: "" });
    renderLineButtons();
  }
});

// ---- finish → asset prompts → Stage 2 ------------------------------------
function finishStage1() {
  const vid = $("#s1Video");
  S1.mode = "idle"; clearRawTimer();
  vid.pause(); vid.removeAttribute("src"); vid.load();
  vid.classList.add("hidden");
  $("#s1Count").textContent = "";
  $("#s1Name").textContent = "";
  $("#s1Done").classList.remove("hidden");
  setTimeout(startAssetPrompts, 500);
}

function startAssetPrompts() {
  promptAsset({
    kind: "thumbnails", step: "Step 1 of 2", title: "Add thumbnails",
    sub: "Drop your thumbnail image files here (or click to choose). Saved to Assets/Thumbnails.",
    next: () => promptAsset({
      kind: "icons", step: "Step 2 of 2", title: "Add icons",
      sub: "Drop your icon files here (or click to choose). Saved to Assets/Icons.",
      next: null,   // last step → Finish button enters Stage 2
    }),
  });
}

function promptAsset(cfg) {
  showStage("assetPrompt");
  $("#apStep").textContent = cfg.step;
  $("#apTitle").textContent = cfg.title;
  $("#apSub").textContent = cfg.sub;
  $("#apNext").textContent = cfg.next ? "Next →" : "Finish →";

  const drop = $("#apDrop");
  drop.onclick = () => pickAssets(cfg.kind, () => renderApList(cfg.kind));
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("drag-over"); };
  drop.ondragleave = () => drop.classList.remove("drag-over");
  drop.ondrop = async (e) => {
    e.preventDefault(); drop.classList.remove("drag-over");
    if (e.dataTransfer.files.length) {
      await uploadAssets(cfg.kind, e.dataTransfer.files);
      renderApList(cfg.kind);
    }
  };
  $("#apSkip").onclick = () => cfg.next ? cfg.next() : enterStage2();
  $("#apNext").onclick = () => cfg.next ? cfg.next() : enterStage2();

  renderApList(cfg.kind);
}

function renderApList(kind) {
  const list = $("#apList");
  list.innerHTML = "";
  (ASSETS[kind] || []).forEach((name) => {
    const chip = assetThumb(kind, name);
    const rm = el("button", "asset-rm"); rm.textContent = "×"; rm.title = "Remove";
    rm.addEventListener("click", async (e) => {
      e.stopPropagation(); await removeAsset(kind, name); renderApList(kind);
    });
    chip.appendChild(rm);
    list.appendChild(chip);
  });
}
