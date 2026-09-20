// Semantic Microscope viewer. Vanilla JS, no build step.
// All dimensions render as parallel lanes; the selected dimension is emphasised, not isolated.

"use strict";

const RAMPS = {
  viridis: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
  magma: ["#000004", "#180f3d", "#440f76", "#721f81", "#9e2f7f", "#cd4071", "#f1605d", "#fd9668", "#feca8d", "#fcfdbf"],
};
// Okabe-Ito, colour-blind safe, assigned in criteria order and never cycled. "other" is neutral.
const CATEGORICAL = ["#e69f00", "#56b4e9", "#009e73", "#f0e442", "#0072b2", "#d55e00", "#cc79a7", "#000000"];
const CAT_OTHER = "#9a9aa3";

const LANE_W = 44, LANE_W_ON = 60, LANE_GAP = 5, DIM_ALPHA = 0.4, TICK_EVERY = 50;

const state = {
  runs: [], runId: null, meta: null, records: [],
  dims: [], dim: null, ramp: "viridis",
  hover: null, pinned: null, maxChars: 1,
  layout: new URL(location.href).searchParams.get("layout") === "wall" ? "wall" : "lanes",
  wall: { rows: 0, cols: 0, cell: 0, gap: 1, side: 0 },
};

const $ = (id) => document.getElementById(id);
const hooks = { onRunLoaded: null, onDimChanged: null, onFocusChanged: null, onLaneClick: null, statusExtra: null };
const lanes = $("lanes"), lctx = lanes.getContext("2d");
const ruler = $("ruler"), rctx = ruler.getContext("2d");

// ---------- colour ----------
function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
const RAMP_RGB = Object.fromEntries(Object.entries(RAMPS).map(([k, v]) => [k, v.map(hexToRgb)]));

function rampRgb(t, name = state.ramp) {
  const stops = RAMP_RGB[name];
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
  const a = stops[i], b = stops[i + 1];
  return a.map((v, k) => Math.round(v + (b[k] - v) * f));
}
const rgbStr = (c, a = 1) => (a >= 1 ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`);
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// Colour of a record in a dimension, or null when there is no answer.
function colorRgb(rec, dim) {
  const a = rec.answers && rec.answers[dim.name];
  if (!a) return null;
  if (a.kind === "noul") return rampRgb(a.value);
  if (a.kind === "score") return rampRgb(dim.nLevels > 1 ? a.value / (dim.nLevels - 1) : 0);
  if (a.kind === "choice") return hexToRgb(dim.catColor[a.value] || CAT_OTHER);
  return null;
}

// Scalar in [0,1] used for j/k thresholds; choice has none.
function normValue(rec, dim) {
  const a = rec.answers && rec.answers[dim.name];
  if (!a) return null;
  if (a.kind === "noul") return a.value;
  if (a.kind === "score") return dim.nLevels > 1 ? a.value / (dim.nLevels - 1) : 0;
  return null;
}

function dimsFromMeta(meta, records) {
  const q = (meta && meta.questions && meta.questions.dimensions) || {};
  if (!Object.keys(q).length) {
    const r = records.find((x) => x.answers && Object.keys(x.answers).length);
    if (r) for (const [k, v] of Object.entries(r.answers)) q[k] = { type: v.kind, label: k, criteria: v.legend ? Object.values(v.legend) : v.distribution ? Object.fromEntries(Object.keys(v.distribution).map((o) => [o, null])) : undefined };
  }
  return Object.entries(q).map(([name, spec]) => {
    const d = { name, kind: spec.type, label: spec.label || name, nLevels: null, legend: null, options: null, catColor: {} };
    if (d.kind === "score") { d.legend = spec.criteria || []; d.nLevels = d.legend.length; }
    if (d.kind === "choice") {
      d.options = Object.keys(spec.criteria || {});
      let k = 0;
      for (const o of d.options) d.catColor[o] = o === "other" ? CAT_OTHER : CATEGORICAL[k++] || CAT_OTHER;
    }
    return d;
  });
}

// ---------- geometry ----------
function laneX() {
  // Returns [{x, w}] for each dimension in order, emphasised lane wider.
  let x = 0;
  return state.dims.map((d) => {
    const w = d === state.dim ? LANE_W_ON : LANE_W;
    const r = { x, w };
    x += w + LANE_GAP;
    return r;
  });
}
const lanesWidth = () => laneX().reduce((m, l) => Math.max(m, l.x + l.w), 0);

function sizeCanvas(c, W, H) {
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
  c.style.width = W + "px";
  c.style.height = H + "px";
  return dpr;
}

// ---------- drawing ----------
function drawLanes() {
  if (state.layout === "wall") { drawWall(); return; }
  const t0 = performance.now();
  const H = $("lanes-wrap").clientHeight, W = lanesWidth();
  const dpr = sizeCanvas(lanes, W, H);
  lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  lctx.clearRect(0, 0, W, H);
  const n = state.records.length;
  if (!n) return;
  const rowH = H / n, px = 1 / dpr, grey = hexToRgb(cssVar("--grey-row") || "#888888");
  const geo = laneX();
  state.dims.forEach((d, li) => {
    const { x, w } = geo[li];
    const on = d === state.dim;
    lctx.globalAlpha = on ? 1 : DIM_ALPHA;
    const minW = 4, usable = w - minW - 2;
    for (let i = 0; i < n; i++) {
      const rec = state.records[i];
      const rw = minW + usable * Math.min(1, (rec.char_end - rec.char_start) / state.maxChars);
      const y0 = Math.round(i * rowH * dpr) * px, y1 = Math.round((i + 1) * rowH * dpr) * px;
      const c = colorRgb(rec, d);
      lctx.fillStyle = c ? rgbStr(c) : rgbStr(grey, 0.45);
      lctx.fillRect(x, y0, rw, Math.max(y1 - y0, px));
    }
  });
  lctx.globalAlpha = 1;
  lanes.dataset.lastDrawMs = (performance.now() - t0).toFixed(1);
}

// ---------- wall: near-square grid, one cell per sentence, row-major ----------
function wallGeometry() {
  const n = state.records.length;
  // Available height is the lane column minus the caption row; lanes-wrap itself is content-sized in wall mode.
  const avail = $("lanes-pane").clientHeight - $("lane-labels").offsetHeight - 2;
  const side = Math.max(40, Math.min(avail, Math.floor(innerWidth * 0.55)));
  const rows = Math.max(1, Math.ceil(Math.sqrt(n)));
  const cols = Math.max(1, Math.ceil(n / rows));
  const gap = side / rows >= 4 ? 1 : 0;
  const cell = Math.max(1, Math.floor((side - gap * (rows - 1)) / rows));
  state.wall = { rows, cols, cell, gap, side: cell * rows + gap * (rows - 1) };
  return state.wall;
}
function cellRect(i) {
  const { cols, cell, gap } = state.wall;
  const r = Math.floor(i / cols), c = i % cols;
  return { x: c * (cell + gap), y: r * (cell + gap), w: cell, h: cell };
}
function idxAtWall(x, y) {
  const { rows, cols, cell, gap } = state.wall;
  const c = Math.floor(x / (cell + gap)), r = Math.floor(y / (cell + gap));
  if (c < 0 || r < 0 || c >= cols || r >= rows) return null;
  const i = r * cols + c;
  return i < state.records.length ? i : null;
}
function drawWall() {
  const t0 = performance.now();
  const g = wallGeometry();
  const W = g.cols * (g.cell + g.gap), H = g.rows * (g.cell + g.gap);
  const dpr = sizeCanvas(lanes, W, H);
  lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  lctx.clearRect(0, 0, W, H);
  const grey = hexToRgb(cssVar("--grey-row") || "#888888");
  const n = state.records.length;
  for (let i = 0; i < n; i++) {
    const c = colorRgb(state.records[i], state.dim);
    const r = cellRect(i);
    lctx.fillStyle = c ? rgbStr(c) : rgbStr(grey, 0.35);
    lctx.fillRect(r.x, r.y, r.w, r.h);
  }
  $("lanes-wrap").style.width = W + "px";
  $("lanes-wrap").style.height = H + "px";
  ruler.style.display = "none";
  lanes.dataset.lastDrawMs = (performance.now() - t0).toFixed(1);
}

function drawRuler() {
  if (state.layout === "wall") return;
  ruler.style.display = "";
  $("lanes-wrap").style.height = "";
  const H = $("lanes-wrap").clientHeight, W = 46;
  ruler.style.left = lanesWidth() + 4 + "px";
  $("lanes-wrap").style.width = lanesWidth() + 4 + W + "px";
  const dpr = sizeCanvas(ruler, W, H);
  rctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  rctx.clearRect(0, 0, W, H);
  const n = state.records.length;
  if (!n) return;
  const rowH = H / n, px = 1 / dpr;
  rctx.fillStyle = cssVar("--line");
  rctx.fillRect(0, 0, px, H);
  rctx.font = "10px " + cssVar("--mono");
  rctx.textBaseline = "top";
  rctx.fillStyle = cssVar("--ink-3");
  // Pick the smallest tick step that keeps labels at least 14px apart.
  let step = TICK_EVERY;
  for (const s of [50, 100, 200, 250, 500, 1000, 2000, 5000]) { step = s; if (rowH * s >= 14) break; }
  for (let i = 0; i < n; i += step) {
    const y = Math.round(i * rowH * dpr) * px;
    rctx.fillStyle = cssVar("--ink-3");
    rctx.fillRect(0, y, 6, px);
    rctx.fillText(String(i), 9, Math.min(y + 1, H - 11));
  }
  rctx.fillStyle = cssVar("--ink-3");
  rctx.fillRect(0, H - px, 6, px);
  rctx.textBaseline = "bottom";
  rctx.fillText(String(n), 9, H);
}

function drawLegend() {
  const d = state.dim, el = $("legend");
  if (!d) { el.innerHTML = ""; return; }
  if (d.kind === "choice") {
    el.innerHTML = d.options.map((o) => `<span class="cat"><span class="sw" style="background:${d.catColor[o]}"></span>${esc(o)}</span>`).join("");
    return;
  }
  const lo = d.kind === "noul" ? "p=0" : esc(d.legend[0] || "0");
  const hi = d.kind === "noul" ? "p=1" : esc(d.legend[d.legend.length - 1] || String(d.nLevels - 1));
  el.innerHTML = `<span>${lo}</span><canvas id="legend-ramp" width="140" height="9"></canvas><span>${hi}</span>`;
  const c = $("legend-ramp"), g = c.getContext("2d");
  for (let x = 0; x < c.width; x++) { g.fillStyle = rgbStr(rampRgb(x / (c.width - 1))); g.fillRect(x, 0, 1, c.height); }
}

function renderLabels() {
  if (state.layout === "wall") {
    const g = wallGeometry();
    $("lane-labels").innerHTML = `<span class="wall-cap">wall · ${state.records.length} sentences · ${g.rows} × ${g.cols} cells, row-major · coloured by <b>${esc(state.dim ? state.dim.label : "")}</b></span>`;
    return;
  }
  const geo = laneX();
  $("lane-labels").innerHTML = state.dims.map((d, i) =>
    `<span class="lbl${d === state.dim ? " on" : ""}" data-i="${i}" style="width:${geo[i].w}px;margin-right:${LANE_GAP}px" title="${esc(d.label)}"><kbd>${i + 1}</kbd> ${esc(d.name)}</span>`).join("");
}

// ---------- status line ----------
const fmt = (v) => (v == null ? "–" : (Math.round(v * 100) / 100).toString().replace(/^0\./, "."));

function renderStatus() {
  const idx = state.pinned ?? state.hover;
  if (idx === null || idx === undefined) {
    $("status-idx").textContent = "–"; $("status-idx").className = "dim";
    $("status-text").textContent = "hover a lane or the text · click to pin · 1–9 emphasis · " + (hooks.statusExtra ? hooks.statusExtra() : "");
    $("status-vals").innerHTML = "";
    return;
  }
  const rec = state.records[idx];
  $("status-idx").textContent = "#" + rec.idx + (state.pinned !== null ? "*" : ""); $("status-idx").className = "";
  $("status-text").textContent = rec.error && !Object.keys(rec.answers || {}).length ? `[${rec.error}] ${rec.text}` : rec.text;
  $("status-vals").innerHTML = state.dims.map((d) => {
    const a = rec.answers && rec.answers[d.name];
    let v;
    if (!a) v = "–";
    else if (a.kind === "noul") v = fmt(a.value);
    else if (a.kind === "score") v = `${fmt(a.value)}/${d.nLevels - 1}`;
    else v = `<span class="sw" style="background:${d.catColor[a.value] || CAT_OTHER}"></span>${esc(a.value)} ${fmt(a.distribution[a.value])}`;
    return `<span class="v${d === state.dim ? " on" : ""}">${esc(shortLabel(d))} <b>${v}</b></span>`;
  }).join("") + (hooks.statusExtra ? `<span class="v hint">${esc(hooks.statusExtra())}</span>` : "");
}
const shortLabel = (d) => d.name;
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function renderStats() {
  const m = state.meta || {}, lat = m.latency_ms || {};
  const parts = [`<b>${state.records.length}</b> sentences`];
  if (lat.p50 != null) parts.push(`p50 <b>${lat.p50}ms</b>`);
  if (lat.p95 != null) parts.push(`p95 <b>${lat.p95}ms</b>`);
  if (m.estimated_cost_usd != null) parts.push(`<b>$${m.estimated_cost_usd.toFixed(4)}</b>`);
  if (m.n_errors) parts.push(`<b>${m.n_errors}</b> errors`);
  if (m.n_cached) parts.push(`${m.n_cached} cached`);
  $("stats").innerHTML = parts.join(" · ");
}

// ---------- events ----------
function redrawAll() { renderLabels(); drawLanes(); drawRuler(); drawLegend(); renderStatus(); }

function setDim(d) {
  if (!d || d === state.dim) return;
  state.dim = d;
  $("dim-select").value = d.name;
  redrawAll();
  if (hooks.onDimChanged) hooks.onDimChanged();
  console.debug(`lanes redraw ${lanes.dataset.lastDrawMs}ms for ${state.records.length} rows x ${state.dims.length} lanes`);
}

function idxAtY(y) {
  const n = state.records.length, H = $("lanes-wrap").clientHeight;
  return Math.min(n - 1, Math.max(0, Math.floor((y / H) * n)));
}
function idxAtPoint(e) {
  const r = lanes.getBoundingClientRect();
  if (state.layout === "wall") return idxAtWall(e.clientX - r.left, e.clientY - r.top);
  return idxAtY(e.clientY - r.top);
}
function setHover(idx) {
  if (idx === state.hover) return;
  state.hover = idx;
  if (hooks.onFocusChanged) hooks.onFocusChanged();
  if (state.pinned === null) renderStatus();
}

lanes.addEventListener("mousemove", (e) => setHover(idxAtPoint(e)));
lanes.addEventListener("mouseleave", () => setHover(null));
lanes.addEventListener("click", (e) => {
  const idx = idxAtPoint(e);
  if (idx === null) return;
  state.pinned = state.pinned === idx ? null : idx;
  if (hooks.onFocusChanged) hooks.onFocusChanged();
  renderStatus();
  if (state.pinned !== null && hooks.onLaneClick) hooks.onLaneClick(idx);
});
$("lane-labels").addEventListener("click", (e) => { const l = e.target.closest(".lbl"); if (l) setDim(state.dims[Number(l.dataset.i)]); });
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key >= "1" && e.key <= "9") { const d = state.dims[Number(e.key) - 1]; if (d) setDim(d); }
  if (e.key === "Escape") { state.pinned = null; if (hooks.onFocusChanged) hooks.onFocusChanged(); renderStatus(); }
});
$("dim-select").addEventListener("change", (e) => setDim(state.dims.find((d) => d.name === e.target.value)));
$("layout-select").addEventListener("change", (e) => {
  state.layout = e.target.value;
  const url = new URL(location.href); url.searchParams.set("layout", state.layout); history.replaceState(null, "", url);
  document.body.dataset.layout = state.layout;
  redrawAll();
  if (hooks.onDimChanged) hooks.onDimChanged();
});
$("ramp-select").addEventListener("change", (e) => { state.ramp = e.target.value; redrawAll(); });
$("run-select").addEventListener("change", (e) => loadRun(e.target.value));
window.addEventListener("resize", () => { drawLanes(); drawRuler(); if (hooks.onFocusChanged) hooks.onFocusChanged(); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawAll);

// ---------- loading ----------
async function fetchRuns() {
  try { const r = await fetch("/api/runs"); if (r.ok) return await r.json(); } catch (_) { /* static server */ }
  return [];
}

async function loadRun(runId) {
  state.runId = runId;
  const url = new URL(location.href); url.searchParams.set("run", runId); history.replaceState(null, "", url);
  $("doc-name").textContent = "loading " + runId;
  const [jsonlRes, metaRes] = await Promise.all([fetch(`/data/${runId}.jsonl`), fetch(`/data/${runId}.meta.json`)]);
  if (!jsonlRes.ok) { $("doc-name").textContent = `no data for ${runId}`; return; }
  const text = await jsonlRes.text();
  state.records = text.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l)).sort((a, b) => a.idx - b.idx);
  state.meta = metaRes.ok ? await metaRes.json() : null;
  const lens = state.records.map((r) => r.char_end - r.char_start).sort((a, b) => a - b);
  state.maxChars = Math.max(1, lens[Math.min(lens.length - 1, Math.floor(lens.length * 0.95))]);
  state.dims = dimsFromMeta(state.meta, state.records);
  state.hover = null; state.pinned = null; state.dim = null;
  const src = (state.meta && state.meta.source_file) || runId;
  $("doc-name").textContent = src.split("/").pop();
  document.title = `${src.split("/").pop()} · Semantic Microscope`;
  $("dim-select").innerHTML = state.dims.map((d) => `<option value="${d.name}">${esc(d.label)}</option>`).join("");
  renderStats();
  setDim(state.dims[0]);
  if (hooks.onRunLoaded) await hooks.onRunLoaded();
}

async function main() {
  $("layout-select").value = state.layout;
  document.body.dataset.layout = state.layout;
  state.runs = await fetchRuns();
  const sel = $("run-select");
  if (state.runs.length > 1) { sel.hidden = false; sel.innerHTML = state.runs.map((r) => `<option value="${r.run_id}">${r.run_id}</option>`).join(""); }
  const wanted = new URL(location.href).searchParams.get("run");
  const runId = wanted || (state.runs[0] && state.runs[0].run_id);
  if (!runId) { $("doc-name").textContent = "no runs in data/. Run: uv run microscope label <file>"; return; }
  if (state.runs.length > 1) sel.value = runId;
  await loadRun(runId);
}

main();
