// ── Identity matrix (an alignment) ──────────────────────────────────────
// Pairwise identity as Geneious counts it: a base against a gap is a difference,
// a gap against a gap isn't compared, and gaps at a sequence's ends are missing
// data (only where both sequences have started and not yet ended counts). Of the
// alignment as shown (unsaved gap edits included); a selection limits it to its
// columns and rows. Shown in the side panel's Identity tab, beside the alignment
// (⤢ gives it the whole work area); the sample × variant matrix is the Variants
// tab's Matrix view.
let matrix = null;          // { names, same, compared, c0, c1 }
let matrixShows = "identity";  // or "differences"
let matrixRun = 0;          // a newer computation stops an older one
let matrixKey = "";         // what the matrix shown was computed from
let matrixTimer = 0;        // a selection being dragged: computed once it settles
let variantMode = "list";  // the Variants tab: "list" or "matrix" (remembered)
try { variantMode = JSON.parse(localStorage.getItem("cladia.variantmode") || "{}").mode || "list"; } catch (e) {}

function setVariantMode(mode) {
  variantMode = mode;
  try { localStorage.setItem("cladia.variantmode", JSON.stringify({ mode })); } catch (e) {}
  syncSidePanel();
}

const alignmentShown = () => seqShown?.mode === "alignment" && !$("seqview").classList.contains("inactive");
// Variants to list: an alignment's attached VCF, or a VCF item (its reference in the viewer)
const variantsShown = () => !!seqShown?.variantData && !$("seqview").classList.contains("inactive") &&
  (seqShown.mode === "alignment" || !!seqShown.variantItem);

function updateMatrixView() {
  const now = typeof sideTabNow === "function" ? sideTabNow() : null;  // (app-panel.js loads later)
  const on = now === "identity" && alignmentShown();
  const variants = now === "variants" && variantMode === "matrix" && variantsShown();
  for (const b of document.querySelectorAll("[data-variantmode]")) b.classList.toggle("active", b.dataset.variantmode === variantMode);
  if (variants) updateVariantMatrixView();
  if (!on || !seqView?.rows.length) return;
  // computed again only when the alignment, its gaps or the selection changed
  const s = seqView.selection;
  const key = [seqShown.items[0].id, seqShown.alignDoc?.version, seqView.rows.length, s && [s.c0, s.c1, seqView.selectedRows(s)]].join("|");
  if (key !== matrixKey) {
    matrixKey = key;
    computeMatrix();
  }
}

async function computeMatrix() {
  const run = ++matrixRun;
  const s = seqView.selection;
  const rows = s ? seqView.selectedRows(s).map((r) => seqView.rows[r]) : seqView.rows;  // rows picked apart too
  const c0 = s ? s.c0 : 0, c1 = s ? s.c1 : seqView.length - 1;
  const n = rows.length, GAP = 45;  // "-"
  const codes = rows.map((r) => Uint8Array.from(r.seq.slice(c0, c1 + 1), (ch) => ch.charCodeAt(0)));
  const ends = rows.map((r) => [Math.max(r.first, c0) - c0, Math.min(r.last, c1) - c0]);
  const same = Array.from({ length: n }, () => new Float64Array(n));
  const compared = Array.from({ length: n }, () => new Float64Array(n));
  $("matrix-body").textContent = "";
  const pairs = (n * (n - 1)) / 2;
  let done = 0;
  for (let i = 0; i < n; i++) {
    done += n - i - 1;  // progress by pairs: the first rows hold most of them
    for (let j = i + 1; j < n; j++) {
      const a = codes[i], b = codes[j];
      const lo = Math.max(ends[i][0], ends[j][0]), hi = Math.min(ends[i][1], ends[j][1]);
      let sm = 0, cmp = 0;
      for (let k = lo; k <= hi; k++) {
        const x = a[k], y = b[k];
        if (x === GAP && y === GAP) continue;
        cmp++;
        if (x === y) sm++;
      }
      same[i][j] = same[j][i] = sm;
      compared[i][j] = compared[j][i] = cmp;
    }
    if (n > 40 && i % 5 === 4) {  // big: show progress, let the window breathe
      $("matrix-title").textContent = `Computing… ${Math.floor((100 * done) / pairs)} %`;
      await new Promise((ok) => setTimeout(ok));
      if (run !== matrixRun) return;
    }
  }
  matrix = { names: rows.map((r) => seqView.rowLabel(r)), same, compared, c0, c1, partial: !!s };
  if (n > 40) {
    $("matrix-title").textContent = "Drawing the table…";
    await new Promise((ok) => setTimeout(ok));
    if (run !== matrixRun) return;
  }
  renderMatrix();
}

const matrixValue = (i, j) => {
  const cmp = matrix.compared[i][j];
  if (!cmp) return null;
  return matrixShows === "identity" ? (100 * matrix.same[i][j]) / cmp : cmp - matrix.same[i][j];
};

// Red (least alike) through yellow to green (most alike), as in Excel
function heat(t) {
  const stops = [[248, 105, 107], [255, 235, 132], [99, 190, 123]];
  const [p, q, u] = t < 0.5 ? [stops[0], stops[1], t * 2] : [stops[1], stops[2], (t - 0.5) * 2];
  return `rgb(${p.map((v, k) => Math.round(v + (q[k] - v) * u)).join(",")})`;
}

// The table is built as one HTML string (90,000 cells for 300 sequences: building
// them one by one, each with its tooltip, took ~2 s after the computing was done);
// a cell's tooltip is made when the mouse comes over it
function renderMatrix() {
  const { names, c0, c1 } = matrix;
  const n = names.length;
  const identity = matrixShows === "identity";
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = i === j ? null : matrixValue(i, j);
      if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
  }
  const columns = c1 - c0 + 1;
  $("matrix-title").textContent = `${n} sequences · ` +
    (matrix.partial ? `columns ${(c0 + 1).toLocaleString()}–${(c1 + 1).toLocaleString()} (the selection)` : `${columns.toLocaleString()} columns`);
  for (const b of document.querySelectorAll("[data-matrixshows]")) b.classList.toggle("active", b.dataset.matrixshows === matrixShows);
  const colours = Array.from({ length: 101 }, (_, k) => heat(k / 100));  // 101 shades, made once
  const html = ['<table><tr><th class="corner"></th>'];  // the corner stays in place both ways
  names.forEach((name, j) => html.push(`<th title="${esc(name)}">${j + 1}</th>`));
  html.push("</tr>");
  for (let i = 0; i < n; i++) {
    html.push(`<tr><th class="name">${i + 1}  ${esc(names[i])}</th>`);
    for (let j = 0; j < n; j++) {
      if (i === j) { html.push('<td class="self"></td>'); continue; }
      const v = matrixValue(i, j);
      if (v === null) { html.push("<td>–</td>"); continue; }
      const t = hi > lo ? (identity ? (v - lo) / (hi - lo) : (hi - v) / (hi - lo)) : 1;
      html.push(`<td style="background:${colours[Math.round(t * 100)]}">${identity ? v.toFixed(1) : v.toLocaleString()}</td>`);
    }
    html.push("</tr>");
  }
  html.push("</table>");
  $("matrix-body").innerHTML = html.join("");
}

// A cell's tooltip, made when the mouse is over it
function matrixTip(e) {
  const td = e.target.closest("#matrix-body td");
  if (!td || td.title || !matrix) return;
  const i = td.parentElement.rowIndex - 1, j = td.cellIndex - 1;
  if (i === j) return;
  const cmp = matrix.compared[i][j], sm = matrix.same[i][j], names = matrix.names;
  td.title = !cmp ? "No positions in common"
    : `${names[i]} – ${names[j]}: ${((100 * sm) / cmp).toFixed(2)} % identical, ` +
      `${(cmp - sm).toLocaleString()} different of ${cmp.toLocaleString()} positions compared`;
}

// The matrix as text: tab-separated (Copy, pastes into Excel) or CSV
function matrixText(sep) {
  const cell = (v) => (sep === "," && /[",\n]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const lines = [["", ...matrix.names].map(cell).join(sep)];
  matrix.names.forEach((name, i) => {
    const row = matrix.names.map((_, j) => {
      if (i === j) return matrixShows === "identity" ? "100" : "0";
      const v = matrixValue(i, j);
      return v === null ? "" : matrixShows === "identity" ? v.toFixed(2) : String(v);
    });
    lines.push([cell(name), ...row].join(sep));
  });
  return lines.join("\n") + "\n";
}

async function copyMatrix() {
  if (!matrix) return;
  try { await navigator.clipboard.writeText(matrixText("\t")); } catch (e) { return toast("Couldn't copy", true); }
  toast("Matrix copied: paste it into Excel");
}

async function exportMatrix() {
  if (!matrix) return;
  const base = (seqShown.items[0].name || "alignment").replace(/[\\/:*?"<>|\s]+/g, "_");
  saveTextFile(`${base}_${matrixShows === "identity" ? "identity" : "differences"}.csv`, matrixText(","), "text/csv");
}
