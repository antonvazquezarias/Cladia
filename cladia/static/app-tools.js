// Cladia's page, continued from app-viewer.js: the tools, result tables, figures, view windows, the tree view, export, jobs.
// The page's scripts share one global scope and run in the order index.html loads them,
// so code here can use what earlier files declare; at load time it mustn't call into later ones.

// The nodes selected in the graph view as queries (null if there are none): the server reads their bases
function graphQueries() {
  if (!graphView || !graphShown || $("graphview").classList.contains("inactive")) return null;
  const nodes = graphView.selectedNodes();
  return nodes.length ? nodes.map((n) => ({ item: graphShown.item.id, node: n.id,
                                           label: `Node ${n.id} · ${n.length.toLocaleString()} bp` })) : null;
}

// The viewer's selection as queries (null if nothing is selected there)
function viewerQueries() {
  const s = seqView?.selection;
  if (!s || !seqShown || $("seqview").classList.contains("inactive")) return null;
  // a part: its range goes in the name; the whole of a row: no range
  const range = `${(s.c0 + 1).toLocaleString()}–${(s.c1 + 1).toLocaleString()}`;
  const part = (whole) => (whole ? { start: null, end: null, range: "" } : { start: s.c0 + 1, end: s.c1 + 1, range: ` ${range}` });
  if (s.consensus) {
    const sequence = seqView.consensus.slice(s.c0, s.c1 + 1).replace(/[- ]/g, "");
    const { range: shown, ...where } = part(s.c0 === 0 && s.c1 >= seqView.length - 1);
    return [{ item: seqShown.alignment.id, name: "Consensus", sequence, ...where, label: `Consensus${shown}` }];
  }
  const queries = [];
  for (const r of seqView.selectedRows(s)) {
    const row = seqView.rows[r];
    const pieces = seqShown.mode === "single" || s.parts ? seqView.selectionPieces(s) : [[s.c0, s.c1]];
    const sequence = pieces.map(([a, b]) => row.seq.slice(a, b + 1)).join("").replace(/-/g, "");
    if (!sequence) continue;  // only gaps in this row
    const item = seqShown.mode === "alignment" ? seqShown.alignment : seqShown.graph || seqShown.items[seqShown.mode === "single" ? 0 : r];
    const whole = seqShown.mode === "alignment" ? s.c0 <= row.first && s.c1 >= row.last
      : seqShown.mode === "single" ? seqView.selectionLength(s) >= row.seq.length : s.c0 === 0 && s.c1 >= row.seq.length - 1;
    const { range: shown, ...where } = part(whole);
    // exactly a feature (e.g. clicked): named after it rather than its positions (a gene first)
    const exact = whole || s.wraps ? [] : (row.features || []).filter((f) => f.name && f.c0 === s.c0 && f.c1 === s.c1);
    const feature = (exact.find((f) => f.type === "gene") || exact[0])?.name;
    queries.push({ item: item.id, name: row.name, sequence, ...where, feature,
                   label: `${row.name}${feature ? ` ${feature}` : shown}${!feature && shown && seqShown.mode === "alignment" ? " (columns)" : ""}` +
                          ` · ${sequence.length.toLocaleString()} ${seqView.unit}` });
  }
  return queries;
}

// The Test tab's pair source: each pair of a primer design, and the pairs saved
// as lists (Save primers). Choosing one fills the forward and reverse fields.
async function loadPrimerSources(form) {
  const select = form.primer_source;
  if (!select) return;
  let data;
  try {
    data = await api("GET", "/api/primers/sources");
  } catch (e) {
    return;  // type them below
  }
  const keep = select.value;
  select.innerHTML = '<option value="">Type them below</option>';
  const group = (label) => {
    const g = document.createElement("optgroup");
    g.label = label;
    select.append(g);
    return g;
  };
  if (data.designs.length) {
    const g = group("From a primer design");
    for (const design of data.designs) {
      for (const pair of design.pairs) {
        const option = new Option(`${design.label} · pair ${pair.n}${pair.product ? ` (${pair.product} bp)` : ""}`,
                                  `${design.id}:${pair.n}`);
        option.dataset.forward = pair.forward;
        option.dataset.reverse = pair.reverse;
        g.append(option);
      }
    }
  }
  if (data.lists.length) {
    const g = group("Primer sequences in the library");
    for (const list of data.lists) {
      const option = new Option(list.label, `${list.id}:list`);
      option.dataset.forward = list.forward;
      option.dataset.reverse = list.reverse;
      g.append(option);
    }
  }
  select.value = keep;
  select.onchange = () => {
    const option = select.selectedOptions[0];
    if (!option?.dataset.forward && !option?.dataset.reverse) return;
    if (option.dataset.forward) form.forward.value = option.dataset.forward;  // a single primer fills its own side
    if (option.dataset.reverse) form.reverse.value = option.dataset.reverse;
  };
}

function openTool(name) { return openRegisteredCategory(name); }

// ── Result tables (BLAST hits, primer pairs) ─────────────────────────────
// One view for the tables tools make: a title, a filter, columns sorted by
// clicking their header, ticks and Copy / Export CSV, plus the kind's own
// actions on the ticked rows. A table is a spec:
//   { title, columns: [{field, title, how, raw(row), sortable, firstDown, cls}],
//     rows, filter(row, text), filterTitle, empty, tickable,
//     actions: [{label(n), title, enabled(n), run()}] }
// The BLAST and primer result tables live in resultview.js.

// A text file: the Save dialog in the app window, a download in a browser
async function saveTextFile(filename, text, type) {
  if (inPywebview()) {
    const path = await window.pywebview.api.save_file(filename, filename.split(".").pop());
    if (!path) return;
    await api("POST", "/api/write", { path, text });
    toast(`Saved ${path}`);
  } else {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type }));
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }
}

// ── Figures (figure.js) ──────────────────────────────────────────────────
// A figure is a library item (kind "figure") drawing a genome, a tree or a
// comparison, its source (library.add_figure). Figure ▾ at the right of the
// viewer bar makes a new one of what is shown, or opens one already made; a
// figure opens in the editor, drawn from its source each time, with ◀ <source>
// first in the bar. Tip names can take organisms and descriptions from the
// library's sequences of the same name (tree names have "_" for spaces).
let figureEditor = null, figureShown = null;

// The item shown that can have figures: a nucleotide sequence (a list's too), a tree, a comparison
function figureSourceShown() {
  if (!$("treeview").classList.contains("inactive")) return treeShown?.item || null;
  if ($("seqview").classList.contains("inactive") || !seqShown || seqShown.graph || seqShown.mapping) return null;
  if (seqShown.comparison) return seqShown.items[0];
  const it = seqShown.mode === "single" && seqShown.items.length === 1 ? seqShown.items[0] : null;
  return it && it.kind === "sequence" && !it.trace && !it.protein ? it : null;
}

// The figure items drawing this item (a list's sequence: by its list and name)
function figuresOf(source) {
  const id = source.parent || source.id, record = source.parent ? source.name : null;
  return items.filter((it) => it.kind === "figure" && it.source === id && (it.source_record ?? null) === record);
}

// Figure ▾: shown for an item that can have figures (a figure itself has ◀ <source> instead)
function renderFigureSwitch() {
  const source = figureSourceShown();
  $("view-figure").hidden = !source;
  if (source) {
    const n = figuresOf(source).length;
    const button = $("view-figure-button");  // "New ▾" until it has figures, then "New · n ▾" (n to open)
    button.textContent = n ? `New · ${n} ▾` : "New ▾";
    button.title = n ? `A new figure of this ${source.kind === "sequence" ? "genome" : source.kind}, or one of its ${n}` : "Make a figure of it for a paper (SVG, PDF, PNG, TIFF)";
  }
}

function openFigureMenu(e) {
  const source = figureSourceShown();
  if (!source) return;
  const made = figuresOf(source), part = selectionPart(source);
  const box = e.currentTarget.getBoundingClientRect();
  showMenu({ clientX: box.right - 260, clientY: box.bottom + 2 }, [
    ["New figure", () => newFigure(source)],
    part.why ? [`New figure of the selection (${part.why})`, null, true]
      : [`New figure of the selection: ${part.what}`, () => newFigure(source, part)],
    ...(made.length ? ["-", { heading: `Figures of ${source.label}` }, ...made.map((f) => [f.label, () => openItem(f.id)])] : []),
  ]);
}

// A figure of part of what is shown, from the viewer's selection: a tree's
// selected clade (or the clade spanning the selected tips), a stretch of a
// genome (a linear map of it), or some of a comparison's genomes. Returns
// { part, settings, name, what } or { why } it can't be made.
function selectionPart(source) {
  if (source.kind === "tree") {
    const tips = treeView?.selectedTipNames() || [];
    if (tips.length < 2) return { why: "select a clade, or two tips or more" };
    return { part: { tips }, name: `${source.label} (${tips.length} tips)`, what: `the clade of ${tips.length} tips` };
  }
  const s = seqView?.selection;
  if (source.kind === "comparison") {
    const rows = s ? seqView.selectedRows(s) : [];
    if (rows.length < 2) return { why: "select two genomes or more" };
    const all = seqShown.comparison.genomes.map((g, i) => i);
    return { part: { genomes: rows }, settings: { hidden: all.filter((i) => !rows.includes(i)) },
             name: `${source.label} (${rows.length} genomes)`, what: `${rows.length} of the ${all.length} genomes` };
  }
  if (!s || s.parts) return { why: "select a stretch of the sequence" };
  if (s.wraps) return { why: "a stretch across the origin can't be drawn yet" };
  const from = s.c0 + 1, to = s.c1 + 1, range = `${from.toLocaleString()}–${to.toLocaleString()}`;
  return { part: { start: from, end: to }, settings: { shape: "linear", range: { from, to } },
           name: `${source.label} ${range}`, what: `bases ${range}` };
}

async function newFigure(source, part = null) {
  if (hasUnsavedChanges()) return toast("Save or undo the changes first: the figure draws the saved item", true);
  try {
    const { id } = await api("POST", "/api/figures", { source: source.id, part: part?.part, settings: part?.settings, name: part?.name });
    await loadList();
    openItem(id);
  } catch (e) { toast(e.message, true); }
}

// Show one item, as a click on it in the list does
function openItem(id) {
  const it = itemById(id);
  if (!it) return;
  selected = [id];
  anchorId = id;
  for (const id of listAncestors(it)) expanded.add(id);
  showSelection();
  renderList();
}

function figureLookup() {
  const byName = new Map();
  for (const it of items) {
    if (it.kind !== "sequence" || it.missing) continue;
    const facts = { organism: it.organism || "", description: it.description || "" };
    for (const n of [it.name, it.name.replace(/ /g, "_")]) if (!byName.has(n)) byName.set(n, facts);
  }
  return (name) => byName.get(name) || byName.get(String(name).replace(/^_R_/, ""));
}

// it: a figure item
async function showFigure(it) {
  showView("figureview");
  renderFigureSwitch();  // (no Figure ▾ on a figure: it has ◀ <source>)
  $("figure-back").hidden = true;
  if (figureShown?.item.id === it.id && figureShown.item.mtime === it.mtime) return renderFigureBack();
  figureShown = { item: it };
  if (figureEditor) {
    try { await figureEditor.flush(); } catch (e) { return; }
    if (figureShown?.item.id !== it.id) return;
  }
  const data = await api("GET", `/api/figure/${it.id}`);
  if (figureShown?.item.id !== it.id) return;
  figureShown.data = data;
  if (data.missing) {
    figureEditor?.clear?.();
    setTitle(it.label, "", [], "Figure · its source is no longer in the library (restore it from the trash to draw it)");
    return;
  }
  const src = data.source;
  let drawn;
  try {
    drawn = data.of === "dotplot" ? await dotplotFigureSource(data)
      : await api("GET", data.of === "tree" ? `/api/tree/${src}` : data.of === "genome" ? `/api/sequences/${src}` : `/api/comparison/${src}`);
  } catch (e) {
    if (figureShown?.item.id !== it.id) return;
    figureEditor?.clear?.();
    setTitle(it.label, "", [], `Figure · ${e.message}`);
    return;
  }
  if (figureShown?.item.id !== it.id) return;
  if (data.of === "tree" && data.part?.tips) {  // a figure of one clade: the tree cut there, each time (live)
    const clade = cladeOf(drawn.nodes, data.part.tips);
    if (clade.lost) toast(`${clade.lost} of this figure's tips are no longer in the tree`, true);
    drawn = { ...drawn, nodes: clade.nodes };
  }
  figureEditor ??= new FigureEditor($("figureview"), {
    bar: $("figure-controls"),  // its zoom, undo and export buttons go in the viewer bar
    lookup: (name) => figureShown?.lookup?.(name),  // (none while a figure is being opened)
    onSave: (settings, id) => api("PUT", `/api/figure/${id}`, settings),
    onExport: exportFigure,
    onError: (message) => toast(message, true),
  });
  figureShown.lookup = figureLookup();
  figureEditor.open({ of: data.of, settings: data.settings, title: "", itemId: data.id }, drawn);
  const what = data.of === "tree" ? "tree" : data.of === "genome" ? "genome" : "genome comparison";
  setTitle(it.label, "", [], data.of === "dotplot" ? `Dot plot figure · sequences from ${data.source_label}` : `Figure of the ${what} ${data.source_label}`);
  renderFigureBack();
}

// A dot plot figure's two sequences, found again from its part ({ a, b }: each
// { item } a sequence, { list, record } a list's sequence, { row } an alignment's
// row, { genome } a comparison's genome); its source is the alignment or comparison
async function dotplotFigureSource(data) {
  let whole = null;
  const one = async (ref) => {
    if (ref.row !== undefined || ref.genome !== undefined) {
      whole ??= await api("GET", ref.row !== undefined ? `/api/alignment/${data.source}` : `/api/comparison/${data.source}`);
      if (ref.genome !== undefined) {
        const g = whole.genomes?.[ref.genome];
        if (!g) throw new Error("its genome is no longer in the comparison");
        return { name: g.name, seq: g.data.sequence };
      }
      const row = (whole.rows || whole).find((r) => r.name === ref.row);
      if (!row) throw new Error(`the row ${ref.row} is no longer in the alignment`);
      return { name: row.name, seq: row.sequence };
    }
    const it = ref.list ? items.find((x) => x.parent === ref.list && x.name === ref.record) : itemById(ref.item);
    if (!it) throw new Error("one of its sequences is no longer in the library");
    const d = await api("GET", `/api/sequences/${it.id}`);
    return { name: it.label, seq: d.sequence };
  };
  const part = data.part || {};
  const a = await one(part.a), b = JSON.stringify(part.b) === JSON.stringify(part.a) ? a : await one(part.b);
  return { a, b };
}

// The smallest clade holding these tips (their last common ancestor and all below
// it), as tree nodes again: { nodes, lost } (lost: how many tips are no longer
// there). nodes are the server's flat list, parents first ({ name, length, support, parent }).
function cladeOf(nodes, tips) {
  const wanted = new Set(tips);
  const inner = new Set(nodes.map((n) => n.parent));
  const found = nodes.map((n, i) => i).filter((i) => wanted.has(nodes[i].name) && !inner.has(i));
  const lost = wanted.size - found.length;
  if (found.length < 2) return { nodes, lost };  // nothing left to cut out: the whole tree
  const path = (i) => { const up = []; for (; i >= 0; i = nodes[i].parent) up.push(i); return up; };
  let common = path(found[0]);
  for (const i of found.slice(1)) { const up = new Set(path(i)); common = common.filter((a) => up.has(a)); }
  const top = common[0];  // the deepest node above them all
  const keep = new Map([[top, 0]]);
  const out = [{ ...nodes[top], parent: -1, length: 0 }];
  for (let i = top + 1; i < nodes.length; i++) {  // parents come first, so a kept parent is already known
    if (!keep.has(nodes[i].parent)) continue;
    keep.set(i, out.length);
    out.push({ ...nodes[i], parent: keep.get(nodes[i].parent) });
  }
  return { nodes: out, lost };
}

// ◀ <source>: back to what the figure shows
function renderFigureBack() {
  const data = figureShown?.data, back = $("figure-back");
  back.hidden = !data || data.missing || $("figureview").classList.contains("inactive");
  if (back.hidden) return;
  back.textContent = `◀ ${data.source_label}`;
  back.title = `Back to ${data.source_label}`;
}
// SVG and PDF are vector; PNG and TIFF the page drawn at the dpi chosen
async function exportFigure(format, { svg, png, dpi }) {
  const name = figureShown.item.name.replace(/[\\/:*?"<>|\s]+/g, "_");
  const filename = `${name}.${format === "tiff" ? "tif" : format}`;
  try {
    if (inPywebview()) {
      const path = await window.pywebview.api.save_file(filename, format);
      if (!path) return;
      await api("POST", "/api/figure/export", { format, svg, png, dpi, path });
      toast(`Saved ${path}`);
    } else {
      const { data } = await api("POST", "/api/figure/export", { format, svg, png, dpi });
      const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([bytes], { type: { svg: "image/svg+xml", pdf: "application/pdf", png: "image/png", tiff: "image/tiff" }[format] }));
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
      toast(`Exported ${filename}`);
    }
  } catch (e) {
    toast(e.message, true);
  }
}

// The alignment identity matrix lives in matrix.js.

// ── View windows ─────────────────────────────────────────────────────────
// New window shows the selection in a second window, view only, to see two
// things side by side. When it gets the focus it reloads what was saved since.
function openInNewWindow() {
  const chosen = selected.map(itemById).filter((it) => it && !it.missing);
  if (!chosen.length) return;
  const ids = chosen.map((it) => it.id);
  const title = chosen.length === 1 ? chosen[0].label : `${chosen.length} items`;
  if (inPywebview()) window.pywebview.api.open_view(ids, title);
  else window.open(`?view=${ids.join(",")}`, "_blank");  // browser (development): a new tab
}

async function startViewWindow() {
  document.body.classList.add("view-window");
  await loadList();
  selected = viewIds.filter((id) => itemById(id));
  document.title = "Cladia · " + (selected.length === 1 ? itemById(selected[0]).label : `${selected.length} items`);
  showSelection();
}

// ── Assembly graphs ──────────────────────────────────────────────────────
// A graph (GFA) in the graph view (graphview.js): each group of linked nodes as
// parallel lines (graph.layout on the server): its longest path the main line,
// the rest above and below, links as short hooks, a group that closes on itself
// as rings; nodes coloured by depth (Viridis: yellow low, purple high), a reference's matches
// as a strip on them. A node's track (a walk through its group, graph.tracks)
// opens on its own in the sequence viewer, for its bases: a circular one as a
// circle, its coverage, a selection to copy, BLAST or save; "◀ Graph" goes back.
// (Dropped on the way: a force layout, and the tracks as rows in the sequence
// viewer: base positions made small nodes slivers.)
const graphData = new Map();  // "layout"/"tracks" + key -> fetched (the last of each)
let graphShown = null;  // the graph view's: { item, data } (data: the layout)
let graphView = null;

// What is hidden: remembered per graph (in the page); a big graph starts without its short nodes
const graphFilters = {};
function graphFilter(it) {
  if (!graphFilters[it.id]) {
    // minLength: groups with fewer bases hidden (0: everything shown, as Bandage); walks: routes chosen ("A+,B-")
    graphFilters[it.id] = { colours: true, labels: "auto", links: true, minLength: 0, minDepth: 0, onlyHits: false, toScale: false, walks: [],
                             order: "track", compact: false };  // (order: the server's, which group comes first; compact: how they are packed)
  }
  return graphFilters[it.id];
}

async function fetchGraph(it, what) {
  const filter = graphFilter(it);
  const query = new URLSearchParams({ min_length: filter.minLength, min_depth: filter.minDepth, only_hits: filter.onlyHits,
                                      to_scale: filter.toScale, order: filter.order, walks: filter.walks.join(";") });
  // keyed by what the server uses, not by the display toggles: those must not refetch (a new layout resets the view)
  const key = `${what}:${it.id}:${it.mtime}:${it.hits || ""}:${query}`;
  if (graphData.has(key)) return graphData.get(key);
  const data = await api("GET", `/api/graph/${it.id}/${what}?${query}`);
  for (const k of [...graphData.keys()]) if (k.startsWith(`${what}:`)) graphData.delete(k);
  graphData.set(key, data);
  return data;
}

// What the bar acts on: the graph view's graph, or the track shown in the sequence viewer
function currentGraph() {
  if (graphShown && !$("graphview").classList.contains("inactive")) return { it: graphShown.item, layout: graphShown.data, track: null };
  return seqShown?.graph ? { it: seqShown.graph, tracks: seqShown.comparison, track: seqShown.track } : null;
}

async function showGraph(it) {
  showView("graphview");
  const data = await fetchGraph(it, "layout");
  const compact = graphFilter(it).compact;
  if (graphView) graphView.options.compact = compact;  // (before the layout is arranged)
  if (!graphView) graphView = new GraphView($("graphview"), { onNodeMenu: openGraphNodeMenu, onOpen: openGraphTrack, onChange: renderGraphSelection,
    onCopy: copyNodeNames, compact, onPath: () => { renderGraphSelection(); renderGraphPath(); } });
  if (graphShown?.data !== data) graphView.setData(data);
  graphShown = { item: it, data };
  applyGraphDisplay();
  renderGraphControls();
}

// The graph again (from a track shown on its own), the node it came from selected
async function backToGraph() {
  const it = seqShown.graph, from = seqShown.track;
  seqKey = null;
  await showGraph(it);
  const node = from && graphView.nodes.find((n) => n.id === from.node);
  if (node) graphView.reveal(node);
}

function renderGraphControls() {
  const shown = currentGraph(), it = shown?.it;
  // a track on its own uses the sequence viewer's bar; ◀ Graph leads back, first in the bar
  $("graph-controls").hidden = !it || !!shown.track;
  $("graph-back").hidden = !shown?.track;
  if (!it) return;
  const filter = graphFilter(it);
  $("graph-min-length").value = filter.minLength || "";
  $("graph-min-depth").value = filter.minDepth || "";
  $("graph-only-hits").checked = filter.onlyHits;
  $("graph-to-scale").checked = filter.toScale;
  $("graph-depth-colours").checked = filter.colours;
  $("graph-labels").value = filter.labels;
  $("graph-links").checked = filter.links;
  $("graph-order").value = filter.order;
  $("graph-compact").checked = filter.compact;
  const track = shown.track;
  renderGraphSelection();
  renderGraphPath();
  if (track) {
    const [lo, hi] = shown.tracks.depth_range || [];
    return setTitle(it.label, "", [track.genome.name], ["Track of an assembly graph", track.genome.data.description,
      lo ? `node colours: depth ${lo.toFixed(1)}× (yellow) to ${hi.toFixed(1)}× (purple)` : ""].filter(Boolean).join(" · "));
  }
  const data = shown.layout, reference = data.reference;
  $("graph-only-hits-label").hidden = !reference;
  $("graph-reference").textContent = reference ? `compared with ${reference.name}` : "";
  $("graph-reference-clear").hidden = !reference;
  const groups = data.groups.filter((g) => Object.keys(g.nodes).length > 1).length;  // (the depth colours: the legend in the view's corner)
  setTitle(it.label, "", [`${data.nodes.toLocaleString()} nodes`, `${groups} group${groups === 1 ? "" : "s"}`], ["Assembly graph",
    data.left ? `${data.left.toLocaleString()} nodes hidden` : ""].filter(Boolean).join(" · "));
}

// The bar's count of selected nodes, and what can be done with them
function renderGraphSelection() {
  updateViewerZoom();
  const n = graphView?.chosen.size || 0, path = !!graphView?.pathMode, steps = graphView?.path.length || 0;
  $("graph-selected").textContent = path ? (steps ? `path of ${steps} step${steps === 1 ? "" : "s"}` : "no path yet")
    : n ? `${n} node${n === 1 ? "" : "s"}` : "nothing";
  $("graph-export").textContent = path ? "Save path as sequence…" : "Save as sequence…";
  $("graph-export").disabled = path ? !steps : !n;
  scheduleToolStates();
}

// Nodes (pick nodes to export or BLAST) or Path (a route through the graph, a node as often as it is used)
function setGraphMode(mode) {
  if (!graphView) return;
  graphView.setPathMode(mode === "path");
  for (const b of document.querySelectorAll("[data-gmode]")) b.classList.toggle("active", b.dataset.gmode === mode);
  $("graph-gaps-label").hidden = mode !== "path";
  renderGraphSelection();
  renderGraphPath();
}

// The path as a strip of numbered steps under the bar
function renderGraphPath() {
  const box = $("graph-path"), view = graphView, shown = currentGraph();
  box.hidden = !(view?.pathMode && shown && !shown.track && !$("graphview").classList.contains("inactive"));
  if (box.hidden) return;
  box.replaceChildren();
  const label = (text) => { const s = document.createElement("span"); s.className = "muted"; s.textContent = text; return s; };
  if (!view.path.length) return box.append(label("Click the end of a node to start the path; the ends that can follow light up."));
  view.path.forEach((s, i) => {
    const chip = document.createElement("span");
    chip.className = `step${s.gap ? " gap" : ""}`;
    const num = document.createElement("b");
    num.textContent = i + 1;
    chip.append(num, document.createTextNode(`${s.dir === "right" ? "→" : "←"} ${s.node.id} · ${s.node.length.toLocaleString()} bp`));
    if (i === view.path.length - 1) {
      const x = document.createElement("button");
      x.textContent = "×";
      x.title = "Remove this step (Backspace)";
      x.onclick = () => view.pathUndo();
      chip.append(x);
    }
    box.append(chip);
  });
  const total = view.path.reduce((n, s) => n + s.node.length, 0);
  box.append(label(`${total.toLocaleString()} bp before overlaps`));
  if (view.pathCloses()) {
    const c = document.createElement("span");
    c.className = "closes";
    c.textContent = "closes: circular";
    box.append(c);
  }
  if (view.path.length === 1) {
    const b = document.createElement("button");
    b.className = "secondary";
    b.textContent = "Reverse first step";
    b.onclick = () => view.pathReverseFirst();
    box.append(b);
  }
  const clear = document.createElement("button");
  clear.className = "secondary";
  clear.textContent = "Clear";
  clear.onclick = () => view.pathClear();
  box.append(clear);
}

// Export the selected nodes: one sequence each (a list for several), or joined into one (a path)
function openGraphExport() {
  if (graphView?.pathMode) return openPathExport();
  const nodes = graphView?.selectedNodes() || [];
  if (!nodes.length) return;
  const form = $("graph-export-dialog").querySelector("form"), it = graphShown.item;
  const total = nodes.reduce((s, n) => s + n.length, 0);
  form.name.value = nodes.length === 1 ? `${it.name} node ${nodes[0].id}` : `${it.name} nodes`;
  form.how.value = "list";
  $("graph-export-how").hidden = nodes.length === 1;
  $("graph-export-what").textContent = `${nodes.length} node${nodes.length === 1 ? "" : "s"}, ${total.toLocaleString()} bp in all`;
  form.querySelector(".error").hidden = true;
  $("graph-export-dialog").showModal();
}

// The path as a new sequence: joined, overlaps once, circular when it closes
function openPathExport() {
  const steps = graphView.path;
  if (!steps.length) return;
  const form = $("graph-export-dialog").querySelector("form"), it = graphShown.item;
  form.name.value = `${it.name} path`;
  form.how.value = "join";
  $("graph-export-how").hidden = true;
  $("graph-export-what").textContent = `The path of ${steps.length} step${steps.length === 1 ? "" : "s"} as one sequence` +
    (graphView.pathCloses() ? ", closing into a circle" : "") + (steps.some((s) => s.gap) ? " (gaps in it will be refused)" : "");
  form.querySelector(".error").hidden = true;
  $("graph-export-dialog").showModal();
}

async function runGraphExport(event) {
  if (event.submitter?.value !== "run") return;
  event.preventDefault();
  const form = event.target, error = form.querySelector(".error");
  const path = graphView.pathMode, nodes = graphView.selectedNodes(), it = graphShown.item;
  const name = form.name.value.trim();
  if (!name) return;
  try {
    const made = await api("POST", `/api/graph/${it.id}/nodes`, {
      steps: path ? graphView.pathOrients() : nodes.map((n) => n.id + n.o), name,
      join: path || (nodes.length > 1 && form.how.value === "join") });
    $("graph-export-dialog").close();
    await loadList();
    toast(made.count > 1 ? `Saved ${made.count} nodes as the list “${name}” (Ctrl+Z in the list undoes it)`
                         : `Saved “${name}” (Ctrl+Z in the list undoes it)`);
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  }
}

// ── Tracks: walks through a group, for the sequence viewer ──
// The track a node is in (a main track, else a branch of one): {row, branch}
function trackOf(tracks, id) {
  const has = (walk) => (walk || "").split(",").some((s) => s.slice(0, -1) === id);
  const row = tracks.genomes.findIndex((g) => !g.reference && !g.lane && has(g.walk));
  if (row >= 0) return { row, branch: null };
  for (const [r, g] of tracks.genomes.entries()) {
    const branch = g.lane?.find((b) => has(b.walk));
    if (branch) return { row: r, branch };
  }
  return null;
}

// A track, or one branch of a lane, as a sequence of its own: {name, walk, data: {sequence, features, circular, description}}
function trackGenome(data, row, branch = null) {
  const g = data.genomes[row];
  if (!branch) return g;
  const length = branch.col1 - branch.col0 + 1, first = branch.base0;
  return { name: branch.name, walk: branch.walk, data: { circular: false, description: branch.description,
    sequence: g.data.sequence.replace(/-/g, "").slice(first, first + length),
    features: g.data.features.filter((f) => f.start >= first && f.start < first + length)
      .map((f) => ({ ...f, start: f.start - first, end: f.end - first })) } };
}

// A node feature's node name, from its name ("IR+ 245.0× (×2)" -> "IR")
const nodeId = (f) => f.name.match(/^(.*?)[+-](?: |$)/)?.[1] ?? null;

// A node's track (or branch) on its own in the sequence viewer: a circular one as a
// circle (the map switch makes it a line), its coverage, the node selected
async function openGraphTrack(node) {
  const it = graphShown.item, data = await fetchGraph(it, "tracks");
  const where = trackOf(data, node.id);
  if (!where) return toast(`Node ${node.id} isn't in a track (hidden by the filter?)`, true);
  const { row, branch } = where, genome = trackGenome(data, row, branch);
  if (!startViewer("single", `graphtrack:${it.id}:${row}:${branch?.name || ""}:${data.genomes[row].walk || ""}:${node.id}`, [it])) return;
  seqShown.graph = it;
  seqShown.comparison = data;
  seqShown.track = { row, branch, genome, node: node.id };
  seqShown.map = genome.data.circular ? "circular" : "linear";
  const view = getSeqView();
  view.circularMap = false;
  view.setRows([{ name: genome.name, sequence: genome.data.sequence, description: genome.data.description }], "single");
  view.circularSequence = !!genome.data.circular;
  view.setFeatures({ [genome.name]: { features: viewerFeatures(genome.data), code: 1 } });
  // its coverage: each position the depth of the node there (the deeper where two overlap), as a mapping's graph
  const depth = new Array(genome.data.sequence.length).fill(0);
  for (const f of genome.data.features) {
    const d = Number(f.notes?.depth?.[0]) || 0;
    for (let p = f.start; p <= f.end && p < depth.length; p++) depth[p] = Math.max(depth[p], d);
  }
  view.setMapping({ depth, consensus: genome.data.sequence, insertions: {}, minDepth: 0, coverageOnly: true });
  view.setCircular(seqShown.map === "circular");
  renderSeqControls("single");
  renderGraphControls();
  const f = view.rows[0].features.find((x) => nodeId(x) === node.id) || null;
  if (f) {
    view.select(f.c0, f.c1, 0, 0);
    if (!view.circularMap) view.centerOn(Math.round((f.c0 + f.c1) / 2));
  }
}

// Node names (contig names) to the clipboard, a line each: Ctrl+C in the graph, and its menu
async function copyNodeNames(names) {
  try {
    await navigator.clipboard.writeText(names.join("\n"));
    toast(names.length === 1 ? `Copied ${names[0]}` : `Copied ${names.length} names`);
  } catch (e) {
    toast("Couldn't copy", true);
  }
}

// The graph view's node menu (its track found in the tracks)
async function openGraphNodeMenu(e, node) {
  const it = graphShown.item, data = await fetchGraph(it, "tracks");
  const where = trackOf(data, node.id);
  const entries = [];
  const several = graphView.chosen.size > 1 ? graphView.chosen.size : 0;
  const names = [...graphView.chosen].map((n) => n.id);
  entries.push([several ? `Copy the ${several} selected names` : `Copy the name ${node.id}`, () => copyNodeNames(names.length ? names : [node.id])], "-");
  if (several) {
    entries.push([`Export the ${several} selected nodes as a sequence…`, openGraphExport],
                 [`BLAST the ${several} selected nodes…`, () => openTool("blast")], "-");
  } else {
    entries.push([`BLAST node ${node.id}…`, () => openTool("blast")]);
  }
  if (where) {
    const g = data.genomes[where.row], what = where.branch ? where.branch.name : g.name;
    const circle = !where.branch && g.data.circular ? " (as a circle)" : "";
    entries.push([`Show ${what} on its own${circle}`, () => openGraphTrack(node)], "-",
                 [`Save node ${node.id} as a sequence…`, () => saveGraphNode(data, where, node.id)],
                 [`Save ${what} as a sequence…`, () => saveGraphTrack(trackGenome(data, where.row, where.branch))]);
    const routes = [];
    const index = g.data.features.findIndex((f) => nodeId(f) === node.id);
    if (!where.branch && g.walk?.includes(",") && index >= 0)
      routes.push([`Turn node ${node.id} round in ${g.name}`, () => changeRoute(data, where.row, { turn: index })]);
    if (where.branch) routes.push([`Use ${where.branch.name} in ${data.genomes[g.branch_of].name}`,
                                   () => changeRoute(data, g.branch_of, { branch: where.branch.walk })]);
    const main = where.branch ? g.branch_of : where.row;
    if (data.genomes[main].chosen) routes.push([`Back to Cladia's route for ${data.genomes[main].name}`, () => resetRoute(data, main)]);
    if (routes.length) entries.push("-", { heading: "Route" }, ...routes);
  } else {
    entries.push([`Node ${node.id} isn't in a track`, null, true]);
  }
  showMenu(e, entries);
}

// The sequence viewer's menu, for a track shown on its own
function graphTrackMenuEntries(e) {
  const s = seqView.selection, track = seqShown.track, data = seqShown.comparison, g = data.genomes[track.row];
  const p = seqView.point(e), node = seqView.featureUnder(p.x, p.y);
  const id = node && nodeId(node);
  const entries = ["-", ["Save the selection as a sequence…", () => saveGraphTrack(track.genome, s), !s],
                   [`Save ${track.genome.name} as a sequence…`, () => saveGraphTrack(track.genome)],
                   ["◀ Back to the graph", backToGraph]];
  const routes = [];
  if (!track.branch && id && g.walk?.includes(",")) routes.push([`Turn node ${id} round in ${g.name}`, () => changeRoute(data, track.row, { turn: node.index })]);
  if (!track.branch && g.chosen) routes.push([`Back to Cladia's route for ${g.name}`, () => resetRoute(data, track.row)]);
  if (routes.length) entries.push("-", { heading: "Route" }, ...routes);
  return entries;
}

// A main track's route changed: a node turned round, or a branch put in (the
// server checks the links); remembered for this graph; a track on its own shown again
async function changeRoute(data, r, change) {
  const shown = currentGraph(), it = shown.it, genome = data.genomes[r];
  let walk;
  try {
    ({ walk } = await api("POST", `/api/graph/${it.id}/route`, { walk: genome.walk, ...change }));
  } catch (e) {
    return toast(e.message, true);
  }
  setRoute(it, genome.walk, walk);
  await afterRoute(shown);
  toast(`${genome.name}'s route changed: open it on its own to see it`);
}

// old: the route replaced (null: Cladia's own again); routes sharing nodes with it go
function setRoute(it, old, walk) {
  const filter = graphFilter(it);
  const nodes = (w) => new Set(w.split(",").map((s) => s.slice(0, -1)));
  const mine = nodes(old);
  filter.walks = filter.walks.filter((w) => ![...nodes(w)].some((n) => mine.has(n)));
  if (walk) filter.walks.push(walk);
}

async function resetRoute(data, r) {
  const shown = currentGraph();
  setRoute(shown.it, data.genomes[r].walk, null);
  await afterRoute(shown);
}

async function afterRoute(shown) {
  if (!shown.track) return;
  seqKey = null;
  graphShown = graphShown || { item: shown.it, data: await fetchGraph(shown.it, "layout") };
  await openGraphTrack({ id: shown.track.node });
}

function filterGraph() {
  const it = currentGraph()?.it;  // (the graph view keeps its graph while the new layout loads)
  if (!it) return;
  Object.assign(graphFilter(it), { minLength: Number($("graph-min-length").value) || 0, toScale: $("graph-to-scale").checked,
                                   minDepth: Number($("graph-min-depth").value) || 0, onlyHits: $("graph-only-hits").checked,
                                   order: $("graph-order").value });
  showGraph(it).then(() => findGraphNode(0));
}

// Find: nodes whose names contain the text; Enter (Shift+Enter) goes through them
function findGraphNode(step) {
  if (!graphView) return;
  const text = $("graph-find").value.trim();
  const found = graphView.find(text, step);
  $("graph-found").textContent = !text ? "" : found.count ? `${found.at + 1} of ${found.count}` : "none shown";
}

// Compare with a reference: BLAST of it against the nodes (a job); the hits stay beside the graph
function openGraphCompare() {
  const form = $("graph-blast-dialog").querySelector("form");
  form._pickers = {};
  const last = currentGraph()?.layout?.reference?.item;
  const sequences = items.filter((it) => it.kind === "sequence" && !it.missing && !it.protein);
  mountSingle(form, "graphcompare", "reference", form.query, {
    initial: (last && itemById(last) ? last : selectionFor(TOOL_INPUTS.graphcompare.reference)[0]?.id) || "" });
  $("graph-blast-dialog").showModal();
}

async function runGraphCompare(event) {
  const form = event.target;
  if (event.submitter?.value !== "run") return;
  try {
    await api("POST", `/api/graph/${currentGraph().it.id}/blast`, { queries: [form.query.value], task: form.task.value });
    pollJobs();
    toast("Comparing: the nodes it matches are marked when the job is done");
  } catch (e) {
    toast(e.message, true);
  }
}

// A finished comparison: the graph shown again with the reference's marks
async function clearGraphComparison() {
  const it = currentGraph()?.it;
  if (!it) return;
  try {
    await api("POST", `/api/graph/${it.id}/clear-comparison`);
    await reloadGraphHits(it.id);
    toast("Comparison removed");
  } catch (e) { toast(e.message, true); }
}

async function reloadGraphHits(id) {
  await loadList();
  if (currentGraph()?.it.id !== id) return;
  seqKey = null;
  graphShown = null;
  showGraph(itemById(id));
}

// A track or branch (or a selection in it, sel: {c0, c1, wraps} in its own
// positions) as a new sequence: its nodes become misc_features saying where they
// came from; a whole circular track is saved circular
async function saveGraphTrack(genome, sel = null) {
  const it = currentGraph().it, length = genome.data.sequence.length;
  // a selection across the origin of a circular track: the track read from its start
  const origin = sel?.wraps ? sel.c0 : 0;
  const [a, b] = !sel ? [0, length - 1] : sel.wraps ? [0, length - sel.c0 + sel.c1] : [Math.max(0, sel.c0), Math.min(length - 1, sel.c1)];
  if (a > b) return toast("The selection is outside this track", true);
  const whole = a === 0 && b === length - 1;
  const from = (x) => ((x + origin) % length) + 1;
  const name = await askText({ title: "Save as a new sequence", label: "Name", ok: "Save",
    value: `${it.name} ${genome.name}${whole ? "" : ` ${from(a)}-${from(b)}`}` });
  if (!name) return;
  const turned = (f) => ({ ...f, start: (f.start - origin + length) % length, end: (f.start - origin + length) % length + f.end - f.start });
  const whole1 = (f) => f.type === "node" && f.start <= a && f.end >= b;  // one node over all of it: it only labels the sequence
  const features = (genome.data.features || []).map(turned).filter((f) => f.end >= a && f.start <= b && !whole1(f)).map((f) => ({
    type: f.type === "node" ? "misc_feature" : f.type, name: f.type === "node" ? `node ${f.notes.node[0]}` : f.name,
    strand: f.strand, start: Math.max(a, f.start) - a, end: Math.min(b, f.end) - a,
    notes: f.type === "node" ? { note: [`assembly graph node ${f.name}`] } : f.notes,
  }));
  const bases = genome.data.sequence.slice(origin) + genome.data.sequence.slice(0, origin);
  const sequence = { name, data: { sequence: bases.slice(a, b + 1), features, geneticCode: 1,
                                    circular: whole && genome.data.circular, description: `from ${it.name}, ${genome.data.description}` } };
  try {
    await api("POST", "/api/library/new-sequences", { sequences: [sequence], folder: it.folder || null });
    await loadList();
    toast(`Saved “${name}”: ${(b - a + 1).toLocaleString()} bp${sequence.data.circular ? ", circular" : ""} (Ctrl+Z in the list undoes it)`);
  } catch (e) {
    toast(e.message, true);
  }
}

// One node's bases (along its own + strand) as a new sequence
async function saveGraphNode(data, where, id) {
  const it = graphShown.item;
  const name = await askText({ title: "Save the node as a new sequence", label: "Name", ok: "Save", value: `${it.name} node ${id}` });
  if (!name) return;
  try {
    await api("POST", `/api/graph/${it.id}/nodes`, { steps: [id + "+"], name });
    await loadList();
    toast(`Saved “${name}” (Ctrl+Z in the list undoes it)`);
  } catch (e) { toast(e.message, true); }
}

const COMPLEMENT = { A: "T", C: "G", G: "C", T: "A", a: "t", c: "g", g: "c", t: "a" };
const reverseComplementText = (s) => s.split("").reverse().map((c) => COMPLEMENT[c] || "N").join("");

// ── Tree view ────────────────────────────────────────────────────────────
// Cladia's own tree viewer (treeview.js). The View choices are remembered;
// right-click a node to reroot (saved, not in view windows), collapse or copy
// its tip names; collapses and ladderizing only change the view.
const treeLayout = { align: false, support: true, lengths: false, ladderize: false, ...loadJSON("cladia.tree") };
let treeLayoutLoaded = null, treeLayoutSave = Promise.resolve();
const treeLayoutChanged = new Set();
function saveTreeLayout(change) {
  treeLayoutSave = treeLayoutSave.then(() => api("PUT", "/api/settings", {tree_layout: change}))
    .catch(e => toast(`Could not save tree view choices: ${e.message}`, true));
}
function loadTreeLayout() {
  return treeLayoutLoaded ||= api("GET", "/api/settings").then(saved => {
    if (saved.tree_layout) {
      for (const [key, value] of Object.entries(saved.tree_layout)) {
        if (!treeLayoutChanged.has(key)) treeLayout[key] = value;
      }
    } else {
      saveTreeLayout({...treeLayout}); // migrate this browser's existing choices once
    }
    renderTreeControls();
  }).catch(e => {
    treeLayoutLoaded = null;
    toast(`Could not load tree view choices: ${e.message}`, true);
  });
}
let treeKey = null;    // what the tree view shows (item id and file time)
let treeShown = null;  // { item, tips, key: loaded file version }
let treeView = null;
let treeUnlocked = null;  // the tree id explicitly unlocked in this window
const treeEditable = () => !$("treeview").classList.contains("inactive") &&
  !!treeShown && treeUnlocked === treeShown.item.id && !viewIds;

async function setTreeEditMode(on) {
  const id = treeShown?.item.id;
  if (!id || viewIds) return;
  if (on && !(await takeLocks())) return;
  if (treeShown?.item.id !== id || $("treeview").classList.contains("inactive")) return updateLocks();
  treeUnlocked = on ? id : null;
  renderTreeEditButtons();
  treeView?.canvas.focus();
}

function renderTreeEditButtons() {
  const on = treeEditable(), lock = $("tree-lock");
  lock.innerHTML = on ? ICONS.unlock : ICONS.lock;
  lock.classList.toggle("active", on);
  lock.title = on ? "Editable: changes are saved at once; click to make it read only" : "Read only: click to edit";
  lock.setAttribute("aria-label", on ? "Disable tree editing" : "Enable tree editing");
  lock.setAttribute("aria-pressed", String(on));
  lock.disabled = !!viewIds;
  updateLocks();
  if (treeShown) updateTreeUndo(treeShown.item);
}
const treeViewports = new Map();  // per item, valid only for the loaded file version

function rememberTreeViewport() {
  if (!treeView?.nodes || !treeShown || $("treeview").classList.contains("inactive")) return;
  treeViewports.set(treeShown.item.id, { key: treeShown.key, state: treeView.viewportState() });
}

function getTreeView() {
  if (!treeView) treeView = new TreeView($("treeview"), { onNodeMenu: openTreeMenu, onChange: updateViewerZoom, onCopy: copyNodeNames });
  return treeView;
}

async function showTree(it) {
  if (treeUnlocked !== it.id) treeUnlocked = null;
  rememberTreeViewport();
  showView("treeview");
  renderTreeControls();
  treeView?.resize();
  const key = `${it.id}:${it.mtime}`;
  if (treeKey === key) return renderFigureSwitch();
  treeKey = key;
  const [{ nodes }] = await Promise.all([api("GET", `/api/tree/${it.id}`), loadTreeLayout()]);
  if (treeKey !== key) return;
  const view = getTreeView();
  view.setShow(treeLayout);
  view.setTree(nodes);
  const saved = treeViewports.get(it.id);
  if (saved?.key === key) view.restoreViewport(saved.state);
  else view.fitWhole();
  treeShown = { item: it, tips: view.tipCount, key };
  renderTreeEditButtons();
  findTips(false);  // Highlight matches without overriding the restored viewport.
  renderFigureSwitch();
  updateTreeUndo(it);
}

// Rerooting saves a new tree state; its persistent history supplies undo and redo.
async function rerootTree(it, outgroup) {
  if (!treeEditable() || treeShown.item.id !== it.id) return lockedHint();
  try {
    await api("POST", `/api/tree/${it.id}/reroot`, { outgroup });
  } catch (e) {
    return toast(e.message, true);
  }
  await redrawTree(it.id);
  toast("Rerooted and saved (Ctrl+Z undoes it)");
}

// Removing tips saves a new tree state too (Ctrl+Z / ↶ goes back)
async function pruneTree(it, tips) {
  if (!treeEditable() || treeShown.item.id !== it.id) return lockedHint();
  if (!tips.length) return toast("Select the tips to remove first", true);
  try {
    await api("POST", `/api/tree/${it.id}/prune`, { tips });
  } catch (e) {
    return toast(e.message, true);
  }
  await redrawTree(it.id);
  toast(`Removed ${tips.length} tip${tips.length === 1 ? "" : "s"} and saved (Ctrl+Z undoes it)`);
}

// The tips a removal acts on: the selection, else the clade or tip right-clicked
function tipsToRemove(node) {
  const chosen = treeView.selectedTipNames();
  return chosen.length ? chosen : node ? treeView.tipNames(node.index) : [];
}

async function treeUndo(redo) {
  if (!treeEditable()) return lockedHint();
  return savedUndo(redo);
}

// ↶ ↷ of a tree: usable only when its saved versions go that way (each reroot saves one)
async function updateTreeUndo(it) {
  $("tree-undo").disabled = $("tree-redo").disabled = true;
  try {
    const h = await api("GET", `/api/library/${it.id}/history`);
    if (treeShown?.item.id !== it.id) return;
    $("tree-undo").disabled = !treeEditable() || !(h.current > 0);
    $("tree-redo").disabled = !treeEditable() || !(h.current < h.versions.length - 1);
  } catch (e) {}  // no history yet: both stay off
}

async function redrawTree(id) {
  await loadList();
  treeKey = null;
  await showTree(itemById(id));
}

function openTreeMenu(e, node) {
  const view = treeView, it = treeShown.item;
  const names = view.tipNames(node.index);
  const copyNames = view.selectedTipNames();
  const what = node.tip ? `“${node.name}”` : `this clade (${node.tips} tips)`;
  const removing = tipsToRemove(node);
  const can = treeEditable();
  showMenu(e, [
    { heading: can ? "Edit (saved at once)" : "Edit (unlock 🔒 first)" },
    [node.root ? "Reroot (this is the root)" : `Reroot on ${what}`, () => rerootTree(it, names), !can || node.root],
    [removing.length === 1 ? `Remove the tip “${removing[0]}”` : `Remove the ${removing.length} selected tips`,
      () => pruneTree(it, removing), !can || (node.root && !view.selectedTipNames().length)],
    node.tip ? null : [node.collapsed ? "Expand this clade" : "Collapse this clade", () => view.toggleCollapse(node.index), node.root],
    [copyNames.length === 1 ? "Copy the name" : `Copy the ${copyNames.length} selected tip names`, () => copyNodeNames(copyNames)],
  ]);
}

function changeTreeLayout(change) {
  Object.keys(change).forEach(key => treeLayoutChanged.add(key));
  Object.assign(treeLayout, change);
  saveTreeLayout({...change});
  try { localStorage.setItem("cladia.tree", JSON.stringify(treeLayout)); } catch (e) {}
  renderTreeControls();
  treeView?.setShow(treeLayout);
}

function renderTreeControls() {
  for (const key of ["align", "support", "lengths", "ladderize"]) $(`tree-${key}`).checked = !!treeLayout[key];
}

// Find: matching tip names are highlighted, Enter / ◀ ▶ go through them
function findTips(jump = true) {
  if (!treeView || !treeShown) return;
  const query = $("tree-search").value;
  const found = treeView.find(query, jump);
  $("tree-found").textContent = query.trim() ? `${found} found` : "";
  setTitle(treeShown.item.label, "", [`${treeShown.tips.toLocaleString()} tips`], "Tree");
  $("tree-prev").disabled = $("tree-next").disabled = !(query.trim() && found);  // ◀ ▶ only with matches
}

function findTipsStep(step) {
  treeView?.findStep(step);
}

// ── Export ───────────────────────────────────────────────────────────────
// The only way files leave Cladia. Several items go into one file.
const exportable = () => selected.map(itemById).filter((it) => it && !it.missing);

function openExport() {
  const chosen = exportable();
  if (!chosen.length) return;
  if (chosen.some(it => it.kind === "item_list")) return toast("Select the items inside the list to export them", true);
  const form = $("export-dialog").querySelector("form");
  const hasAlignment = chosen.some((it) => it.kind === "alignment");
  const oneAlignment = chosen.length === 1 && chosen[0].kind === "alignment";
  const hasMapping = chosen.some((it) => it.kind === "mapping");
  const onlyTrees = chosen.every((it) => it.kind === "tree");
  const oneGraph = chosen.length === 1 && chosen[0].kind === "graph";  // as GFA, or its nodes as FASTA
  const oneVcf = chosen.length === 1 && chosen[0].kind === "variants";  // as the VCF it is
  const bamOk = chosen.length === 1 && !!chosen[0].bam;  // one mapping that kept its BAM
  for (const option of form.format.options) {
    // gaps don't belong in GenBank; alignments have no CDSs; trees are Newick only;
    // a mapping exports its consensus, or its BAM
    const v = option.value;
    option.disabled = oneVcf ? v !== "vcf" : v === "vcf" || (oneGraph ? v !== "gfa" && v !== "fasta"
      : onlyTrees ? v !== "newick" && v !== "nexus"
      : v === "newick" || v === "gfa" || (v === "bam" && !bamOk) || (hasMapping && v === "protein")
        || ((v === "nexus" || v === "phylip") && !oneAlignment)
        || (hasAlignment && !["fasta", "nexus", "phylip"].includes(v)));
  }
  if (onlyTrees) form.format.value = "newick";
  else if (oneGraph) form.format.value = "gfa";
  else if (oneVcf) form.format.value = "vcf";
  else if (form.format.selectedOptions[0]?.disabled) form.format.value = "fasta";
  $("export-what").textContent = chosen.length === 1 ? chosen[0].label : `${chosen.length} items, in one file`;
  const error = form.querySelector(".error");
  error.hidden = !hasUnsavedChanges();
  error.textContent = "The open sequence has unsaved changes; Export uses the saved version.";
  $("export-dialog").showModal();
}

async function runExport(form, event) {
  if (event.submitter?.value !== "run") return;  // Cancel just closes
  event.preventDefault();
  const chosen = exportable();
  const fmt = form.format.value;
  const base = chosen.length === 1 ? chosen[0].name : "sequences";
  const ending = { genbank: ".gb", fasta: ".fasta", gff3: ".gff3", gff3fasta: ".gff3", nexus: ".nex", phylip: ".phy", protein: "_proteins.fasta", newick: ".nwk", bam: ".bam", gfa: ".gfa", vcf: ".vcf" }[fmt];
  const filename = base.replace(/[\\/:*?"<>|\s]+/g, "_") + ending;
  const ids = chosen.map((it) => it.id);
  const codes = codeChoices;  // proteins use the genetic code picked in the Code menu, if any
  const error = form.querySelector(".error");
  try {
    if (inPywebview()) {
      const path = await window.pywebview.api.save_file(filename, fmt);
      if (!path) return;  // Save dialog cancelled
      await api("POST", "/api/export", { ids, format: fmt, path, codes });
      toast(`Exported to ${path}`);
    } else if (fmt === "bam") {  // browser: the server sends the file
      window.location.href = `/api/mapping/${ids[0]}/bam`;
    } else {  // browser: the file is downloaded
      const { text } = await api("POST", "/api/export", { ids, format: fmt, codes });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
      toast(`Exported ${filename}`);
    }
    form.closest("dialog").close();
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  }
}

// ── Jobs ─────────────────────────────────────────────────────────────────
// The sidebar shows every running and queued job of this session (a scroll bar
// past JOBS_SHOWN of them), then the newest finished ones up to JOBS_SHOWN rows in
// all, always at least the last one (its results are what you want next); every
// job's log is saved when it ends and Archive… lists them all, earlier sessions
// too. Logs are fetched when opened.
const JOBS_SHOWN = 3;

async function pollJobs() {
  const jobs = await api("GET", "/api/jobs");
  // a job that finished since the last look says so (a toast), so it isn't missed
  const before = pollJobs.statuses || {};
  for (const job of jobs) {
    if (["tool-install", "tool-uninstall"].includes(job.kind) &&
        ["done", "failed", "cancelled"].includes(job.status) && before[job.id] !== job.status) {
      registeredToolsPromise = null;
      toolEnginesPromise = null;
      toolStoragePromise = null;
      renderRegisteredToolbar();
      if ($("settings-dialog").open && settingsTab.shown === "tools") renderToolEngines();
    }
    if (before[job.id] !== "queued" && before[job.id] !== "running") continue;
    if (job.kind === "blastdb" && $("settings-dialog").open && settingsTab.shown === "blastdbs") renderBlastDbs();
    if (job.status === "done") toast(job.warning || `Done: ${job.title}`, !!job.warning);
    if (job.status === "done" && job.kind === "graphblast") reloadGraphHits(job.outputs[0]?.id);
    else if (job.status === "failed") toast(`Failed: ${job.title} (see its log)`, true);
  }
  // the first tool run on a PC sets up Cladia's Linux (execution.ensure_image's detail): say so, once
  if (!pollJobs.linuxNoted && jobs.some((j) => j.status === "running" && /^preparing Cladia's Linux/.test(j.progress?.detail || ""))) {
    pollJobs.linuxNoted = true;
    toast("First tool run on this PC: Cladia is setting up its Linux tools. It downloads several hundred MB " +
      "and takes a few minutes, only this once; the job's bar shows how far it is.");
  }
  pollJobs.statuses = Object.fromEntries(jobs.map((j) => [j.id, j.status]));
  pollJobs.last = jobs;
  renderJobs(jobs);
  const active = jobs.some((j) => j.status === "queued" || j.status === "running");
  clearTimeout(pollJobs.timer);
  if (active) pollJobs.timer = setTimeout(pollJobs, 1500);

  // Pick up result files when jobs finish; they are marked new
  const finished = jobs.filter((j) => j.status === "done");
  for (const job of finished.filter((j) => !fresh.jobs.includes(j.id))) {
    fresh.jobs.push(job.id);
    fresh.items.push(...job.outputs.filter((o) => !o.updated).map((o) => o.id).filter(Boolean));
  }
  fresh.jobs = fresh.jobs.slice(-200);
  saveFresh();
  if (finished.map((j) => j.id).join() !== pollJobs.finished) {
    pollJobs.finished = finished.map((j) => j.id).join();
    refreshShown();
    windows?.postMessage("changed");
  }
}

// Results of tools are marked "new" in the list until opened (also after a
// restart); `jobs` remembers which jobs' results were already marked
const fresh = { items: [], jobs: [], ...loadJSON("cladia.fresh") };
const saveFresh = () => { try { localStorage.setItem("cladia.fresh", JSON.stringify(fresh)); } catch (e) {} };

function markSeen(chosen) {
  const ids = new Set(chosen.flatMap((it) => [it.id, it.parent]));
  if (!fresh.items.some((id) => ids.has(id))) return;
  fresh.items = fresh.items.filter((id) => !ids.has(id));
  saveFresh();
  renderList();
}

// "14:02" today, else "21 Oct 14:02" (and the year if it isn't this one)
function when(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000), now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  const day = d.toLocaleDateString([], { day: "numeric", month: "short", ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
  return `${day} ${time}`;
}

function duration(job) {
  if (!job.started || !job.finished) return "";
  const s = Math.round(job.finished - job.started);
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

// A job's result: a link that opens it, or its name struck through if it's gone
function outputLink(out) {
  const a = document.createElement("a");
  a.textContent = out.label;
  a.title = out.label;  // (cut short in the Jobs panel)
  if (!itemById(out.id)) {
    a.className = "gone";
    a.title = `${out.label}: no longer in the list`;
    return a;
  }
  a.onclick = () => {
    const previous = selected;
    selected = [out.id];
    anchorId = out.id;
    if (!showSelection()) selected = previous;
    renderList();
    $("archive-dialog").close();
  };
  return a;
}

// A running job's bar (jobs.py "Progress"): a block per step, done ones filled,
// the current one filled by its fraction, or shimmering when it has none; one
// step = one bar. The list is redrawn every poll: the shimmer's phase comes
// from the clock, so it moves on smoothly.
const SHIMMER = 1600;  // ms per sweep, as in app.css

function progressBar(p) {
  p = p || { steps: [""], at: 0, fraction: null, detail: "" };
  const box = document.createElement("div");
  box.className = "progress";
  const bar = document.createElement("div");
  bar.className = "bar";
  p.steps.forEach((name, i) => {
    const block = document.createElement("span");
    block.title = name;
    if (i < p.at) block.className = "done";
    else if (i === p.at && p.fraction != null) {
      block.className = "part";
      block.append(Object.assign(document.createElement("i"), { style: `width: ${Math.round(100 * p.fraction)}%` }));
    } else if (i === p.at) {
      block.className = "wait";
      block.style.setProperty("--delay", `-${Date.now() % SHIMMER}ms`);
    }
    bar.append(block);
  });
  const label = document.createElement("div");
  label.className = "progress-label";
  const name = p.steps[p.at] || "";
  label.textContent = (p.steps.length > 1 ? `${p.at + 1}/${p.steps.length} ` : "") + name +
    (p.detail ? `${name ? " · " : ""}${p.detail}` : "");
  box.append(bar, label);
  return box;
}

// Finished jobs taken off the list with Clear (this session; their logs stay in the archive)
const clearedJobs = new Set();
let lastJobs = [];

function clearJobs() {
  for (const job of lastJobs) if (job.status !== "running" && job.status !== "queued") clearedJobs.add(job.id);
  renderJobs(lastJobs);
}

function renderJobs(jobs) {
  lastJobs = jobs;
  const busy = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  $("jobs-running").textContent = busy ? `${busy} job${busy === 1 ? "" : "s"} running (see Jobs, bottom left)` : "";
  // running first, then the queued ones, each oldest first: the order they started or will start
  // (the server sends newest first)
  const active = [...jobs.filter((j) => j.status === "running").reverse(), ...jobs.filter((j) => j.status === "queued").reverse()];
  const finished = jobs.filter((j) => !active.includes(j) && !clearedJobs.has(j.id));
  $("clear-jobs").hidden = !finished.length;
  const shown = finished.slice(0, Math.max(1, JOBS_SHOWN - active.length));
  const box = $("jobs-active"), list = $("jobs");
  const scrolled = box.scrollTop;  // redrawn every poll: keep where it was scrolled to
  box.innerHTML = "";
  for (const job of active) box.append(jobRow(job));
  // three rows high, and a little of the fourth so it shows there are more
  const rows = box.children;
  box.style.maxHeight = rows.length > JOBS_SHOWN ? `${rows[JOBS_SHOWN].offsetTop - rows[0].offsetTop + 14}px` : "";
  box.classList.toggle("scrolls", rows.length > JOBS_SHOWN);
  box.scrollTop = scrolled;
  list.innerHTML = jobs.length ? "" : '<li class="empty">None in this session</li>';
  for (const job of shown) list.append(jobRow(job));
  if (finished.length > shown.length) {
    const more = document.createElement("li");
    more.className = "more";
    more.innerHTML = `<a>${finished.length - shown.length} more in the archive…</a>`;
    more.querySelector("a").onclick = openArchive;
    list.append(more);
  }
}

function jobRow(job) {
    const li = document.createElement("li");
    li.className = job.status;
    li.innerHTML = '<div class="title"></div><div class="status"></div><div class="links"></div>';
    li.querySelector(".title").textContent = job.title;
    li.querySelector(".status").textContent = job.status;
    const time = document.createElement("span");
    time.className = "when";
    time.textContent = when(job.finished || job.started || job.created);
    li.querySelector(".status").append(time);
    if (job.status === "running") li.insertBefore(progressBar(job.progress), li.querySelector(".links"));
    const links = li.querySelector(".links");
    for (const out of job.outputs || []) links.append(outputLink(out));
    if (job.status === "done" && job.result?.download) {
      const a = document.createElement("a");
      a.textContent = "Download ZIP";
      a.href = `/api/project/download/${job.result.download}`;
      a.download = "Cladia-project.zip";
      a.target = "_blank";
      links.append(a);
    }
    if (job.status === "queued" || job.status === "running") {
      const a = document.createElement("a");
      a.textContent = "cancel";
      a.onclick = async () => {
        if (job.status === "running" && !confirm(`Stop ${job.title}? What it has done so far is lost.`)) return;
        await api("POST", `/api/jobs/${job.id}/cancel`);
        pollJobs();
      };
      links.append(a);
    }
    if (job.has_log) {
      const a = document.createElement("a");
      a.textContent = "log";
      a.onclick = () => openLog(job.id);
      links.append(a);
    }
    return li;
}

// The log dialog; a running job's log is read again every 2 s while it's open
const logText = $("log-text");
logText.addEventListener("pointerdown", () => { openLog.selecting = true; });
window.addEventListener("pointerup", () => { openLog.selecting = false; });
window.addEventListener("pointercancel", () => { openLog.selecting = false; });
$("log-dialog").addEventListener("close", () => {
  clearTimeout(openLog.timer);
  openLog.requested = null;
  openLog.selecting = false;
});

async function openLog(id) {
  clearTimeout(openLog.timer);
  openLog.requested = id;
  const job = await api("GET", `/api/logs/${id}`);
  if (openLog.requested !== id) return;
  $("log-title").textContent = job.title;
  const facts = [job.status, `started ${when(job.started || job.created)}`];
  if (job.finished) facts.push(`ended ${when(job.finished)}`, duration(job));
  $("log-facts").textContent = facts.filter(Boolean).join(" · ");
  const pre = logText;
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  const next = (job.error ? `ERROR: ${job.error}\n\n` : "") + (job.log || "(no output)");
  const dialog = $("log-dialog"), sameLog = dialog.open && openLog.id === id;
  const selection = window.getSelection();
  const selectedLog = sameLog && selection && !selection.isCollapsed &&
    (pre.contains(selection.anchorNode) || pre.contains(selection.focusNode));
  if (!sameLog || !(openLog.selecting || selectedLog)) {
    if (pre.textContent !== next) {
      pre.textContent = next;
      if (sameLog && atEnd) pre.scrollTop = pre.scrollHeight;
    }
  }
  openLog.id = id;
  if (!dialog.open) { dialog.showModal(); pre.scrollTop = pre.scrollHeight; }
  if (job.status === "running" || job.status === "queued" || (sameLog && (openLog.selecting || selectedLog))) {
    openLog.timer = setTimeout(() => { if ($("log-dialog").open) openLog(id); }, 2000);
  }
}

// The archive: every saved log, newest first; ticked ones can be deleted
let archive = [];
const archiveTicked = new Set();

async function openArchive() {
  archive = await api("GET", "/api/logs");
  $("archive-filter").value = "";
  archiveTicked.clear();
  renderArchive();
  $("archive-dialog").showModal();
}

function archiveCount() {
  const button = $("archive-delete");
  button.disabled = !archiveTicked.size;
  button.textContent = archiveTicked.size ? `Delete ${archiveTicked.size}` : "Delete";
}

async function deleteArchived() {
  const n = archiveTicked.size;
  if (!n || !confirm(`Delete ${n === 1 ? "this job's log" : `these ${n} jobs' logs`}? This can't be undone.`)) return;
  const { removed } = await api("POST", "/api/logs/delete", { ids: [...archiveTicked] });
  if (removed < n) toast(`${n - removed} still running or queued: kept`);
  archive = await api("GET", "/api/logs");
  archiveTicked.clear();
  renderArchive();
  pollJobs();  // this session's deleted ones leave the Jobs list too
}

function renderArchive() {
  const words = $("archive-filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  const text = (j) => [j.title, j.status, j.kind, ...(j.outputs || []).map((o) => o.label), when(j.created)].join(" ").toLowerCase();
  const rows = archive.filter((j) => words.every((w) => text(j).includes(w)));
  const body = $("archive-body");
  body.innerHTML = "";
  archiveCount();
  if (!rows.length) {
    body.innerHTML = `<p class="muted">${archive.length ? "Nothing matches." : "No jobs yet."}</p>`;
    return;
  }
  const table = document.createElement("table");
  table.innerHTML = "<tr><th class='tick'><input type='checkbox' title='All shown'></th><th>Ended</th><th>Job</th>"
    + "<th>Status</th><th>Took</th><th>Results</th></tr>";
  const all = table.querySelector("th input");
  const boxes = [];
  all.checked = rows.every((j) => archiveTicked.has(j.id));
  all.onchange = () => {  // every row shown (the filter's), ticked or not
    for (const job of rows) all.checked ? archiveTicked.add(job.id) : archiveTicked.delete(job.id);
    for (const box of boxes) box.checked = all.checked;
    archiveCount();
  };
  for (const job of rows) {
    const tr = document.createElement("tr");
    tr.className = "row";
    const tick = document.createElement("td");
    tick.className = "tick";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = archiveTicked.has(job.id);
    box.onchange = () => {
      box.checked ? archiveTicked.add(job.id) : archiveTicked.delete(job.id);
      all.checked = rows.every((j) => archiveTicked.has(j.id));
      archiveCount();
    };
    tick.onclick = (e) => { e.stopPropagation(); if (e.target !== box) box.click(); };  // the cell, not the log
    tick.append(box);
    boxes.push(box);
    tr.append(tick);
    for (const value of [when(job.finished || job.created), job.title, job.status, duration(job)]) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    tr.children[3].className = job.status;
    const results = document.createElement("td");
    for (const out of job.outputs || []) {
      const link = outputLink(out);
      link.addEventListener("click", (e) => e.stopPropagation());
      results.append(link, " ");
    }
    tr.append(results);
    tr.onclick = () => openLog(job.id);
    table.append(tr);
  }
  body.append(table);
}

function closeGraphShow() {
  $("graph-show-panel").hidden = true;
  $("graph-show").setAttribute("aria-expanded", "false");
}
function applyGraphDisplay() {
  if (!graphShown || !graphView) return;
  const f = graphFilter(graphShown.item);
  Object.assign(graphView.options, { colours: f.colours, labels: f.labels, links: f.links });
  if (!!f.compact !== !!graphView.options.compact) {  // packed again (no new layout from the server)
    graphView.options.compact = f.compact;
    graphView.arrange();
    graphView.fit();
  }
  graphView.draw();
}
function changeGraphDisplay() {
  const it = currentGraph()?.it;
  if (!it) return;
  Object.assign(graphFilter(it), { colours: $("graph-depth-colours").checked, labels: $("graph-labels").value, links: $("graph-links").checked,
                                   compact: $("graph-compact").checked });
  applyGraphDisplay();
}
function resetGraphDisplay() {
  const it = currentGraph()?.it;
  if (!it) return;
  Object.assign(graphFilter(it), { minLength: 0, minDepth: 0, onlyHits: false, toScale: false, colours: true, labels: "auto", links: true,
                                   order: "track", compact: false });
  showGraph(it).then(() => graphView.fit());
}
