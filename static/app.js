"use strict";

const LINE_COLORS = ["#4ea1ff", "#ffb347", "#46d18a", "#c77dff",
                     "#ff7a9c", "#5ad1d1", "#f2c14e", "#8d99ae"];

let MODEL = null;     // current model from backend
let ROOT = "";        // active root folder
let pollTimer = null;
const unusedOpen = {}; // line name -> bool, persisted across re-renders
const origNames = {};  // clip.key -> original filename, this session only (cleared on reload)
let painted = false;   // first-paint flag, gates intro animation
let ASSETS = { thumbnails: [], icons: [] }; // current folder's assets
let scanRendered = false;     // Stage 2 has been rendered for the current scan
let cardByKey = {};           // clip.key -> card element, for live preview patching

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };

function fmt(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60), mm = m % 60;
    return `${h}:${String(mm).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function lineColor(i) { return LINE_COLORS[i % LINE_COLORS.length]; }

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function showStatus(html, kind) {
  const s = $("#status");
  s.className = "status" + (kind ? " " + kind : "");
  s.innerHTML = html;
}
function hideStatus() { $("#status").className = "status hidden"; }

// ---------------------------------------------------------------------------
// Load / scan
// ---------------------------------------------------------------------------
async function chooseFolder() {
  try {
    const { path } = await api("/api/choose-folder", {});
    if (path) { $("#pathInput").value = path; load(); } // auto-load on selection
  } catch (e) { /* dialog unavailable; user can paste a path */ }
}

async function load() {
  const path = $("#pathInput").value.trim();
  if (!path) { showStatus("Enter or choose a folder first.", "error"); return; }
  let state;
  try {
    state = await api("/api/folder-state", { path });
  } catch (e) {
    showStatus("⚠️ " + e.message, "error");
    return;
  }
  ROOT = state.root;
  ASSETS = state.assets || { thumbnails: [], icons: [] };
  if (state.phase === 1) startStage1(state);   // defined in stage1.js
  else enterStage2();
}

// ---- stage routing --------------------------------------------------------
function showStage(name) {
  ["stage1", "assetPrompt", "stage2"].forEach((id) =>
    $("#" + id).classList.toggle("hidden", id !== name));
}

function setPhaseTag(n) {
  const tag = $("#phaseTag");
  tag.textContent = n === 1 ? "Stage 1 · Sort into lines" : "Stage 2 · Organize";
  tag.classList.remove("hidden");
}

async function enterStage2() {
  showStage("stage2");
  setPhaseTag(2);
  $("#packageBtn").classList.remove("hidden");
  $("#packageBtn").disabled = true;
  painted = false;
  scanRendered = false;
  Object.keys(origNames).forEach((k) => delete origNames[k]);
  try {
    const st = await api("/api/folder-state", { path: ROOT });
    ASSETS = st.assets;
  } catch (e) { /* keep whatever we have */ }
  renderAssetBar();
  try {
    await api("/api/scan", { path: ROOT });
    pollScan();
  } catch (e) {
    showStatus("⚠️ " + e.message, "error");
  }
}

function pollScan() {
  clearTimeout(pollTimer);
  api("/api/scan-status").then((st) => {
    if (st.error) { showStatus("⚠️ " + st.error, "error"); }
    if (st.model) {
      if (!scanRendered) {
        // open Stage 2 immediately with all clips (previews fill in below)
        MODEL = st.model; ROOT = st.model.root;
        $("#packageBtn").disabled = false;
        render();
        scanRendered = true;
      } else {
        applyScanUpdate(st.model);  // patch durations + thumbnails as they land
      }
    }
    if (st.running) {
      const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
      showStatus(
        `Generating previews… ${st.done}/${st.total}` +
        `<span class="progress"><i style="width:${pct}%"></i></span>`);
      pollTimer = setTimeout(pollScan, 600);
    } else {
      hideStatus();
      $("#packageBtn").disabled = false;
    }
  }).catch((e) => showStatus("⚠️ " + e.message, "error"));
}

// Patch already-rendered cards as their previews/durations become available,
// without rebuilding the DOM (preserves hover, menus, scroll).
function applyScanUpdate(model) {
  MODEL = model;
  model.lines.forEach((ln) => {
    ln.clips.forEach((c) => {
      const card = cardByKey[c.key];
      if (card && c.ready && card.dataset.ready !== "1") markCardReady(card, c);
    });
    const box = $(`.line-box[data-line="${CSS.escape(ln.name)}"]`);
    if (box) {
      const d = box.querySelector(".line-dur");
      if (d) d.textContent = fmt(ln.activeDuration);
    }
  });
  renderTimeline();
}

function markCardReady(card, c) {
  card.dataset.ready = "1";
  card.classList.remove("loading");
  const film = card.querySelector(".film");
  if (film) {
    film.style.backgroundImage = `url(/thumb/${c.key}.jpg)`;
    film.style.backgroundSize = `${c.n * 100}% 100%`;
  }
  const t = card.querySelector(".badge .t");
  if (t) t.textContent = fmt(c.duration);
}

function applyModel(data) {
  if (data && data.model) { MODEL = data.model; render(); }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function render() {
  const wrap = $("#lines");
  wrap.innerHTML = "";
  cardByKey = {};
  if (!MODEL || !MODEL.lines.length) {
    const e = el("div", "empty-hint");
    e.textContent = "No line subfolders found in this folder.";
    wrap.appendChild(e);
    renderTimeline();
    return;
  }
  const intro = !painted; painted = true;
  MODEL.lines.forEach((line, idx) => wrap.appendChild(renderLine(line, idx, intro)));
  renderTimeline();
}

function renderLine(line, idx, intro) {
  const box = el("div", "line-box");
  box.dataset.line = line.name;
  if (intro) { box.classList.add("intro"); box.style.animationDelay = (idx * 70) + "ms"; }

  const head = el("div", "line-head");
  const h = el("h2"); h.textContent = line.label;
  const dur = el("span", "line-dur"); dur.textContent = fmt(line.activeDuration);
  const active = line.clips.filter((c) => c.active);
  const unused = line.clips.filter((c) => !c.active);
  const meta = el("span", "line-meta");
  meta.textContent = `${active.length} active${unused.length ? ` · ${unused.length} unused` : ""}`;
  head.append(h, dur, meta);
  box.appendChild(head);

  const grid = el("div", "clip-grid");
  active.forEach((c) => grid.appendChild(renderClip(c, idx)));
  box.appendChild(grid);

  if (unused.length) {
    const open = !!unusedOpen[line.name];
    const sec = el("div", "unused-section" + (open ? "" : " collapsed"));
    const uh = el("div", "unused-head");
    uh.innerHTML = `<span class="chev">▾</span> Unused (${unused.length})`;
    uh.addEventListener("click", () => {
      sec.classList.toggle("collapsed");
      unusedOpen[line.name] = !sec.classList.contains("collapsed");
    });
    const ug = el("div", "clip-grid");
    unused.forEach((c) => ug.appendChild(renderClip(c, idx)));
    sec.append(uh, ug);
    box.appendChild(sec);
  }

  // drag target
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag-over"); });
  box.addEventListener("dragleave", () => box.classList.remove("drag-over"));
  box.addEventListener("drop", (e) => {
    e.preventDefault();
    box.classList.remove("drag-over");
    const payload = safeParse(e.dataTransfer.getData("text/plain"));
    if (payload && payload.line !== line.name) moveClip(payload, line.name);
  });

  return box;
}

function renderClip(c, lineIdx) {
  const ready = c.ready !== false; // older models without the flag are treated as ready
  const card = el("div", "clip" + (c.active ? "" : " inactive") + (ready ? "" : " loading"));
  card.draggable = true;
  card.dataset.name = c.name;
  card.dataset.ready = ready ? "1" : "0";
  cardByKey[c.key] = card;

  const film = el("div", "film");
  if (ready) {
    film.style.backgroundImage = `url(/thumb/${c.key}.jpg)`;
    film.style.backgroundSize = `${c.n * 100}% 100%`;
  }
  card.appendChild(film);

  const badge = el("div", "badge");
  const glyph = { main: '<span class="mk mk-main">★</span>',
                  sub: '<span class="mk mk-sub">SUB</span>',
                  outro: '<span class="mk mk-outro">OUTRO</span>' }[c.mark] || "";
  badge.innerHTML = glyph + `<span class="t">${ready ? fmt(c.duration) : "…"}</span>`;
  const name = el("div", "name"); name.textContent = c.name;
  const dot = el("div", "scrub-dot");
  card.append(badge, name, dot);
  if (!c.active) {
    const toggle = el("div", "toggle"); toggle.textContent = "+";
    card.appendChild(toggle);
  }

  // three-dot menu (visible on hover)
  const menuBtn = el("div", "menu-btn"); menuBtn.textContent = "⋯";
  menuBtn.addEventListener("mousedown", (e) => e.stopPropagation()); // don't start a drag
  menuBtn.addEventListener("click", (e) => { e.stopPropagation(); openMenu(e, c); });
  menuBtn.addEventListener("dblclick", (e) => e.stopPropagation());
  card.appendChild(menuBtn);

  // hover scrub through filmstrip frames (only once the preview is ready)
  card.addEventListener("mousemove", (e) => {
    if (card.dataset.ready !== "1") return;
    const r = card.getBoundingClientRect();
    const f = Math.min(0.9999, Math.max(0, (e.clientX - r.left) / r.width));
    const i = Math.floor(f * c.n);
    film.style.backgroundPositionX = (c.n > 1 ? (i / (c.n - 1)) * 100 : 0) + "%";
    card.classList.add("scrubbing");
    dot.style.left = (f * 100) + "%";
    dot.style.width = "2px";
    showTip(e, `<b>${c.name}</b><br>${card.querySelector(".badge .t").textContent} · ${c.active ? "active" : "unused"}`);
  });
  card.addEventListener("mouseleave", () => {
    film.style.backgroundPositionX = "0%";
    card.classList.remove("scrubbing");
    hideTip();
  });

  // click = toggle, double-click = open in default viewer
  let clickTimer = null;
  card.addEventListener("click", () => {
    if (card._dragged) { card._dragged = false; return; }
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => toggleClip(c), 220);
  });
  card.addEventListener("dblclick", () => {
    clearTimeout(clickTimer);
    openSidePlayer(c.name, clipRelPath(c));   // in-app player, not the OS one
  });

  card.addEventListener("dragstart", (e) => {
    card.classList.add("dragging");
    card._dragged = true;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", JSON.stringify(
      { name: c.name, line: c.line, active: c.active }));
  });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));

  return card;
}

// Persisted segment elements, keyed by clip.key, so widths animate (lerp)
// across re-renders instead of the whole bar being rebuilt.
const segEls = {};

function renderTimeline() {
  const tl = $("#timeline");
  const total = MODEL ? MODEL.totalActive : 0;
  $("#totalDuration").textContent = fmt(total);

  const active = [];
  if (MODEL) MODEL.lines.forEach((ln, i) =>
    ln.clips.filter((c) => c.active).forEach((c) => active.push({ c, i, label: ln.label })));
  $("#clipCount").textContent = active.length ? `${active.length} clips` : "";

  const want = new Set(active.map((a) => a.c.key));

  // 1. create a segment for any new clip (starts at 0% so it can grow in)
  const fresh = [];
  active.forEach(({ c }) => {
    if (segEls[c.key]) return;
    const seg = el("div", "seg");
    seg._key = c.key;
    seg.style.width = "0%";
    seg.addEventListener("mousemove", (e) => showTip(e, segTip(seg._d)));
    seg.addEventListener("mouseenter", () => highlightCard(seg._d.name, true));
    seg.addEventListener("mouseleave", () => { hideTip(); highlightCard(seg._d.name, false); });
    segEls[c.key] = seg;
    tl.appendChild(seg);
    fresh.push(seg);
  });

  // 2. order active segments, stepping over departing ones so survivors
  //    keep their position (a removed segment shrinks in place, not at the front)
  let ref = tl.firstChild;
  active.forEach(({ c }) => {
    while (ref && !want.has(ref._key)) ref = ref.nextSibling;
    const seg = segEls[c.key];
    if (seg === ref) ref = ref.nextSibling;
    else tl.insertBefore(seg, ref);
  });

  // 3. flush new segments at 0% so the width change actually transitions
  fresh.forEach((s) => void s.offsetWidth);

  // 4. set data + target widths (animates resize / grow-in)
  active.forEach(({ c, i, label }) => {
    const seg = segEls[c.key];
    seg._d = { label, name: c.name, dur: c.duration };
    seg.style.background = lineColor(i);
    seg.style.width = total ? (c.duration / total * 100) + "%" : "0%";
  });

  // 5. shrink + remove departing segments in place
  Object.keys(segEls).forEach((k) => {
    if (want.has(k)) return;
    const seg = segEls[k];
    delete segEls[k];
    seg.style.minWidth = "0";
    seg.style.width = "0%";
    const done = (e) => {
      if (e.propertyName !== "width") return;
      seg.removeEventListener("transitionend", done);
      seg.remove();
    };
    seg.addEventListener("transitionend", done);
    setTimeout(() => seg.remove(), 600); // safety net
  });
}

function segTip(d) { return `<b>${d.label}</b><br>${d.name}<br>${fmt(d.dur)}`; }

function highlightCard(name, on) {
  document.querySelectorAll(`.clip[data-name="${CSS.escape(name)}"]`)
    .forEach((n) => n.style.outline = on ? "2px solid #fff" : "");
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------
async function toggleClip(c) {
  try {
    const data = await api("/api/toggle",
      { root: ROOT, line: c.line, name: c.name, active: !c.active });
    applyModel(data);
  } catch (e) { showStatus("⚠️ " + e.message, "error"); }
}

async function moveClip(payload, toLine) {
  try {
    const data = await api("/api/move",
      { root: ROOT, name: payload.name, fromLine: payload.line, toLine, active: payload.active });
    applyModel(data);
  } catch (e) { showStatus("⚠️ " + e.message, "error"); }
}

async function openClip(c) {
  try { await api("/api/open", { root: ROOT, line: c.line, name: c.name, active: c.active }); }
  catch (e) { showStatus("⚠️ " + e.message, "error"); }
}

// Rename via mark ({mark}) or restore to a specific name ({target}).
async function renameClip(c, opts) {
  const original = origNames[c.key] || c.name; // the very first (pre-mark) name
  try {
    const data = await api("/api/rename",
      Object.assign({ root: ROOT, line: c.line, name: c.name, active: c.active }, opts));
    const ln = data.model.lines.find((l) => l.name === c.line);
    const nc = ln && ln.clips.find((x) => x.name === data.newName);
    delete origNames[c.key];
    if (opts.mark && nc) origNames[nc.key] = original; // remember how to undo
    // a restore ({target}) intentionally drops the record — we're back to original
    applyModel(data);
  } catch (e) { showStatus("⚠️ " + e.message, "error"); }
}
function markClip(c, mark) { return renameClip(c, { mark }); }
function restoreClip(c) {
  const o = origNames[c.key];
  if (o) return renameClip(c, { target: o });
}

// ---------------------------------------------------------------------------
// Clip context menu
// ---------------------------------------------------------------------------
const MENU_ITEMS = [
  ["main", "★", "ic-main", "Mark as main clip"],
  ["sub", "▮", "ic-sub", "Mark as sub clip"],
  ["outro", "⤴", "ic-outro", "Mark as outro"],
];
let menuEl = null;

function ensureMenu() {
  if (!menuEl) {
    menuEl = el("div", "clip-menu hidden");
    document.body.appendChild(menuEl);
  }
  return menuEl;
}
function hideMenu() { if (menuEl) menuEl.classList.add("hidden"); }

function openMenu(e, c) {
  const m = ensureMenu();
  m.innerHTML = "";
  MENU_ITEMS.forEach(([key, ic, cls, label]) => {
    const it = el("div", "menu-item");
    it.innerHTML = `<span class="mi-ic ${cls}">${ic}</span>${label}` +
      (c.mark === key ? '<span class="mi-check">✓</span>' : "");
    it.addEventListener("click", (ev) => { ev.stopPropagation(); hideMenu(); markClip(c, key); });
    m.appendChild(it);
  });
  const orig = origNames[c.key];
  if (orig) {
    m.appendChild(el("div", "menu-sep"));
    const it = el("div", "menu-item");
    it.innerHTML = `<span class="mi-ic ic-restore">↩</span>Restore name` +
      `<span class="mi-orig">${orig}</span>`;
    it.title = orig;
    it.addEventListener("click", (ev) => { ev.stopPropagation(); hideMenu(); restoreClip(c); });
    m.appendChild(it);
  }
  m.appendChild(el("div", "menu-sep"));
  const openIt = el("div", "menu-item");
  openIt.innerHTML = `<span class="mi-ic">↗</span>Open in system player`;
  openIt.addEventListener("click", (ev) => { ev.stopPropagation(); hideMenu(); openClip(c); });
  m.appendChild(openIt);
  m.classList.remove("hidden");
  const pad = 6, r = m.getBoundingClientRect();
  let x = e.clientX, y = e.clientY;
  if (x + r.width > window.innerWidth) x = window.innerWidth - r.width - pad;
  if (y + r.height > window.innerHeight) y = window.innerHeight - r.height - pad;
  m.style.left = x + "px"; m.style.top = y + "px";
}

document.addEventListener("click", hideMenu);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMenu(); });
window.addEventListener("scroll", hideMenu, true);

async function packageVideo() {
  $("#packageBtn").disabled = true;
  try {
    await api("/api/package", { path: ROOT });
  } catch (e) {
    showStatus("⚠️ " + e.message, "error");
    $("#packageBtn").disabled = false;
    return;
  }
  pollPackage();
}

function pollPackage() {
  api("/api/package-status").then((st) => {
    if (st.error) {
      showStatus("⚠️ Packaging failed: " + st.error, "error");
      $("#packageBtn").disabled = false;
      return;
    }
    if (st.running) {
      const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
      showStatus(`📦 Packaging… ${st.done}/${st.total}` +
        `<span class="progress"><i style="width:${pct}%"></i></span>`);
      setTimeout(pollPackage, 300);
    } else if (st.zip) {
      const gb = st.bytes / 1e9;
      let msg = `✅ Packaged ${st.count} files → <b>${st.zip}</b> (${gb.toFixed(2)} GB)`;
      if (gb > 4) {
        msg += `<div class="hint">Over 4 GB, so Windows' built-in zip viewer can't open it ` +
          `(that's the "access denied" error). Extract with 7-Zip, or in PowerShell:<br>` +
          `<code>Expand-Archive "${st.zip}" -DestinationPath "&lt;folder&gt;"</code><br>` +
          `On macOS it opens normally.</div>`;
      }
      showStatus(`<div class="pkg-msg">${msg}</div>`);
      $("#packageBtn").disabled = false;
    } else {
      setTimeout(pollPackage, 300);
    }
  }).catch((e) => {
    showStatus("⚠️ " + e.message, "error");
    $("#packageBtn").disabled = false;
  });
}

// ---------------------------------------------------------------------------
// Assets (shared by Stage 2 bar and the Stage 1 prompt)
// ---------------------------------------------------------------------------
const ASSET_KINDS = [["thumbnails", "Thumbnails"], ["icons", "Icons"]];

function isImageName(name) {
  return /\.(png|jpe?g|gif|webp|svg|bmp|heic|tiff?|avif)$/i.test(name);
}
function assetUrl(kind, name) {
  return `/api/asset-file?root=${encodeURIComponent(ROOT)}&kind=${kind}&name=${encodeURIComponent(name)}`;
}

async function uploadAsset(kind, file) {
  const q = `?root=${encodeURIComponent(ROOT)}&kind=${kind}&name=${encodeURIComponent(file.name)}`;
  const res = await fetch("/api/asset-add" + q, { method: "POST", body: file });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  ASSETS = data.assets;
  return data;
}
async function uploadAssets(kind, fileList) {
  for (const f of fileList) {
    try { await uploadAsset(kind, f); }
    catch (e) { showStatus("⚠️ " + e.message, "error"); }
  }
}
async function removeAsset(kind, name) {
  const data = await api("/api/asset-remove", { root: ROOT, kind, name });
  ASSETS = data.assets;
}
function pickAssets(kind, after) {
  const inp = $("#assetFileInput");
  inp.value = "";
  inp.onchange = async () => {
    if (inp.files.length) { await uploadAssets(kind, inp.files); if (after) after(); }
  };
  inp.click();
}

function assetThumb(kind, name) {
  const chip = el("div", "asset-chip");
  if (isImageName(name)) {
    const img = el("img"); img.src = assetUrl(kind, name); img.alt = name; img.loading = "lazy";
    chip.appendChild(img);
  } else {
    const ext = el("div", "asset-ext");
    ext.textContent = (name.split(".").pop() || "file").toUpperCase();
    chip.appendChild(ext);
  }
  const cap = el("div", "asset-cap"); cap.textContent = name; cap.title = name;
  chip.appendChild(cap);
  return chip;
}

function renderAssetBar() {
  const bar = $("#assetBar");
  bar.innerHTML = "";
  ASSET_KINDS.forEach(([kind, label]) => {
    const group = el("div", "asset-group");
    const head = el("div", "asset-head");
    head.innerHTML = `<span>${label}</span><span class="asset-n">${(ASSETS[kind] || []).length}</span>`;
    const items = el("div", "asset-items");
    (ASSETS[kind] || []).forEach((name) => {
      const chip = assetThumb(kind, name);
      const rm = el("button", "asset-rm"); rm.textContent = "×"; rm.title = "Remove";
      rm.addEventListener("click", async (e) => {
        e.stopPropagation(); await removeAsset(kind, name); renderAssetBar();
      });
      chip.appendChild(rm);
      items.appendChild(chip);
    });
    const add = el("button", "asset-add"); add.textContent = "＋";
    add.title = "Add " + label.toLowerCase();
    add.addEventListener("click", () => pickAssets(kind, renderAssetBar));
    items.appendChild(add);

    group.addEventListener("dragover", (e) => { e.preventDefault(); group.classList.add("drag-over"); });
    group.addEventListener("dragleave", () => group.classList.remove("drag-over"));
    group.addEventListener("drop", async (e) => {
      e.preventDefault(); group.classList.remove("drag-over");
      if (e.dataTransfer.files.length) { await uploadAssets(kind, e.dataTransfer.files); renderAssetBar(); }
    });

    group.append(head, items);
    bar.appendChild(group);
  });
}

// ---------------------------------------------------------------------------
// In-app side player (plays raw .MOV; falls back to preview if the browser
// can't decode it). Replaces launching the slow system media player.
// ---------------------------------------------------------------------------
let spMode = "idle", spRel = "";

function clipRelPath(c) {
  const parts = [c.line];
  if (!c.active) parts.push("Unused");
  parts.push(c.name);
  return parts.join("/");
}

function openSidePlayer(title, relpath) {
  const v = $("#spVideo");
  spRel = relpath; spMode = "raw";
  $("#spTitle").textContent = title;
  $("#spNote").classList.add("hidden");
  v.src = `/api/clip?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(relpath)}`;
  v.load();
  const p = v.play(); if (p && p.catch) p.catch(() => {});
  $("#sidePlayer").classList.remove("hidden");
  document.body.classList.add("side-open");
}

function closeSidePlayer() {
  const v = $("#spVideo");
  spMode = "idle";
  v.pause(); v.removeAttribute("src"); v.load();
  $("#sidePlayer").classList.add("hidden");
  document.body.classList.remove("side-open");
}

$("#spClose").addEventListener("click", closeSidePlayer);
$("#spVideo").addEventListener("error", function () {
  if (spMode !== "raw") return;        // idle (cleared src) or already on preview
  spMode = "preview";
  $("#spNote").textContent = "Showing a low-res preview — this browser can't play the original .MOV.";
  $("#spNote").classList.remove("hidden");
  this.src = `/api/preview?root=${encodeURIComponent(ROOT)}&path=${encodeURIComponent(spRel)}`;
  this.load();
  const p = this.play(); if (p && p.catch) p.catch(() => {});
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#sidePlayer").classList.contains("hidden")) closeSidePlayer();
});

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------
function showTip(e, html) {
  const t = $("#tooltip");
  t.innerHTML = html;
  t.classList.remove("hidden");
  const pad = 14;
  let x = e.clientX + pad, y = e.clientY + pad;
  const r = t.getBoundingClientRect();
  if (x + r.width > window.innerWidth) x = e.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
  t.style.left = x + "px"; t.style.top = y + "px";
}
function hideTip() { $("#tooltip").classList.add("hidden"); }

function safeParse(s) { try { return JSON.parse(s); } catch { return null; } }

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
$("#chooseBtn").addEventListener("click", chooseFolder);
$("#packageBtn").addEventListener("click", packageVideo);
$("#pathInput").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });

// Heartbeat: lets the server auto-quit when this tab/window closes. Brief
// reloads pause the pings only briefly, under the server's timeout.
let heartbeatOn = true;
function heartbeat() { if (heartbeatOn) fetch("/api/heartbeat").catch(() => {}); }
heartbeat();
setInterval(heartbeat, 3000);

$("#quitBtn").addEventListener("click", async () => {
  if (!window.confirm("Stop Video Organizer? You can relaunch it anytime.")) return;
  heartbeatOn = false;
  try { await api("/api/quit", {}); } catch (e) { /* server is going down */ }
  document.title = "Video Organizer — stopped";
  document.body.innerHTML =
    '<div style="height:100vh;display:flex;flex-direction:column;align-items:center;' +
    'justify-content:center;gap:10px;color:#93a0b0;font-family:-apple-system,sans-serif;">' +
    '<div style="font-size:40px;">👋</div>' +
    '<div style="font-size:16px;color:#e7ebf0;">Video Organizer has stopped.</div>' +
    '<div>You can close this tab. Relaunch from the app icon anytime.</div></div>';
});
