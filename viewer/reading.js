// Reading pane, tooltips, scroll sync and keyboard navigation (Milestone 5).
// Shares the global `state` and helpers from app.js; loaded after it.

"use strict";

const TINT_ALPHA = 0.35;
const reading = $("reading-pane");
const overlay = $("lanes-overlay"), octx = overlay.getContext("2d");
const tooltip = $("tooltip");
let spanTops = []; // cached offsetTop per idx, rebuilt on layout
let flashTimer = null;
const nav = { threshold: 0.5 };

// ---------- build ----------
async function fetchSource() {
  const src = state.meta && state.meta.source_file;
  if (!src) return null;
  try { const r = await fetch("/" + src.replace(/^\/+/, "")); if (r.ok) return await r.text(); } catch (_) { /* fall back */ }
  return null;
}

async function buildReading() {
  const source = await fetchSource();
  const frag = document.createDocumentFragment();
  let cursor = 0;
  for (const rec of state.records) {
    const gap = source ? source.slice(cursor, rec.char_start) : (cursor ? (rec.text === rec.text.toUpperCase() ? "\n\n" : " ") : "");
    if (gap) frag.appendChild(document.createTextNode(gap));
    const span = document.createElement("span");
    span.className = "s";
    span.dataset.idx = rec.idx;
    span.textContent = source ? source.slice(rec.char_start, rec.char_end) : rec.text;
    frag.appendChild(span);
    cursor = rec.char_end;
  }
  if (source && cursor < source.length) frag.appendChild(document.createTextNode(source.slice(cursor)));
  reading.replaceChildren(frag);
  reading.classList.toggle("reconstructed", !source);
  tintReading();
  cacheSpanTops();
  drawOverlay();
  document.fonts.ready.then(() => { cacheSpanTops(); drawOverlay(); });
}

function tintReading() {
  const spans = reading.querySelectorAll("span.s");
  const grey = hexToRgb(cssVar("--grey-row") || "#888888");
  for (const span of spans) {
    const rec = state.records[Number(span.dataset.idx)];
    const c = colorRgb(rec, state.dim);
    span.style.backgroundColor = c ? rgbStr(c, TINT_ALPHA) : rgbStr(grey, 0.18);
  }
}

function cacheSpanTops() {
  spanTops = [];
  const base = reading.getBoundingClientRect().top - reading.scrollTop;
  for (const span of reading.querySelectorAll("span.s")) spanTops[Number(span.dataset.idx)] = span.getBoundingClientRect().top - base;
}

// ---------- scroll sync ----------
function visibleRange() {
  const n = state.records.length;
  if (!n || !spanTops.length) return null;
  const top = reading.scrollTop, bottom = top + reading.clientHeight;
  let lo = 0, hi = n - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (spanTops[mid] < top) lo = mid + 1; else hi = mid; }
  const first = Math.max(0, lo - 1);
  let last = first;
  while (last + 1 < n && spanTops[last + 1] <= bottom) last++;
  return [first, last];
}

function drawWallOverlay() {
  const g = state.wall;
  const W = g.cols * (g.cell + g.gap), H = g.rows * (g.cell + g.gap);
  const dpr = sizeCanvas(overlay, W, H);
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  octx.clearRect(0, 0, W, H);
  const range = visibleRange();
  if (range) {
    octx.fillStyle = cssVar("--ink");
    octx.globalAlpha = 0.18;
    for (let i = range[0]; i <= range[1]; i++) { const r = cellRect(i); octx.fillRect(r.x, r.y, r.w, r.h); }
    octx.globalAlpha = 1;
  }
  const focus = state.pinned ?? state.hover;
  if (focus !== null && focus !== undefined) {
    const r = cellRect(focus);
    octx.strokeStyle = cssVar("--accent");
    octx.lineWidth = Math.max(1, Math.min(2, g.cell / 4));
    octx.strokeRect(r.x - 1, r.y - 1, r.w + 2, r.h + 2);
  }
}

function drawOverlay() {
  if (state.layout === "wall") { drawWallOverlay(); return; }
  const H = $("lanes-wrap").clientHeight, W = lanesWidth();
  const dpr = sizeCanvas(overlay, W, H);
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  octx.clearRect(0, 0, W, H);
  const n = state.records.length;
  if (!n) return;
  const rowH = H / n, px = 1 / dpr;
  const range = visibleRange();
  if (range) {
    const y0 = Math.round(range[0] * rowH * dpr) * px, y1 = Math.round((range[1] + 1) * rowH * dpr) * px;
    octx.fillStyle = cssVar("--ink");
    octx.globalAlpha = 0.08;
    octx.fillRect(0, y0, W, y1 - y0);
    octx.globalAlpha = 0.55;
    octx.fillRect(0, y0, W, px);
    octx.fillRect(0, y1 - px, W, px);
    octx.globalAlpha = 1;
  }
  const focus = state.pinned ?? state.hover;
  if (focus !== null && focus !== undefined) {
    const y = Math.round(focus * rowH * dpr) * px;
    octx.fillStyle = cssVar("--accent");
    octx.fillRect(0, y - px, W, Math.max(rowH, 2 * px) + 2 * px);
  }
}

function scrollToIdx(idx, flash = true) {
  const span = reading.querySelector(`span.s[data-idx="${idx}"]`);
  if (!span) return;
  const target = spanTops[idx] - reading.clientHeight / 2 + span.offsetHeight / 2;
  reading.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
  if (flash) {
    reading.querySelectorAll("span.s.flash").forEach((s) => s.classList.remove("flash"));
    span.classList.add("flash");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => span.classList.remove("flash"), 1200);
  }
}

// ---------- tooltip ----------
function bar(p, color) {
  return `<span class="bar"><span class="fill" style="width:${Math.round(p * 100)}%;background:${color}"></span></span>`;
}

function tooltipHtml(rec) {
  const rows = state.dims.map((d) => {
    const a = rec.answers && rec.answers[d.name];
    const on = d === state.dim ? " on" : "";
    if (!a) return `<div class="tt-dim${on}"><div class="tt-h">${esc(d.label)}</div><div class="tt-none">no answer${rec.error ? ": " + esc(rec.error) : ""}</div></div>`;
    if (a.kind === "noul") {
      return `<div class="tt-dim${on}"><div class="tt-h">${esc(d.label)} <b>${fmt(a.value)}</b></div>` +
        `<div class="tt-row">${bar(a.value, rgbStr(rampRgb(a.value)))}<span class="tt-n">p=${fmt(a.value)}</span></div></div>`;
    }
    if (a.kind === "score") {
      const keys = Object.keys(a.distribution).sort((x, y) => Number(x) - Number(y));
      return `<div class="tt-dim${on}"><div class="tt-h">${esc(d.label)} <b>${fmt(a.value)}</b> / ${d.nLevels - 1} · conf ${fmt(a.confidence)}</div>` +
        keys.map((k) => `<div class="tt-row"><span class="tt-k">${esc(a.legend[k] || k)}</span>${bar(a.distribution[k], rgbStr(rampRgb(Number(k) / Math.max(1, d.nLevels - 1))))}<span class="tt-n">${fmt(a.distribution[k])}</span></div>`).join("") + `</div>`;
    }
    const keys = Object.keys(a.distribution).sort((x, y) => a.distribution[y] - a.distribution[x]);
    return `<div class="tt-dim${on}"><div class="tt-h">${esc(d.label)} <b>${esc(a.value)}</b> · conf ${fmt(a.confidence)}</div>` +
      keys.map((k) => `<div class="tt-row"><span class="tt-k">${esc(k)}</span>${bar(a.distribution[k], d.catColor[k] || CAT_OTHER)}<span class="tt-n">${fmt(a.distribution[k])}</span></div>`).join("") + `</div>`;
  });
  const meta = `#${rec.idx} · chars ${rec.char_start}–${rec.char_end}` + (rec.cached ? " · cached" : rec.latency_ms ? ` · ${rec.latency_ms}ms` : "") + (rec.input_tokens ? ` · ${rec.input_tokens} tok` : "");
  return `<div class="tt-meta">${meta}</div>${rows.join("")}`;
}

function showTooltip(rec, x, y) {
  tooltip.innerHTML = tooltipHtml(rec);
  tooltip.hidden = false;
  const pad = 12, w = tooltip.offsetWidth, h = tooltip.offsetHeight;
  let left = x + pad, top = y + pad;
  if (left + w > innerWidth - 8) left = Math.max(8, x - w - pad);
  if (top + h > innerHeight - 34) top = Math.max(8, innerHeight - 34 - h);
  tooltip.style.left = left + "px";
  tooltip.style.top = top + "px";
}
const hideTooltip = () => { tooltip.hidden = true; };

// ---------- keyboard nav ----------
function passes(rec, dim, prev) {
  if (dim.kind === "choice") {
    const a = rec.answers && rec.answers[dim.name], b = prev && prev.answers && prev.answers[dim.name];
    return !!a && (!b || a.value !== b.value); // category boundary
  }
  const v = normValue(rec, dim);
  return v !== null && v >= nav.threshold;
}

function jump(dir) {
  const n = state.records.length;
  if (!n) return;
  const from = state.pinned ?? state.hover ?? (visibleRange() ? visibleRange()[0] + (dir > 0 ? -1 : 1) : -1);
  for (let step = 1; step <= n; step++) {
    const i = from + dir * step;
    if (i < 0 || i >= n) break;
    if (passes(state.records[i], state.dim, state.records[i - 1])) { pin(i); return; }
  }
}

function pin(idx) {
  state.pinned = idx;
  drawOverlay();
  renderStatus();
  scrollToIdx(idx);
}

function navHint() {
  const d = state.dim;
  if (!d) return "";
  return d.kind === "choice" ? "j/k: category change" : `j/k: ≥ ${fmt(nav.threshold)} ([ ] adjust)`;
}

// ---------- events ----------
reading.addEventListener("mousemove", (e) => {
  const span = e.target.closest && e.target.closest("span.s");
  if (!span) { hideTooltip(); setHover(null); return; }
  const idx = Number(span.dataset.idx);
  setHover(idx);
  showTooltip(state.records[idx], e.clientX, e.clientY);
});
reading.addEventListener("mouseleave", () => { hideTooltip(); setHover(null); });
reading.addEventListener("click", (e) => {
  const span = e.target.closest && e.target.closest("span.s");
  if (!span) return;
  const idx = Number(span.dataset.idx);
  state.pinned = state.pinned === idx ? null : idx;
  drawOverlay(); renderStatus();
});
let scrollPending = false;
reading.addEventListener("scroll", () => {
  if (scrollPending) return;
  scrollPending = true;
  requestAnimationFrame(() => { scrollPending = false; drawOverlay(); });
});
window.addEventListener("resize", () => { cacheSpanTops(); drawOverlay(); });
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "j") { e.preventDefault(); jump(1); }
  else if (e.key === "k") { e.preventDefault(); jump(-1); }
  else if (e.key === "[") { nav.threshold = Math.max(0, +(nav.threshold - 0.1).toFixed(2)); renderStatus(); }
  else if (e.key === "]") { nav.threshold = Math.min(1, +(nav.threshold + 0.1).toFixed(2)); renderStatus(); }
  else if (e.key === "Enter" && (state.pinned ?? state.hover) != null) scrollToIdx(state.pinned ?? state.hover);
});

// Hooks called from app.js.
hooks.onRunLoaded = buildReading;
hooks.onDimChanged = () => { tintReading(); drawOverlay(); };
hooks.onFocusChanged = drawOverlay;
hooks.onLaneClick = (idx) => scrollToIdx(idx);
hooks.statusExtra = navHint;
