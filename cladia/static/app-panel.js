// ── The side panel and the annotation table ──────────────────────────────
// A panel beside the view (collapsible, resizable, "maximise" fills the work area)
// with two tabs on a strip at its right edge: Annotations (the features of what
// is shown as a table, below) and Metadata (app.js: the item open in the viewer).
// Loaded after app-tools.js: it uses the viewer's and the editor's functions.

const SIDE_KEY = "cladia.side";
const SIDE_DEFAULT = 440;
const sideState = { open: false, tab: "annotations", width: SIDE_DEFAULT, ...loadJSON(SIDE_KEY), max: false };
const saveSide = () => { try { localStorage.setItem(SIDE_KEY, JSON.stringify({ open: sideState.open, tab: sideState.tab, width: sideState.width })); } catch (e) {} };

const SIDE_TABS = {
  annotations: { title: "Annotations", pane: "annotationview", available: () => annotationSources() !== null },
  identity: { title: "Identity matrix", pane: "matrixview", available: () => alignmentShown() },
  dotplot: { title: "Dot plot", pane: "dotplotview", available: () => dotplotShown() },
  properties: { title: "Protein properties", pane: "propertiesview", available: () => proteinShown() },
  variants: { title: "Variants", pane: "variantview", available: () => variantsShown() },
  checks: { title: "Submission checks", pane: "checksview", available: () => annotationSources()?.some((s) => s.doc) || false },
  metadata: { title: "Metadata", pane: "metadataview", available: () => shownIds.length === 1 && !itemById(shownIds[0])?.missing },
};

// The tab showing: the one chosen if it applies to what is shown, else the other, else none
function sideTabNow() {
  if (!sideState.open || !shownIds.length) return null;
  const order = [sideState.tab, ...Object.keys(SIDE_TABS).filter((k) => k !== sideState.tab)];
  return order.find((k) => SIDE_TABS[k].available()) || null;
}

function renderSidePanel() {
  const now = sideTabNow(), any = shownIds.length > 0;
  $("side-tabs").hidden = !any;
  for (const b of document.querySelectorAll("[data-sidetab]")) {
    b.disabled = !SIDE_TABS[b.dataset.sidetab].available();
    b.classList.toggle("active", now === b.dataset.sidetab);
  }
  $("side-panel").hidden = !now;
  $("workarea").classList.toggle("side-max", !!now && sideState.max);
  $("workarea").style.setProperty("--side-width", `${sideState.width}px`);
  $("side-title").textContent = now ? SIDE_TABS[now].title : "";
  $("side-max").innerHTML = sideState.max ? ICONS.restore : ICONS.maximize;
  $("side-max").setAttribute("aria-label", sideState.max ? "Back beside the view" : "Fill the work area");
  for (const [name, tab] of Object.entries(SIDE_TABS)) $(tab.pane).hidden = name !== now || (name === "variants" && variantMode === "matrix");
  $("variantmatrixview").hidden = !(now === "variants" && variantMode === "matrix");  // the Variants tab's other view
  redrawProperties();  // (a plot is drawn at the panel's width)
  redrawDotplot();
}

// The panel for what is shown, soon (a view changed: the sequence viewer gave way to a graph, say)
let sideFrame = 0;
function scheduleSidePanel() {
  if (sideFrame) return;
  sideFrame = requestAnimationFrame(() => { sideFrame = 0; syncSidePanel(); });
}

// After what is shown changed (a selection): the panel for it
function syncSidePanel() {
  renderSidePanel();
  const now = sideTabNow();
  if (now === "metadata") showMetadata();
  else if (now === "annotations") renderAnnotations();
  else if (now === "variants") renderVariants();
  else if (now === "checks") renderChecks();
  else if (now === "properties") renderProperties();
  else if (now === "dotplot") renderDotplot();
  if (now === "identity" || now === "variants") updateMatrixView();
  if (now !== "annotations") atCancelEdit();
  scheduleChecks();
}

function openSideTab(tab) {
  if (sideState.open && sideTabNow() === tab) sideState.open = false;
  else { sideState.open = true; sideState.tab = tab; }
  saveSide();
  syncSidePanel();
}

$("side-close").onclick = () => { sideState.open = false; sideState.max = false; saveSide(); syncSidePanel(); };
$("side-max").onclick = () => { sideState.max = !sideState.max; renderSidePanel(); };
for (const b of document.querySelectorAll("[data-sidetab]")) b.onclick = () => openSideTab(b.dataset.sidetab);
for (const b of document.querySelectorAll("[data-variantmode]")) b.onclick = () => setVariantMode(b.dataset.variantmode);

const variantState = { data: null, parsed: null, visible: [], limit: 200, expanded: null, renderKey: null };
function renderVariants() {
  const data = seqShown?.variantData;
  const list = $("variant-list");
  if (!data) { list.replaceChildren(); return; }
  if (variantState.data !== data) {
    variantState.data = data;
    variantState.parsed = seqShown.parsedVariants || parseVariantVcf(data.vcf);
    variantState.limit = 200;
    variantState.expanded = null;
    variantState.renderKey = null;
  }
  const renderKey = JSON.stringify([data.stale, $("variant-filter").value, $("variant-type").value, $("variant-missing").value,
    $("variant-min-alt").value, variantState.limit, variantState.expanded?.line]);
  if (renderKey === variantState.renderKey) return;
  variantState.renderKey = renderKey;
  const parsed = variantState.parsed;
  const visible = filterVariantSites(parsed.sites, {
    query: $("variant-filter").value, type: $("variant-type").value, missing: $("variant-missing").value,
    minAlternate: Math.max(0, Number($("variant-min-alt").value) || 0),
  });
  variantState.visible = visible;
  $("variant-summary").textContent = `${visible.length} of ${parsed.sites.length} sites`;
  $("variant-status").textContent = `${data.reference} reference${data.stale ? " · Alignment changed; rerun detection" : ""}`;
  $("variant-more").hidden = visible.length <= variantState.limit;
  $("variant-export").disabled = !visible.length;
  const reference = seqView?.rows?.find((row) => row.name === data.reference);
  const columns = reference && !data.stale ? seqView.variantBaseColumns : [];
  const fragment = document.createDocumentFragment();
  for (const site of visible.slice(0, variantState.limit)) {
    const entry = document.createElement("div");
    entry.className = "variant-entry";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "variant-row";
    button.title = data.stale ? "Calls may no longer match this alignment" :
      `Go to reference position ${site.position}; show sample calls${site.repeat ? ` · repeat motif ${site.repeat}` : ""}`;
    for (const value of [site.position, site.type === "Repeat indel" ? "STR" : site.type,
                         `${site.ref} → ${site.alt}`, site.alternateCount, site.missingCount]) {
      const cell = document.createElement("span");
      cell.textContent = value;
      button.append(cell);
    }
    button.onclick = () => {
      variantState.expanded = variantState.expanded === site ? null : site;
      const col = columns[site.position - 1];
      if (col !== undefined && reference) {
        const row = seqView.rows.indexOf(reference);
        seqView.select(col, col, row, row);
        seqView.centerOn(col);
      }
      renderVariants();
    };
    entry.append(button);
    if (variantState.expanded === site) {
      const calls = document.createElement("div");
      calls.className = "variant-calls";
      for (const call of site.calls) {
        const line = document.createElement("div");
        line.className = "variant-call";
        const name = document.createElement("span");
        name.textContent = call.name;
        const allele = document.createElement("span");
        allele.textContent = `${call.bases} (${call.gt})`;
        if (call.missing) allele.className = "muted";
        line.append(name, allele);
        calls.append(line);
      }
      entry.append(calls);
    }
    fragment.append(entry);
  }
  list.replaceChildren(fragment);
  if (!visible.length) list.textContent = parsed.sites.length ? "No sites match these filters" : "No SNP sites detected";
}
for (const id of ["variant-filter", "variant-min-alt"]) $(id).addEventListener("input", () => { variantState.limit = 200; renderVariants(); });
for (const id of ["variant-type", "variant-missing"]) $(id).addEventListener("change", () => { variantState.limit = 200; renderVariants(); });
$("variant-more").onclick = () => { variantState.limit += 200; renderVariants(); };
$("variant-export").onclick = () => {
  const name = seqShown?.alignment?.name || "alignment";
  if (variantState.data === seqShown?.variantData && variantState.visible.length)
    saveTextFile(`${name.replace(/[^\w.-]+/g, "_")}_variants_filtered.vcf`,
                 filteredVariantVcf(variantState.parsed, variantState.visible), "text/vcf");
};
$("side-edge").addEventListener("mousedown", (e) => {
  e.preventDefault();
  document.body.classList.add("resizing");
  const area = $("workarea").getBoundingClientRect();
  const move = (ev) => {
    sideState.width = Math.round(Math.max(280, Math.min(area.width * 0.8, area.right - 28 - ev.clientX)));
    $("workarea").style.setProperty("--side-width", `${sideState.width}px`);
  };
  const up = () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    document.body.classList.remove("resizing");
    saveSide();
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
});
$("side-edge").addEventListener("dblclick", () => { sideState.width = SIDE_DEFAULT; saveSide(); renderSidePanel(); });

// ── Submission checks ────────────────────────────────────────────────────
// The sequences as edited go to /api/checks (checks.py) a moment after each
// change while the Annotations or Checks tab shows; the problems come back per
// sequence and feature, and are used only while the documents are as they were
// when asked (`chk.stamp`). Lists over CHECK_AUTO sequences wait for the button.

const CHECK_AUTO = 50;
const chk = { stamp: "", results: [], request: 0, timer: 0 };
const checksFor = (row, index) => (chk.stamp && chk.stamp === docsStamp() ? chk.results[row]?.filter((p) => p.feature === index) || [] : []);

function scheduleChecks(force = false) {
  const now = sideTabNow();
  if (now !== "annotations" && now !== "checks") return;
  const sources = annotationSources()?.filter((s) => s.doc) || [];
  if (!sources.length || chk.stamp === docsStamp()) return;
  if (sources.length > CHECK_AUTO && !force) return;
  clearTimeout(chk.timer);
  chk.timer = setTimeout(() => runChecks(sources), force ? 0 : 500);
}

async function runChecks(sources) {
  const stamp = docsStamp(), request = ++chk.request;
  try {
    const found = await api("POST", "/api/checks", { sequences: sources.map((s) => ({ ...s.doc.toData(), geneticCode: docCode(s.doc) })) });
    if (request !== chk.request) return;
    chk.results = [];
    sources.forEach((s, i) => { chk.results[s.row] = found[i].problems; });
    chk.stamp = stamp;
    scheduleAnnotations();
  } catch (e) { if (request === chk.request) toast(e.message, true); }
}

// The Checks tab: the problems per sequence; a click selects the feature (table and viewer)
function renderChecks() {
  const box = $("checks-list"), sources = annotationSources() || [];
  box.replaceChildren();
  const fresh = chk.stamp === docsStamp();
  const all = sources.flatMap((s) => (fresh ? chk.results[s.row] || [] : []).map((p) => ({ ...p, src: s })));
  const errors = all.filter((p) => p.severity === "error").length, warnings = all.length - errors;
  const many = sources.length > CHECK_AUTO && !fresh;
  $("checks-summary").textContent = many ? `${sources.length} sequences: check them when you want` : !fresh ? "Checking…"
    : !all.length ? "No problems found" : `${errors} error${errors === 1 ? "" : "s"}, ${warnings} warning${warnings === 1 ? "" : "s"}`;
  $("checks-run").hidden = !many;
  for (const p of all) {
    const row = document.createElement("div");
    row.className = `chk-item ${p.severity}`;
    row.textContent = (sources.length > 1 ? `${p.src.name}: ` : "") + p.message;
    if (p.feature !== null) {
      row.classList.add("link");
      row.onclick = () => {
        at.sources = sources;
        at.rows = buildAnnotationRows(sources);
        const r = at.rows.find((x) => x.src === p.src && x.index === p.feature);
        if (!r) return;
        at.chosen = new Set([r.key]);
        at.anchor = r.key;
        revealAnnotation(r);
      };
    }
    box.append(row);
  }
}
$("checks-run").onclick = () => scheduleChecks(true);

// The files for a submission: the sequences as saved (FASTA + feature table, side by
// side) and, with a submitter chosen, the template (.sbt) with its authors and contact.
// One small dialog asks for both: the submitter (Settings > Submitters), this
// submission's own authors / title / release date over the preset's, the locus tag prefix.
const SUBMISSION_KEY = "cladia.submission";
let submissionDo = null;  // what the dialog does with its request; false back = stay open
let submissionPresets = [];

function fillSubmission(form) {
  const preset = submissionPresets.find((p) => p.name === form.submitter.value);
  $("submission-details").hidden = !preset;
  $("submission-none").hidden = submissionPresets.length > 0;
  if (preset) {
    form.authors.value = preset.authors;
    form.title.value = preset.title;
    form.hold.value = preset.hold;
  }
}

async function askSubmission(title, button, action) {
  if (hasUnsavedChanges()) return toast("Save (or undo) your changes first (Ctrl+S)", true);
  const form = $("submission-dialog").querySelector("form");
  submissionPresets = (await api("GET", "/api/settings")).submitters || [];
  let last = {};
  try { last = JSON.parse(localStorage.getItem(SUBMISSION_KEY)) || {}; } catch (e) {}
  form.submitter.innerHTML = submissionPresets.map((p) => `<option>${esc(p.name)}</option>`).join("") +
    '<option value="">No template</option>';
  form.submitter.value = submissionPresets.some((p) => p.name === last.submitter) ? last.submitter : (submissionPresets[0]?.name ?? "");
  form.prefix.value = last.prefix || "";
  fillSubmission(form);
  $("submission-title").textContent = title;
  $("submission-run").textContent = button;
  submissionDo = action;
  form.querySelector(".error").hidden = true;
  $("submission-dialog").showModal();
}

$("submission-dialog").querySelector("[name=submitter]").addEventListener("change", (e) => fillSubmission(e.target.form));
$("submission-dialog").querySelector("form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "run") return;
  e.preventDefault();
  const form = e.target, error = form.querySelector(".error");
  const request = { locus_tag_prefix: form.prefix.value.trim(), submitter: form.submitter.value,
                    overrides: { authors: form.authors.value, title: form.title.value.trim(), hold: form.hold.value } };
  try {
    error.hidden = true;
    if (await submissionDo({ ids: shownIds, ...request }) === false) return;
    try { localStorage.setItem(SUBMISSION_KEY, JSON.stringify({ submitter: request.submitter, prefix: request.locus_tag_prefix })); } catch (e) {}
    $("submission-dialog").close();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
});

// "; left out: /ID x40, /Dbxref x20": qualifiers that aren't GenBank's, not written to the table
const leftOut = (dropped) => (Object.keys(dropped || {}).length
  ? `; left out, not GenBank qualifiers: ${Object.entries(dropped).map(([k, n]) => `/${k} x${n}`).join(", ")}` : "");

$("checks-validate").onclick = () => askSubmission("Validate with NCBI", "Validate", async (req) => {
  await api("POST", "/api/jobs/validate", req);
  toast("Validating with table2asn: the report will be in the job's log");
  pollJobs();
});

$("checks-export").onclick = () => askSubmission("Export for GenBank", "Export", async (req) => {
  const name = (shownIds.length === 1 ? itemById(shownIds[0])?.label : "submission").replace(/[^\w.-]+/g, "_");
  if (inPywebview()) {
    const path = await window.pywebview.api.save_file(`${name}.tbl`, "tbl");
    if (!path) return false;
    const done = await api("POST", "/api/export/submission", { ...req, path });
    toast(`Saved ${done.path}, the .fsa${done.sbt ? " and the .sbt" : ""} beside it` + leftOut(done.dropped));
  } else {
    const done = await api("POST", "/api/export/submission", req);
    await saveTextFile(`${name}.tbl`, done.tbl, "text/plain");
    await saveTextFile(`${name}.fsa`, done.fsa, "text/plain");
    if (done.sbt) await saveTextFile(`${name}.sbt`, done.sbt, "text/plain");
    if (Object.keys(done.dropped || {}).length) toast(leftOut(done.dropped).slice(2));
  }
});

// ── The annotation table ─────────────────────────────────────────────────
// One row per feature of what is shown: a sequence's, or every sequence of a
// list (a Sequence column then), read from the documents being edited, so edits
// in the table, the viewer and the dialog are the same undo steps. A row click
// selects the feature in the viewer (and the viewer's selection finds its row);
// Ctrl/Shift+click select several rows, for Set… and Delete. Only the rows in
// view are built (a big list has tens of thousands).

const AT_KEY = "cladia.annotations";
const AT_ROW = 22;
const AT_COLUMNS = [
  { key: "seq", label: "Sequence", width: 70, multi: true },
  { key: "name", label: "Name", width: 104, edit: "text" },
  { key: "type", label: "Type", width: 90, edit: "type" },
  { key: "start", label: "Start", width: 56, num: true, edit: "number" },
  { key: "end", label: "End", width: 56, num: true, edit: "number" },
  { key: "length", label: "Length", width: 58, num: true },
  { key: "strand", label: "Strand", width: 52, edit: "strand" },
  { key: "gene", label: "Gene", width: 80, edit: "qualifier", q: "gene" },
  { key: "product", label: "Product", width: 150, edit: "qualifier", q: "product" },
  { key: "note", label: "Note", width: 160, edit: "qualifier", q: "note" },
  { key: "location", label: "Location", width: 150, edit: "location" },
  { key: "protein", label: "Protein (aa)", width: 80, num: true },
  { key: "problems", label: "Problems", width: 62, num: true },
];
const AT_DEFAULT = ["seq", "name", "type", "start", "end", "strand"];
const AT_TYPES = ["gene", "CDS", "mRNA", "tRNA", "rRNA", "exon", "intron", "misc_feature", "misc_RNA", "ncRNA", "repeat_region",
                  "D-loop", "rep_origin", "regulatory", "5'UTR", "3'UTR", "ORF", "STS", "primer_bind", "variation"];
const NAME_KEYS = ["label", "gene", "product", "locus_tag", "note"];  // as genbank.py: where a feature's name comes from
const at = { rows: [], view: [], sources: [], chosen: new Set(), anchor: null, editing: null, sortKey: null,
             filter: "", type: "", onlyProblems: false, typesKey: "", columns: [...AT_DEFAULT], widths: {}, sort: null, ...loadJSON(AT_KEY) };
at.chosen = new Set();
const saveAt = () => { try { localStorage.setItem(AT_KEY, JSON.stringify({ columns: at.columns, widths: at.widths, sort: at.sort })); } catch (e) {} };

// What the table lists: [{ row, name, doc, features, length, editable }]; null when nothing here has features to list
function annotationSources() {
  if (!seqShown || $("seqview").classList.contains("inactive")) return null;  // (seqShown is left behind when another view takes over)
  // A comparison's genomes and an alignment's rows (their sequences' features, clipped to the row): shown, not edited
  const cmp = seqShown.comparison;
  if (cmp) return cmp.genomes.map((g, row) => ({ row, doc: null, features: g.data.features || [], length: g.data.sequence.length,
                                                 editable: false, name: g.name }));
  if (seqShown.mode === "alignment" && seqShown.alignSourcesAll && seqView?.rows) {
    const rows = seqView.rows.map((r, row) => ({ row, doc: null, features: seqShown.alignSourcesAll[r.name]?.features || [],
                                                 length: r.bases, editable: false, name: r.name })).filter((s) => s.features.length);
    return rows.length ? rows : null;
  }
  if (seqShown.docs?.length) {
    const several = seqShown.mode === "sequences";
    return seqShown.docs.map((doc, row) => ({ row, doc, features: doc.features, length: doc.length, editable: true,
      name: several ? seqShown.rowNames[row] : seqShown.items[0].label }));
  }
  const ref = seqShown.mapping?.reference;  // a mapping's reference, a graph's track: shown, not edited
  if (ref?.features) return [{ row: 0, doc: null, features: ref.features, length: ref.sequence.length, editable: false, name: ref.name }];
  const track = seqShown.track?.genome?.data;
  if (track?.features) return [{ row: 0, doc: null, features: track.features, length: track.sequence.length, editable: false, name: seqShown.track.genome.name }];
  return null;
}

const featureNameOf = (notes, type) => { for (const k of NAME_KEYS) if (notes?.[k]?.[0]) return notes[k][0]; return type; };
const strandText = (s) => (s === -1 ? "−" : "+");

// The rows (all of them, before the filter): one object per feature with what the columns show
function buildAnnotationRows(sources) {
  const rows = [];
  for (const src of sources) {
    src.features.forEach((f, index) => {
      const parts = f.locations?.length ? f.locations : [{ start: f.start, end: f.end }];
      const length = parts.reduce((n, p) => n + (p.end >= p.start ? p.end - p.start + 1 : 0), 0);
      const notes = f.notes || {};
      rows.push({ key: `${src.row}:${index}`, src, index, f, parts, length, seq: src.name,
        name: f.name || "", type: f.type || "", start: f.start + 1, end: f.end + 1, strand: f.strand === -1 ? -1 : 1,
        gene: notes.gene?.[0] || "", product: notes.product?.[0] || "", note: (notes.note || []).join("; "),
        protein: f.type === "CDS" ? (notes.translation?.[0]?.length || Math.floor(length / 3)) : null,
        multi: parts.length > 1, hidden: hiddenType(f.type),
        issues: checksFor(src.row, index) });
    });
  }
  return rows;
}

const locationOf = (r) => (r.location ??= SeqEdit.locationText(r.f));
function atCell(r, key) {
  switch (key) {
    case "seq": return r.seq;
    case "strand": return strandText(r.strand);
    case "location": return locationOf(r);
    case "start": case "end": case "length": return r[key].toLocaleString();
    case "protein": return r.protein === null ? "" : r.protein.toLocaleString();
    case "problems": return r.issues.length ? String(r.issues.length) : "";
    default: return r[key] ?? "";
  }
}
const atValue = (r, key) => (key === "location" ? locationOf(r) : key === "problems" ? r.issues.length || null : r[key]);

// Filtered and sorted (the file's own order when no column sorts)
function atFilterAndSort() {
  const q = at.filter.trim().toLowerCase();
  let view = at.rows.filter((r) => (!at.type || r.type === at.type) && (!at.onlyProblems || r.issues.length)
    && (!q || `${r.name}\t${r.type}\t${r.product}\t${r.gene}\t${r.note}`.toLowerCase().includes(q)));
  if (at.sort) {
    const { key, dir } = at.sort;
    const num = AT_COLUMNS.find((c) => c.key === key)?.num;
    view = view.map((r, i) => [r, i]).sort(([a, i], [b, j]) => {
      const x = atValue(a, key), y = atValue(b, key);
      const blank = (v) => v === null || v === undefined || v === "";
      if (blank(x) !== blank(y)) return blank(x) ? 1 : -1;  // empty last, either way
      const c = num ? x - y : atCollator.compare(String(x), String(y));
      return (c || (a.src.row - b.src.row) || (a.index - b.index) || (i - j)) * (blank(x) ? 1 : dir);
    }).map(([r]) => r);
  }
  at.view = view;
}

const atCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const atColumns = () => at.columns.map((k) => AT_COLUMNS.find((c) => c.key === k))
  .filter((c) => c && (!c.multi || at.sources.length > 1));
const atWidth = (c) => at.widths[c.key] || c.width;

let atFrame = 0;
// Redraw the table soon (after every edit, undo, save: renderEditButtons calls this)
function scheduleAnnotations() {
  if (atFrame) return;
  atFrame = requestAnimationFrame(() => {
    atFrame = 0;
    renderSidePanel();
    if (sideTabNow() === "annotations" && !at.editing) renderAnnotations();
    if (sideTabNow() === "checks") renderChecks();
    if (sideTabNow() === "properties") scheduleProperties();  // an edit changes them
    scheduleChecks();
  });
}

function renderAnnotations() {
  const sources = annotationSources();
  at.sources = sources || [];
  at.rows = sources ? buildAnnotationRows(sources) : [];
  const keys = new Set(at.rows.map((r) => r.key));
  for (const k of [...at.chosen]) if (!keys.has(k)) at.chosen.delete(k);
  // the type filter: the types there are, with counts
  const counts = {};
  for (const r of at.rows) counts[r.type] = (counts[r.type] || 0) + 1;
  const typesKey = Object.entries(counts).sort().join("|");
  if (typesKey !== at.typesKey) {
    at.typesKey = typesKey;
    const select = $("at-type");
    select.replaceChildren(new Option("All types", ""), ...Object.keys(counts).sort().map((t) => new Option(`${t} (${counts[t]})`, t)));
    if (!(at.type in counts)) at.type = "";
    $("at-types").replaceChildren(...[...new Set([...AT_TYPES, ...Object.keys(counts)])].map((t) => new Option(t)));
  }
  $("at-type").value = at.type;
  if ($("at-filter").value !== at.filter) $("at-filter").value = at.filter;
  atFilterAndSort();
  renderAtHead();
  renderAtBody(true);
}

function renderAtHead() {
  const head = $("at-head");
  head.replaceChildren();
  const total = atColumns().reduce((n, c) => n + atWidth(c), 0);
  head.style.width = `${total}px`;
  for (const c of atColumns()) {
    const h = document.createElement("div");
    h.className = `at-h${c.num ? " num" : ""}`;
    h.style.width = `${atWidth(c)}px`;
    h.textContent = c.label + (at.sort?.key === c.key ? (at.sort.dir > 0 ? " ▲" : " ▼") : "");
    h.title = "Click to sort (again: the other way, then the file's order)";
    h.onclick = () => {
      at.sort = at.sort?.key !== c.key ? { key: c.key, dir: 1 } : at.sort.dir > 0 ? { key: c.key, dir: -1 } : null;
      saveAt(); atCancelEdit(); renderAnnotations();
    };
    const grip = document.createElement("span");
    grip.className = "grip";
    grip.onclick = (e) => e.stopPropagation();
    grip.onmousedown = (e) => {
      e.preventDefault(); e.stopPropagation();
      const x0 = e.clientX, w0 = atWidth(c);
      const move = (ev) => { at.widths[c.key] = Math.max(36, w0 + ev.clientX - x0); renderAtHead(); renderAtBody(true); };
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); saveAt(); };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    };
    h.append(grip);
    head.append(h);
  }
}

// The rows in view (a few more each side); force: from scratch
function renderAtBody(force = false) {
  const scroller = $("at-scroller"), rowsBox = $("at-rows"), spacer = $("at-spacer");
  const cols = atColumns(), total = cols.reduce((n, c) => n + atWidth(c), 0);
  spacer.style.height = `${at.view.length * AT_ROW}px`;
  spacer.style.width = `${total}px`;
  const first = Math.max(0, Math.floor(scroller.scrollTop / AT_ROW) - 8);
  const last = Math.min(at.view.length, Math.ceil((scroller.scrollTop + scroller.clientHeight) / AT_ROW) + 8);
  if (!force && rowsBox.dataset.first === String(first) && rowsBox.dataset.last === String(last)) return;
  rowsBox.dataset.first = first; rowsBox.dataset.last = last;
  rowsBox.style.top = `${first * AT_ROW}px`;
  const fragment = document.createDocumentFragment();
  for (let i = first; i < last; i++) {
    const r = at.view[i], div = document.createElement("div");
    div.className = `at-row${at.chosen.has(r.key) ? " chosen" : ""}${r.hidden ? " hidden-type" : ""}${r.issues.some((p) => p.severity === "error") ? " flag" : ""}`;
    div.dataset.i = i;
    if (r.issues.length) div.title = r.issues.map((p) => p.message).join("\n");
    if (r.hidden) div.title = `${r.type} features are hidden in the viewer (Show ▾)`;
    for (const c of cols) {
      const cell = document.createElement("div");
      cell.className = `at-cell${c.num ? " num" : ""}`;
      cell.style.width = `${atWidth(c)}px`;
      cell.dataset.key = c.key;
      if (c.key === "type") {
        const sw = document.createElement("span");
        sw.className = "at-swatch";
        sw.style.background = featureColor(r.type);
        cell.append(sw);
      }
      cell.append(document.createTextNode(atCell(r, c.key)));
      cell.title = atCell(r, c.key);
      div.append(cell);
    }
    fragment.append(div);
  }
  rowsBox.replaceChildren(fragment);
  const n = at.view.length, m = at.rows.length;
  $("at-empty").hidden = n > 0;
  $("at-empty").textContent = !at.sources.length ? "Nothing here has features to list." : !m ? "No annotations yet." : "No feature matches the filter.";
  atUpdateActions();
}

// The selection's bar: how many, and whether they can be changed
function atUpdateActions() {
  const k = at.chosen.size, n = at.view.length, m = at.rows.length;
  $("at-actions").hidden = !k;
  $("at-status").textContent = k ? `${k.toLocaleString()} selected of ${m.toLocaleString()}`
    : !m ? "" : n === m ? `${m.toLocaleString()} feature${m === 1 ? "" : "s"}` : `${n.toLocaleString()} of ${m.toLocaleString()} features`;
  const can = at.sources.some((s) => s.editable) && editable();
  $("at-set").disabled = $("at-delete").disabled = !can;
  $("at-set").title = $("at-delete").title = can ? "" : "Switch to Editable first (the Edit group in the bar)";
}

// Which rows are selected: the classes of the rows built (not rebuilt: a double-click needs its row to stay)
function atMarkChosen() {
  for (const div of $("at-rows").children) div.classList.toggle("chosen", at.chosen.has(at.view[Number(div.dataset.i)]?.key));
  atUpdateActions();
}

$("at-scroller").addEventListener("scroll", () => { if (at.editing) atFinishEdit(true); renderAtBody(); });
$("at-filter").addEventListener("input", (e) => { at.filter = e.target.value; atCancelEdit(); atFilterAndSort(); $("at-scroller").scrollTop = 0; renderAtBody(true); });
$("at-problems").addEventListener("change", (e) => { at.onlyProblems = e.target.checked; atCancelEdit(); atFilterAndSort(); $("at-scroller").scrollTop = 0; renderAtBody(true); });
$("at-type").addEventListener("change", (e) => { at.type = e.target.value; atCancelEdit(); atFilterAndSort(); $("at-scroller").scrollTop = 0; renderAtBody(true); });

// Columns: which are shown
$("at-columns").onclick = () => {
  const panel = $("at-columns-panel");
  panel.hidden = !panel.hidden;
  $("at-columns").setAttribute("aria-expanded", String(!panel.hidden));
  if (panel.hidden) return;
  panel.replaceChildren();
  for (const c of AT_COLUMNS) {
    if (c.multi && at.sources.length < 2) continue;
    const label = document.createElement("label");
    label.className = "toggle";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = at.columns.includes(c.key);
    box.onchange = () => {
      at.columns = AT_COLUMNS.map((x) => x.key).filter((k) => (k === c.key ? box.checked : at.columns.includes(k)));
      if (!atColumns().length) { at.columns = [...AT_DEFAULT]; }
      if (at.sort && !at.columns.includes(at.sort.key)) at.sort = null;
      saveAt(); renderAnnotations();
    };
    label.append(box, ` ${c.label}`);
    panel.append(label);
  }
};
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#at-columns-panel, #at-columns")) { $("at-columns-panel").hidden = true; $("at-columns").setAttribute("aria-expanded", "false"); }
});

// ── Selecting rows, and the viewer ──
const atRowAt = (e) => { const div = e.target.closest(".at-row"); return div ? at.view[Number(div.dataset.i)] : null; };

function atChoose(keys, anchor = true) {
  at.chosen = new Set(keys);
  if (anchor) at.anchor = [...at.chosen].pop() ?? null;
  atMarkChosen();
}

// The row's feature selected in the viewer, in the middle
let atSyncing = false;
function revealAnnotation(r) {
  if (!seqView || !seqShown || $("seqview").classList.contains("inactive")) return;
  const vrow = seqView.rows[r.src.row], vf = vrow?.features?.find((x) => x.index === r.index);
  if (!vf) return;  // not drawn (its type is hidden)
  atSyncing = true;
  try { seqView.revealFeature(vf, r.src.row); } finally { atSyncing = false; }
}

$("at-rows").addEventListener("mousedown", (e) => {
  if (e.button !== 0 || e.target.closest("input, select")) return;
  const r = atRowAt(e);
  if (!r) return;
  if (at.editing) atFinishEdit(true);
  if (e.shiftKey && at.anchor) {  // a stretch of the rows shown
    const from = at.view.findIndex((x) => x.key === at.anchor), to = at.view.indexOf(r);
    const [a, b] = from < 0 ? [to, to] : [Math.min(from, to), Math.max(from, to)];
    atChoose(at.view.slice(a, b + 1).map((x) => x.key), false);
  } else if (e.ctrlKey || e.metaKey) {
    const keys = new Set(at.chosen);
    keys.has(r.key) ? keys.delete(r.key) : keys.add(r.key);
    atChoose(keys);
  } else {
    atChoose([r.key]);
    revealAnnotation(r);
  }
  $("at-scroller").focus({ preventScroll: true });
});

// The viewer's selection is a feature (or several with the same range): their rows
function annotationsFollowViewer(s) {
  if (atSyncing || sideTabNow() !== "annotations" || !s || s.consensus || s.r0 !== s.r1) return;
  const vrow = seqView.rows[s.r0];
  const hit = (vrow?.features || []).filter((f) => f.c0 === s.c0 && f.c1 === s.c1);
  if (!hit.length) return;
  const keys = hit.map((f) => `${s.r0}:${f.index}`);
  at.chosen = new Set(keys.filter((k) => at.rows.some((r) => r.key === k)));
  at.anchor = keys[0];
  const i = at.view.findIndex((r) => at.chosen.has(r.key));
  if (i >= 0) {
    const scroller = $("at-scroller"), top = i * AT_ROW;
    if (top < scroller.scrollTop) scroller.scrollTop = top;
    else if (top + AT_ROW + AT_ROW > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = top + AT_ROW * 2 - scroller.clientHeight;
  }
  renderAtBody();  // (the rows in view, if it scrolled)
  atMarkChosen();
}

$("at-scroller").tabIndex = 0;
$("at-scroller").addEventListener("keydown", (e) => {
  if (at.editing) return;
  const key = e.key;
  if ((key === "Delete" || key === "Backspace") && at.chosen.size) { e.preventDefault(); atDelete(); }
  else if (key === "Escape") { atChoose([]); }
  else if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === "a") { e.preventDefault(); atChoose(at.view.map((r) => r.key), false); }
  else if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === "c") { e.preventDefault(); atCopy(true); }
  else if (key === "ArrowDown" || key === "ArrowUp") {
    e.preventDefault();
    const i = at.view.findIndex((r) => r.key === at.anchor), next = at.view[Math.max(0, Math.min(at.view.length - 1, (i < 0 ? -1 : i) + (key === "ArrowDown" ? 1 : -1)))];
    if (!next) return;
    atChoose([next.key]);
    const scroller = $("at-scroller"), top = at.view.indexOf(next) * AT_ROW;
    if (top < scroller.scrollTop) scroller.scrollTop = top;
    else if (top + AT_ROW * 2 > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = top + AT_ROW * 2 - scroller.clientHeight;
    revealAnnotation(next);
  }
});

// ── Copy and export: the table as shown ──
function atText(sep, selectedOnly = false) {
  const cols = atColumns();
  const cell = (v) => (sep === "," && /[",\n]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const rows = selectedOnly && at.chosen.size ? at.view.filter((r) => at.chosen.has(r.key)) : at.view;
  // (plain digits and a plain "-" for a spreadsheet, not the table's thousands separators and minus sign)
  const plain = (r, c) => (c.key === "start" || c.key === "end" || c.key === "length" ? String(r[c.key])
    : c.key === "strand" ? (r.strand === -1 ? "-" : "+") : atCell(r, c.key));
  return [cols.map((c) => cell(c.label)).join(sep), ...rows.map((r) => cols.map((c) => cell(plain(r, c))).join(sep))].join("\n") + "\n";
}
async function atCopy(selectedOnly = false) {
  try { await navigator.clipboard.writeText(atText("\t", selectedOnly)); } catch (e) { return toast("Couldn't copy", true); }
  toast(selectedOnly && at.chosen.size ? "Selected rows copied: paste them into Excel" : "Table copied: paste it into Excel");
}
$("at-copy").onclick = () => atCopy(false);
$("at-csv").onclick = () => {
  const name = (seqShown?.items?.[0]?.name || "annotations").replace(/[\\/:*?"<>|\s]+/g, "_");
  saveTextFile(`${name}_annotations.csv`, atText(","), "text/csv");
};

// ── Editing ──
// A cell: double-click. Each change is one undo step per sequence (SeqDocument.change),
// then the viewer is drawn from the documents again (refreshSingle), as after any edit.
function atNeedEditable() {
  if (editable() && at.sources.some((s) => s.editable)) return true;
  toast("Switch to Editable first (the Edit group in the bar)", true);
  return false;
}

// The feature after a change to one field: the name follows /gene, /product... when it came from there
function atChangeFeature(f, src, field, value) {
  const notes = { ...(f.notes || {}) };
  switch (field) {
    case "name": return { ...f, name: value };
    case "type": return { ...f, type: value };
    case "strand": return { ...f, strand: Number(value) === -1 ? -1 : 1 };
    case "location": {
      const where = SeqEdit.parseLocation(value, src.length);
      if (!where) throw new Error(`Not a location in 1..${src.length}: write e.g. 100..200, complement(100..200) or join(1..50,80..120)`);
      return SeqEdit.withParts({ ...f, strand: where.strand, partial: where.partial }, where.parts);
    }
    case "start": case "end": {
      const parts = f.locations?.length ? f.locations : [{ start: f.start, end: f.end }];
      if (parts.length > 1) throw new Error("This feature has several parts: change its Location instead");
      const n = Math.round(Number(String(value).replace(/[\s,]/g, "")));
      if (!(n >= 1 && n <= src.length)) throw new Error(`Not a position in 1..${src.length}`);
      const p = { ...parts[0], [field]: n - 1 };
      if (p.start > p.end) throw new Error("The start can't be after the end (a feature across the origin: use its Location)");
      return SeqEdit.withParts({ ...f }, [p]);
    }
    default: {  // a qualifier
      const was = featureNameOf(f.notes, f.type) === f.name;
      if (value === "" || value === null) delete notes[field]; else notes[field] = [value];
      const out = { ...f, notes };
      if (was) out.name = featureNameOf(notes, f.type);  // (saving writes the name back into the qualifier it came from)
      return out;
    }
  }
}

// Apply `field = value` to these rows: one undo step per sequence
function atApply(rows, field, value) {
  const bySrc = new Map();
  for (const r of rows) if (r.src.editable) (bySrc.get(r.src) || bySrc.set(r.src, new Set()).get(r.src)).add(r.index);
  const changes = [];
  try {
    for (const [src, indices] of bySrc) {  // (checked before anything changes)
      for (const i of indices) changes.push([src, i, atChangeFeature(src.features[i], src, field, value)]);
    }
  } catch (e) { toast(e.message, true); return false; }
  for (const [src, indices] of bySrc) {
    const mine = new Map(changes.filter(([s]) => s === src).map(([, i, f]) => [i, f]));
    src.doc.change((s) => ({ ...s, features: s.features.map((f, i) => mine.get(i) || f) }));
  }
  refreshSingle();
  return true;
}

function atCancelEdit() { at.editing = null; }
function atFinishEdit(commit) {
  const ed = at.editing;
  if (!ed) return;
  at.editing = null;
  if (commit && ed.input.value !== ed.before) atApply([ed.row], ed.field, ed.input.value);
  renderAtBody(true);
}

$("at-rows").addEventListener("dblclick", (e) => {
  const cell = e.target.closest(".at-cell"), r = atRowAt(e);
  if (!cell || !r) return;
  const c = AT_COLUMNS.find((x) => x.key === cell.dataset.key);
  if (!c?.edit) return;
  if (!atNeedEditable()) return;
  if ((c.key === "start" || c.key === "end") && r.multi) return toast("This feature has several parts: change its Location instead", true);
  atStartEdit(r, c, cell);
});

function atStartEdit(r, c, cell) {
  let input;
  const before = c.edit === "strand" ? String(r.strand) : c.key === "start" || c.key === "end" ? String(r[c.key])
    : c.key === "location" ? locationOf(r) : c.edit === "qualifier" ? (c.key === "note" ? r.note : r[c.key]) : r[c.key];
  if (c.edit === "strand") {
    input = document.createElement("select");
    input.append(new Option("+", "1"), new Option("−", "-1"));
  } else {
    input = document.createElement("input"); input.autocomplete = "off";
    if (c.edit === "type") input.setAttribute("list", "at-types");
  }
  input.value = before;
  at.editing = { row: r, field: c.key === "start" || c.key === "end" || c.key === "location" || c.key === "name" || c.key === "type" || c.key === "strand" ? c.key : c.q, input, before };
  cell.replaceChildren(input);
  input.focus();
  if (input.select) input.select();
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); atFinishEdit(true); $("at-scroller").focus({ preventScroll: true }); }
    else if (e.key === "Escape") { e.preventDefault(); atFinishEdit(false); $("at-scroller").focus({ preventScroll: true }); }
  });
  input.addEventListener("blur", () => { if (at.editing?.input === input) atFinishEdit(true); });
  if (c.edit === "strand") input.addEventListener("change", () => atFinishEdit(true));
}

// Several rows at once
function atDelete() {
  if (!at.chosen.size || !atNeedEditable()) return;
  const bySrc = new Map();
  for (const r of at.rows) if (at.chosen.has(r.key) && r.src.editable) (bySrc.get(r.src) || bySrc.set(r.src, []).get(r.src)).push(r.index);
  let n = 0;
  for (const [src, indices] of bySrc) { src.doc.deleteFeatures(indices); n += indices.length; }
  at.chosen = new Set();
  at.anchor = null;
  refreshSingle();
  toast(`Deleted ${n} feature${n === 1 ? "" : "s"} (Ctrl+Z undoes it)`);
}
$("at-delete").onclick = atDelete;

$("at-set").onclick = () => {
  if (!at.chosen.size || !atNeedEditable()) return;
  const form = $("at-set-dialog").querySelector("form");
  $("at-set-what").textContent = `${at.chosen.size.toLocaleString()} feature${at.chosen.size === 1 ? "" : "s"} selected`;
  form.field.value = "type";
  form.key.value = form.value.value = "";
  $("at-set-key-row").hidden = true;
  form.querySelector(".error").hidden = true;
  $("at-set-dialog").showModal();
  form.value.focus();
};
$("at-set-dialog").querySelector("[name=field]").addEventListener("change", (e) => {
  $("at-set-key-row").hidden = e.target.value !== "other";
  e.target.form.value.setAttribute("list", e.target.value === "type" ? "at-types" : "");
});
$("at-set-dialog").querySelector("form").addEventListener("submit", (e) => {
  if (e.submitter?.value !== "run") return;
  e.preventDefault();
  const form = e.target, error = form.querySelector(".error");
  const field = form.field.value === "other" ? form.key.value.trim().replace(/^\//, "") : form.field.value;
  const value = form.value.value.trim();
  const fail = (text) => { error.textContent = text; error.hidden = false; };
  if (!field) return fail("Write the qualifier's name, e.g. codon_start");
  if ((field === "type" || field === "name") && !value) return fail(`A feature needs a ${field}`);
  if (field === "strand" && !["+", "-", "−", "1", "-1"].includes(value)) return fail("The strand is + or -");
  const rows = at.rows.filter((r) => at.chosen.has(r.key));
  const done = atApply(rows, field, field === "strand" ? (value === "+" || value === "1" ? "1" : "-1") : value);
  if (done) $("at-set-dialog").close();
});

// The table's own Ctrl+Z / Ctrl+Y: as everywhere (the editor's undo)
$("annotationview").addEventListener("keydown", (e) => {
  // (read only, Undo would go to the previous saved version: not from a key pressed in a table)
  if (at.editing || !editable() || !(e.ctrlKey || e.metaKey) || !["z", "y"].includes(e.key.toLowerCase())) return;
  e.preventDefault();
  undoRedo(e.key.toLowerCase() === "y" || e.shiftKey);
});
