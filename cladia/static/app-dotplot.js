// ── The Dot plot tab of the side panel ──────────────────────────────────────
// Two of the DNA sequences shown in the viewer (a sequence against itself, two
// rows of a list, an alignment without its gaps, two genomes of a comparison),
// drawn live from what is shown, edits included: forward matches in one
// colour, reverse-complement ones (inversions, inverted repeats) in another.
// Hover gives both positions; a click shows A's place in the viewer. "New
// figure" makes a dot plot figure item of the same two sequences.
// Uses dotplot.js; loaded before app-panel.js, which lists the tab.

const DP_COLORS = { forward: "#1f5fa8", reverse: "#c0392b" };
const DP_MARGIN = { left: 46, top: 30, right: 10, bottom: 8 };
const dp = { rowsKey: "", a: 0, b: 0, seqA: null, seqB: null, result: null, key: "", token: 0, layout: null };

// The sequence viewer shows DNA rows a dot plot can use
function dotplotShown() {
  return !!seqShown && !$("seqview").classList.contains("inactive") && !seqShown.graph && !seqShown.mapping &&
         !seqView?.protein && (seqView?.rows?.length || 0) > 0;
}

const dpRows = () => seqView.rows;
const dpSeq = (r) => DotPlot.strip(dpRows()[r]?.seq || "");

// The selects follow the rows shown; a new set of rows starts from the selected ones (else the first two)
function dotplotFillAxes() {
  const rows = dpRows(), key = `${seqKey}|${rows.map((r) => r.name).join("\t")}`;
  if (key === dp.rowsKey) return;
  dp.rowsKey = key;
  const picked = seqView.selection ? seqView.selectedRows(seqView.selection) : [];
  dp.a = picked.length >= 2 ? picked[0] : 0;
  dp.b = picked.length >= 2 ? picked[1] : Math.min(1, rows.length - 1);
  for (const id of ["dp-a", "dp-b"]) {
    const select = $(id);
    select.replaceChildren(...rows.map((r, i) => new Option(r.name, i)));
    select.value = id === "dp-a" ? dp.a : dp.b;
  }
  $("dp-a").closest("label").hidden = $("dp-b").closest("label").hidden = rows.length < 2;
}

async function renderDotplot() {
  if (!dotplotShown()) { dp.key = ""; dp.result = null; return; }
  dotplotFillAxes();
  const canvas = $("dp-plot"), body = $("dp-body");
  const a = dpSeq(dp.a), b = dpSeq(dp.b);
  if (!a.length || !b.length) { dp.result = null; drawDotplot(); $("dp-status").textContent = "No bases to compare"; return; }
  // the plot keeps both sequences to one scale, as wide as the panel allows
  const room = { w: Math.max(60, body.clientWidth - DP_MARGIN.left - DP_MARGIN.right),
                 h: Math.max(60, body.clientHeight - DP_MARGIN.top - DP_MARGIN.bottom) };
  const scale = Math.min(room.w / a.length, room.h / b.length);
  const w = Math.max(20, Math.round(a.length * scale)), h = Math.max(20, Math.round(b.length * scale));
  const ratio = window.devicePixelRatio || 1;
  const cols = Math.round(w * ratio), rows = Math.round(h * ratio), word = Number($("dp-word").value);
  dp.layout = { w, h, ratio, lengths: [a.length, b.length] };
  canvas.style.width = `${w + DP_MARGIN.left + DP_MARGIN.right}px`;
  canvas.style.height = `${h + DP_MARGIN.top + DP_MARGIN.bottom}px`;
  const key = `${dp.a}|${dp.b}|${word}|${cols}|${rows}`;
  if (key === dp.key && dp.seqA === a && dp.seqB === b) return drawDotplot();
  const token = ++dp.token;
  $("dp-status").textContent = "Comparing…";
  try {
    const result = await DotPlot.run({ a, b, word, cols, rows }, () => token === dp.token);
    if (!result) return;
    Object.assign(dp, { result, key, seqA: a, seqB: b });
    drawDotplot();
  } catch (e) {
    if (token !== dp.token) return;
    dp.result = null; drawDotplot();
    $("dp-status").textContent = e.message;
  }
}

function dotplotStatus() {
  const r = dp.result, rows = dpRows();
  if (!r) return;
  const self = dp.a === dp.b ? " against itself" : ` × ${rows[dp.b].name}`;
  $("dp-status").textContent = `${rows[dp.a].name}${self} · ${r.word}-base words` +
    (r.skipped ? " · very repetitive words thinned" : "");
}

function drawDotplot() {
  const canvas = $("dp-plot"), L = dp.layout, r = dp.result;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * ratio); canvas.height = Math.round(canvas.clientHeight * ratio);
  const g = canvas.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, canvas.width, canvas.height);
  if (!r || !L) return;
  const { left, top } = DP_MARGIN;
  // the dots: one image pixel per cell, at the canvas's own resolution
  const image = g.createImageData(r.cols, r.rows);
  const paint = (grid, hex) => {
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const dot = ratio > 1.5 ? 2 : 1;  // (one device pixel is too faint on a high-density screen)
    for (let i = 0; i < grid.length; i++) {
      if (!grid[i]) continue;
      const x = i % r.cols, y = (i - x) / r.cols;
      for (let dy = 0; dy < dot && y + dy < r.rows; dy++)
        for (let dx = 0; dx < dot && x + dx < r.cols; dx++) image.data.set([c[0], c[1], c[2], 255], ((y + dy) * r.cols + x + dx) * 4);
    }
  };
  paint(r.reverse, DP_COLORS.reverse);
  paint(r.forward, DP_COLORS.forward);
  g.putImageData(image, Math.round(left * ratio), Math.round(top * ratio));
  // frame, ticks, names
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--muted").trim() || "#777";
  g.strokeStyle = css.getPropertyValue("--border").trim() || "#ccc";
  g.lineWidth = 1;
  g.strokeRect(left - 0.5, top - 0.5, L.w + 1, L.h + 1);
  g.fillStyle = ink; g.font = "10px sans-serif";
  const ticks = (length, size, horizontal) => {
    const step = DotPlot.tickStep(length, Math.max(2, Math.floor(size / 70)));
    for (let v = horizontal ? 0 : step; v <= length; v += step) {  // (0 once, at the top)
      const p = (v / length) * size;
      const text = DotPlot.tickText(v, step);
      if (horizontal) { g.textAlign = "center"; g.fillText(text, left + p, top - 4); }
      else { g.textAlign = "right"; g.fillText(text, left - 4, top + p + 3); }
    }
  };
  ticks(L.lengths[0], L.w, true);
  ticks(L.lengths[1], L.h, false);
  g.textAlign = "left";
  g.fillStyle = css.getPropertyValue("--text").trim() || "#222";
  const rows = dpRows();
  g.fillText(fitText(g, `A: ${rows[dp.a]?.name}`, L.w), left, 11);
  g.save(); g.translate(10, top + L.h); g.rotate(-Math.PI / 2);
  g.fillText(fitText(g, `B: ${rows[dp.b]?.name}`, L.h), 0, 0);
  g.restore();
  dotplotStatus();
}

function fitText(g, text, width) {
  if (g.measureText(text).width <= width) return text;
  while (text.length > 1 && g.measureText(text + "…").width > width) text = text.slice(0, -1);
  return text + "…";
}

// The positions under the mouse (1-based, in each sequence's own bases), or null
function dotplotAt(e) {
  const L = dp.layout;
  if (!L || !dp.result) return null;
  const box = $("dp-plot").getBoundingClientRect();
  const x = e.clientX - box.left - DP_MARGIN.left, y = e.clientY - box.top - DP_MARGIN.top;
  if (x < 0 || y < 0 || x > L.w || y > L.h) return null;
  return { a: Math.min(L.lengths[0], Math.floor((x / L.w) * L.lengths[0]) + 1),
           b: Math.min(L.lengths[1], Math.floor((y / L.h) * L.lengths[1]) + 1),
           span: Math.max(1, Math.round(L.lengths[0] / L.w)) };
}

// A row's column of its nth base (an alignment's or a lined-up comparison's rows have gaps)
function dotplotColumn(row, base) {
  const seq = dpRows()[row].seq;
  for (let c = 0, n = 0; c < seq.length; c++) if (seq[c] !== "-" && seq[c] !== "." && ++n === base) return c;
  return seq.length - 1;
}

$("dp-plot").onmousemove = (e) => {
  const at = dotplotAt(e), rows = dpRows();
  if (!at) return dotplotStatus();
  $("dp-status").textContent = `A ${rows[dp.a].name} ${at.a.toLocaleString()} · B ${rows[dp.b].name} ${at.b.toLocaleString()}`;
};
$("dp-plot").onmouseleave = () => dotplotStatus();
$("dp-plot").onclick = (e) => {
  const at = dotplotAt(e);
  if (!at) return;
  const half = Math.floor(at.span / 2), c0 = dotplotColumn(dp.a, Math.max(1, at.a - half));
  const c1 = dotplotColumn(dp.a, Math.min(dp.layout.lengths[0], at.a + half));
  seqView.select(c0, c1, dp.a, dp.a);
  if (seqView.circularMap) seqView.rotateTo((c0 + c1 + 1) / 2);
  else seqView.centerOn((c0 + c1 + 1) / 2);
  seqView.scrollToRow?.(dp.a);
  seqView.draw();
};
$("dp-a").onchange = () => { dp.a = Number($("dp-a").value); renderDotplot(); };
$("dp-b").onchange = () => { dp.b = Number($("dp-b").value); renderDotplot(); };
$("dp-word").onchange = () => renderDotplot();

// the plot is drawn at the panel's size: again when the panel is resized or maximised
let dpResize = 0;
function redrawDotplot() {
  if ($("dotplotview").hidden || !dotplotShown()) return;
  clearTimeout(dpResize);
  dpResize = setTimeout(renderDotplot, 120);
}
new ResizeObserver(redrawDotplot).observe($("dp-body"));

// How a row is found again later (a figure is drawn from the library, not the viewer)
function dotplotRef(r) {
  if (seqShown.comparison) return { genome: r };
  if (seqShown.mode === "alignment") return { row: dpRows()[r].name };
  const it = seqShown.items[seqShown.mode === "single" ? 0 : r];
  return it.parent ? { list: it.parent, record: it.name } : { item: it.id };
}

$("dp-figure").onclick = async () => {
  if (!dotplotShown()) return;
  if (hasUnsavedChanges()) return toast("Save or undo the changes first: the figure draws the saved sequences", true);
  const source = seqShown.comparison ? seqShown.items[0] : seqShown.mode === "alignment" ? seqShown.alignment
    : seqShown.items[seqShown.mode === "single" ? 0 : dp.a];
  const names = dpRows();
  const name = dp.a === dp.b ? `${names[dp.a].name} dot plot` : `${names[dp.a].name} × ${names[dp.b].name} dot plot`;
  try {
    const { id } = await api("POST", "/api/figures", { source: source.id, of: "dotplot", name,
      part: { a: dotplotRef(dp.a), b: dotplotRef(dp.b) }, settings: { word: Number($("dp-word").value) } });
    await loadList();
    openItem(id);
  } catch (e) { toast(e.message, true); }
};
