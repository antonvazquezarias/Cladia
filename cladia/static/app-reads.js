// ── Reads: the report of a reads item (reads.py) ─────────────────────────────
// A page, not a sequence view: the sample's files, then its reads' lengths,
// quality and base composition along them, mean read qualities, GC per read,
// and the first reads themselves. Charts are small inline SVGs. Per-position
// numbers are from the first reads of each file (the report says how many).

let readsShown = null;  // { item, data, more }
const READS_FIRST = 50;  // reads listed before "Show more"
const READ_BASE_COLORS = { A: "#3a9a46", C: "#2f6fbf", G: "#c4932b", T: "#c0392b", N: "#888" };

async function showReads(it) {
  showView("readsview");
  if (readsShown?.item.id === it.id && readsShown.item.mtime === it.mtime) return readsTitle();
  readsShown = { item: it, data: null, more: READS_FIRST };
  $("readsview").replaceChildren(Object.assign(document.createElement("p"), { className: "muted reads-loading", textContent: "Reading the report…" }));
  const data = await api("GET", `/api/reads/${it.id}`);
  if (readsShown?.item.id !== it.id) return;
  readsShown.data = data;
  readsTitle();
  renderReads();
}

const readCount = (n) => Number(n).toLocaleString();
const readBytes = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`;

function readsTotals(d) {
  const reads = d.stats.reduce((n, s) => n + s.reads, 0), bases = d.stats.reduce((n, s) => n + s.bases, 0);
  return { reads, bases, mean: reads ? bases / reads : 0 };
}

function readsTitle() {
  const { item: it, data: d } = readsShown;
  if (!d) return;
  const t = readsTotals(d);
  const kind = `${d.long ? "Long reads" : "Short reads"}${d.paired ? ", paired" : ""} · ${d.files.length} file${d.files.length === 1 ? "" : "s"}`;
  setTitle(it.label, "", [`${readCount(d.paired ? t.reads / 2 : t.reads)} ${d.paired ? "pairs" : "reads"}`, `${(t.bases / 1e6).toFixed(1)} Mb`,
                          `mean ${Math.round(t.mean).toLocaleString()} bp`],
           `${kind}${d.removed || !d.present.length ? " · read files deleted, the report kept" : ""}`);
}

// ── Charts ──
const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

// A chart frame: { svg, x(v), y(v) } over [x0, x1] × [y0, y1], with axis ticks
function chartFrame({ x0, x1, y0, y1, xLabel, yLabel, yTicks, width = 520, height = 170 }) {
  const m = { l: 44, r: 10, t: 8, b: 32 };
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "reads-chart", role: "img" });
  const x = (v) => m.l + ((v - x0) / Math.max(1e-9, x1 - x0)) * (width - m.l - m.r);
  const y = (v) => height - m.b - ((v - y0) / Math.max(1e-9, y1 - y0)) * (height - m.t - m.b);
  svg.append(svgEl("line", { x1: m.l, y1: y(y0), x2: width - m.r, y2: y(y0), class: "axis" }),
             svgEl("line", { x1: m.l, y1: y(y0), x2: m.l, y2: y(y1), class: "axis" }));
  for (const v of yTicks || [y0, (y0 + y1) / 2, y1]) {
    svg.append(svgEl("line", { x1: m.l, y1: y(v), x2: width - m.r, y2: y(v), class: "grid" }),
               svgEl("text", { x: m.l - 4, y: y(v) + 3, "text-anchor": "end" }, v >= 100 ? Math.round(v).toLocaleString() : String(+v.toFixed(1))));
  }
  const step = DotPlot.tickStep(x1 - x0, 6);
  for (let v = Math.ceil(x0 / step) * step; v <= x1; v += step)
    svg.append(svgEl("text", { x: x(v), y: height - m.b + 13, "text-anchor": "middle" }, v.toLocaleString()));
  svg.append(svgEl("text", { x: (m.l + width - m.r) / 2, y: height - 4, "text-anchor": "middle", class: "label" }, xLabel),
             svgEl("text", { x: 10, y: (m.t + height - m.b) / 2, "text-anchor": "middle", class: "label",
                             transform: `rotate(-90 10 ${(m.t + height - m.b) / 2})` }, yLabel));
  return { svg, x, y };
}

function barChart(values, { x0 = 0, step = 1, xLabel, yLabel, color = "var(--accent)" }) {
  const top = Math.max(1, ...values.map((v) => v[1]));
  const last = values.length ? values[values.length - 1][0] + step : x0 + 1;
  const f = chartFrame({ x0, x1: last, y0: 0, y1: top, xLabel, yLabel, yTicks: [0, Math.round(top / 2), top] });
  for (const [v, n] of values) {
    if (!n) continue;
    const left = f.x(v), right = f.x(v + step);
    f.svg.append(svgEl("rect", { x: left, y: f.y(n), width: Math.max(0.8, right - left - 0.4), height: f.y(0) - f.y(n), fill: color }));
  }
  return f.svg;
}

function lineChart(series, { x0, x1, y0, y1, xLabel, yLabel, yTicks, bands }) {
  const f = chartFrame({ x0, x1, y0, y1, xLabel, yLabel, yTicks });
  for (const [from, to, cls] of bands || [])  // FastQC-like quality zones
    f.svg.insertBefore(svgEl("rect", { x: f.x(x0), y: f.y(to), width: f.x(x1) - f.x(x0), height: f.y(from) - f.y(to), class: cls }), f.svg.firstChild);
  let legendX = f.x(x0) + 6;
  for (const s of series) {
    const points = s.values.map((v, i) => `${f.x(i + 1).toFixed(1)},${f.y(v).toFixed(1)}`).join(" ");
    f.svg.append(svgEl("polyline", { points, fill: "none", stroke: s.color, "stroke-width": 1.6 }));
    if (series.length > 1) {
      f.svg.append(svgEl("text", { x: legendX, y: 18, fill: s.color, class: "legend" }, s.name));
      legendX += 12 + 7 * s.name.length;
    }
  }
  return f.svg;
}

// lengths [[length, n]] in at most `bins` bars
function lengthBars(lengths, bins = 60) {
  if (!lengths.length) return { values: [], step: 1, x0: 0 };
  const lo = lengths[0][0], hi = lengths[lengths.length - 1][0];
  const step = Math.max(1, Math.ceil((hi - lo + 1) / bins));
  const counts = new Map();
  for (const [len, n] of lengths) {
    const b = lo + Math.floor((len - lo) / step) * step;
    counts.set(b, (counts.get(b) || 0) + n);
  }
  return { values: [...counts.entries()], step, x0: lo };
}

function readsSection(title, ...content) {
  const box = document.createElement("section");
  box.className = "reads-card";
  box.append(Object.assign(document.createElement("h3"), { textContent: title }), ...content);
  return box;
}
const readsNote = (text) => Object.assign(document.createElement("p"), { className: "muted", textContent: text });

function renderReads() {
  const { data: d } = readsShown;
  const page = $("readsview");
  page.replaceChildren();
  const names = d.files.map((f) => f.name);
  const fileColors = ["#2f6fbf", "#c0392b", "#3a9a46", "#8e44ad"];

  // files
  const table = document.createElement("table");
  table.className = "reads-files";
  table.innerHTML = "<tr><th>File</th><th>Reads</th><th>Bases</th><th>Size</th><th title='Of the file as imported'>SHA-256</th></tr>";
  d.files.forEach((f, i) => {
    const s = d.stats[i], tr = table.insertRow();
    for (const v of [f.name, readCount(s.reads), readCount(s.bases), readBytes(f.size), f.sha256.slice(0, 12) + "…"]) tr.insertCell().textContent = v;
    tr.cells[4].title = f.sha256;
    if (!d.present.includes(f.name)) tr.classList.add("gone");
  });
  page.append(readsSection("Files", table,
    readsNote(d.present.length ? `Imported ${d.imported}. Per-position charts use the first ${readCount(Math.min(...d.stats.map((s) => s.sampled)))} reads of each file.`
                          : `Imported ${d.imported}. The read files were deleted; this report is what is left.`)));

  // lengths (all files together)
  const all = new Map();
  for (const s of d.stats) for (const [len, n] of s.lengths) all.set(len, (all.get(len) || 0) + n);
  const lb = lengthBars([...all.entries()].sort((a, b) => a[0] - b[0]));
  page.append(readsSection("Read lengths", barChart(lb.values, { x0: lb.x0, step: lb.step, xLabel: "length (bases)", yLabel: "reads" })));

  // quality along the reads, a line per file
  const longest = Math.max(1, ...d.stats.map((s) => s.quality.length));
  page.append(readsSection("Quality along the reads",
    lineChart(d.stats.map((s, i) => ({ name: names[i], values: s.quality, color: fileColors[i % 4] })),
              { x0: 1, x1: longest, y0: 0, y1: 42, yTicks: [0, 20, 30, 40], xLabel: "position in the read", yLabel: "mean Phred quality",
                bands: [[0, 20, "q-bad"], [20, 30, "q-fair"], [30, 42, "q-good"]] }),
    readsNote(longest >= 1000 ? "Positions past 1,000 are left out (long reads)." : "Phred 20: 1 error in 100 bases; 30: 1 in 1,000.")));

  // base composition along the reads, all files together
  const total = Array.from({ length: longest }, (_, p) => d.stats.reduce((n, s) => n + "ACGTN".split("").reduce((m, b) => m + (s.composition[b][p] || 0), 0), 0));
  const share = (b) => total.map((n, p) => n ? 100 * d.stats.reduce((m, s) => m + (s.composition[b][p] || 0), 0) / n : 0);
  page.append(readsSection("Base composition along the reads",
    lineChart("ACGT".split("").map((b) => ({ name: b, values: share(b), color: READ_BASE_COLORS[b] })),
              { x0: 1, x1: longest, y0: 0, y1: 100, yTicks: [0, 25, 50, 75, 100], xLabel: "position in the read", yLabel: "% of bases" }),
    readsNote("Flat lines are expected; a wavy start is common with Nextera libraries, steps or spikes suggest adapters or contamination.")));

  // mean quality per read, GC per read
  const sum = (key, size) => Array.from({ length: size }, (_, i) => [i, d.stats.reduce((n, s) => n + (s[key][i] || 0), 0)]);
  page.append(readsSection("Mean quality of each read", barChart(sum("read_quality", 42), { xLabel: "mean Phred quality", yLabel: "reads" })),
              readsSection("GC content of each read", barChart(sum("gc", 101), { xLabel: "% GC", yLabel: "reads", color: "#7d6aa8" })));

  // the first reads
  const first = d.stats.flatMap((s, i) => s.first.map((r) => ({ file: i, name: r[0], seq: r[1], qual: r[2], length: r[3] })));
  const list = document.createElement("div");
  list.className = "reads-first";
  for (const r of first.slice(0, readsShown.more)) {
    const row = document.createElement("div");
    row.innerHTML = `<span class="name"></span><span class="len"></span><code></code>`;
    row.querySelector(".name").textContent = (d.files.length > 1 ? `${["R1", "R2", "3", "4"][r.file] || r.file + 1} · ` : "") + r.name;
    row.querySelector(".len").textContent = `${readCount(r.length)} bp`;
    const code = row.querySelector("code");
    code.textContent = r.seq + (r.length > r.seq.length ? "…" : "");
    code.title = "Quality (Phred + 33): " + r.qual;
    list.append(row);
  }
  const more = document.createElement("button");
  more.className = "secondary";
  more.textContent = "Show more";
  more.hidden = readsShown.more >= first.length;
  more.onclick = () => { readsShown.more += 200; renderReads(); };
  page.append(readsSection(`The first reads (${readCount(first.length)} kept)`, list, more));
}

async function dropReadFiles(it) {
  const size = (await api("GET", `/api/reads/${it.id}`).catch(() => null))?.files?.reduce((n, f) => n + f.size, 0) || 0;
  if (!confirm(`Delete the read files of ${it.label} (${readBytes(size)})? They don't go to the Trash. The report stays.`)) return;
  try {
    await api("POST", `/api/reads/${it.id}/remove-files`);
    toast(`Deleted the read files of ${it.label}`);
    readsShown = null;
    await loadList();
    showSelection();
  } catch (e) { toast(e.message, true); }
}
