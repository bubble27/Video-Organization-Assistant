"use strict";
// Stage 1: review each loose clip and file it into a line.
// Depends on globals from app.js: $, el, api, fmt, ROOT, ASSETS, showStage,
// setPhaseTag, enterStage2, uploadAssets, assetThumb, removeAsset.

const S1 = { clips: [], idx: 0, lines: [1], unused: false, mark: null, busy: false };

function startStage1(state) {
  showStage("stage1");
  setPhaseTag(1);
  $("#packageBtn").classList.add("hidden");
  hideStatus();
  S1.clips = state.loose.slice();
  S1.lines = state.lines && state.lines.length ? state.lines.slice() : [1];
  S1.idx = 0;
  S1.unused = false;
  S1.mark = null;
  renderLineButtons();
  showCurrent();
}

function s1Current() { return S1.clips[S1.idx]; }

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
  vid.src = `/api/clip?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(name)}`;
  vid.load();
  const play = vid.play();
  if (play && play.catch) play.catch(() => {});
}

$("#s1Video").addEventListener("loadedmetadata", function () {
  if (isFinite(this.duration)) $("#s1Dur").textContent = fmt(this.duration);
});
$("#s1Video").addEventListener("error", function () {
  $("#s1Dur").textContent = "(can't preview HEVC here — use Open ↗ / Safari on Mac)";
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
  if (name) api("/api/open", { root: ROOT, line: "", name, active: true }).catch(() => {});
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
function renderLineButtons() {
  const wrap = $("#s1Lines");
  wrap.innerHTML = "";
  S1.lines.slice().sort((a, b) => a - b).forEach((n) => {
    const b = el("button", "s1-line");
    b.innerHTML = `<span class="s1-line-key">${n <= 9 ? n : "·"}</span> Line ${n}`;
    b.addEventListener("click", () => assignTo(n));
    wrap.appendChild(b);
  });
  const add = el("button", "s1-line s1-add"); add.textContent = "＋";
  add.title = "Add another line";
  add.addEventListener("click", () => {
    const next = (S1.lines.length ? Math.max.apply(null, S1.lines) : 0) + 1;
    S1.lines.push(next);
    renderLineButtons();
  });
  wrap.appendChild(add);
}

async function assignTo(n) {
  if (S1.busy) return;
  const name = s1Current();
  if (!name) return;
  if (!S1.lines.includes(n)) { S1.lines.push(n); renderLineButtons(); }
  S1.busy = true;

  // stop the player so the file isn't being range-requested while it moves
  const vid = $("#s1Video");
  vid.pause(); vid.removeAttribute("src"); vid.load();

  try {
    const res = await api("/api/assign",
      { root: ROOT, name, line: n, unused: S1.unused, mark: S1.mark });
    // merge any newly-created line folders the server reports
    const merged = new Set(S1.lines);
    (res.state.lines || []).forEach((x) => merged.add(x));
    S1.lines = Array.from(merged).sort((a, b) => a - b);
    S1.clips.splice(S1.idx, 1);          // remove the filed clip from the queue
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
    const next = (S1.lines.length ? Math.max.apply(null, S1.lines) : 0) + 1;
    S1.lines.push(next); renderLineButtons();
  }
});

// ---- finish → asset prompts → Stage 2 ------------------------------------
function finishStage1() {
  const vid = $("#s1Video");
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
