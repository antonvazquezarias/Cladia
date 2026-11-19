// Cladia's page, continued from app.js: the sequence viewer, mappings, labels, the viewer's menu, editing, edit locks, gap editing, the feature dialog.
// The page's scripts share one global scope and run in the order index.html loads them,
// so code here can use what earlier files declare; at load time it mustn't call into later ones.

// ── Sequence viewer ──────────────────────────────────────────────────────
// Cladia's own viewer (seqview.js) for one sequence, several, or an alignment:
// the same zoom, selection, find, copy, features and translations everywhere.
let seqView = null;   // created on first use
let seqKey = null;    // what it shows (mode, item ids and file times)
let seqShown = null;  // { mode, items, alignment }
const seqShow = { features: true, translations: true, complement: true, variants: true, labels: ["name"], nameWidth: 150,
                  ...loadJSON("cladia.view") };
seqShow.hiddenTypes ??= seqShow.mrna ? [] : ["mRNA"];  // (the mRNAs toggle there was before)
const translators = {};  // genetic code -> codon table (for translations)
// Feature colours (Geneious-like)
const FEATURE_COLORS = { gene: "#3fa34d", CDS: "#f2d024", rRNA: "#d93b30", tRNA: "#8e44ad",
                         intron: "#ffffff", exon: "#1e1e1e", mRNA: "#3d7edb", ORF: "#e0a030",
                         repeat_region: "#a45bcc" };
const COLOR_BY_LOWER = Object.fromEntries(Object.entries(FEATURE_COLORS).map(([k, v]) => [k.toLowerCase(), v]));
// any case ("cds" too: the server reads the usual spelling, but older copies, as a comparison's, may not)
const featureColor = (type) => FEATURE_COLORS[type] || COLOR_BY_LOWER[String(type).toLowerCase()] || "#b9b9b9";
// CDS translation colours from OpenVectorEditor (TeselaGen, MIT); see CONTRIBUTING.md.
const AA_COLORS = {
  A: "hsl(327.3, 100%, 69%)", R: "hsl(258.1, 100%, 69%)", N: "hsl(268.9, 100%, 69%)", D: "hsl(268.9, 100%, 69%)",
  C: "hsl(335.1, 100%, 69%)", E: "hsl(268.9, 100%, 69%)", Q: "hsl(268.9, 100%, 69%)", G: "hsl(303.1, 100%, 69%)",
  H: "hsl(272.2, 100%, 69%)", I: "hsl(356.9, 100%, 69%)", L: "hsl(349.4, 100%, 69%)", K: "hsl(264.7, 100%, 69%)",
  O: "hsl(264.7, 100%, 69%)", M: "hsl(328.5, 100%, 69%)", F: "hsl(338.4, 100%, 69%)", P: "hsl(289.9, 100%, 69%)",
  S: "hsl(298.6, 100%, 69%)", T: "hsl(299.8, 100%, 69%)", U: "hsl(0, 100%, 69%)", W: "hsl(297.6, 100%, 69%)",
  Y: "hsl(293.2, 100%, 69%)", V: "hsl(353.6, 100%, 69%)", B: "hsl(268.9, 100%, 69%)", J: "hsl(352, 100%, 69%)",
  X: "hsl(60, 100%, 69%)", Z: "hsl(268.9, 100%, 69%)", "*": "hsl(0, 100%, 69%)",
};
const WHEEL_PAN = 0.35;  // pixels moved per pixel of wheel (a mouse notch is ~100)

function getSeqView() {
  if (seqView) return seqView;
  seqView = new SeqView($("seqview"), {
    wheelPan: WHEEL_PAN,
    labelFields: seqShow.labels,
    nameWidth: seqShow.nameWidth,
    onNameWidth: (w) => changeSeqShow({ nameWidth: w }),
    onOpenRow: openViewerRow,
    onBlockClick: (index, row) => (seqShown?.comparison ? lineUpOn(index, row) : null),  // a genome comparison's block
    onSelection: (s) => {
      activeRow();
      updateViewerZoom();
      annotationsFollowViewer(s);
      scheduleToolStates();
      const read = seqView?.readCaret || seqView?.readSel;  // a mapping's read selection
      $("at-add").disabled = !s || !editable() || !seqShown?.doc;  // the Annotations tab's + Feature


      clearTimeout(matrixTimer);  // the Identity tab follows the selection, once it settles
      matrixTimer = setTimeout(updateMatrixView, 300);
    },
    onFeatureDoubleClick: (f, row) => {
      if (!seqShown?.docs || f.index === undefined) return;
      if (seqShown.mode === "sequences") activeRow(row);
      openFeatureDialog(f.index);
    },
    translate: (codon, code) => {
      if (!geneticCodes.some((c) => c.id === code)) code = 1;
      translators[code] = translators[code] || codonTable(code);
      return (translators[code][codon.toLowerCase()] || "X").toUpperCase();
    },
    aaColor: (aa) => AA_COLORS[aa] || "#ddd",
  });
  seqView.setShow(seqShow);
  return seqView;
}

// Editor JSON (/api/sequences) -> the viewer's features: parts with start <= end
// (a feature across the origin of a circular sequence is split), CDS reading frame
function viewerFeatures(data) {
  const n = data.sequence.length;
  return (data.features || []).map((f, index) => ({ f, index })).filter(({ f }) => !hiddenType(f.type)).map(({ f, index }) => {
    const parts = [];
    for (const p of f.locations && f.locations.length ? f.locations : [f]) {
      if (p.start <= p.end) parts.push({ start: p.start, end: p.end });
      else parts.push({ start: p.start, end: n - 1 }, { start: 0, end: p.end });
    }
    return { name: f.name, type: f.type, strand: f.strand, locations: parts, index,
             codonStart: Number(f.notes?.codon_start?.[0]) || 1, color: f.color || featureColor(f.type) };  // (a graph's nodes: by depth)
  });
}

function startViewer(mode, key, items) {
  showView("seqview");
  renderSeqControls(mode);
  if (seqKey === key) return false;  // already shown
  seqKey = key;
  if (unlocked !== shownKey(items)) unlocked = null;  // something else: read only again
  seqShown = { mode, items };
  $("seq-find").value = "";
  $("seq-found").textContent = "";
  $("seq-prev").disabled = $("seq-next").disabled = true;
  return true;
}

async function showSingle(it) {
  const key = `single:${it.id}:${it.mtime}`;
  if (!startViewer("single", key, [it])) return renderFigureSwitch();
  const [data, trace, tracks] = await Promise.all([api("GET", `/api/sequences/${it.id}`),
                                                   it.trace ? api("GET", `/api/trace/${it.id}`) : null,  // a Sanger read
                                                   it.tracks ? api("GET", `/api/tracks/${it.id}`) : null]);  // bedGraph / WIG
  await codesLoaded;
  if (seqKey !== key) return;
  const view = getSeqView();
  view.setDataTracks(tracks?.tracks || []);
  seqShown.trace = trace;
  seqShown.fileCode = seqShown.openCode = data.geneticCode;
  seqShown.map = data.circular ? "circular" : "linear";  // each sequence opens with its own topology
  seqShown.doc = new SeqDocument(data);
  seqShown.docs = [seqShown.doc];
  view.circularMap = false;
  refreshSingle(false);
  view.setCircular(seqShown.map === "circular");
  if (trace) {  // a read opens at its start, zoomed in far enough to see the trace
    view.zoom(12 / view.charWidth);
    view.centerOn(0);
  }
  renderSeqCode();
  renderDataTrackControls();
  renderSeqControls("single");
  renderFigureSwitch();
}

// total: how many sequences were picked (only the first ones that fit are shown: rowsToShow);
// list: the sequence list being shown, if that's what was picked
async function showSequences(sequences, total, list) {
  const key = "sequences:" + sequences.map((it) => `${it.id}:${it.mtime}`).join(",");
  if (!startViewer("sequences", key, sequences)) return;
  // in batches: one request a row was slow with many rows (each looked through the library)
  const batches = [];
  for (let i = 0; i < sequences.length; i += ROWS_PER_REQUEST) batches.push(sequences.slice(i, i + ROWS_PER_REQUEST));
  const all = (await Promise.all(batches.map((b) => api("POST", "/api/sequences", { ids: b.map((it) => it.id) })))).flat();
  await codesLoaded;
  if (seqKey !== key) return;
  const used = new Set();
  const names = sequences.map((it) => {  // row names must be unique
    let name = list ? it.name : it.label;
    while (used.has(name)) name += "′";
    used.add(name);
    return name;
  });
  // each row is a document to edit; one undo for all of them (the last edit of any)
  const shown = Object.assign(seqShown, { rowNames: names, history: [], future: [], doc: null, row: null, list });
  shown.docs = all.map((data, i) => Object.assign(new SeqDocument(data), {
    onChange: () => { shown.history.push(i); shown.future = []; },
  }));
  // the title: the name and count on top; the kind (and what is left out) underneath
  shown.title = [list ? list.label : `${total} sequences`, [`${total.toLocaleString()} sequences`],
                 [list ? "Sequence list" : "Sequences",
                  total > sequences.length ? `showing the first ${sequences.length} (at most ${MAX_BASES / 1e6} Mb or ${MAX_ROWS} at once)` : ""]];
  refreshRows(false);
  renderSeqControls("sequences");  // Sort ▾: now that it's known a list is shown
}

// Several sequences: every row from its document (keepView: after an edit)
function refreshRows(keepView = true) {
  const { docs, rowNames, items: shownItems } = seqShown;
  const view = getSeqView();
  view.setRows(docs.map((doc, i) => ({ name: rowNames[i], sequence: doc.sequence, description: doc.data.description,
                                       organism: doc.data.organism })), "sequences", keepView);
  view.setFeatures(Object.fromEntries(docs.map((doc, i) => [rowNames[i], {
    features: viewerFeatures({ sequence: doc.sequence, features: doc.features }),
    code: codeChoices[shownItems[i].id] || docCode(doc), item: shownItems[i].id,
  }])));
  renderEditButtons();
  refind();  // the search hits of the edited sequence
}

// A document's genetic code: its CDSs' (/transl_table), else the one it opened with
const docCode = (doc) => doc.features.map((f) => f.type === "CDS" && Number(f.notes?.transl_table?.[0])).find(Boolean)
  || doc.data.geneticCode;

// Several sequences: the one edited is the row with the cursor, or the row the
// selection is in (a selection over several rows edits none). row: set it.
function activeRow(row) {
  if (seqShown?.mode !== "sequences" || !seqShown.docs) return;
  if (row === undefined) {
    const s = seqView.selection;
    row = s ? (s.r0 === s.r1 ? s.r0 : null) : seqView.caret !== null ? seqView.caretRow : null;
  }
  seqShown.row = row;
  seqShown.doc = row === null || row < 0 ? null : seqShown.docs[row];
}

// The selection within the document edited (rows end at their own lengths)
function rowSelection() {
  const s = seqView.selection, doc = seqShown.doc;
  if (!s || !doc || s.wraps) return s;
  const c1 = Math.min(s.c1, doc.length - 1);
  return s.c0 > c1 ? null : { ...s, c1 };
}

async function showAlignment(it) {
  const key = `alignment:${it.id}:${it.mtime}`;
  if (!startViewer("alignment", key, [it])) return;
  seqShown.alignment = it;
  const rows = await api("GET", `/api/alignment/${it.id}`);
  if (seqKey !== key) return;
  seqShown.variantData = it.variants ? await api("GET", `/api/alignment/${it.id}/variants`) : null;
  if (seqKey !== key) return;
  const view = getSeqView();
  view.setRows(rows, "alignment");
  seqShown.parsedVariants = seqShown.variantData && !seqShown.variantData.stale ? parseVariantVcf(seqShown.variantData.vcf) : null;
  view.setVariantSites(seqShown.parsedVariants?.sites || [], seqShown.variantData?.reference);
  seqShown.alignDoc = new AlignmentDocument(rows);
  seqShown.rowInfo = Object.fromEntries(rows.map((r) => [r.name, { description: r.description, organism: r.organism }]));
  renderSeqControls("alignment");
  renderEditButtons();
  // Each row's features, from the library sequence it came from (if known)
  await codesLoaded;
  const { rows: sources } = await api("GET", `/api/alignment/${it.id}/features`);
  if (seqKey !== key) return;
  for (const info of Object.values(sources)) {
    info.code = codeChoices[info.item] || info.code;  // a genetic code picked for that sequence wins
    info.features = info.features.map((f) => ({ ...f, color: featureColor(f.type) }));
  }
  await Promise.all(Object.entries(sources).filter(([, info]) => info.trace).map(async ([name, info]) => {
    const trace = await api("GET", `/api/trace/${info.item}`).catch(() => null);
    info.trace = trace && { ...(name.startsWith("_R_") ? reverseTrace(trace) : trace), offset: info.offset || 0 };
  }));
  if (seqKey !== key) return;
  seqShown.alignSourcesAll = sources;
  seqShown.alignSources = shownSources(sources);
  view.setFeatures(seqShown.alignSources);
  if (Object.values(sources).some((info) => info.trace)) {  // assembled reads: zoomed in to see their traces
    view.zoom(12 / view.charWidth);
    view.centerOn(0);
  }
  updateMatrixView();  // showing the matrix: of this alignment
  scheduleSidePanel();  // (the annotation table lists these features)
}

// A read's trace for its reverse complement: bases complemented and reversed,
// scans counted from the other end, each dye curve given to its complement
function reverseTrace(t) {
  const swap = { A: "T", C: "G", G: "C", T: "A" };
  const n = t.bases.length, scans = Math.max(...Object.values(t.channels).map((c) => c.length)) - 1;
  const bases = [...t.bases].reverse().map((b) => swap[b] || b).join("");
  const channels = Object.fromEntries(Object.entries(t.channels).map(([b, c]) => [swap[b] || b, [...c].reverse()]));
  return { ...t, bases, channels, quality: [...t.quality].reverse(), peaks: [...t.peaks].reverse().map((p) => scans - p),
           start: n - t.end, end: n - t.start };
}

// An alignment's row features without the hidden types
function shownSources(sources) {
  return Object.fromEntries(Object.entries(sources).map(([name, info]) =>
    [name, { ...info, features: info.features.filter((f) => !hiddenType(f.type)) }]));
}

// ── Mappings (reads mapped to a reference, from the Map reads tool) ──────
// The reference with its features in the viewer, the coverage graph and the
// consensus above it (seqview.js setMapping), a summary with the gaps and
// differences to jump to. View only.
async function showMapping(it) {
  const key = `mapping:${it.id}:${it.mtime}`;
  if (!startViewer("single", key, [it])) return;
  const data = await api("GET", `/api/mapping/${it.id}`);
  await codesLoaded;
  if (seqKey !== key) return;
  const ref = data.reference;
  seqShown.mapping = data;
  // reads shown unless this mapping was told to hide them
  const hidden = (it.reads_mode || mappingReadsModes[it.id]) === "none";
  mappingReadsMode = data.reads && it.reads && !hidden ? "all" : "none";
  seqShown.fileCode = ref.geneticCode;
  seqShown.map = "linear";  // reads, coverage and agreement read best along a line; Circular stays a click away
  const view = getSeqView();
  clearTimeout(mappingReadsTimer);
  mappingReadsRequest++;
  mappingReadsPending = mappingReadsCoverage = null;
  view.onViewChange = null;
  view.setReads(null);  // another mapping's reads, if any
  view.circularMap = false;
  view.setRows([{ name: ref.name, sequence: ref.sequence, description: ref.description, organism: ref.organism }], "single");
  view.circularSequence = !!ref.circular;
  view.setFeatures({ [ref.name]: { features: viewerFeatures(ref), code: codeChoices[it.id] || ref.geneticCode, item: it.id } });
  view.onViewChange = (first, last) => loadMappingReads(it, first, last);
  view.setMapping({ depth: data.depth, consensus: data.consensus_ref, insertions: data.insertions, minDepth: data.options.min_depth,
                    agreement: data.agreement, clips: data.clips, variable: data.variable, extension: data.extension });
  view.setCircular(seqShown.map === "circular");
  renderSeqCode();
  renderSeqControls("single");
  const s = data.summary;
  const ext = data.extension || {};
  setTitle(it.label, ref.organism, [`${s.mapped_percent} % of ${s.reads.toLocaleString()} reads mapped`, `mean depth ${s.mean_depth}×`],
    [`Reads mapped to ${ref.name}`, `${s.difference_count} difference${s.difference_count === 1 ? "" : "s"}`,
     ext.left || ext.right ? `extended ${ext.left} + ${ext.right} bp` : ""].filter(Boolean).join(" · "));
  renderMappingSummary();  // the Metadata tab may be showing this mapping already
}

// ── Variants (a VCF item, library.add_vcf) ───────────────────────────────
// Its reference sequence in the viewer, read only, with each site as a
// "variation" feature (REF → ALT), and the side panel's Variants tab (list and
// sample × site matrix), as for an alignment's attached VCF. No reference in the
// library: the tab alone, with the reason in the title.
const VARIANT_COLOR = "#d35400";
async function showVariantsItem(it) {
  const key = `variants:${it.id}:${it.mtime}`;
  if (!startViewer("single", key, [it])) return;
  const data = await api("GET", `/api/variants/${it.id}`);
  const ref = data.reference ? await api("GET", `/api/sequences/${data.reference}`).catch(() => null) : null;
  await codesLoaded;
  if (seqKey !== key) return;
  const parsed = parseVariantVcf(data.vcf);
  seqShown.variantItem = it;
  seqShown.variantData = { vcf: data.vcf, reference: ref?.name || data.chrom || "", stale: false };
  seqShown.parsedVariants = parsed;
  const view = getSeqView();
  view.setDataTracks([]);
  const types = new Set(parsed.sites.map((s) => s.type));
  const facts = [`${parsed.sites.length.toLocaleString()} site${parsed.sites.length === 1 ? "" : "s"}`,
                 parsed.samples.length ? `${parsed.samples.length} sample${parsed.samples.length === 1 ? "" : "s"}` : "no samples",
                 [...types].join(", ")].filter(Boolean);
  if (!ref) {
    const last = Math.min(10_000_000, Math.max(1, ...parsed.sites.map((s) => s.position + s.ref.length)));  // positions only: N for the bases
    view.setRows([{ name: data.chrom || it.label, sequence: "N".repeat(last) }], "single");
    setTitle(it.label, "", facts, `Variants · not linked to a sequence: ${data.note || "its reference isn't in the library"}`);
  } else {
    view.circularMap = false;
    view.setRows([{ name: ref.name, sequence: ref.sequence, description: ref.description, organism: ref.organism }], "single");
    view.circularSequence = !!ref.circular;
    const sites = parsed.sites.map((s, i) => ({ name: `${s.ref.length > 12 ? s.ref.slice(0, 10) + "…" : s.ref} → ${s.alt}`, type: "variation", strand: 0,
      locations: [{ start: s.position - 1, end: Math.min(ref.sequence.length, s.position - 1 + Math.max(1, s.ref.length)) - 1 }],
      index: -1 - i, codonStart: 1, color: VARIANT_COLOR }));
    view.setFeatures({ [ref.name]: { features: [...viewerFeatures(ref), ...(hiddenType("variation") ? [] : sites)],
                                     code: ref.geneticCode, item: data.reference } });
    view.setCircular(false);
    setTitle(it.label, ref.organism, facts, [`Variants on ${data.reference_label}`, data.note].filter(Boolean).join(" · "));
  }
  view.variantBaseColumns = Array.from({ length: view.length || 0 }, (_, i) => i);  // (the Variants tab jumps by position)
  seqShown.fileCode = ref?.geneticCode;
  seqShown.map = "linear";
  renderSeqCode();
  renderSeqControls("single");
  if (!sideState.open) { sideState.open = true; sideState.tab = "variants"; }
  scheduleSidePanel();
}

// The reads of the window a mapping shows: fetched as the view moves, cached per
// window (the lane is packed from them; the server caps them and says when there
// are more)
const mappingReadsCache = new Map();  // mapping id -> Map(window -> data)
const mappingReadsModes = loadJSON("cladia.mapping-reads") || {};  // each mapping remembers its own Show choice
let mappingReadsMode = "none";
let mappingReadsTimer = null;
let mappingReadsPending = null;
let mappingReadsCoverage = null;
let mappingReadsRequest = 0;

function loadMappingReads(item, first, last) {
  if (mappingReadsMode !== "all") return;
  // Full mode loads every stored read in the visible range (rounded to index bins).
  const length = seqShown.mapping?.reference?.sequence?.length || last;
  const start = Math.floor(first / 250) * 250;
  const end = Math.min(length, Math.ceil(last / 250) * 250);
  const limit = 200000;
  const key = `${start}-${end}-${limit}-all`;
  const hasRoom = (range) => {
    if (!range || range.id !== item.id) return false;
    return range.all && first >= range.start && last <= range.end;
  };
  if (hasRoom(mappingReadsCoverage) || hasRoom(mappingReadsPending)) return;

  let per = mappingReadsCache.get(item.id);
  if (!per) mappingReadsCache.set(item.id, (per = new Map()));
  clearTimeout(mappingReadsTimer);
  const request = ++mappingReadsRequest;
  const range = { id: item.id, start, end, request, all: true };
  mappingReadsPending = range;
  if (per.has(key)) {
    mappingReadsPending = null;
    mappingReadsCoverage = range;
    const cached = per.get(key);
    return setTimeout(() => {
      if (request === mappingReadsRequest && seqShown.mapping && seqShown.items[0]?.id === item.id) {
        getSeqView().setReads(cached);
      }
    }, 0);  // not inside the draw
  }
  mappingReadsTimer = setTimeout(async () => {
    try {
      const data = await api("GET", `/api/mapping/${item.id}/reads?start=${start}&end=${end}&limit=${limit}&all_reads=true`);
      per.set(key, data);
      while (per.size > 16) per.delete(per.keys().next().value);
      if (request !== mappingReadsRequest) return;  // a later pan requested a newer window
      mappingReadsPending = null;
      mappingReadsCoverage = range;
      if (seqShown.mapping && seqShown.items[0]?.id === item.id) getSeqView().setReads(data);
    } catch (e) {
      if (request === mappingReadsRequest) {
        mappingReadsPending = null;
        mappingReadsCoverage = range;  // don't retry a missing sidecar on every draw
      }
    }
  }, 40);
}

function refreshMappingReadMode() {
  if (!seqShown?.mapping || !seqView) return;
  clearTimeout(mappingReadsTimer);
  mappingReadsRequest++;
  mappingReadsPending = mappingReadsCoverage = null;
  seqView.setReads(null);
  if (mappingReadsMode === "none") return;
  const first = Math.max(0, Math.floor(seqView.scrollX / seqView.charWidth));
  const last = Math.min(seqView.length,
    Math.ceil((seqView.scrollX + seqView.seqWidth) / seqView.charWidth));
  loadMappingReads(seqShown.items[0], first, last);
}

async function saveMappingReadsMode(id, mode) {
  try {
    const saved = await api("PUT", `/api/mapping/${id}/reads-mode`, { mode });
    const item = itemById(id);
    if (item) item.reads_mode = saved.reads_mode;
  } catch (e) {
    toast(`Couldn't save this mapping's reads setting: ${e.message}`, true);
  }
}

function setMappingReadsMode(mode) {
  mappingReadsMode = mode;
  const item = seqShown?.mapping && seqShown.items[0];
  const id = item?.id;
  if (!id) return;
  mappingReadsModes[id] = mode;
  try { localStorage.setItem("cladia.mapping-reads", JSON.stringify(mappingReadsModes)); } catch (e) {}
  item.reads_mode = mode;
  refreshMappingReadMode();
  saveMappingReadsMode(id, mode);
}

// Leaving a mapping: no more read windows, and the lane goes
function stopMappingReads() {
  if (!seqView) return;  // no viewer yet: nothing to clear (and don't make one)
  clearTimeout(mappingReadsTimer);
  mappingReadsRequest++;
  mappingReadsPending = mappingReadsCoverage = null;
  seqView.onViewChange = null;
  if (seqView.reads) seqView.setReads(null);
}

// A genome comparison (progressiveMauve): its genomes as rows (the snapshots taken
// when compared, so the blocks fit them; read only), each with its blocks lane
async function showComparison(it) {
  const key = `comparison:${it.id}:${it.mtime}`;
  if (!startViewer("sequences", key, [it])) return renderFigureSwitch();
  const data = await api("GET", `/api/comparison/${it.id}`);
  await codesLoaded;
  if (seqKey !== key) return;
  seqShown.comparison = data;
  drawComparison(null);
  renderSeqControls("sequences");
  renderFigureSwitch();
  setTitle(it.label, "", [`${data.genomes.length} genomes`, `${data.blocks.length} shared block${data.blocks.length === 1 ? "" : "s"}`],
           "Genome comparison · click a block to line it up in every genome");
  scheduleSidePanel();  // (the annotation table lists the genomes' features)
}

// The comparison's rows; shifts: each genome moved along by that many columns
// (leading gaps, so positions, copying and BLAST stay the genome's own)
function drawComparison(shifts, active = null, keepView = false) {
  const data = seqShown.comparison, view = getSeqView();
  shifts = shifts || data.genomes.map(() => 0);
  view.setRows(data.genomes.map((g, i) => ({ name: g.name, sequence: "-".repeat(shifts[i]) + g.data.sequence,
                                            description: g.data.description, organism: g.data.organism })), "sequences", keepView);
  view.setFeatures(Object.fromEntries(data.genomes.map((g) => [g.name, {
    features: viewerFeatures(g.data), code: codeChoices[g.item] || g.data.geneticCode, item: g.item,
  }])));
  view.setComparison(data.blocks.map((b) => ({ parts: b.parts.map((p) => ({ ...p, row: p.genome })) })), shifts, active);
  seqShown.shifts = shifts;
}

// Mauve's click: every genome that has the block moved so it starts in the same
// column (the others stay), the block selected in the genome clicked
function lineUpOn(index, row) {
  const data = seqShown.comparison, block = data.blocks[index];
  const starts = new Map(block.parts.map((p) => [p.genome, p.start]));
  const target = Math.max(...starts.values());
  drawComparison(data.genomes.map((g, i) => (starts.has(i) ? target - starts.get(i) : 0)), index, true);
  const part = block.parts.find((p) => p.genome === row) || block.parts[0];
  seqView.select(seqView.blockColumn(part.genome, part.start), seqView.blockColumn(part.genome, part.end), part.genome, part.genome);
  seqView.centerOn(target - 1);  // the lined-up starts in the middle
}

// A comparison's block as an alignment (its rows get their genomes' features)
async function extractBlock(index) {
  const it = seqShown.items[0];
  try {
    const { id } = await api("POST", `/api/comparison/${it.id}/extract`, { block: index });
    await loadList();
    toast(`Block ${index + 1} is now an alignment: ${itemById(id)?.label}`);
  } catch (e) {
    toast(e.message, true);
  }
}

// The summary, in the side panel's Metadata tab: numbers, the read files, coverage
// gaps and differences (click: go there)
function renderMappingSummary() {
  const data = seqShown?.mapping, box = $("mapping-summary");
  const on = !!data && metadataItem?.id === seqShown.items[0].id;
  $("metadata-mapping").hidden = !on;
  if (!on || box.dataset.of === `${metadataItem.id}:${metadataItem.mtime}`) return;
  box.dataset.of = `${metadataItem.id}:${metadataItem.mtime}`;
  const s = data.summary;
  box.innerHTML = "";
  const table = document.createElement("table");
  const rows = [
    ["Preset", data.options.preset || "sr"],
    ["Reference", `${data.reference.name} (${data.depth.length.toLocaleString()} bp${data.reference.circular ? ", circular" : ""})`],
    ["Reads", `${s.reads.toLocaleString()} · ${s.mapped.toLocaleString()} mapped (${s.mapped_percent} %)` +
              (s.properly_paired_percent ? ` · ${s.properly_paired_percent} % properly paired` : "")],
    ["Read length", `${s.read_length} bp` + (s.insert_size ? ` · insert size ${s.insert_size} ± ${s.insert_size_sd} bp` : "")],
    ["Depth", `mean ${s.mean_depth}× · min ${s.min_depth}× · max ${s.max_depth}×`],
    ["Covered", `${s.covered_percent} % at ${data.options.min_depth}× or more (minimum mapping quality ${data.options.min_mapq})`],
    ["Read files", s.pairs.map((p) => p.files.join(" + ")).join("; ")],
  ];
  if (["ont", "ont_hq", "clr"].includes(data.options.read_type)) {
    const warning = document.createElement("p");
    warning.textContent = "Consensus from raw long reads: check indels and polish with Medaka (Nanopore) or a suitable polishing tool when accuracy matters.";
    box.append(warning);
  }
  const ext = data.extension;
  if (data.options.extend_rounds) {
    rows.splice(1, 0, ["Extended", data.reference.circular ? "No: circular reference"
      : `${ext.left} bp at the start, ${ext.right} bp at the end, in ${ext.rounds} round${ext.rounds === 1 ? "" : "s"} ` +
        `(at most ${data.options.extend_rounds}; ${Math.round(data.options.min_agree * 100)} % of the reads agreeing, ` +
        `${data.options.min_depth}+ reads)`]);
  }
  for (const [k, v] of rows) {
    const tr = table.insertRow();
    tr.insertCell().textContent = k;
    tr.insertCell().textContent = v;
  }
  box.append(table);
  const list = (title, entries, total, text) => {
    const h = document.createElement("h4");
    h.textContent = `${title} (${total.toLocaleString()}${total > entries.length ? `, first ${entries.length} listed` : ""})`;
    box.append(h);
    if (!entries.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "None"; box.append(p); return; }
    const ul = document.createElement("ul");
    ul.className = "jump-list";
    for (const e of entries) {
      const li = document.createElement("li");
      li.textContent = text(e);
      li.onclick = () => goToRange(e.start, e.end);
      ul.append(li);
    }
    box.append(ul);
  };
  const where = (e) => (e.start === e.end ? `${e.start + 1}` : `${e.start + 1}–${e.end + 1}`);
  list("Coverage gaps", s.gaps.map(([start, end]) => ({ start, end })), s.gap_count,
       (e) => `${where(e)} (${(e.end - e.start + 1).toLocaleString()} bp under ${data.options.min_depth}×)`);
  list("Differences from the reference", s.differences, s.difference_count, (e) =>
    e.kind === "substitution" ? `${where(e)}: ${e.ref} → ${e.alt}`
    : e.kind === "deletion" ? `${where(e)}: ${e.ref} deleted`
    : `after ${e.start + 1}: ${e.alt} inserted`);
  if (data.variable) variableList(box, data);
}

// Variable positions: where a second base is in at least the chosen share of the
// reads (5 % or more, and 3+ reads, are kept with the mapping)
function variableList(box, data) {
  const h = document.createElement("h4");
  const pick = document.createElement("select");
  for (const p of [5, 10, 20, 30]) pick.append(new Option(`${p} %`, p));
  pick.value = loadJSON("cladia.variable").min || data.options?.variable_min || 5;
  const ul = document.createElement("ul");
  ul.className = "jump-list";
  const render = () => {
    try { localStorage.setItem("cladia.variable", JSON.stringify({ min: Number(pick.value) })); } catch (e) {}
    const min = Number(pick.value) / 100;
    const shown = data.variable.filter((v) => {
      const sorted = Object.values(v.counts).sort((a, b) => b - a);
      return sorted[1] / v.depth >= min;
    });
    h.firstChild.textContent = `Variable positions (${shown.length.toLocaleString()}) · second base in at least `;
    ul.innerHTML = "";
    if (!shown.length) { ul.innerHTML = '<li class="muted">None</li>'; return; }
    for (const v of shown) {
      const li = document.createElement("li");
      const counts = Object.entries(v.counts).filter(([, n]) => n).sort((a, b) => b[1] - a[1]);
      li.textContent = `${v.pos + 1}: ${counts.map(([b, n]) => `${b} ${n}`).join(" · ")} ` +
                       `(${Math.floor((100 * counts[0][1]) / v.depth)} % agree, ${v.depth}×)`;  // floor, as the agreement row
      li.onclick = () => goToRange(v.pos, v.pos);
      ul.append(li);
    }
  };
  h.append(document.createTextNode(""), pick, document.createTextNode(" of the reads"));
  pick.onchange = render;
  box.append(h, ul);
  render();
}

// Select a stretch and show it (both maps)
function goToRange(start, end) {
  seqView.select(start, end, 0, 0);
  if (seqView.circularMap) seqView.rotateTo((start + end + 1) / 2);
  else seqView.showPosition(start);
}

async function consensusAsSequence(id = seqShown?.mapping && seqShown.items[0].id) {
  if (!id) return;
  try {
    const { id: newId } = await api("POST", `/api/mapping/${id}/consensus`);
    await loadList();
    toast(`New sequence: ${itemById(newId)?.label}`);
  } catch (e) {
    toast(`Failed: ${e.message}`, true);
  }
}

// ── Names: what the viewer's names column shows (Show ▾ > Names) ────────
// Any of name, organism and description, joined by " | " (remembered); the
// matrix uses them too. Hovering a name shows all three. Only the fields some
// shown row has are offered; with just the name, the section hides.
function renderLabelFields() {
  const box = $("seq-label-fields");
  const present = Object.entries(SeqView.LABEL_FIELDS)
    .filter(([field]) => field === "name" || seqView?.rows.some((r) => r[field]));
  $("seq-label-options").hidden = present.length < 2;
  box.replaceChildren(...present.map(([field, title]) => {
    const label = document.createElement("label"); label.className = "toggle";
    const tick = document.createElement("input"); tick.type = "checkbox";
    tick.checked = seqShow.labels.includes(field);
    tick.onchange = () => {
      const fields = Object.keys(SeqView.LABEL_FIELDS)  // in a fixed order
        .filter((f) => f === field ? tick.checked : seqShow.labels.includes(f));
      changeSeqShow({ labels: fields.length ? fields : ["name"] });
      seqView?.setLabels(seqShow.labels);
      matrixKey = "";  // the matrix's names too
      updateMatrixView();
      renderLabelFields();
    };
    label.append(tick, ` ${title}`);
    return label;
  }));
}

// ── Curate ▾: changes to the whole item shown ───────────────────────────
// Everything that changes the item rather than a spot in it, in one menu by the
// Edit group, so it doesn't depend on knowing where to right-click. Edits of the
// open document need Editable (they are undone with ↶); the others are saved at
// once (their earlier state stays under Metadata > Saved versions).
function openCurateMenu(e) {
  if (!seqView || !seqShown) return;
  const doc = seqShown.doc, can = editable(), s = seqView.selection;
  const ids = shownIds.filter((id) => ["sequence", "list"].includes(itemById(id)?.kind));
  const one = shownIds.length === 1 ? itemById(shownIds[0]) : null;
  const entries = [];
  if (doc && seqShown.mode !== "alignment") {
    const at = s ? s.c0 : seqView.caret;
    const whose = seqShown.mode === "sequences" ? ` ${seqShown.rowNames[seqShown.row]}` : "";
    entries.push({ heading: can ? `Edit${whose}` : `Edit${whose} (unlock 🔒 first)` });
    if (!doc.data.protein) entries.push(
      ["Reverse complement the whole sequence", () => reverseComplement(null), !can],
      [at ? `Make position ${(at + 1).toLocaleString()} the origin` : "Make the cursor the origin (click a position first)",
       () => setOrigin(at), !can || !at || !doc.circular],
      [doc.circular ? "Mark as a linear sequence" : "Mark as a circular sequence", () => setTopology(!doc.circular), !can],
      cdsCount(doc) ? ["Genetic code of the sequence…", openCodeDialog, !can]
        : ["Genetic code of the sequence (it has no CDSs to store it on)", null, true]);
    entries.push([`Delete all ${doc.features.length} features…`, () => deleteFeatures(doc.features.map((f, i) => i), true),
                  !can || !doc.features.length]);
  }
  if (seqShown.mode === "alignment") {
    entries.push({ heading: can ? "Edit" : "Edit (unlock 🔒 first)" },
      ["Remove the selected sequences", removeAlignmentRows, !can || !s?.wholeRows]);
  }
  if (ids.length || one?.trace) entries.push("-", { heading: "Saved at once" });
  if (ids.length) entries.push(
    ["Circularize from overlap…", () => circularizeSelected(ids)],
    ["Remove all annotations…", () => removeAnnotations(ids)],
    ["Import metadata from a table…", () => openMetaImport(ids)]);
  if (one?.kind === "sequence" && !one.parent) entries.push(["Import annotations or a data track…", () => pickAnnotations(one)]);
  if (one?.trace) entries.push(["Trim ends by quality…", () => trimRead(one)]);
  if (!entries.length) entries.push(["Nothing to curate here", null, true]);
  const box = e.currentTarget.getBoundingClientRect();
  showMenu({ clientX: box.left, clientY: box.bottom + 2 }, entries);
}

// The tree's Curate ▾: rerooting and removing tips are saved (↶ undoes them)
function openTreeCurateMenu(e) {
  const view = treeView, it = treeShown?.item;
  if (!view || !it) return;
  const node = view.selected >= 0 ? view.nodes[view.selected] : null;
  const what = !node ? "" : node.tip ? `“${node.name}”` : `the selected clade (${node.tips} tips)`;
  const removing = view.selectedTipNames();
  const can = treeEditable();
  const box = e.currentTarget.getBoundingClientRect();
  showMenu({ clientX: box.left, clientY: box.bottom + 2 }, [
    { heading: can ? "Edit (saved at once)" : "Edit (unlock 🔒 first)" },
    !node ? ["Reroot (select a clade or tip first)", null, true]
      : [node.root ? "Reroot (the selection is the root)" : `Reroot on ${what}`, () => rerootTree(it, view.tipNames(node.index)), !can || node.root],
    !removing.length ? ["Remove tips (select them first)", null, true]
      : [removing.length === 1 ? `Remove the tip “${removing[0]}”` : `Remove the ${removing.length} selected tips`,
        () => pruneTree(it, removing), !can],
  ]);
}

// ── Right-click on the viewer ───────────────────────────────────────────
// Copy, a feature from the selection, and the sequence's origin and topology
// (these change the sequence: Editable only, undoable).
function openViewerMenu(e) {
  e.preventDefault();
  if (!seqView) return;
  const s = seqView.selection, doc = seqShown?.doc;
  const entries = [["Copy", copySeqSelection, !s && !seqView.readCaret && !seqView.readSel, null, "Ctrl+C"]];
  if (seqShown?.mode === "alignment") entries.push(["Extract the selection as a new alignment", extractSeqSelection, !s]);
  if (seqShown?.mapping) entries.push(["Save the consensus as a sequence", () => consensusAsSequence()]);
  if (seqShown?.mode === "alignment" && s?.wholeRows) {
    entries.push(["Save selected rows as sequences", saveAlignmentRowsAsSequences]);
    entries.push(["Remove selected sequences from alignment", removeAlignmentRows, !editable()]);
  }
  if (seqShown?.graph) return showMenu(e, [...entries, ...graphTrackMenuEntries(e)]);  // an assembly graph's track
  if (seqShown?.comparison) {  // a block: the one under the mouse, else the one chosen
    const p = seqView.point(e), hit = seqView.blockAt(p.x, p.y);
    if (hit) { seqView.compare.active = hit.block.index; seqView.draw(); }
    const index = seqView.compare?.active;
    entries.push("-", index === null || index === undefined
      ? ["Right-click a block to extract it as an alignment", null, true]
      : [`Extract block ${index + 1} as an alignment`, () => extractBlock(index)],
      ["Each genome from its start (not lined up)", () => drawComparison(null, null, true), !seqShown.shifts?.some(Boolean)]);
    return showMenu(e, entries);
  }
  if (seqShown?.mode === "sequences" && !doc) {
    entries.push("-", ["Click in a sequence (or select within one) to edit it", null, true]);
  }
  if (doc) {
    const can = editable();
    const at = s ? s.c0 : seqView.caret;
    // one heading says what's being edited, or (read only) how to edit, instead of a note on every entry
    const whose = seqShown.mode === "sequences" ? ` ${seqShown.rowNames[seqShown.row]}` : "";
    entries.push("-", { heading: can ? `Edit${whose}` : `Edit${whose} (switch to Editable first)` },
      ["New feature from the selection…", () => openFeatureDialog("new", rowSelection()), !s || !can],
      ["Delete the features in the selection", () => deleteFeatures(featuresInSelection()),
       !can || !featuresInSelection().length],
      [`Delete all ${doc.features.length} features…`, () => deleteFeatures(doc.features.map((f, i) => i), true),
       !can || !doc.features.length],
      "-",
      ...(doc.data.protein ? [] : [
        [s?.wraps ? "Reverse complement the selection (not across the origin)" : "Reverse complement the selection",
         () => reverseComplement(rowSelection()), !s || s.wraps || s.parts || !can],
        ["Reverse complement the whole sequence", () => reverseComplement(null), !can],
        "-"]),
      [at ? `Make position ${(at + 1).toLocaleString()} the origin` : "Make the cursor the origin (click a position first)",
       () => setOrigin(at), !can || !at || !doc.circular],
      [doc.circular ? "Mark as a linear sequence" : "Mark as a circular sequence", () => setTopology(!doc.circular), !can],
      doc.data.protein ? null : cdsCount(doc)
        ? ["Genetic code of the sequence…", openCodeDialog, !can]
        : ["Genetic code of the sequence (it has no CDSs to store it on)", null, true],
    );
  }
  showMenu(e, entries);
}

// The features lying wholly inside the selection (indices in the document)
function featuresInSelection() {
  const s = rowSelection(), doc = seqShown?.doc;
  if (!s || !doc) return [];
  const pieces = s.parts || (s.wraps ? [[s.c0, doc.length - 1], [0, s.c1]] : [[s.c0, s.c1]]);
  return doc.features.map((f, i) => [f, i]).filter(([f]) => SeqEdit.partsOf(f)
    .every((p) => pieces.some(([a, b]) => p.start >= a && p.end <= b)
      || (s.wraps && p.start > p.end && p.start >= s.c0 && p.end <= s.c1))).map(([, i]) => i);  // a part across the origin
}

function deleteFeatures(indices, ask = false) {
  if (!indices.length) return;
  if (ask && !confirm(`Delete all ${indices.length} features of ${seqShown.rowNames?.[seqShown.row] || seqShown.items[0].label}? (Ctrl+Z undoes it)`)) return;
  const row = seqShown.row;
  seqShown.doc.deleteFeatures(indices);
  refreshSingle();
  if (seqShown.mode === "sequences") activeRow(row);
  toast(`${indices.length} feature${indices.length === 1 ? "" : "s"} deleted (Ctrl+Z undoes it; Save keeps it)`);
}

// Base `at` becomes position 1 (the view stays where it was)
function setOrigin(at) {
  const { doc } = seqShown;
  const view = seqView, L = doc.length;
  if (seqShown.mode === "sequences") {  // one of several rows: the view stays as it is
    const row = seqShown.row;
    doc.setOrigin(at);
    refreshSingle();
    view.setCaret(0, row);
    return toast(`Position ${(at + 1).toLocaleString()} is now position 1 (Ctrl+Z undoes it)`);
  }
  const rotation = ((view.rotation - at) % L + L) % L;
  const scroll = view.scrollX - at * view.charWidth;
  doc.setOrigin(at);
  refreshSingle();
  view.rotation = rotation;
  view.scrollX = scroll < 0 ? scroll + L * view.charWidth : scroll;
  view.setCaret(0);
  toast(`Position ${(at + 1).toLocaleString()} is now position 1 (Ctrl+Z undoes it)`);
}

// The selection's bases (or all) turned round; features inside turn with them
function reverseComplement(s) {
  const { doc } = seqShown;
  if (s) doc.reverseComplement(s.c0, s.c1);
  else doc.reverseComplement();
  refreshSingle();
  toast(`${s ? "Selection" : "Sequence"} reverse complemented (Ctrl+Z undoes it)`);
}

const cdsCount = (doc) => doc.features.filter((f) => f.type === "CDS").length;

// Saved in the sequence (/transl_table on its CDSs); the Code menu then shows it as
// the file's code. A pick in the Code menu alone only changes what's shown.
function openCodeDialog() {
  const form = $("code-dialog").querySelector("form");
  const it = seqShown.items[seqShown.row ?? 0];
  form.code.innerHTML = "";
  for (const code of geneticCodes) form.code.append(new Option(`${code.id} · ${code.name}`, code.id));
  form.code.value = codeChoices[it.id] || docCode(seqShown.doc);
  $("code-cds").textContent = cdsCount(seqShown.doc);
  $("code-dialog").showModal();
}

function codeDialogDone(form, event) {
  if (event.submitter?.value !== "save") return;
  const it = seqShown.items[seqShown.row ?? 0], code = Number(form.code.value);
  seqShown.doc.setGeneticCode(code);
  delete codeChoices[it.id];  // the sequence's own code from now on
  try { localStorage.setItem("cladia.codes", JSON.stringify(codeChoices)); } catch (e) {}
  refreshSingle();
  toast(`Genetic code ${code} set on ${cdsCount(seqShown.doc)} CDSs: their translations are updated when you save`);
}

function setTopology(circular) {
  seqShown.doc.setCircular(circular);
  refreshSingle();
  toast(circular ? "Now a circular sequence" : "Now a linear sequence");
}

// ── Editing (a sequence, or an alignment's gaps, in Cladia's viewer) ────
// A sequence and its features live in a SeqDocument, an alignment's rows in an
// AlignmentDocument (seqedit.js); every edit redraws the viewer from it, keeping
// the view. Ctrl+S saves into Cladia's copy.
// Everything opens read only, as in OVE: the Read only / Editable switch unlocks
// the item shown (until something else is shown).
// Several sequences: each row has its document; the one edited is the row with
// the cursor or the selection (activeRow), and Save writes every changed one.
let unlocked = null;  // the items (ids, joined) that may be edited now
const editDoc = () => seqShown?.doc || seqShown?.alignDoc || null;  // the document edited now
const shownDocs = () => seqShown?.docs || (seqShown?.alignDoc ? [seqShown.alignDoc] : []);
const shownKey = (shown = seqShown?.items) => shown?.map((it) => it.id).join(",");
const canEdit = () => !seqShown?.mapping && shownDocs().length > 0;
const editable = () => !$("seqview").classList.contains("inactive") && canEdit() && unlocked === shownKey();

async function setEditMode(on) {
  if (!canEdit()) return;
  if (on && !(await takeLocks())) return renderEditButtons();  // edited in another window: stays read only
  unlocked = on ? shownKey() : null;
  renderEditButtons();
  if (metadataItem) metadataEditState(editable() && !viewIds);
  if (!metadataShown()) seqView?.canvas.focus();
}

// ── Edit locks: one window edits an item at a time ──────────────────────
// Editable takes the lock on what is shown (all of a list's sequences); it is
// kept while Editable or with unsaved changes, renewed every 5 s (the server
// lets a lock run out after 15 s: a closed or crashed window), and given back
// otherwise or when the window closes.
let heldLocks = [];  // item ids this window holds
const lockIds = () => !$("treeview").classList.contains("inactive") && treeShown ? [treeShown.item.id]
  : !seqShown ? [] : seqShown.mode === "alignment" ? [seqShown.alignment.id] : seqShown.items.map((it) => it.id);

async function takeLocks() {
  const ids = lockIds();
  try {
    await api("POST", "/api/locks", { window: windowId, ids, dirty: hasUnsavedChanges() });
  } catch (e) {
    toast(`${e.message}: read only here until that window is done with it (saved, or back to Read only)`, true);
    return false;
  }
  heldLocks = ids;
  return true;
}

let lockedDirty = false;  // what the server was last told: unsaved changes or not

function updateLocks() {  // after any change of what is shown, edit mode or saves
  const keep = (editable() || treeEditable() || hasUnsavedChanges()) && heldLocks.join() === lockIds().join();
  if (keep && hasUnsavedChanges() !== lockedDirty) renewLocks();  // closing Cladia asks about them
  if (keep || !heldLocks.length) return;
  api("POST", "/api/locks/release", { window: windowId, ids: heldLocks }).catch(() => {});
  heldLocks = [];
}

function renewLocks() {
  lockedDirty = hasUnsavedChanges();
  api("POST", "/api/locks", { window: windowId, ids: heldLocks, dirty: lockedDirty }).catch((e) => toast(e.message, true));
}

setInterval(() => { if (heldLocks.length) renewLocks(); }, 5000);
window.addEventListener("pagehide", () => {  // the window closes: its locks go now
  navigator.sendBeacon("/api/locks/release", new Blob([JSON.stringify({ window: windowId })], { type: "application/json" }));
});

// What this window shows, as last loaded or saved (item file times): another
// window's change shows here (when this one has no unsaved changes of its own)
let shownVersion = "", shownIds = [];
const versionOf = (ids) => ids.map((id) => {
  const it = itemById(id);
  return it?.kind === "item_list"
    ? JSON.stringify([it.label, listMembers(it).map(member => member.id)])
    : it?.mtime;
}).join(",");

async function refreshShown({ focus = false } = {}) {
  if (focus && listVersion) {
    // Nothing the list shows changed (the version covers every item's mtime,
    // name, description and folder): skip the full fetch and render.
    try {
      if ((await api("GET", "/api/library/version")).version === listVersion) return;
    } catch (e) { /* the full load below reports a real problem */ }
  }
  await loadList();
  if (versionOf(shownIds) === shownVersion || hasUnsavedChanges()) return;
  seqKey = treeKey = null;  // reload from the saved copy
  showSelection(shownIds);
}

// A key that would edit while read only: say how to edit (now and then)
let lockHintAt = 0;
function lockedHint() {
  if (Date.now() - lockHintAt < 4000) return;
  lockHintAt = Date.now();
  toast("Read only: switch to Editable (top bar) to make changes");
}

// The viewer shows the document as it is now
function refreshSingle(keepView = true) {
  if (seqShown.mode === "sequences") return refreshRows();
  const { doc, items: [it] } = seqShown;
  const view = getSeqView();
  const name = it.parent ? it.name : it.label;
  const circular = view.circularMap;
  const table = doc.features.map((f) => f.type === "CDS" && Number(f.notes?.transl_table?.[0])).find(Boolean);
  seqShown.fileCode = table || seqShown.openCode;  // the CDSs' own code (setGeneticCode, undo), else the file's
  view.setRows([{ name, sequence: doc.sequence, description: doc.data.description, organism: doc.data.organism }], "single", keepView);
  view.circularSequence = doc.circular;
  view.setFeatures({ [name]: { features: viewerFeatures({ sequence: doc.sequence, features: doc.features }),
                               code: codeChoices[it.id] || seqShown.fileCode, item: it.id } });
  view.setCircular(circular);
  if (seqShown.trace) view.setTrace(seqShown.trace);  // (setRows cleared it)
  renderSeqCode();
  renderEditButtons();
  refind();  // the search hits of the edited sequence
}

// The viewer's title, two lines: the name in bold with the short facts after it
// (length, topology, counts), then the organism in italics and the description,
// which goes last since it is the one to be cut short ("…"; all of it on hover)
function setTitle(name, organism, facts = [], description = "") {
  const note = $("view-note");
  note.textContent = "";
  const top = document.createElement("span");
  top.className = "top";
  const b = document.createElement("b");
  b.textContent = name;
  top.append(b);
  const more = facts.filter(Boolean).join(" · ");
  if (more) top.append(`  ${more}`);
  const sub = document.createElement("span");
  sub.className = "sub";
  if (organism) {
    const i = document.createElement("i");
    i.textContent = organism;
    sub.append(i);
  }
  if (description) sub.append(organism ? ` · ${description}` : description);
  note.append(top);
  if (sub.textContent) note.append(sub);
  note.title = [top.textContent, sub.textContent].filter(Boolean).join("\n");
}

function renderEditButtons() {
  updateLocks();
  const doc = editDoc(), on = editable(), several = seqShown?.mode === "sequences" && seqShown.docs;
  const changed = shownDocs().filter((d) => d.dirty).length;
  const metadataView = metadataLast();  // the form was edited after the sequence: Undo and Redo go to it first
  $("seq-save").disabled = !changed && !metadataDirty();
  // usable only while editing and with something to undo or redo (read only, Ctrl+Z still steps through saved versions)
  $("seq-undo").disabled = !on || !((metadataView && metadataHistory.length) || (several ? seqShown.history.length : doc?.undoStack.length));
  $("seq-redo").disabled = !on || !((metadataView && metadataFuture.length) || (several ? seqShown.future.length : doc?.redoStack.length));
  $("at-add").disabled = !on || !seqView?.selection || !doc;
  const lock = $("seq-lock");  // one toggle: closed lock = read only, open = editable
  lock.innerHTML = on ? ICONS.unlock : ICONS.lock;
  lock.classList.toggle("active", on);
  lock.title = on ? "Editable: click to make it read only again" : "Read only: click to edit";
  const star = changed || metadataDirty() ? " *" : "";
  // the title is the sequence viewer's only while it is the view shown (lock renewals call this on a timer)
  if ($("seqview").classList.contains("inactive") || seqShown?.mapping || seqShown?.graph || seqShown?.comparison) {
    // nothing: another view, or a viewer that sets its own title
  } else if (several) {
    setTitle(seqShown.title[0] + star, "", seqShown.title[1],
      [...seqShown.title[2], changed ? `${changed} changed, not saved` : ""].filter(Boolean).join(" · "));
  } else if (seqShown?.mode === "single" && doc) {
    setTitle(seqShown.items[0].label + star, doc.data.organism,
      [`${doc.length.toLocaleString()} ${doc.data.protein ? "aa" : "bp"}`, doc.data.protein ? "" : doc.circular ? "circular" : "linear"],
      doc.data.description || (doc.data.organism ? "" : seqShown.trace ? "Sanger read" : doc.data.protein ? "Protein" : "Sequence"));
  } else if (seqShown?.mode === "alignment" && doc) {
    setTitle(seqShown.items[0].label + star, "", [`${doc.names.length} sequences`, `${doc.length.toLocaleString()} columns`], "Alignment");
  }
  scheduleAnnotations();  // the side panel's table follows every edit, undo and save
}

// Bases typed or pasted: over the selection, or at the cursor
function typeBases(bases) {
  const { doc } = seqShown;
  if (!doc) return toast("Click in a sequence first, to place the cursor", true);
  const s = rowSelection(), caret = seqView.caret, row = seqShown.row ?? 0;
  if (s?.parts) return toast("A feature's parts are selected: select one stretch to type over (drag)", true);
  let at;
  if (s?.wraps) at = doc.replaceAcross(s.c0, s.c1, bases) - bases.length;  // the bases go before the origin
  else if (s) { doc.replace(s.c0, s.c1, bases); at = s.c0; }
  else if (caret !== null) { doc.insert(caret, bases); at = caret; }
  else return toast("Click in the sequence first, to place the cursor", true);
  refreshSingle();
  seqView.setCaret(at + bases.length, row);
  seqView.showPosition(at + bases.length);
}

function deleteBases(forward) {
  const { doc } = seqShown;
  if (!doc) return;
  const s = rowSelection(), caret = seqView.caret, row = seqShown.row ?? 0;
  if (s?.parts) return toast("A feature's parts are selected: select one stretch to delete (drag)", true);
  let at;
  if (s?.wraps) at = doc.replaceAcross(s.c0, s.c1);  // the cursor at the end, by the origin
  else if (s) { doc.remove(s.c0, s.c1); at = s.c0; }
  else if (caret !== null && !forward && caret > 0) { doc.remove(caret - 1, caret - 1); at = caret - 1; }
  else if (caret !== null && forward && caret < doc.length) { doc.remove(caret, caret); at = caret; }
  else return;
  refreshSingle();
  seqView.setCaret(at, row);
  seqView.showPosition(at);
}

function undoRedo(redo) {
  if (!editable()) return savedUndo(redo);
  if (metadataLast()) {  // the form was edited after the sequence
    const [from, to] = redo ? [metadataFuture, metadataHistory] : [metadataHistory, metadataFuture];
    const snapshot = from.pop();
    if (snapshot) {
      to.push(metadataValues());
      for (const key of metadataFields) $("metadata-form").elements[key].value = snapshot[key];
      renderEditButtons(); return;
    }
  }
  if (seqShown.mode === "sequences") {  // the last edit, whichever sequence it was in
    const [from, to] = redo ? [seqShown.future, seqShown.history] : [seqShown.history, seqShown.future];
    const i = from.pop();
    if (i === undefined) return;
    redo ? seqShown.docs[i].redo() : seqShown.docs[i].undo();
    to.push(i);
    return refreshRows();
  }
  const doc = editDoc();
  if (!(redo ? doc.redo() : doc.undo())) return;
  if (seqShown.alignDoc) refreshAlignment(); else refreshSingle();
}

// Keys on the viewer (it has the focus after a click)
function editKey(e) {
  const ctrl = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();
  if (ctrl && key === "s") { e.preventDefault(); return saveShown(); }
  if (!editable() && ctrl && (key === "z" || key === "y")) {
    e.preventDefault(); return savedUndo(key === "y" || e.shiftKey);
  }
  if (!editable()) {
    const edits = (ctrl && ["z", "y", "x", "v"].includes(key)) || (!ctrl && !e.altKey &&
      (e.key === "Backspace" || e.key === "Delete" || e.key === " " || (e.key.length === 1 && SeqEdit.cleanBases(e.key, seqView.protein))));
    if (edits) lockedHint();
    return;
  }
  if (seqShown.alignDoc) return alignmentKey(e);
  if (ctrl && (key === "z" && !e.shiftKey)) { e.preventDefault(); return undoRedo(false); }
  if (ctrl && (key === "y" || (key === "z" && e.shiftKey))) { e.preventDefault(); return undoRedo(true); }
  if (ctrl && key === "x") { e.preventDefault(); copySeqSelection(); return deleteBases(false); }
  if (ctrl || e.altKey) return;
  if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); return deleteBases(e.key === "Delete"); }
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {  // the cursor moves
    const s = seqView.selection;
    const from = seqView.caret ?? (s ? (e.key === "ArrowLeft" ? s.c0 : s.c1 + 1) : null);
    if (from === null) return;
    e.preventDefault();
    const to = s && seqView.caret === null ? from : from + (e.key === "ArrowLeft" ? -1 : 1);
    seqView.setCaret(to, s ? s.r0 : seqView.caretRow);
    return seqView.showPosition(to);
  }
  if (e.key.length === 1 && SeqEdit.cleanBases(e.key, seqView.protein)) { e.preventDefault(); typeBases(e.key.toUpperCase()); }
}

// Pasted text: FASTA headers and anything but bases are left out
function pasteBases(text) {
  const bases = SeqEdit.cleanBases(text.split(/\r?\n/).filter((l) => !l.startsWith(">")).join(""), seqView.protein);
  if (!bases) return toast("The clipboard has no DNA sequence to paste", true);
  typeBases(bases);
  toast(`Pasted ${bases.length} ${seqView.unit}`);
}

// A save changes the item's file (mtime, length, feature count). Update the cached
// item instead of reloading the whole library: on a big list that was a full fetch
// and render for every Ctrl+S.
function noteSaved(it, saved) {
  if (!it) return;
  if (saved.mtime != null) it.mtime = saved.mtime;
  if (saved.length != null) it.length = saved.length;
  if (saved.features != null) it.features = saved.features;
  const parent = it.parent && itemById(it.parent);
  if (parent && saved.mtime != null) parent.mtime = saved.mtime;  // a list's file changed
}

const saveShown = () => (seqShown?.alignDoc ? saveAlignment() : seqShown?.mode === "sequences" ? saveRows() : saveSingle());

async function saveSingle() {
  if (!canEdit() || (!seqShown.doc?.dirty && !metadataDirty())) return;
  const { doc, items: [it] } = seqShown;
  try {
    const metadata = metadataDirty() && metadataItem?.id === it.id
      ? Object.fromEntries(metadataFields.filter((key) => metadataValues()[key] !== (metadataInitial[key] || ""))
          .map((key) => [key, metadataValues()[key]])) : undefined;
    const saved = await api("PUT", `/api/sequences/${it.id}`, { ...doc.toData(), metadata, geneticCode: codeChoices[it.id] || seqShown.fileCode });
    // the saved file's features (with the ids the next save needs)
    const fresh = await api("GET", `/api/sequences/${it.id}`);
    doc.features = fresh.features;
    doc.data = fresh;
    doc.markSaved();
    if (metadata) {
      metadataInitial = { ...metadataInitial, ...metadataValues() };
      metadataHistory = []; metadataFuture = [];
    }
    const now = itemById(it.id) || it;  // the list may have been reloaded meanwhile
    noteSaved(now, saved);
    seqShown.items = [now];
    seqKey = `single:${now.id}:${now.mtime}`;  // no reload: undo stays
    shownVersion = versionOf(shownIds);
    refreshSingle();
    toast("Saved");
  } catch (e) {
    toast(`Save failed: ${e.message}`, true);
  }
}

// Several sequences: each changed one, one at a time (a list's share a file)
async function saveRows() {
  if (!canEdit()) return;
  const shown = seqShown;
  const changed = shown.docs.map((doc, i) => [doc, shown.items[i]]).filter(([doc]) => doc.dirty);
  if (!changed.length) return;
  try {
    for (const [doc, it] of changed) {
      const saved = await api("PUT", `/api/sequences/${it.id}`, { ...doc.toData(), geneticCode: codeChoices[it.id] || docCode(doc) });
      doc.features = (await api("GET", `/api/sequences/${it.id}`)).features;  // with the ids the next save needs
      noteSaved(itemById(it.id) || it, saved);  // the list may have been reloaded meanwhile
      doc.markSaved();
    }
    shown.items = shown.items.map((it) => itemById(it.id) || it);
    if (seqShown === shown) seqKey = "sequences:" + shown.items.map((it) => `${it.id}:${it.mtime}`).join(",");  // no reload: undo stays
    shownVersion = versionOf(shownIds);
    refreshRows();
    toast(`Saved ${changed.length} sequence${changed.length === 1 ? "" : "s"}`);
  } catch (e) {
    toast(`Save failed: ${e.message}`, true);
  }
}

// ── Gap editing (an alignment) ──────────────────────────────────────────
// Space or "-" pushes the selected block (or the bases from the cursor up to the
// next gap) one column right; Backspace pulls it left into the gap before it;
// Delete moves the gaps inside the selection to its end (or removes the gap
// after the cursor). Bases never change.
function refreshAlignment() {
  const { alignDoc: doc } = seqShown;
  if (seqShown.variantData) { seqShown.variantData.stale = true; seqShown.parsedVariants = null; }
  const view = getSeqView();
  view.setRows(doc.rows().map((r) => ({ ...r, ...seqShown.rowInfo?.[r.name] })), "alignment", true);
  if (seqShown.alignSources) view.setFeatures(seqShown.alignSources);
  renderSeqControls("alignment");
  renderEditButtons();
  refind();  // the search hits of the edited sequence
}

function alignmentKey(e) {
  const ctrl = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();
  if (ctrl && key === "z" && !e.shiftKey) { e.preventDefault(); return undoRedo(false); }
  if (ctrl && (key === "y" || (key === "z" && e.shiftKey))) { e.preventDefault(); return undoRedo(true); }
  if (ctrl || e.altKey) return;
  const s = seqView.selection, caret = seqView.caret, row = seqView.caretRow || 0;
  if (s?.consensus) return;  // the consensus has no gaps of its own to move
  if (s?.wholeRows && e.key === "Delete") { e.preventDefault(); return removeAlignmentRows(); }
  if (s?.wholeRows && [" ", "-", "Backspace"].includes(e.key)) {
    e.preventDefault(); return toast("Select a block of bases to edit gaps; Delete removes selected rows", true);
  }
  if (s?.rows && [" ", "-", "Backspace", "Delete"].includes(e.key)) {
    e.preventDefault();
    return toast("Gaps move in a block of rows: drag over them, or Shift+click, instead of Ctrl+click", true);
  }
  if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key) && caret !== null) {  // the cursor moves
    e.preventDefault();
    const dx = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    const dy = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0;
    seqView.setCaret(caret + dx, row + dy);
    return seqView.showPosition(caret + dx);
  }
  // the block: the selection, or the cursor (c1 = c0 - 1)
  const b = s ? { r0: s.r0, r1: s.r1, c0: s.c0, c1: s.c1 } : caret !== null ? { r0: row, r1: row, c0: caret, c1: caret - 1 } : null;
  const { alignDoc: doc } = seqShown;
  let moved = null;  // columns the block moved
  if (e.key === " " || e.key === "-") {
    e.preventDefault();
    if (!b) return toast("Click in a row first, to place the cursor", true);
    if (doc.push(b.r0, b.r1, b.c0, b.c1)) moved = 1;
  } else if (e.key === "Backspace") {
    e.preventDefault();
    if (!b) return;
    if (doc.pull(b.r0, b.r1, b.c0, b.c1)) moved = -1;
    else return toast(s ? "No gap before the selection in every selected row" : "No gap before the cursor", true);
  } else if (e.key === "Delete") {
    e.preventDefault();
    if (!b) return;
    if (s ? doc.squeeze(b.r0, b.r1, b.c0, b.c1) : doc.pull(b.r0, b.r1, b.c0 + 1, b.c0)) moved = 0;
    else return toast(s ? "No gaps in the selection" : "No gap after the cursor", true);
  } else if (e.key.length === 1 && /[a-z*]/i.test(e.key)) {
    return toast("In an alignment only gaps can be edited: Space or - adds one, Backspace/Delete removes one", true);
  } else return;
  if (moved === null) return;
  refreshAlignment();
  if (s) seqView.select(b.c0 + moved, b.c1 + moved, b.r0, b.r1);
  else seqView.setCaret(caret + moved, row);
  seqView.showPosition(b.c0 + moved);
}

function removeAlignmentRows() {
  if (!editable() || !seqShown?.alignDoc || !seqView?.selection?.wholeRows) return;
  const rows = seqView.selectedRows();
  if (rows.length >= seqShown.alignDoc.names.length) return toast("Keep at least one sequence in the alignment", true);
  if (!seqShown.alignDoc.removeRows(rows)) return;
  seqView.clearSelection();
  refreshAlignment();
  toast(`Removed ${rows.length} sequence${rows.length === 1 ? "" : "s"} from the alignment (Ctrl+Z to undo)`);
}

async function saveAlignmentRowsAsSequences() {
  if (seqShown?.mode !== "alignment" || !seqView?.selection?.wholeRows) return;
  const id = seqShown.alignment.id, all = seqShown.alignDoc.rows();
  const rows = seqView.selectedRows().map((i) => ({ ...all[i] }));
  try {
    await api("POST", `/api/alignment/${id}/copy-rows`, { rows, as_sequences: true });
    await loadList();
    toast(`Saved ${rows.length} sequence${rows.length === 1 ? "" : "s"} in a new list`);
  } catch (e) { toast(`Couldn't save the sequences: ${e.message}`, true); }
}

async function saveAlignment() {
  if (!canEdit() || !seqShown.alignDoc?.dirty) return;
  const { alignDoc: doc, items: [it] } = seqShown;
  try {
    const saved = await api("PUT", `/api/alignment/${it.id}`, { rows: doc.rows() });
    doc.markSaved();
    const now = itemById(it.id) || it;  // the list may have been reloaded meanwhile
    noteSaved(now, saved);
    seqShown.items = [now];
    seqShown.alignment = now;
    seqKey = `alignment:${now.id}:${now.mtime}`;  // no reload: undo stays
    shownVersion = versionOf(shownIds);
    renderEditButtons();
    toast("Saved");
  } catch (e) {
    toast(`Save failed: ${e.message}`, true);
  }
}

// ── Feature dialog: double-click a feature, or + Feature on a selection ──
let featureEditing = null;  // index in the document's features, or "new"

function openFeatureDialog(index, range) {
  const form = $("feature-dialog").querySelector("form");
  if (!seqShown.doc) return;
  if (index === "new" && !range) return toast("Select bases within one sequence first", true);
  const f = index === "new"
    ? SeqEdit.withParts({ name: "new feature", type: "misc_feature", strand: 1, notes: {} },
        range.parts ? range.parts.map(([start, end]) => ({ start, end }))
          : range.wraps ? [{ start: range.c0, end: seqShown.doc.length - 1 }, { start: 0, end: range.c1 }]
                    : [{ start: range.c0, end: range.c1 }])
    : seqShown.doc.features[index];
  featureEditing = index;
  $("feature-title").textContent = index === "new" ? "New feature" : `Feature: ${f.name}`;
  form.name.value = f.name;
  form.type.value = f.type;
  form.location.value = SeqEdit.locationText(f);
  form.qualifiers.value = Object.entries(f.notes || {})
    .flatMap(([k, values]) => values.map((v) => `${k}=${v}`)).join("\n");
  const readOnly = !editable();  // read only: shown, not changed
  for (const field of [form.name, form.type, form.location, form.qualifiers]) field.readOnly = readOnly;
  $("feature-delete").hidden = index === "new" || readOnly;
  $("feature-ok").hidden = readOnly;
  $("feature-cancel").textContent = readOnly ? "Close" : "Cancel";
  form.querySelector(".error").hidden = true;
  $("feature-dialog").showModal();
}

function featureDialogDone(form, event) {
  const action = event.submitter?.value;
  if (action === "cancel") return;
  event.preventDefault();
  if (!editable()) return $("feature-dialog").close();  // read only (Enter in a field)
  const { doc } = seqShown;
  if (action === "delete") {
    doc.deleteFeature(featureEditing);
  } else {
    const error = form.querySelector(".error");
    const where = SeqEdit.parseLocation(form.location.value, doc.length);
    if (!where) {
      error.textContent = `Not a location in 1..${doc.length}: write e.g. 100..200, complement(100..200) or join(1..50,80..120)`;
      error.hidden = false;
      return;
    }
    const notes = {};
    for (const line of form.qualifiers.value.split(/\r?\n/)) {
      const i = line.indexOf("=");
      if (i > 0) (notes[line.slice(0, i).trim()] ||= []).push(line.slice(i + 1).trim());
    }
    const base = featureEditing === "new" ? {} : doc.features[featureEditing];
    const feature = SeqEdit.withParts({ ...base, name: form.name.value.trim(), type: form.type.value.trim(),
                                        strand: where.strand, notes, partial: where.partial }, where.parts);
    if (featureEditing === "new") doc.addFeature(feature);
    else doc.setFeature(featureEditing, feature);
  }
  $("feature-dialog").close();
  refreshSingle();
}

function changeSeqShow(change) {
  Object.assign(seqShow, change);
  try { localStorage.setItem("cladia.view", JSON.stringify(seqShow)); } catch (e) {}
  if (seqView) seqView.setShow(seqShow);
  if ("hiddenTypes" in change && seqShown) {  // the features shown change
    if (seqShown.docs) refreshSingle();
    else if (seqShown.alignSourcesAll) seqView.setFeatures(seqShown.alignSources = shownSources(seqShown.alignSourcesAll));
    else if (seqShown.mapping) {
      const ref = seqShown.mapping.reference;
      seqView.setFeatures({ [ref.name]: { features: viewerFeatures(ref),
        code: codeChoices[seqShown.items[0].id] || ref.geneticCode, item: seqShown.items[0].id } });
    } else if (seqShown.mode === "sequences") { seqKey = null; showSelection(shownIds); }
  }
  renderSeqControls(seqShown?.mode);
}

// The feature types in what is shown, with how many of each (all of them, hidden or not)
function shownTypes() {
  const lists = [];
  if (seqShown?.docs) lists.push(...seqShown.docs.map((d) => d.features));
  else if (seqShown?.alignSourcesAll) lists.push(...Object.values(seqShown.alignSourcesAll).map((i) => i.features));
  else if (seqShown?.mapping) lists.push(seqShown.mapping.reference.features || []);
  else if (seqShown?.comparison) lists.push(...seqShown.comparison.genomes.map((g) => g.data.features || []));
  const counts = new Map();
  for (const features of lists) for (const f of features || []) counts.set(f.type, (counts.get(f.type) || 0) + 1);
  return [...counts].sort(([a, n], [b, m]) => m - n || a.localeCompare(b));
}

// View > Show: a tick per feature type, with its colour (the types of what is shown)
function renderTypes() {
  const box = $("seq-types");
  box.innerHTML = "";
  const types = shownTypes();
  box.hidden = !seqShow.features || !types.length;
  if (!types.length) return;
  const head = document.createElement("div");
  head.className = "types-head";
  head.innerHTML = "<span>Feature types</span><a data-all>All</a><a>None</a>";
  head.querySelector("[data-all]").onclick = () => setHidden(seqShow.hiddenTypes.filter((t) => !types.some(([x]) => x === t)));
  head.lastElementChild.onclick = () => setHidden([...new Set([...seqShow.hiddenTypes, ...types.map(([t]) => t)])]);
  const list = document.createElement("div");
  list.className = "types-list";
  for (const [type, count] of types) {
    const label = document.createElement("label");
    label.className = "type";
    label.innerHTML = `<input type="checkbox"><span class="swatch"></span><span></span><span class="count">${count}</span>`;
    const box = label.querySelector("input");
    box.checked = !hiddenType(type);
    box.onchange = () => setHidden(box.checked ? seqShow.hiddenTypes.filter((t) => t !== type) : [...seqShow.hiddenTypes, type]);
    label.querySelector(".swatch").style.background = featureColor(type);
    label.children[2].textContent = type;
    list.append(label);
  }
  box.append(head, list);
}

function setHidden(hiddenTypes) {
  changeSeqShow({ hiddenTypes });
  renderTypes();
}

// ── Data tracks (bedGraph / WIG kept beside a sequence, library.add_tracks) ──
// Shown under the ruler like the GC track; Show ▾ ticks each one (hidden ones by
// name in seqShow.hiddenData) and removes it.
async function loadDataTracks(it, view) {
  const list = it.tracks ? (await api("GET", `/api/tracks/${it.id}`)).tracks : [];
  view.setDataTracks(list);
}

function renderDataTrackControls() {
  const tracks = seqView?.gcEnabled() ? seqView.dataTracks || [] : [];
  $("seq-data-controls").hidden = !tracks.length;
  const box = $("seq-data-list");
  box.replaceChildren();
  for (const t of tracks) {
    const row = document.createElement("label");
    row.className = "toggle data-track";
    row.title = t.file ? `From ${t.file}` : "";
    row.innerHTML = `<input type="checkbox"> <span></span> <button type="button" class="link" title="Take this track off the sequence">remove</button>`;
    const tick = row.querySelector("input");
    tick.checked = !(seqShow.hiddenData || []).includes(t.name);
    tick.onchange = () => changeSeqShow({ hiddenData: tick.checked ? (seqShow.hiddenData || []).filter((n) => n !== t.name)
                                                                  : [...(seqShow.hiddenData || []), t.name] });
    row.querySelector("span").textContent = t.name;
    row.querySelector("button").onclick = async (e) => {
      e.preventDefault();
      const it = seqShown?.items?.[0];
      if (!it || !confirm(`Remove the track ${t.name} from ${it.label}?`)) return;
      try {
        await api("POST", `/api/tracks/${it.id}/remove`, { name: t.name });
        await loadList();
        await loadDataTracks(itemById(it.id) || it, seqView);
        seqView.layout?.(); seqView.draw();
        renderDataTrackControls();
      } catch (err) { toast(err.message, true); }
    };
    box.append(row);
  }
}

// The controls that make sense for what is shown
function renderSeqControls(mode) {
  $("seq-sort-group").hidden = !(seqShown?.list || (mode === "alignment" && seqShown?.alignment));
  $("seq-variant-options").hidden = mode !== "alignment" || !seqShown?.parsedVariants?.sites.length;
  $("seq-variants").checked = !!seqShow.variants;
  $("seq-gc-controls").hidden = !seqView?.gcEnabled();
  $("seq-gc-options").hidden = !(seqShow.gc || seqShow.gcSkew);
  $("seq-gc-mode-label").hidden = !seqShow.gcSkew;
  $("seq-gc-size").value = seqShow.gcSize || "compact";
  $("seq-gc-window").options[0].text = `Auto (${SeqEdit.autoWindow(seqView?.length || 1).toLocaleString()} bp)`;
  $("seq-gc").checked = !!seqShow.gc;
  $("seq-gc-skew").checked = !!seqShow.gcSkew;
  $("seq-gc-window").value = seqShow.gcWindow || "0";
  $("seq-gc-mode").value = seqShow.gcCumulative ? "cumulative" : "window";
  renderDataTrackControls();
  $("seq-features").checked = seqShow.features;
  const noReads = !seqShown?.mapping?.reads || !seqShown?.items?.[0]?.reads;
  $("seq-reads").checked = mappingReadsMode === "all";
  $("seq-reads").disabled = noReads;
  $("seq-reads-label").title = noReads ? "This mapping kept no reads: map again to see them" : "The reads under the sequence";
  $("seq-reads-label").hidden = !seqShown?.mapping;
  $("seq-translations").checked = seqShow.translations;
  $("seq-complement").checked = seqShow.complement;

  const dnaOnly = mode !== "single" || !!seqView?.protein;  // a protein: no map switch, genetic code or complement
  const types = shownTypes();
  const hasCds = types.some(([type]) => String(type).toLowerCase() === "cds");
  $("seq-features-label").hidden = !types.length;
  $("seq-map").hidden = dnaOnly || !!seqShown?.trace;  // a Sanger read is never circular
  $("seq-code-label").hidden = dnaOnly || !hasCds || !seqShow.features;
  $("seq-complement-label").hidden = dnaOnly || !!seqShown?.mapping;
  $("seq-strand-options").hidden = $("seq-complement-label").hidden;
  $("seq-annotation-options").hidden = !types.length;
  $("seq-translations-label").hidden = !!seqView?.protein || !hasCds || !seqShow.features;
  // mappings, comparisons and a graph's tracks aren't edited: no Edit group, no + Feature
  const editing = !seqShown?.mapping && !seqShown?.comparison && !seqShown?.graph && !seqShown?.variantItem;  // (nor a VCF's reference)
  updateMatrixView();
  $("group-edit").hidden = !editing;
  $("graph-back").hidden = !seqShown?.track;  // a graph's track: ◀ Graph
  renderFigureSwitch();  // shown or hidden once, for this item



  renderLabelFields();
  renderEditButtons();
  for (const b of document.querySelectorAll("[data-seqmap]")) b.classList.toggle("active", b.dataset.seqmap === seqShown?.map);
}

// The genetic code of a single sequence (the Code menu)
function renderSeqCode() {
  const select = $("seq-code");
  const it = seqShown?.items[0];
  select.innerHTML = "";
  for (const code of geneticCodes) {
    const fromFile = code.id === seqShown?.fileCode ? " (from file)" : "";
    select.append(new Option(`${code.id} · ${code.name}${fromFile}`, code.id));
  }
  if (it) select.value = codeChoices[it.id] || seqShown.fileCode;
}

function changeSeqCode(number) {
  const it = seqShown?.items[0];
  if (!it) return;
  if (number === seqShown.fileCode) delete codeChoices[it.id];
  else codeChoices[it.id] = number;
  try { localStorage.setItem("cladia.codes", JSON.stringify(codeChoices)); } catch (e) {}
  seqView.setCode(number);
}

function seqFind() {
  if (!seqView) return;
  const count = seqView.find($("seq-find").value);
  matchAt = -1;
  $("seq-found").textContent = $("seq-find").value.trim() ? `${count} found` : "";
  findArrows();
  if (count) seqFindStep(1);
}

// After an edit: the search again, on what is there now; the next match is
// the first one from where the current one was
let matchAt = -1;  // where the match last stepped to starts (the edit clears the matches)

function refind() {
  if (!seqView || !$("seq-find").value.trim()) return;
  const at = matchAt;
  const count = seqView.find($("seq-find").value);
  const next = seqView.matches.findIndex((m) => m.c0 >= at);
  seqView.matchIndex = (next < 0 ? 0 : next) - 1;
  $("seq-found").textContent = `${count} found`;
  findArrows();
}

// ◀ ▶ usable only with matches to step through
function findArrows() {
  $("seq-prev").disabled = $("seq-next").disabled = !seqView?.matches.length;
}

function seqFindStep(step) {
  if (!seqView || !seqView.matches.length) return;
  const i = seqView.findNext(step);
  const m = seqView.matches[i];
  matchAt = m.c0;
  const label = m.feature || m.name;
  $("seq-found").textContent = `${i + 1} of ${seqView.matches.length}${label ? ` · ${label}` : ""}`;
}

async function copySeqSelection() {
  const range = seqView?.readSel;  // the part of one read selected in a mapping's lane
  const caret = !range && seqView?.readCaret;  // a click (a cursor): its read, whole
  const read = range?.read || caret?.read;
  const label = (seqShown?.items[0]?.label || "read").replace(/\s+/g, "_");
  const text = range ? seqView.readRangeFasta(label)
    : read ? readFasta(read)
    : seqView?.selectionCopy();
  if (!text) return;
  lastCopy = read ? null : { text, make: copyMaker() };  // before the clipboard write (async): Ctrl+V may follow at once
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {  // clipboard API not allowed: the old way (which takes the focus: given back)
    const focused = document.activeElement;
    const area = Object.assign(document.createElement("textarea"), { value: text });
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    focused?.focus();
  }
  if (range) return toast(`Copied read ${read[0] + 1}–${read[1] + 1}, ` +
                          `columns ${range.c0 + 1}–${range.c1 + 1} (${range.c1 - range.c0 + 1} bp)`);
  if (read) return toast(`Copied read ${read[0] + 1}–${read[1] + 1} (${read[2]})`);
  const s = seqView.selection;
  toast(seqShown.mode === "single" ? `Copied ${text.length} ${seqView.unit}`
    : s.consensus ? `Copied the consensus: ${text.length} ${seqView.unit}`
    : s.r0 === s.r1 ? `Copied ${text.length} ${seqView.unit} of ${seqView.rows[s.r0].name}`
    : s.wholeRows && seqShown.mode === "alignment" ? `Copied ${seqView.selectedRows(s).length} alignment rows as FASTA`
    : `Copied ${seqView.selectedRows(s).length} sequences × ${s.c1 - s.c0 + 1} columns as FASTA`);
}

// A mapping's read as FASTA: ><mapping> <start>–<end> <strand>
function readFasta(read) {
  const label = (seqShown.items[0]?.label || "read").replace(/\s+/g, "_");
  return `>${label} ${read[0] + 1}-${read[1] + 1} ${read[2]}\n${read[5]}\n`;
}

async function extractSeqSelection() {
  const s = seqView?.selection;
  const aln = seqShown?.alignment;
  if (!s || !aln) return;
  if (seqShown.alignDoc?.dirty) return toast("Save the alignment changes before extracting a block", true);
  if (s.rows) return toast("Extract takes a block of rows: drag over the names, or Shift+click, instead of Ctrl+click", true);
  const { id } = await api("POST", "/api/library/extract", { id: aln.id, start: s.c0, end: s.c1, rows: [s.r0, s.r1] });
  await loadList();
  toast(`Extracted ${itemById(id)?.label}`);
}

// Double-clicking a name opens that sequence (if it's in the list)
function openViewerRow(name) {
  if (seqShown?.graph) return;  // a graph's track: already on its own
  const row = seqView?.rows.find((r) => r.name === name);  // row.item: from the features data
  const target = itemById(row?.item) || items.find((it) => it.kind === "sequence" && it.name === name.replace(/^_R_/, ""));
  if (!target) return toast(`${name} isn't a sequence in the list`, true);
  selected = [target.id];
  anchorId = target.id;
  if (target.parent) expanded.add(target.parent);
  showSelection();
  renderList();
}

// ── The bar's icons ──────────────────────────────────────────────────────
// One set, drawn the same way (16 px, a 1.8 px line in the text colour), so the
// icon buttons look alike and stay centred at any text size (text glyphs such
// as − + ↶ ◀ sat off centre when Windows' text size was raised). Buttons with
// data-icon get theirs at startup (installIcons).
const ICON = (body) =>`<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" ` +
  `stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const CORNERS = '<path d="M1.8 5.5V1.8h3.7M10.5 1.8h3.7v3.7M14.2 10.5v3.7h-3.7M5.5 14.2H1.8v-3.7"/>';
const ICONS = {
  minus: ICON('<path d="M3 8h10"/>'),
  plus: ICON('<path d="M3 8h10M8 3v10"/>'),
  whole: ICON(CORNERS),  // fit whole: four corners
  selection: ICON(CORNERS + '<rect x="5.5" y="5.5" width="5" height="5" fill="currentColor" stroke="none"/>'),  // fit selection
  undo: ICON('<path d="M5.5 3 2.5 6l3 3"/><path d="M2.5 6h7a3.5 3.5 0 0 1 0 7H6.5"/>'),
  redo: ICON('<path d="M10.5 3l3 3-3 3"/><path d="M13.5 6h-7a3.5 3.5 0 0 0 0 7h3"/>'),
  prev: ICON('<path d="M10 3 5 8l5 5"/>'),
  // the side panel: fill the work area (arrows out), back (arrows in), close
  maximize: ICON('<path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"/>'),
  restore: ICON('<path d="M13 7H9V3M9 7l4.5-4.5M3 9h4v4M7 9l-4.5 4.5"/>'),
  close: ICON('<path d="M4 4l8 8M12 4l-8 8"/>'),
  next: ICON('<path d="M6 3l5 5-5 5"/>'),
  // the edit lock: a tall shackle, closed (read only) or swung open (editable)
  lock: ICON('<rect x="2.8" y="7.2" width="10.4" height="7.3" rx="1.6"/><path d="M5 7.2V5a3 3 0 0 1 6 0v2.2"/>'),
  unlock: ICON('<rect x="2.8" y="7.2" width="10.4" height="7.3" rx="1.6"/><path d="M5 7.2V5a3 3 0 0 1 6 0"/>'),
};
function installIcons(root = document) {
  for (const b of root.querySelectorAll("[data-icon]")) b.innerHTML = ICONS[b.dataset.icon];
}

// Visible zoom controls mirror the wheel gestures; fitting is an explicit action.
function installViewerZoom() {
  for (const [id, tree] of [["seq", false], ["tree", true], ["graph", false]]) {
    const group = document.createElement("span");
    group.className = "group viewer-zoom";
    group.setAttribute("aria-label", "Zoom");
    const pair = (axis, label, hint) => `${label ? `<span class="zoom-axis">${label}</span>` : ""}
      <button class="secondary icon-button zoom-step" data-zoom="${axis}-out" title="Zoom out${hint}" aria-label="Zoom out${label ? ` ${label.toLowerCase()}` : ""}">${ICONS.minus}</button>
      <button class="secondary icon-button zoom-step" data-zoom="${axis}-in" title="Zoom in${hint}" aria-label="Zoom in${label ? ` ${label.toLowerCase()}` : ""}">${ICONS.plus}</button>`;
    group.innerHTML = `<span class="group-label">Zoom</span>` +
      (tree ? pair("rows", "Rows", " · Ctrl+wheel") + pair("branches", "Branches", " · Ctrl+Shift+wheel") : pair("scale", "", " · Ctrl+wheel")) +
      `<button class="secondary icon-button zoom-step" data-zoom="whole" aria-label="Fit whole" title="Fit the whole ${tree ? "tree, including tip labels" : id === "seq" ? "sequence horizontally" : "graph"}">${ICONS.whole}</button>
       <button class="secondary icon-button zoom-step" data-zoom="selection" disabled aria-label="Fit selection" title="Fit the selected ${tree ? "clade or tip" : id === "seq" ? "range or read" : "nodes"}">${ICONS.selection}</button>`;
    group.onclick = e => {
      const action = e.target.closest("[data-zoom]")?.dataset.zoom;
      const view = id === "seq" ? seqView : tree ? treeView : graphView;
      if (!view || !action) return;
      if (action === "whole") tree || id === "seq" ? view.fitWhole() : view.fit();
      else if (action === "selection") view.fitSelection();
      else {
        const factor = action.endsWith("in") ? 1.25 : .8;
        if (tree) action.startsWith("rows") ? view.zoomTips(factor) : view.zoomBranches(factor, view.firstNodeX());
        else if (id === "seq" && view.circularMap) view.circularZoom(factor);
        else view.zoom(factor);
      }
      updateViewerZoom();
    };
    $(id + "-controls").prepend(group);
  }
}

function updateViewerZoom() {
  for (const [id, enabled] of [["seq", seqView?.hasZoomSelection()], ["tree", treeView?.selected >= 0], ["graph", graphView?.pathMode ? graphView.path.length > 0 : graphView?.chosen?.size > 0]]) {
    const button = document.querySelector(`#${id}-controls [data-zoom="selection"]`);
    if (button) button.disabled = !enabled;
  }
}
