// ── Result tables (BLAST hits, primer pairs) ───────────────────────────
let tableView = null;  // { item, data, spec, sort: {col, down}, ticked }

const BLAST_COLUMNS = [
  { field: "accession", title: "Accession", raw: (hit) => hit.accession },
  { field: "title", title: "Description", raw: (hit) => hit.title },
  { field: "organism", title: "Organism", raw: (hit) => hit.organism },
  { field: "max_score", title: "Max score", how: "num", raw: (hit) => hit.max_score },
  { field: "total_score", title: "Total score", how: "num", raw: (hit) => hit.total_score },
  { field: "cover", title: "Query cover", how: "pct", raw: (hit) => hit.cover },
  { field: "evalue", title: "E value", how: "evalue", firstDown: false, raw: (hit) => hit.evalue },
  { field: "identity", title: "Identity", how: "pct", raw: (hit) => hit.identity },
  { field: "length", title: "Length", how: "num", raw: (hit) => hit.length },
];

function blastSpec(item, data) {
  const unit = data.program === "blastp" || data.program === "tblastn" ? "aa" : "bp";
  return {
    title: `<b>${esc(data.query.name)}</b> · ${data.query_length ? `${data.query_length.toLocaleString()} ${unit} · ` : ""}` +
      `${esc(data.program)}, ${esc(data.database)}${data.entrez ? ` · ${esc(data.entrez)}` : ""} · ` +
      `${esc(data.date)} · ${data.hits.length} hits`,
    columns: BLAST_COLUMNS,
    rows: data.hits,
    filter: (hit, text) => [hit.accession, hit.title, hit.organism].some((s) => s?.toLowerCase().includes(text)),
    filterTitle: "Hits whose accession, description or organism contain this",
    empty: "No hits.",
    tickable: true,
    actions: [{ label: (n) => (n ? `Download ticked (${n})` : "Download ticked"),
                title: "Download the ticked hits from GenBank", run: downloadBlastHits }],
  };
}

const PRIMER_COLUMNS = [
  { field: "n", title: "#", how: "num", raw: (pair) => pair.n },
  { field: "fseq", title: "Forward (5′→3′)", cls: "seq", raw: (pair) => pair.left.sequence },
  { field: "fsite", title: "F site", cls: "num", sortable: false,
    raw: (pair) => `${pair.left.start}–${pair.left.end}` },
  { field: "ftm", title: "Tm F", how: "tm", raw: (pair) => pair.left.tm },
  { field: "fgc", title: "GC F", how: "pct", raw: (pair) => pair.left.gc },
  { field: "rseq", title: "Reverse (5′→3′)", cls: "seq", raw: (pair) => pair.right.sequence },
  { field: "rsite", title: "R site", cls: "num", sortable: false,
    raw: (pair) => `${pair.right.start}–${pair.right.end}` },
  { field: "rtm", title: "Tm R", how: "tm", raw: (pair) => pair.right.tm },
  { field: "rgc", title: "GC R", how: "pct", raw: (pair) => pair.right.gc },
  { field: "product", title: "Product (bp)", how: "num", raw: (pair) => pair.product_size },
  { field: "psite", title: "Product site", cls: "num", sortable: false,
    raw: (pair) => `${pair.product_start}–${pair.product_end}${pair.wraps ? " ↵" : ""}` },
  { field: "penalty", title: "Penalty", how: "num2", firstDown: false, raw: (pair) => pair.penalty },
];

const PCR_COLUMNS = [
  { field: "label", title: "Sequence", raw: (hit) => hit.label },
  { field: "strand", title: "Orient.", cls: "num", raw: (hit) => (hit.strand === -1 ? "−" : "+") },
  { field: "fsite", title: "Forward site", cls: "num", sortable: false,
    raw: (hit) => `${hit.forward.start}–${hit.forward.end}` },
  { field: "rsite", title: "Reverse site", cls: "num", sortable: false,
    raw: (hit) => `${hit.reverse.start}–${hit.reverse.end}` },
  { field: "mm", title: "Mismatches", cls: "num", sortable: false,
    raw: (hit) => `${hit.forward.mismatches} / ${hit.reverse.mismatches}` +
      (hit.forward.three || hit.reverse.three ? ` (${hit.forward.three}/${hit.reverse.three} at 3′)` : "") },
  { field: "product", title: "Product (bp)", how: "num", raw: (hit) => hit.product_size },
  { field: "psite", title: "Product site", cls: "num", sortable: false,
    raw: (hit) => `${hit.product_start}–${hit.product_end}${hit.wraps ? " ↵" : ""}` },
];

function primersSpec(item, data) {
  if (data.mode !== "design") return pcrSpec(item, data);
  const template = data.template || {};
  const params = data.parameters || {};
  const pairs = (data.pairs || []).map((pair, i) => ({ ...pair, n: i + 1 }));
  return {
    title: `<b>${esc(template.label || item.label)}</b> · ${Number(template.length || 0).toLocaleString()} bp` +
      `${template.circular ? ", circular" : ""} · Primer3 · product ${params.product_min}–${params.product_max} bp, ` +
      `Tm ${params.tm_min}–${params.tm_max} °C, GC ${params.gc_min}–${params.gc_max} %` +
      (data.target ? ` · target ${data.target.start}–${data.target.end}` : "") +
      ` · ${pairs.length} pair${pairs.length === 1 ? "" : "s"} · ${esc(data.date)}` +
      (data.warnings?.length ? ` · ${esc(data.warnings.join(" "))}` : ""),
    columns: PRIMER_COLUMNS,
    rows: pairs,
    filter: (pair, text) => [pair.left.sequence, pair.right.sequence].some((s) => s.toLowerCase().includes(text)),
    filterTitle: "Pairs whose primers contain this",
    empty: "No primer pair satisfied these constraints. Try a wider product-size or Tm range, or a longer target.",
    tickable: true,
    actions: [
      { label: (n) => (n ? `Save primers (${n})` : "Save primers"),
        title: "One list per ticked pair, with its forward and reverse primer", run: addPrimerSequences },
    ],
  };
}

function pcrSpec(item, data) {
  const params = data.parameters || {};
  const hits = (data.hits || []).map((hit, i) => ({ ...hit, n: i + 1 }));
  const sequences = data.sequences || 0;
  return {
    title: `<b>${esc(data.forward)}</b> / ${esc(data.reverse)} · in-silico PCR · ` +
      `${hits.length} product${hits.length === 1 ? "" : "s"} in ${sequences} sequence${sequences === 1 ? "" : "s"} · ` +
      `up to ${params.mismatches} mismatch${params.mismatches === 1 ? "" : "es"}` +
      `${params.both_orientations ? " · both orientations" : ""} · ${esc(data.date)}`,
    columns: PCR_COLUMNS,
    rows: hits,
    filter: (hit, text) => (hit.label || "").toLowerCase().includes(text),
    filterTitle: "Products whose sequence name contains this",
    empty: "No product found with these primers and settings.",
    tickable: false,
  };
}

async function showTable(item) {
  showView("tableview");
  if (tableView?.item.id === item.id) return;
  let data;
  try {
    data = await api("GET", item.kind === "blast" ? `/api/blast/${item.id}` : `/api/primers/${item.id}`);
  } catch (e) {
    toast(e.message, true);
    return showView("placeholder");
  }
  const spec = item.kind === "blast" ? blastSpec(item, data) : primersSpec(item, data);
  tableView = { item, data, spec, sort: null, ticked: new Set() };
  $("table-filter").value = "";
  renderTable();
}

function tableValue(column, row) {
  const value = column.raw(row);
  if (column.how && (value === undefined || value === null || value === "" || Number.isNaN(Number(value)))) {
    return "—";  // not in the data (e.g. an old design's pair penalty: map_design used to drop it)
  }
  if (column.how === "evalue") return value === 0 ? "0" : value < 0.01 ? value.toExponential(1) : String(Number(value.toPrecision(2)));
  if (column.how === "pct") return `${Number(value).toFixed(0)} %`;
  if (column.how === "tm") return Number(value).toFixed(1);
  if (column.how === "num2") return Number(value).toFixed(2);
  if (column.how === "num") return Number(value).toLocaleString();
  return value;
}

function tableRows() {
  const { spec, sort } = tableView;
  const text = $("table-filter").value.trim().toLowerCase();
  let rows = spec.rows.map((row, i) => ({ row, i }));
  if (text && spec.filter) rows = rows.filter(({ row }) => spec.filter(row, text));
  if (sort) {
    const column = spec.columns.find((c) => c.field === sort.col);
    rows.sort((a, b) => {
      const x = column.raw(a.row), y = column.raw(b.row);
      const compare = typeof x === "number" && typeof y === "number" ? x - y
        : String(x ?? "").localeCompare(String(y ?? ""), undefined, { sensitivity: "base", numeric: true });
      return (compare || a.i - b.i) * (sort.down ? -1 : 1);
    });
  }
  return rows;
}

function renderTable() {
  const { spec, sort } = tableView;
  $("table-title").innerHTML = spec.title;
  const filter = $("table-filter");
  filter.hidden = !spec.filter;
  filter.title = spec.filterTitle || "";
  const rows = tableRows();
  const head = spec.columns.map((c) => {
    const arrow = c.sortable === false ? "" : `<span class="sort${sort?.col === c.field ? "" : " off"}">` +
      `${sort?.col === c.field && !sort.down ? "▴" : "▾"}</span>`;
    const num = c.how || c.cls === "num" ? ' class="num"' : "";  // headed on the side its values sit
    return `<th${num}${c.sortable === false ? "" : ` data-col="${c.field}"`}>${esc(c.title)}${arrow}</th>`;
  }).join("");
  const body = rows.map(({ row, i }) => `<tr data-i="${i}">` +
    (spec.tickable ? `<td><input type="checkbox"${tableView.ticked.has(i) ? " checked" : ""}></td>` : "") +
    spec.columns.map((c) => `<td${c.how ? ' class="num"' : c.cls ? ` class="${c.cls}"` : ""}>${esc(tableValue(c, row))}</td>`).join("") +
    "</tr>").join("");
  $("table-body").innerHTML = spec.rows.length
    ? `<table><thead><tr>${spec.tickable ? '<th><input type="checkbox" title="All shown"></th>' : ""}${head}</tr></thead>` +
      `<tbody>${body}</tbody></table>`
    : `<p class="muted">${esc(spec.empty || "Nothing to show.")}</p>`;
  tableTicked();
}

// The kind's actions' counts, after ticking (the table isn't redrawn: it keeps its scroll)
function tableTicked() {
  const { spec, ticked } = tableView;
  const n = ticked.size;
  const actions = spec.actions || (spec.action ? [spec.action] : []);
  [$("table-action"), $("table-action2")].forEach((button, i) => {
    const action = actions[i];
    button.hidden = !action;
    if (!action) return;
    button.textContent = action.label(n);
    button.title = action.title || "";
    button.disabled = action.enabled ? !action.enabled(n) : !n;
  });
}

function tableClick(e) {
  const th = e.target.closest("th[data-col]");
  if (th) {  // sort: numbers high to low first (E value and penalty low to high), text A to Z
    const column = tableView.spec.columns.find((c) => c.field === th.dataset.col);
    const firstDown = column.firstDown ?? !!column.how;
    const sort = tableView.sort;
    tableView.sort = sort?.col === column.field
      ? (sort.down === firstDown ? null : { col: column.field, down: !firstDown })
      : { col: column.field, down: firstDown };
    return renderTable();
  }
  if (!tableView.spec.tickable) return;
  if (e.target.matches("thead input")) {
    for (const box of $("table-body").querySelectorAll("tbody input")) {
      box.checked = e.target.checked;
      const i = Number(box.closest("tr").dataset.i);
      e.target.checked ? tableView.ticked.add(i) : tableView.ticked.delete(i);
    }
    return tableTicked();
  }
  const tr = e.target.closest("tbody tr");
  if (!tr) return;
  const i = Number(tr.dataset.i);
  const box = tr.querySelector("input");
  if (e.target !== box) box.checked = !box.checked;  // the whole row ticks it
  box.checked ? tableView.ticked.add(i) : tableView.ticked.delete(i);
  tableTicked();
}

function downloadBlastHits() {
  const { data, item, ticked } = tableView;
  const accessions = [...ticked].map((i) => data.hits[i].accession);
  if (!accessions.length) return;
  api("POST", "/api/tool-registry/run", { operation: "download.accessions", engine: "genbank_download",
    inputs: { accessions }, options: { as_list: true }, output: `${data.query.name} BLAST hits`, folder: item.folder })
    .then(() => { toast(`Downloading ${accessions.length} record${accessions.length === 1 ? "" : "s"} (see Jobs)`); pollJobs(); })
    .catch((e) => toast(e.message, true));
}

async function addPrimerSequences() {
  const rows = [...tableView.ticked];
  if (!rows.length) return;
  try {
    const result = await api("POST", `/api/primers/${tableView.item.id}/sequences`, { rows });
    fresh.items.push(...result.items.map((it) => it.id));
    saveFresh();
    toast(result.items.length === 1 ? `Saved ${result.items[0].label}` : `Saved ${result.items.length} lists`);
    loadList();
  } catch (e) { toast(e.message, true); }
}


function tableText(sep) {
  const { spec } = tableView;
  const cell = (v) => (sep === "," && /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? ""));
  const lines = [spec.columns.map((c) => c.title).join(sep)];
  for (const { row } of tableRows()) lines.push(spec.columns.map((c) => cell(c.raw(row))).join(sep));
  return lines.join("\n") + "\n";
}

async function copyTable() {
  try { await navigator.clipboard.writeText(tableText("\t")); } catch (e) { return toast("Couldn't copy", true); }
  toast("Table copied: paste it into Excel");
}

function exportCsv() {
  const base = (tableView.item.name || "table").replace(/[\\/:*?"<>|\s]+/g, "_");
  saveTextFile(`${base}.csv`, tableText(","), "text/csv");
}
