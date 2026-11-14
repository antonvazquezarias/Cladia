// ── Protein properties (the side panel's Properties tab) ────────────────
// For a single protein sequence shown in the viewer: the numbers Biopython's
// ProteinAnalysis gives (POST /api/protein/properties, protein_properties.py),
// the composition as bars and a Kyte-Doolittle hydrophobicity plot along the
// sequence. Computed from the sequence as shown (edits included, saved or not),
// shortly after a change. Loaded before app-panel.js, which lists the tab.

const props = { key: "", data: null, request: 0, timer: 0 };

// The protein shown, or null: a single DNA/protein document that is a protein
function proteinShown() {
  const doc = seqShown?.mode === "single" ? seqShown.doc : null;
  return !!doc && !!doc.data.protein && !$("seqview").classList.contains("inactive");
}

function scheduleProperties() {
  clearTimeout(props.timer);
  props.timer = setTimeout(renderProperties, 400);
}

async function renderProperties() {
  const body = $("props-body");
  if (!proteinShown()) { body.replaceChildren(); props.key = ""; return; }
  const sequence = seqShown.doc.sequence, size = Number($("props-window").value);
  const key = `${seqShown.items[0].id}|${size}|${sequence.length}|${sequence.slice(0, 40)}|${sequence.slice(-40)}`;
  if (key === props.key && props.data) return drawProperties(props.data);
  const request = ++props.request;
  try {
    const data = await api("POST", "/api/protein/properties", { sequence, window: size });
    if (request !== props.request || !proteinShown()) return;
    props.key = key; props.data = data;
    drawProperties(data);
  } catch (e) {
    if (request !== props.request) return;
    props.data = null; props.key = "";
    body.replaceChildren(Object.assign(document.createElement("p"), { className: "muted", textContent: e.message }));
    $("props-summary").textContent = "";
  }
}

const fixed = (x, n) => Number(x).toLocaleString(undefined, { minimumFractionDigits: n, maximumFractionDigits: n });

function propertyRows(d) {
  const excluded = Object.entries(d.excluded).map(([c, n]) => `${c} × ${n}`).join(", ");
  return [
    ["Length", `${d.length.toLocaleString()} aa`],
    ...(d.analysed !== d.length ? [["Counted", `${d.analysed.toLocaleString()} aa (left out: ${excluded || "gaps and stops"})`]] : []),
    ["Molecular weight", `${fixed(d.weight / 1000, 2)} kDa`],
    ["Isoelectric point (pI)", fixed(d.pI, 2)],
    ["Charge at pH 7", fixed(d.charge, 1)],
    ["GRAVY (mean hydropathy)", fixed(d.gravy, 3)],
    ["Aromaticity", fixed(d.aromaticity, 3)],
    ["Instability index", `${fixed(d.instability, 1)} (${d.instability > 40 ? "unstable" : "stable"} by Guruprasad)`],
    ["Extinction coefficient, 280 nm", `${d.extinction.reduced.toLocaleString()} M⁻¹cm⁻¹` +
      (d.extinction.cystines !== d.extinction.reduced ? ` (${d.extinction.cystines.toLocaleString()} with cystines)` : "")],
    ["Helix / turn / sheet", `${fixed(100 * d.structure.helix, 0)} / ${fixed(100 * d.structure.turn, 0)} / ${fixed(100 * d.structure.sheet, 0)} %`],
  ];
}

function drawProperties(d) {
  const body = $("props-body");
  $("props-summary").textContent = `${d.length.toLocaleString()} aa · ${fixed(d.weight / 1000, 1)} kDa · pI ${fixed(d.pI, 1)}`;
  const table = document.createElement("table");
  for (const [name, value] of propertyRows(d)) {
    const tr = table.insertRow();
    tr.insertCell().textContent = name;
    tr.insertCell().textContent = value;
  }
  const heading = (text) => Object.assign(document.createElement("h4"), { textContent: text });
  const bars = document.createElement("div");
  bars.className = "props-bars";
  const most = Math.max(...Object.values(d.composition), 1);
  for (const [aa, share] of Object.entries(d.composition).sort((a, b) => b[1] - a[1])) {
    const bar = document.createElement("i");
    bar.style.width = `${(100 * share) / most}%`;
    bars.append(Object.assign(document.createElement("span"), { textContent: aa }), bar,
                Object.assign(document.createElement("span"), { textContent: `${fixed(share, 1)} %` }));
  }
  const plot = document.createElement("canvas");
  plot.id = "props-plot";
  const note = Object.assign(document.createElement("p"), { className: "muted", textContent:
    d.hydrophobicity.values.length ? `Kyte-Doolittle, window of ${d.hydrophobicity.window}: above 0 hydrophobic, below 0 hydrophilic.`
      : "Too short for a hydrophobicity plot (3 residues are needed)." });
  body.replaceChildren(table, heading("Hydrophobicity along the sequence"), ...(d.hydrophobicity.values.length ? [plot] : []), note,
                       heading("Composition"), bars);
  if (d.hydrophobicity.values.length) drawHydrophobicity(plot, d);
}

// The curve over the sequence's length, a zero line, the scale at the left
function drawHydrophobicity(canvas, d) {
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio);
  const g = canvas.getContext("2d");
  g.scale(ratio, ratio);
  const { positions, values } = d.hydrophobicity;
  const lo = Math.min(-1, ...values), hi = Math.max(1, ...values), pad = 22;
  const x = (p) => pad + ((p - 1) / Math.max(1, d.length - 1)) * (w - pad - 6);
  const y = (v) => 6 + ((hi - v) / (hi - lo)) * (h - 12);
  g.font = "10px sans-serif"; g.fillStyle = "#777"; g.strokeStyle = "#ccc";
  for (const tick of [Math.ceil(lo), 0, Math.floor(hi)]) {
    g.beginPath(); g.moveTo(pad, y(tick)); g.lineTo(w - 6, y(tick)); g.lineWidth = tick === 0 ? 1.2 : 0.5; g.stroke();
    g.fillText(String(tick), 2, y(tick) + 3);
  }
  g.beginPath();
  values.forEach((v, i) => (i ? g.lineTo(x(positions[i]), y(v)) : g.moveTo(x(positions[i]), y(v))));
  g.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#2f6f5e";
  g.lineWidth = 1.5; g.stroke();
}

// the plot is drawn at its width: again when the panel is resized or maximised
function redrawProperties() {
  const plot = $("props-plot");
  if (plot && plot.offsetParent && props.data?.hydrophobicity.values.length) drawHydrophobicity(plot, props.data);
}
new ResizeObserver(redrawProperties).observe($("props-body"));
$("props-window").onchange = () => { props.key = ""; renderProperties(); };
$("props-copy").onclick = () => {
  if (!props.data) return;
  const rows = [...propertyRows(props.data), ...Object.entries(props.data.composition).map(([aa, share]) => [`% ${aa}`, share])];
  const text = rows.map((r) => r.join("\t")).join("\n");
  navigator.clipboard?.writeText(text).then(() => toast("Properties copied"), () => toast("Couldn't copy", true));
};
