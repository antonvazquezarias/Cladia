// Cladia frontend: the library list, the viewer and editing, tools, export and jobs.
try {
  for (const key of Object.keys(localStorage)) {
    if (!key.startsWith("seqlab.")) continue;
    const renamed = `cladia.${key.slice(7)}`;
    if (localStorage.getItem(renamed) === null) localStorage.setItem(renamed, localStorage.getItem(key));
  }
} catch (error) { /* Browser storage may be unavailable. */ }
// All data goes through the Python API in server.py.

// ── State ────────────────────────────────────────────────────────────────
let items = [];        // everything in the list (from /api/library)
let selected = [];     // selected item ids, in the order they were picked
let anchorId = null;   // where a Shift+click range starts
let listVersion = null;  // X-Cladia-Version of the last /api/library load (focus check)

// A view window (New window) is index.html?view=<ids>: just those items, view only
const viewIds = new URLSearchParams(location.search).get("view")?.split(",") || null;

const $ = (id) => document.getElementById(id);
const itemById = (id) => items.find((it) => it.id === id);
const inPywebview = () => !!(window.pywebview && window.pywebview.api);
// HTML-escape text put into a template: names and descriptions come from files.
const esc = (text) => String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ── API ──────────────────────────────────────────────────────────────────
// Every request says which window sent it (edit locks, server.py); a change
// (anything but a read) is told to Cladia's other windows, which then show
// the saved state (refreshShown).
// (the app window's address names it, ?w=, so Python knows which is which)
const windowId = new URLSearchParams(location.search).get("w") || Math.random().toString(36).slice(2);
const windows = "BroadcastChannel" in window ? new BroadcastChannel("cladia") : null;

// One request: its JSON and headers (the library sends its version as a header).
async function apiRequest(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "X-Cladia-Window": windowId, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || res.statusText);
  // other windows reload their library list; locks and settings (folder navigation, tree view) don't change it
  if (method !== "GET" && !url.startsWith("/api/locks") && !url.startsWith("/api/settings")) windows?.postMessage("changed");
  return { data, headers: res.headers };
}

async function api(method, url, body) {
  return (await apiRequest(method, url, body)).data;
}

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = isError ? "error" : "";
  el.hidden = false;
  clearTimeout(toast.timer);
  // long messages stay longer, so they can be read (up to 10 s)
  toast.timer = setTimeout(() => (el.hidden = true), isError ? 7000 : Math.min(10000, Math.max(2500, 45 * message.length)));
}

// A question with one text answer, in Cladia's own dialog (not the browser's prompt()).
// Resolves to the trimmed answer, or null when cancelled; check(answer) returns a
// message to show in the dialog (the answer isn't accepted) or nothing.
function askText({ title, label, value = "", about = "", ok = "OK", check = null }) {
  const dialog = $("ask-dialog"), form = dialog.querySelector("form"), error = form.querySelector(".error");
  $("ask-title").textContent = title;
  $("ask-label").textContent = label;
  $("ask-about").textContent = about;
  $("ask-about").hidden = !about;
  $("ask-ok").textContent = ok;
  form.answer.value = value;
  error.hidden = true;
  return new Promise((resolve) => {
    form.onsubmit = (e) => {
      if (e.submitter?.value !== "ok") return;  // Cancel just closes
      const problem = check?.(form.answer.value.trim());
      if (problem) {
        e.preventDefault();
        error.textContent = problem;
        error.hidden = false;
      }
    };
    dialog.onclose = () => resolve(dialog.returnValue === "ok" ? form.answer.value.trim() : null);
    dialog.returnValue = "";
    dialog.showModal();
    form.answer.select();
  });
}

// ── Sequence list ────────────────────────────────────────────────────────
async function loadList() {
  const [library, newFolders] = await Promise.all([
    apiRequest("GET", "/api/library"),
    api("GET", "/api/folders"),
  ]);
  items = library.data;
  folders = newFolders;
  listVersion = library.headers.get("X-Cladia-Version");
  pruneFolderState();
  renderFolderButton();
  if (!$("folder-popup").hidden) renderFolderTree();
  selected = selected.filter((id) => itemById(id));  // drop ids that are gone
  renderList();
  loadRowFacts();  // the other kinds' numbers come after (the list is drawn at once)
  if (metadataItem) {
    const current = itemById(metadataItem.id), location = $("metadata-details").querySelector('[data-fact="Folder"]');
    if (current && location) location.lastChild.textContent = current.folder || "Library";
  }
  scheduleToolStates();
  if (pollJobs.last) renderJobs(pollJobs.last);  // their results' links follow the list
}

// Sidebar sections: sequences and sequence lists, then alignments.
// A sequence list is one entry; its arrow shows its sequences underneath.
const expanded = new Set();  // ids of the lists shown open
let folders = [];            // folder names (/api/folders)
let currentFolder = localStorage.getItem("cladia.folder") || null;  // the folder shown; null: items at the library root
const inFolder = (it) => !!$("filter").value.trim() || (it.folder || null) === currentFolder;  // the filter: every folder
const isLibraryList = (it) => it.kind === "list" || it.kind === "item_list";
const listOwner = (it) => it?.parent || it?.group;
function listAncestors(it) {
  const seen = new Set();
  for (let id = listOwner(it); id && !seen.has(id); id = listOwner(itemById(id))) seen.add(id);
  return [...seen];
}
const section = (it) => {
  if (listOwner(it)) return "sequence";  // children are rendered in their list's section
  if (it.kind === "primers") return it.mode === "test" ? "pcr" : "primers";  // designs and tests apart
  return ["alignment", "comparison", "tree", "graph", "mapping", "blast", "figure", "variants", "reads"].includes(it.kind) ? it.kind : "sequence";
};
const LIST_ELEMENTS = { sequence: "sequences", alignment: "alignments", comparison: "comparisons", reads: "reads", mapping: "mappings",
                        tree: "trees", graph: "graphs", figure: "figures", variants: "variants", blast: "blasts", primers: "primers", pcr: "pcrs" };
const childrenOf = (list) => items.filter((c) => c.parent === list.id).sort((a, b) => a.record - b.record);
const listMembers = (list) => list.kind === "list" ? childrenOf(list)
  : items.filter((it) => it.group === list.id).sort((a, b) => (a.group_order ?? -1) - (b.group_order ?? -1));

// The rows of one section, in display order
function visibleRows(name) {
  const filter = $("filter").value.trim().toLowerCase();
  const hit = (it) => [it.label, it.organism, it.description, it.search].some((s) => s && s.toLowerCase().includes(filter));
  const rows = [];
  function add(it, parentMatches = false) {
    const matches = parentMatches || hit(it);
    const start = rows.length;
    rows.push(it);
    if (isLibraryList(it) && (expanded.has(it.id) || filter)) {
      for (const child of listMembers(it)) add(child, matches);
    }
    if (!matches && rows.length === start + 1) rows.pop();
  }
  for (const it of items.filter((x) => !listOwner(x) && section(x) === name && inFolder(x))) add(it);
  return rows;
}

function renderList() {
  hideCard();  // its row may be about to go (a delete): it would never get its mouseleave
  const sections = Object.fromEntries(Object.keys(LIST_ELEMENTS).map((name) => [name, visibleRows(name)]));
  if (!viewIds) {
    const visible = new Set(Object.values(sections).flat().map(it => it.id));
    selected = selected.filter(id => visible.has(id));
    if (!visible.has(anchorId)) anchorId = null;
  }
  const chosen = new Set(selected);
  const none = Object.values(sections).every((rows) => !rows.length);
  for (const [name, rows] of Object.entries(sections)) {
    const list = $(LIST_ELEMENTS[name]);
    list.innerHTML = "";
    // empty sections aren't shown; with nothing at all, Sequences says so
    list.hidden = list.previousElementSibling.hidden = !rows.length && !(none && name === "sequence");
    const empty = !items.length ? "None yet" : $("filter").value ? "Nothing matches the filter"
      : "This folder has nothing of its own: import files here, or drop items on it in the folder button's tree";
    if (!rows.length) list.innerHTML = `<li class="empty">${empty}</li>`;
    for (const it of rows) {
      const li = document.createElement("li");
      // the row's first line never wraps (only the name shortens); the description goes under it
      const line = Object.assign(document.createElement("div"), { className: "line" });
      li.append(line);
      if (isLibraryList(it)) {
        const twisty = document.createElement("span");
        twisty.className = "twisty";
        twisty.textContent = expanded.has(it.id) ? "▾" : "▸";
        twisty.onclick = (e) => {
          e.stopPropagation();
          expanded.has(it.id) ? expanded.delete(it.id) : expanded.add(it.id);
          renderList();
        };
        line.append(twisty, renaming === it.id ? renameInput(it) : rowName(it.label));
        const count = document.createElement("small");
        count.textContent = renaming === it.id ? "" : `(${it.count})`;
        line.append(count);
        if (renaming !== it.id && !it.missing) line.append(...rowDetails(it));
      } else if (renaming === it.id) {
        line.append(renameInput(it));
      } else {
        line.append(rowName(it.parent ? it.name : it.label));
        if (!it.missing) line.append(...rowDetails(it));
      }
      if (fresh.items.includes(it.id) && !it.missing && renaming !== it.id) {
        line.append(Object.assign(document.createElement("span"), { className: "badge", textContent: "new" }));
      }
      const description = rowDescription(it);
      if (showDescriptions && description && renaming !== it.id) {  // under the name, on its own line
        li.append(Object.assign(document.createElement("span"), { className: "sub", textContent: description }));
      }
      if (it.missing) li.title = `Cladia's copy is gone: ${it.path}`;  // (others: the hover card, not a second tooltip)
      const depth = listAncestors(it).length;
      li.classList.toggle("child", depth > 0);
      if (depth > 1) li.style.setProperty("--list-depth", depth);
      li.classList.toggle("renaming", renaming === it.id);
      li.classList.toggle("selected", chosen.has(it.id));
      li.classList.toggle("missing", it.missing);
      li.classList.toggle("busy", busyLists.has(it.parent) || busyLists.has(it.id));
      li.classList.toggle("fresh", fresh.items.includes(it.id) && !it.missing);
      li.dataset.id = it.id;
      li.draggable = !viewIds && !it.missing;  // onto a folder (the folder button's menu), or a list
      li.ondragstart = (e) => itemDragStart(e, it);
      if (isLibraryList(it)) {
        const takes = () => draggedIds && !draggedIds.some((id) => id === it.id || listAncestors(it).includes(id))
          && !draggedIds.every((id) => listOwner(itemById(id)) === it.id);
        li.ondragover = (e) => {
          if (!takes()) return;
          e.preventDefault();
          e.stopPropagation();
          $("lists").classList.remove("drop-out");
          li.classList.add("drop-target");
        };
        li.ondragleave = () => li.classList.remove("drop-target");
        li.ondrop = (e) => {
          if (!takes()) return;
          e.preventDefault();
          e.stopPropagation();
          $("lists").classList.remove("drop-out");
          li.classList.remove("drop-target");
          dropIntoList(it, draggedIds);
        };
      }
      li.onclick = (e) => { hideCard(); clickItem(it, e); };
      cardFor(li, it);
      li.oncontextmenu = (e) => { e.stopPropagation(); openListMenu(e, it); };
      list.append(li);
    }
  }
}

// A click only changes which rows are selected: update those classes instead of
// rebuilding the list (a 5,000-item library made every click expensive).
function updateSelection() {
  const chosen = new Set(selected);
  for (const element of Object.values(LIST_ELEMENTS)) {
    for (const li of $(element).querySelectorAll("li[data-id]")) {
      li.classList.toggle("selected", chosen.has(li.dataset.id));
    }
  }
}

// Descriptions: each sequence's description under its name (remembered)
let showDescriptions = localStorage.getItem("cladia.descriptions") === "1";

function toggleDescriptions() {
  showDescriptions = !showDescriptions;
  try { localStorage.setItem("cladia.descriptions", showDescriptions ? "1" : "0"); } catch (e) {}
  $("show-descriptions").classList.toggle("active", showDescriptions);
  renderList();
}

// A row's name: shortened with "…" when the row is too narrow, so what follows
// it (a list's count, the "new" badge) always shows
function rowName(text) {
  return Object.assign(document.createElement("span"), { className: "name", textContent: text });
}

// ── Row details (23 Nov 2026): what a sequence row says besides its name ──
// The organism (italic, only in the room the name leaves), then at the right
// a "read" tag for Sanger reads, a ring for circular sequences and the size
// ("aa" marks a protein). Other kinds show a few numbers from rowFacts
// (tips, hits, depth...). To go back to name-only rows, drop the rowDetails
// calls in renderList and the .org/.size/.tag/.ring rules in app.css.
function rowDetails(it) {
  const span = (className, textContent = "") => Object.assign(document.createElement("span"), { className, textContent });
  const parts = [];
  if (it.kind === "sequence") {
    if (it.length == null) return [];
    parts.push(span("org", it.organism || ""));  // empty, it still pushes the size to the right
    if (it.trace) parts.push(span("tag", "read"));
    if (it.circular) parts.push(span("ring"));
    parts.push(span("size", sizeText(it.length, it.protein)));
    return parts;
  }
  const facts = rowFacts[it.id], text = facts && factsText(it.kind, facts);
  if (!text) return [];
  parts.push(span("org"));
  if (it.kind === "reads" && (facts.paired || facts.long)) parts.push(span("tag", facts.long ? "long" : "paired"));
  parts.push(span("size", text));
  return parts;
}

// ── Facts of the other kinds (/api/library/facts, cached by the server) ──
let rowFacts = {};

async function loadRowFacts() {
  let facts;
  try { facts = await api("GET", "/api/library/facts"); } catch (e) { return; }
  if (JSON.stringify(facts) === JSON.stringify(rowFacts)) return;
  rowFacts = facts;
  renderList();
}

// 950, 12.3 k, 1.5 M
function countText(n) {
  return n < 1e4 ? n.toLocaleString() : n < 1e6 ? `${(n / 1e3).toFixed(n < 1e5 ? 1 : 0)} k` : `${(n / 1e6).toFixed(1)} M`;
}

function factsText(kind, f) {
  const plural = (n, word) => `${countText(n)} ${word}${n === 1 ? "" : "s"}`;
  switch (kind) {
    case "list": return f.total != null ? sizeText(f.total, f.unit === "aa") : "";
    case "alignment": return f.count != null ? `${countText(f.count)} × ${countText(f.columns)} cols` : "";
    case "tree": return f.tips != null ? plural(f.tips, "tip") : "";
    case "graph": return f.nodes != null ? `${plural(f.nodes, "node")} · ${sizeText(f.total || 0)}` : "";
    case "mapping": return f.mean_depth != null ? `${f.mean_depth}× · ${f.covered_percent} % covered` : "";
    case "comparison": return f.genomes ? `${plural(f.genomes, "genome")} · ${plural(f.blocks || 0, "block")}` : "";
    case "blast": return f.hits != null ? plural(f.hits, "hit") : "";
    case "primers": return f.mode === "design" ? plural(f.pairs || 0, "pair") : plural(f.products || 0, "product");
    case "reads": return f.reads != null ? `${plural(f.reads, "read")} · ${sizeText(f.bases || 0)}` : "";
    case "variants": return f.sites != null ? plural(f.sites, "site") : "";
    case "figure": return { tree: "tree", genome: "genome map", comparison: "comparison", dotplot: "dot plot" }[f.of] || "";
  }
  return "";
}

// 679 bp, 1,207 bp (exact for genes and barcodes), 15.8 kb, 152 kb, 1.2 Mb; proteins in aa
function sizeText(n, protein) {
  if (protein) return `${n.toLocaleString()} aa`;
  if (n < 10000) return `${n.toLocaleString()} bp`;
  if (n < 1e6) return `${n < 1e5 ? (n / 1e3).toFixed(1) : Math.round(n / 1e3)} kb`;
  return `${(n / 1e6).toFixed(1)} Mb`;
}

// The description line, without the organism it starts with when the row already shows it
function rowDescription(it) {
  const d = it.description || "";
  return it.organism && d.toLowerCase().startsWith(it.organism.toLowerCase()) ? d.slice(it.organism.length).replace(/^[\s,;:.-]+/, "") : d;
}

// ── The sidebar's width: drag its right edge (remembered) ────────────────
const SIDEBAR = { min: 230, max: 600 };  // (at 200 px "Download…" was cut)

function setSidebarWidth(px) {
  const width = Math.round(Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, px)));
  document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
  return width;
}

function dragSidebarEdge(e) {
  e.preventDefault();
  hideCard();
  document.body.classList.add("resizing");
  let width = null;
  const move = (ev) => { width = setSidebarWidth(ev.clientX); };
  const up = () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    document.body.classList.remove("resizing");
    if (width) try { localStorage.setItem("cladia.sidebar", width); } catch (err) {}
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}
try { const w = Number(localStorage.getItem("cladia.sidebar")); if (w) setSidebarWidth(w); } catch (e) {}

// ── Updates ──────────────────────────────────────────────────────────────
// An installed Cladia knows its version and the folder its installer was run
// from (updates.py); a newer installer there is announced at every start (a
// toast) until it is installed, and Settings > General can show it in Explorer.
async function checkUpdates(announce = true) {
  let u;
  try { u = await api("GET", "/api/updates"); } catch { return; }
  const file = u.newer?.path.split(/[\\/]/).pop();
  $("version-note").textContent = !u.version ? "A development copy (run from the code): updates aren't checked."
    : u.newer ? `Cladia ${u.version}. A newer version, ${u.newer.version}, is ready: close Cladia, then run ${file}.`
    : u.folder ? `Cladia ${u.version}, the newest in ${u.folder}.`
    : `Cladia ${u.version}.`;
  $("update-reveal").hidden = !u.newer;
  if (announce && u.newer) toast(`Cladia ${u.newer.version} is ready to install: close Cladia, then run ${file} ` +
    "(Settings › General shows it in Explorer).");
}

// ── Settings (⚙ by the title) ────────────────────────────────────────────
// Kept by the server (.cladia/settings.json): cores for computing jobs, the NCBI
// e-mail and API key; and what Cladia's files take, with cleanups.
async function openSettings() {
  const dialog = $("settings-dialog"), form = dialog.querySelector("form");
  const s = await api("GET", "/api/settings");
  form.cores.value = s.cores;
  form.cores.max = s.cpus;
  form.text_size.value = String(s.text_size || 100);
  if (!inPywebview()) $("text-size-note").textContent = "In a browser, use the browser's own zoom (Ctrl + +) instead.";
  $("cores-note").textContent = `This PC has ${s.cpus} cores; 0 = ${s.default_cores} (all but one). ` +
    "MAFFT, trees and mapping use them all; most annotation methods one each, several at once.";
  form.ncbi_email.value = s.ncbi_email;
  form.ncbi_api_key.value = s.ncbi_api_key;
  checkUpdates(false);
  subs =(s.submitters || []).map((p) => ({ ...p }));
  subAt = 0;
  renderSubmitters();
  form.querySelector(".error").hidden = true;
  $("blastdb-browse").hidden = !inPywebview();
  settingsTab(settingsTab.shown || "general");
  positionSettings();
  dialog.scrollTop = 0;
  dialog.showModal();
}

function positionSettings() {
  const top = $("toolbar").getBoundingClientRect().bottom + 8;
  $("settings-dialog").style.setProperty("--tool-dialog-top", `${Math.max(8, Math.min(top, window.innerHeight - 80))}px`);
  fitSettingsStorage();
}

function fitSettingsStorage() {
  const dialog = $("settings-dialog"), panel = dialog.querySelector('[data-tab="space"].tab-panel');
  if (!dialog.open || panel.hidden) return;
  const trash = $("trash-list");
  // Measure its normal capped size first, so growing the window restores space.
  trash.style.removeProperty("--trash-fit-height");
  const overflow = panel.scrollHeight - panel.clientHeight;
  if (overflow > 0) {
    // Preserve a usable list in short windows; the panel can still scroll there.
    trash.style.setProperty("--trash-fit-height", `${Math.max(64, trash.getBoundingClientRect().height - overflow - 1)}px`);
  }
}

// Tabs on top: one panel shown; Storage measures and BLAST databases lists when opened
function settingsTab(name) {
  settingsTab.shown = name;
  for (const b of $("settings-tabs").children) b.classList.toggle("active", b.dataset.tab === name);
  for (const panel of $("settings-dialog").querySelectorAll(".tab-panel")) panel.hidden = panel.dataset.tab !== name;
  if (name === "space") renderSpace();
  if (name === "tools") renderToolEngines();
  if (name === "blastdbs") renderBlastDbs();
}

// ── Submitters (Settings > Submitters) ───────────────────────────────────
// Presets for GenBank submissions (submitter.py): edited here as a working copy
// and saved with the rest of the settings (Cancel throws the edits away).
let subs = [], subAt = 0;

function renderSubmitters() {
  const select = $("sub-select");
  subAt = Math.max(0, Math.min(subAt, subs.length - 1));
  select.innerHTML = subs.map((s, i) => `<option value="${i}">${esc(s.name || "(unnamed)")}</option>`).join("");
  select.value = String(subAt);
  select.disabled = !subs.length;
  $("sub-form").hidden = !subs.length;
  $("sub-none").hidden = subs.length > 0;
  for (const id of ["sub-copy", "sub-delete", "sub-export"]) $(id).disabled = !subs.length;
  for (const input of $("sub-form").querySelectorAll("[data-f]")) input.value = subs[subAt]?.[input.dataset.f] ?? "";
}

function freeSubName(base) {
  const taken = new Set(subs.map((s) => s.name.toLowerCase()));
  let name = base, n = 2;
  while (taken.has(name.toLowerCase())) name = `${base} ${n++}`;
  return name;
}

function addSubmitter(preset) {
  subs.push(preset);
  subAt = subs.length - 1;
  renderSubmitters();
}

async function importSbt(file) {
  try {
    const preset = await api("POST", "/api/submitters/parse", { text: await file.text() });
    preset.name = freeSubName(preset.name || file.name.replace(/\.[^.]*$/, ""));
    addSubmitter(preset);
  } catch (e) {
    toast(e.message, true);
  }
}

async function exportSbt() {
  const s = subs[subAt];
  try {
    const { text } = await api("POST", "/api/submitters/sbt", { preset: s });
    await saveTextFile(`${(s.name || "submitter").replace(/[^\w.-]+/g, "_")}.sbt`, text, "text/plain");
  } catch (e) {
    toast(e.message, true);
  }
}

// ── Local BLAST databases (Settings > BLAST databases) ───────────────────
// Listed by the server (blastdb.py): Cladia's own in its "Blast databases"
// folder, ones you have where they are. Making one (from FASTA, or a GenBank
// search) is a job; the list is drawn again when it ends.
async function renderBlastDbs() {
  const { folder, databases } = await api("GET", "/api/blastdbs");
  $("blastdb-folder").textContent = folder;
  const box = $("blastdb-list");
  if (!databases.length) {
    box.innerHTML = '<p class="muted">None yet.</p>';
    return;
  }
  const where = (db) => db.own ? (db.query ? `GenBank: ${db.query}` : `from ${db.source || "FASTA"}`) : db.path;
  box.innerHTML = `<table><tr><th>Name</th><th>Type</th><th class="num">Sequences</th><th class="num">Size</th><th></th></tr>` +
    databases.map((db, i) => `<tr><td><b>${esc(db.name)}</b><div class="where">${esc(where(db))}</div></td>` +
      `<td>${db.type === "prot" ? "protein" : "nucleotide"}</td>` +
      `<td class="num">${db.sequences ? Number(db.sequences).toLocaleString() : "—"}</td>` +
      `<td class="num">${db.missing ? '<span class="error">files gone</span>' : bytes(db.size)}</td>` +
      `<td><button type="button" class="secondary" data-db="${i}">${db.own ? "Delete" : "Remove"}</button></td></tr>`).join("") +
    "</table>";
  for (const button of box.querySelectorAll("[data-db]")) {
    const db = databases[button.dataset.db];
    button.onclick = async () => {
      const ask = db.own ? `Delete the database ${db.name}? Its files in Cladia's folder are deleted.`
                         : `Take ${db.name} off the list? Its files stay where they are.`;
      if (!confirm(ask)) return;
      try { await api("POST", "/api/blastdbs/remove", { name: db.name }); } catch (e) { return toast(e.message, true); }
      renderBlastDbs();
    };
  }
}

async function addBlastDb() {
  const form = $("settings-dialog").querySelector("form");
  try {
    const r = await api("POST", "/api/blastdbs/add", { path: form.blastdb_path.value, name: form.blastdb_add_name.value });
    toast(r.job ? `Making the database (see Jobs)` : `Added ${r.database.name}`);
    form.blastdb_path.value = form.blastdb_add_name.value = "";
    if (r.job) pollJobs();
    renderBlastDbs();
  } catch (e) {
    toast(e.message, true);
  }
}

async function estimateBlastDb() {
  const form = $("settings-dialog").querySelector("form");
  const query = form.blastdb_query.value.trim();
  const out = $("blastdb-estimate-text");
  if (!query) return (out.textContent = "Type a search first.");
  out.textContent = "Asking NCBI…";
  try {
    const e = await api("GET", `/api/blastdbs/estimate?query=${encodeURIComponent(query)}`);
    const n = (x) => Number(x).toLocaleString();
    const time = e.seconds < 90 ? `${e.seconds} s` : e.seconds < 5400 ? `${Math.round(e.seconds / 60)} min` : `${(e.seconds / 3600).toFixed(1)} h`;
    out.textContent = `${n(e.count)} records, ${n(e.mean)} bp on average (${bytes(e.bases).replace("B", "b")} of sequence). ` +
      `The database: about ${bytes(e.size)}. Downloading ${bytes(e.download)} and making it: about ${time}.`;
  } catch (err) {
    out.textContent = err.message;
  }
}

async function downloadBlastDb() {
  const form = $("settings-dialog").querySelector("form");
  const query = form.blastdb_query.value.trim();
  if (!query) return ($("blastdb-estimate-text").textContent = "Type a search first.");
  try {
    await api("POST", "/api/blastdbs/download", { query, name: form.blastdb_name.value.trim() || query });
    toast("Downloading the database (see Jobs)");
    pollJobs();
  } catch (e) {
    toast(e.message, true);
  }
}

async function settingsDone(form, event) {
  if (event.submitter?.value !== "save") return;
  event.preventDefault();
  try {
    await api("PUT", "/api/settings", { cores: Number(form.cores.value) || 0, ncbi_email: form.ncbi_email.value,
                                        ncbi_api_key: form.ncbi_api_key.value, text_size: Number(form.text_size.value),
                                        submitters: subs });
  } catch (e) {
    form.querySelector(".error").textContent = e.message;
    form.querySelector(".error").hidden = false;
    return;
  }
  $("settings-dialog").close();
  toast("Settings saved");
}

const bytes = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.round(n / 1e3)} kB`);

async function renderSpace() {
  const box = $("space-info");
  box.textContent = "Measuring…";
  const [s, recovery] = await Promise.all([api("GET", "/api/settings/space"), api("GET", "/api/project/recovery")]);
  $("project-recovery").textContent = recovery.issues.length
    ? `Interrupted transfers need review: ${recovery.issues.join("; ")}`
    : recovery.removed ? `Cleaned up ${recovery.removed} temporary or interrupted transfers at startup.` : "";
  const rows = [
    ["Library (your items)", bytes(s.library), ""],
    [`Job logs (${s.log_count})`, bytes(s.logs), s.log_count ? '<button type="button" data-clean="logs" class="secondary">Delete those over 30 days old</button>' : ""],
    ["Folders of failed jobs", bytes(s.jobs), s.jobs ? "(cleared when Cladia starts)" : ""],
    ["Project transfer files", bytes(s.transfers || 0), "(temporary; cleaned at startup)"],
    [`Trash (${s.trash_count} item${s.trash_count === 1 ? "" : "s"})`, bytes(s.trash), s.trash_count ? "(below)" : ""],
    ["Saved versions (each item's last 20 saves)", bytes(s.history),
     s.history ? '<button type="button" data-clean="history" class="secondary" title="Your items stay as they are; only earlier versions go, and undoing earlier deletes and moves">Delete saved versions</button>' : ""],
  ];
  for (const linux of s.images) {  // One shared Linux disk; legacy disks remain listed until migrated
    rows.push([`Cladia's Linux: ${esc(linux.name)}`, bytes(linux.size), linux.old ? "(will migrate into Cladia on the next tool run)" : "(shared by tools; removed with Cladia)"]);
  }
  if (s.image_disk) rows.push(["Old container disk (wslc)", bytes(s.image_disk.size),
    `<span title="${esc(s.image_disk.path)}">no longer used by Cladia; other programs may use it</span>`]);
  if (s.images_error) rows.push(["Tool images", "", esc(s.images_error)]);
  box.innerHTML = `<table>${rows.map(([a, b, c]) => `<tr><td>${a}</td><td class="num">${b}</td><td>${c}</td></tr>`).join("")}</table>`;
  renderTrash();
  for (const button of box.querySelectorAll("[data-clean]")) {
    button.onclick = async () => {
      const what = button.dataset.clean;
      if (what === "history" && !confirm("Delete every saved version of your items?\n\nThe items stay as they are now; you can't go back " +
                                         "to earlier versions, or undo earlier deletes and moves.")) return;
      button.disabled = true;
      const { removed } = await api("POST", "/api/settings/clean", { what });
      toast(what === "history" ? `Saved versions deleted (${bytes(removed)})` : Array.isArray(removed)
        ? `Removed ${removed.join(", ") || "nothing"}` : `Deleted ${removed} old log${removed === 1 ? "" : "s"}`);
      renderSpace();
    };
  }
}

$("check-library").onclick = async () => {
  const button = $("check-library"), out = $("library-check-result");
  button.disabled = true; out.textContent = "Checking library…";
  try {
    const result = await api("GET", "/api/library/check");
    out.replaceChildren();
    const summary = document.createElement("p");
    summary.textContent = `Checked ${result.checked} items; ${result.issues.length} issue(s) and ${result.unreferenced.length} unreferenced file(s). No files were changed.`;
    out.append(summary);
    for (const issue of result.issues) {
      const line = document.createElement("div"); line.textContent = `${issue.name}: ${issue.file} — ${issue.problem}`; out.append(line);
    }
    if (result.unreferenced.length) {
      const label = document.createElement("p"); label.textContent = "Unreferenced files (review before removing):"; out.append(label);
      for (const name of result.unreferenced) { const line = document.createElement("div"); line.textContent = name; out.append(line); }
    }
  } catch (e) { out.textContent = `Check failed: ${e.message}`; }
  finally { button.disabled = false; }
};

// ── The trash (Settings > Storage) ─────────────────────────────────────
// Deleted items (library.py "The trash"), ticked to restore or delete for good
const trashTicked = new Set();

async function renderTrash() {
  const box = $("trash-list");
  const found = await api("GET", "/api/trash");
  for (const id of [...trashTicked]) if (!found.some((t) => t.id === id)) trashTicked.delete(id);
  box.innerHTML = found.length ? "" : '<p class="muted">Empty.</p>';
  $("trash-empty").disabled = !found.length;
  const count = () => {
    $("trash-restore").disabled = $("trash-delete").disabled = !trashTicked.size;
    $("trash-restore").textContent = trashTicked.size > 1 ? `Restore ${trashTicked.size}` : "Restore";
  };
  count();
  if (!found.length) return;
  const table = document.createElement("table");
  for (const t of found) {
    const tr = table.insertRow();
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = trashTicked.has(t.id);
    box.onchange = () => { box.checked ? trashTicked.add(t.id) : trashTicked.delete(t.id); count(); };
    tr.insertCell().append(box);
    const where = t.from_list ? `from the list ${t.from_list}` : t.folder ? `in ${t.folder.replace(/\//g, " › ")}` : "";
    tr.insertCell().textContent = t.label;
    tr.insertCell().textContent = [t.kind, where].filter(Boolean).join(", ");
    tr.insertCell().textContent = when(t.deleted);
    const size = tr.insertCell();
    size.className = "num";
    size.textContent = bytes(t.size || 0);
    tr.onclick = (e) => { if (e.target !== box) box.click(); };
  }
  box.append(table);
}

async function restoreTrash() {
  const ids = [...trashTicked];
  const { ids: back } = await api("POST", "/api/trash/restore", { ids });
  trashTicked.clear();
  await loadList();
  toast(`${back.length} item${back.length === 1 ? "" : "s"} restored`);
  renderSpace();
}

async function deleteTrash(all) {
  const ids = all ? null : [...trashTicked];
  const what = all ? "everything in the trash" : `${ids.length} item${ids.length === 1 ? "" : "s"}`;
  if (!confirm(`Delete ${what} for good? This can't be undone.`)) return;
  await api("POST", "/api/trash/delete", { ids });
  trashTicked.clear();
  renderSpace();
}

// Click: open one · Ctrl+click: add/remove · Shift+click: range · double-click: rename.
// (A second click within 400 ms renames: the row may be redrawn between clicks,
// so the browser's own dblclick can't be relied on.)
let lastClick = { id: null, at: 0 };

function clickItem(it, e) {
  if (busyLists.has(it.parent) || busyLists.has(it.id)) return;  // being changed: see removeSelected
  const again = lastClick.id === it.id && Date.now() - lastClick.at < 400 && !e.shiftKey && !e.ctrlKey && !e.metaKey;
  lastClick = { id: it.id, at: again ? 0 : Date.now() };
  if (again) return viewIds || it.missing ? undefined : startRename(it.id);
  const previous = [...selected], previousAnchor = anchorId;
  if (e.shiftKey && anchorId && visibleRows(section(it)).some(row => row.id === anchorId)) {
    const ids = visibleRows(section(it)).map((x) => x.id);
    const [a, b] = [ids.indexOf(anchorId), ids.indexOf(it.id)].sort((x, y) => x - y);
    selected = ids.slice(a, b + 1);
  } else if (e.ctrlKey || e.metaKey) {
    selected = selected.includes(it.id) ? selected.filter((id) => id !== it.id) : [...selected, it.id];
    anchorId = it.id;
  } else {
    selected = [it.id];
    anchorId = it.id;
  }
  if (!showSelection()) { selected = previous; anchorId = previousAnchor; lastClick = {id: null, at: 0}; }
  updateSelection();
}

// Removing deletes Cladia's copies; files outside Cladia are never touched
// The items leave the list at once; the server deletes the files meanwhile.
// Sequences of a list are renumbered after that, so until it's done the list's
// other sequences can't be clicked (busyLists).
const busyLists = new Set();

async function removeSelected() {
  if (!selected.length) return;
  const ids = [...selected];
  const what = ids.length === 1 ? itemById(ids[0])?.label : `${ids.length} items`;
  const keepsMembers = ids.some(id => itemById(id)?.kind === "item_list");
  if (!confirm(`Delete ${what} from Cladia?\n\nIt goes to the trash (Settings > Storage), where it can be restored ` +
               "or deleted for good. The original files you imported are not affected." +
               (keepsMembers ? " Items inside a mixed list stay in the library unless you select them too." : ""))) return;
  // their figures: along with them, or left (marked as missing their source until it is restored)
  const sources = ids.map(itemById).filter(Boolean).flatMap((it) => it.kind === "list" ? [it, ...childrenOf(it)] : [it]);
  const figures = [...new Set(sources.flatMap(figuresOf).map((f) => f.id))].filter((id) => !ids.includes(id));
  if (figures.length && confirm(`Also delete ${figures.length === 1 ? "its figure" : `its ${figures.length} figures`}?\n\n` +
      "OK: they go to the trash too. Cancel: they stay, and draw again if what they show is restored.")) ids.push(...figures);
  selected = [];
  if (!showSelection()) { selected = ids; return; }
  const gone = new Set(ids);
  const lists = new Set(items.filter((it) => gone.has(it.id) && it.parent && !gone.has(it.parent)).map((it) => it.parent));
  items = items.filter((it) => !gone.has(it.id) && !gone.has(it.parent));
  for (const id of lists) busyLists.add(id);
  renderList();
  toast(`Deleted ${what} (in the trash: Settings > Storage)`);
  try {
    await api("POST", "/api/library/remove", { ids });
  } catch (e) {
    toast(`Deleting ${what} failed: ${e.message}`, true);
  }
  for (const id of lists) busyLists.delete(id);
  await loadList();
}

// A sequence list becomes separate sequences
async function splitSelected() {
  const list = itemById(selected[0]);
  if (!list || !isLibraryList(list)) return;
  if (seqShown?.items[0]?.parent === list.id && hasUnsavedChanges()) {
    toast("Save the open sequence first (Ctrl+S)", true);
    return;
  }
  const { ids } = await api("POST", "/api/library/split", { ids: [list.id] });
  seqKey = null;
  await loadList();
  selected = ids;
  anchorId = ids[0];
  renderList();
  showSelection();
  toast(list.kind === "list" ? `${list.label} split into ${ids.length} sequences` : `Ungrouped ${list.label}`);
}

// Sequences (and lists) moved into one new list, as if imported from one file;
// its name is typed right after. ids: the selection, or what was just imported.
async function groupSelected(ids = [...selected], name = "New list", rename = true) {
  const shown = seqShown?.items || [];
  if (hasUnsavedChanges() && shown.some((x) => ids.includes(x.id) || ids.includes(x.parent))) {
    return toast("Save the open sequence first (Ctrl+S)", true);
  }
  try {
    const folder = await chooseResultFolder(ids.map(itemById));
    if (folder === undefined) return;
    const { id } = await api("POST", "/api/library/group", { ids, name, folder });
    seqKey = null;
    selected = [id];
    anchorId = id;
    lastClick = { id: null, at: 0 };
    expanded.add(id);
    await loadList();
    showSelection();
    if (rename) startRename(id);
  } catch (e) {
    toast(`Grouping failed: ${e.message}`, true);
    await loadList();
  }
}

// Taking members out preserves each source list's folder. Drag/drop uses
// moveToFolder instead, with an explicit destination.
function takesOut() {
  return draggedIds?.some((id) => listOwner(itemById(id)));
}

async function takeOutOfLists(ids) {
  const members = ids.filter((id) => listOwner(itemById(id)) && !listAncestors(itemById(id)).some(owner => ids.includes(owner)));
  if (!members.length) return;
  if (hasUnsavedChanges()) return toast("Save (or undo) your changes first (Ctrl+S)", true);
  try {
    const { ids: made } = await api("POST", "/api/library/take-out", { ids: members });
    seqKey = null;
    await loadList();
    selected = made;
    anchorId = made[0];
    showSelection();
    renderList();
    toast(`${made.length} item${made.length === 1 ? "" : "s"} taken out of ${made.length === 1 ? "its list" : "their lists"}`);
  } catch (e) {
    toast(e.message, true);
  }
}

// The server preserves whole items when a drop needs a mixed list.
async function dropIntoList(list, ids) {
  if (hasUnsavedChanges()) return toast("Save (or undo) your changes first (Ctrl+S)", true);
  try {
    const { added, id = list.id } = await api("POST", "/api/library/add-to-list", { id: list.id, ids });
    seqKey = null;
    expanded.add(id);
    if (id !== list.id) {
      selected = [id];
      anchorId = id;
    }
    await loadList();
    showSelection();
    if (added) toast(`${added} item${added === 1 ? "" : "s"} moved into ${list.label}`);
  } catch (e) {
    toast(e.message, true);
  }
}

// Every feature (but the source) taken off the selected sequences, saved at once
// A Sanger read trimmed again from its whole trace (sanger.trim): the error
// probability limit, as on import (0.05), stricter, looser, or 0 for all of it
async function trimRead(it) {
  if (seqShown?.items.some((x) => x.id === it.id) && hasUnsavedChanges()) {
    return toast("Save (or undo) your changes first (Ctrl+S)", true);
  }
  const number = (text) => Number(text.replace(",", "."));
  const answer = await askText({
    title: `Trim ${it.label} by quality`, label: "Error probability limit", value: "0.05", ok: "Trim",
    about: "Bases worse than this at the ends are cut. 0.05 is as on import (about quality 13), " +
      "0.01 stricter (quality 20), 0 keeps the whole read. The sequence is made again from the read's " +
      "base calls: edits and annotations made since are lost.",
    check: (text) => (text !== "" && number(text) >= 0 && number(text) <= 0.5 ? null : "A number from 0 to 0.5, e.g. 0.05"),
  });
  if (answer === null) return;
  const limit = number(answer);
  try {
    const r = await api("POST", `/api/trace/${it.id}/trim`, { limit });
    seqKey = null;
    await loadList();
    showSelection();
    toast(`${it.label}: bases ${r.start + 1}–${r.end} of ${r.length} kept`);
  } catch (e) {
    toast(e.message, true);
  }
}

// Linear sequences whose end repeats their start (an assembled circle): the repeat trimmed, marked circular
async function circularizeSelected(ids = selected.filter((id) => ["sequence", "list"].includes(itemById(id)?.kind))) {
  if (seqShown?.items.some((it) => ids.includes(it.id) || ids.includes(it.parent)) && hasUnsavedChanges()) {
    return toast("Save (or undo) your changes first (Ctrl+S)", true);
  }
  if (!confirm("Circularize the selected sequences whose end repeats their start (30 bases or more)?\n\n" +
      "The repeated end is trimmed and the sequence marked circular. Annotations across the cut are dropped. " +
      "Saved at once; the earlier version stays under Metadata > Saved versions.")) return;
  try {
    const r = await api("POST", "/api/library/circularize", { ids });
    seqKey = null;
    await loadList();
    showSelection();
    const dropped = r.done.reduce((n, d) => n + d.dropped, 0);
    toast(`${r.done.length} circularized` + (r.done.length ? ` (${r.done.map((d) => `${d.label}: ${d.trimmed} bp`).slice(0, 4).join(", ")}${r.done.length > 4 ? "…" : ""})` : "") +
      `; ${r.none} without an overlap` + (r.circular ? `, ${r.circular} already circular` : "") + (dropped ? `; ${dropped} annotations dropped` : ""));
  } catch (e) {
    toast(e.message, true);
  }
}

// ── Metadata from a table ────────────────────────────────────────────────
// CSV, TSV or Excel: the user says which column holds the sequences' names and which column
// goes to each metadata field; the server matches the rows to the sequences (meta.token: the
// table kept there while this works) and says what would change before anything is written.
const meta = { ids: [], token: null, columns: [], timer: 0 };
// Which column a field is guessed to be, by its header (spaces for _ - .)
const META_GUESS = {
  description: /^(description|definition|title)$/, organism: /organism|species|taxon/, accession: /accession|genbank/,
  strain: /^strain/, isolate: /^isolate/, country: /country|geo loc|^location/,
  collection_date: /collect\w* date|^date$/, host: /^host/, organelle: /organelle/, specimen_voucher: /voucher/,
  isolation_source: /isolation source|habitat/, lat_lon: /lat.*lon|coordinates/, collected_by: /collect\w* by|collector/,
  identified_by: /identif/, culture_collection: /culture collection/, bio_material: /bio material|biological material/,
};

function openMetaImport(ids = selected.filter((id) => ["sequence", "list"].includes(itemById(id)?.kind))) {
  if (seqShown?.items.some((it) => ids.includes(it.id) || ids.includes(it.parent)) && hasUnsavedChanges()) {
    return toast("Save (or undo) your changes first (Ctrl+S)", true);
  }
  const count = ids.reduce((n, id) => n + (itemById(id).kind === "list" ? childrenOf(itemById(id)).length : 1), 0);
  Object.assign(meta, { ids, token: null, columns: [] });
  $("meta-what").textContent = `For ${count.toLocaleString()} selected sequence${count === 1 ? "" : "s"}: a table with one row per sequence, ` +
    "its names in one column, and what to import in the others (CSV, TSV or Excel .xlsx).";
  $("meta-file").textContent = "";
  $("meta-body").hidden = true;
  $("meta-run").disabled = true;
  $("meta-dialog").querySelector(".error").hidden = true;
  $("meta-dialog").showModal();
}

async function metaFileChosen(file) {
  const error = $("meta-dialog").querySelector(".error");
  error.hidden = true;
  try {
    const read = await api("POST", "/api/metadata/table", { filename: file.name, ids: meta.ids, data: await base64Of(file) });
    Object.assign(meta, { token: read.token, columns: read.columns, labels: Object.fromEntries(read.fields.map((f) => [f.key, f.label])) });
    $("meta-file").textContent = `${file.name}: ${read.count.toLocaleString()} rows, ${read.columns.length} columns`;
    const options = read.columns.map((c, i) => `<option value="${i}">${esc(c)}</option>`).join("");
    const best = Math.max(...read.matches);
    const byHeader = read.columns.findIndex((c) => /name|sample|^id$|accession|voucher|code/i.test(c));
    $("meta-name").innerHTML = options;
    $("meta-name").value = String(best > 0 ? read.matches.indexOf(best) : Math.max(0, byHeader));
    $("meta-map").innerHTML = read.fields.map((f) =>
      `<label><span>${esc(f.label)}</span><select data-field="${f.key}"><option value="">(not imported)</option>${options}</select></label>`).join("");
    const claimed = new Set([Number($("meta-name").value)]);
    for (const select of $("meta-map").querySelectorAll("select")) {  // columns guessed from their headers
      const found = read.columns.findIndex((c, i) => !claimed.has(i) && META_GUESS[select.dataset.field]?.test(c.toLowerCase().replace(/[_.\-]+/g, " ")));
      if (found >= 0) { select.value = String(found); claimed.add(found); }
    }
    $("meta-body").hidden = false;
    previewMeta();
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  }
}

function metaRequest(apply) {
  const mapping = {};
  for (const select of $("meta-map").querySelectorAll("select")) if (select.value !== "") mapping[select.dataset.field] = Number(select.value);
  return { ids: meta.ids, token: meta.token, name_column: Number($("meta-name").value), mapping, clear_empty: $("meta-clear").checked, apply,
    ...(apply ? { expected_hash: meta.previewHash } : {}) };
}

// What would happen, shown under the choices (a moment after the last change)
function previewMeta() {
  clearTimeout(meta.timer);
  meta.previewHash = null;
  $("meta-run").disabled = true;
  const generation = meta.generation = (meta.generation || 0) + 1;
  meta.timer = setTimeout(async () => {
    const box = $("meta-preview"), request = metaRequest(false);
    $("meta-run").disabled = true;
    if (!Object.keys(request.mapping).length) return (box.textContent = "Choose the column of each field to import.");
    try {
      const r = await api("POST", "/api/library/import-metadata", request);
      if (generation !== meta.generation) return;
      meta.previewHash = r.preview_hash;
      const total = r.matched + r.unmatched_count + r.ambiguous_count, plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
      const lines = [`<b>${r.matched.toLocaleString()} of ${plural(total, "sequence")}</b> found in the table.`];
      if (r.unmatched_count) lines.push(`Not in the table: ${r.unmatched.slice(0, 8).map(esc).join(", ")}${r.unmatched_count > 8 ? "…" : ""}`);
      if (r.unused.length) lines.push(`Table rows that match no sequence: ${r.unused.slice(0, 8).map(esc).join(", ")}${r.unused.length > 8 ? "…" : ""}`);
      if (r.duplicates.length) lines.push(`Repeated table names or accession aliases: ${r.duplicates.slice(0, 5).map(esc).join(", ")}`);
      if (r.ambiguous_count) lines.push(`<span class="error">More than one row matches: ${r.ambiguous.slice(0, 5).map(esc).join(", ")}. Make the names unique before importing.</span>`);
      lines.push(`${plural(r.changed, "sequence")} would change (${plural(r.values, "value")}).`);
      for (const s of r.sample) {
        lines.push(`${esc(s.label)}: ` + s.changes.map(([f, was, v]) => `${esc(meta.labels[f] || f)} ${was ? `${esc(was)} → ` : "= "}${esc(v) || "(empty)"}`).join("; "));
      }
      if (r.matched && !r.sample.length) lines.push("Nothing would change: the sequences already have these values.");
      box.innerHTML = lines.join("<br>");
      $("meta-run").disabled = !r.changed || !!r.ambiguous_count;
    } catch (e) {
      box.textContent = e.message;
    }
  }, 300);
}

async function runMetaImport() {
  const error = $("meta-dialog").querySelector(".error");
  if (!meta.previewHash) return;
  $("meta-run").disabled = true;
  try {
    const r = await api("POST", "/api/library/import-metadata", metaRequest(true));
    $("meta-dialog").close();
    seqKey = null;
    await loadList();
    showSelection();
    toast(`Metadata imported: ${r.values.toLocaleString()} value${r.values === 1 ? "" : "s"} on ${r.changed.toLocaleString()} sequence${r.changed === 1 ? "" : "s"}` +
      (r.unmatched_count ? `; ${r.unmatched_count.toLocaleString()} not in the table` : ""));
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
    previewMeta();
  }
}

// A list of contigs (MEGAHIT's, say) as an assembly graph: links where their ends overlap
async function makeContigGraph() {
  const ids = selected.filter((id) => ["sequence", "list"].includes(itemById(id)?.kind));
  const first = itemById(ids[0]);
  const name = await askText({ title: "Make an assembly graph from contigs", label: "Name of the graph",
    value: `${first.label} graph`, ok: "Make graph",
    about: "Links are made where contig ends overlap by 30 bases or more." });
  if (!name) return;
  try {
    const folder = await chooseResultFolder(ids.map(itemById));
    if (folder === undefined) return;
    const r = await api("POST", "/api/graph/from-contigs", { ids, name, folder });
    await loadList();
    toast(`${r.nodes} contigs, ${r.links} link${r.links === 1 ? "" : "s"}${r.loops ? ` (${r.loops} closing on themselves: circular)` : ""} - "${name}" is in the list`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function removeAnnotations(ids = selected.filter((id) => ["sequence", "list"].includes(itemById(id)?.kind))) {
  const what = ids.length === 1 ? itemById(ids[0]).label : `${ids.length} items`;
  if (seqShown?.items.some((it) => ids.includes(it.id) || ids.includes(it.parent)) && hasUnsavedChanges()) {
    return toast("Save (or undo) your changes first (Ctrl+S)", true);
  }
  if (!confirm(`Remove all annotations (features) from ${what}?\n\nThis is saved at once and can't be undone.`)) return;
  try {
    const { removed } = await api("POST", "/api/library/remove-annotations", { ids });
    seqKey = null;
    await loadList();
    showSelection();
    toast(`${removed} feature${removed === 1 ? "" : "s"} removed from ${what}`);
  } catch (e) {
    toast(e.message, true);
  }
}

// A list's sequences or an alignment's rows reordered (in the file, so tools and
// Export follow it): Sort ▾ in the viewer's bar. The current order is ticked,
// with its direction; choosing it again reverses it.
const SORTS = [  // key, title, [ascending, descending]
  ["name", "Name", ["A to Z", "Z to A"]], ["length", "Length", ["shortest first", "longest first"]],
  ["organism", "Organism", ["A to Z", "Z to A"]], ["description", "Description", ["A to Z", "Z to A"]],
];
const SORT_DESCENDING = { length: true };  // the first way of each (as library.SORT_KEYS)

function openSortMenu(e) {
  const item = seqShown?.list || (seqShown?.mode === "alignment" && seqShown.alignment);
  if (!item) return;
  const now = itemById(item.id)?.sorted;
  const rows = seqView?.rows || [];
  const entries = SORTS.filter(([key]) => key === "name" || key === "length" || rows.some((r) => r[key])).map(([key, title, ways]) => {
    const current = now?.key === key;
    const descending = current ? !now.descending : !!SORT_DESCENDING[key];  // again: the other way
    const label = current ? `✓ ${title} (${ways[now.descending ? 1 : 0]})` : `  ${title} (${ways[descending ? 1 : 0]})`;
    return [label, () => sortList(item, key, descending)];
  });
  if (seqShown.mode === "alignment") {
    const s = seqView.selection;
    const picked = s && !s.consensus ? seqView.selectedRows(s) : [];
    const reference = picked.length === 1 ? picked[0] : !s && seqView.caret !== null ? seqView.caretRow : null;
    const row = Number.isInteger(reference) ? rows[reference] : null;
    const current = now?.key === "distance" && now.reference === row?.name;
    const descending = current ? !now.descending : false;
    entries.push("-", [row ? `${current ? "✓ " : ""}Distance to ${row.name} (${current ? (now.descending ? "farthest" : "closest") : "closest"} first)`
      : "Distance to selected sequence (select one row first)",
      () => sortList(item, "distance", descending, reference), !row],
      ["Distance: mismatch proportion; gaps and ambiguous sites ignored", null, true]);
  }
  entries.push("-", [now ? "Choose the ticked one again to reverse it" : "The order is saved: tools and Export follow it", null, true]);
  const box = e.currentTarget.getBoundingClientRect();
  showMenu({ clientX: box.left, clientY: box.bottom + 2 }, entries);
}

async function sortList(list, key, descending, reference = null) {
  if (hasUnsavedChanges()) return toast("Save (or undo) your changes first (Ctrl+S)", true);
  try {
    await api("POST", "/api/library/sort", { id: list.id, key, descending, reference });
  } catch (e) {
    return toast(e.message, true);
  }
  seqKey = null;
  expanded.add(list.id);
  await loadList();
  if (key === "distance") {
    await showAlignment(itemById(list.id));
    const row = seqView.rows.findIndex(r => r.name === itemById(list.id).sorted.reference);
    if (row >= 0) seqView.select(0, seqView.length - 1, row, row, false, false, null, null, true);
  } else showSelection();
}

// ── Renaming (F2, double-click or Rename): an input in place of the name ──
// A sequence's record is renamed too (library.rename), so its file changes:
// not while it has unsaved changes.
let renaming = null;  // the item being renamed

function startRename(id = selected[0]) {
  const it = itemById(id);
  if (!it || it.missing) return;
  const open = seqShown?.mode === "single" && seqShown.items[0].id === it.id;
  if (it.kind === "sequence" && open && hasUnsavedChanges()) return toast("Save your changes first (Ctrl+S)", true);
  renaming = it.id;
  renderList();
}

async function finishRename(it, name) {
  if (renaming !== it.id) return;  // already done (Enter, then the input's blur)
  renaming = null;
  const old = it.parent ? it.name : it.label;
  if (name.trim() && name.trim() !== old) {
    try {
      await api("POST", "/api/library/rename", { id: it.id, name: name.trim() });
      await loadList();
      const shown = seqShown?.items.findIndex((x) => x.id === it.id || x.id === it.parent);
      if (it.kind === "sequence" && shown >= 0) {  // its file changed: show it again
        seqKey = null;
        showSelection();
      } else if (shown >= 0) {
        seqShown.items[shown] = itemById(seqShown.items[shown].id);
        renderEditButtons();
      }
      if (it.kind === "item_list" && shownIds.includes(it.id)) showSelection(shownIds);
      toast(`Renamed to ${name.trim()}`);
    } catch (e) {
      toast(`Rename failed: ${e.message}`, true);
    }
  }
  renderList();
}

function renameInput(it) {
  const input = document.createElement("input"); input.autocomplete = "off";
  input.className = "rename";
  input.value = it.parent ? it.name : it.label;
  input.onclick = (e) => e.stopPropagation();
  input.ondblclick = (e) => e.stopPropagation();
  input.onkeydown = (e) => {
    e.stopPropagation();  // not the list's Delete / F2
    if (e.key === "Enter") { finishRename(it, input.value); $("lists").focus(); }
    if (e.key === "Escape") { renaming = null; renderList(); $("lists").focus(); }
  };
  input.onblur = () => finishRename(it, input.value);
  setTimeout(() => { input.focus(); input.select(); });
  return input;
}

// ── Right-click menu on the list ─────────────────────────────────────────
// On an item: what can be done with the selection (the item becomes the
// selection first if it wasn't in it). On empty space: paste and import.
function openListMenu(e, it) {
  e.preventDefault();
  if (viewIds) return;
  if (it && !selected.includes(it.id)) {
    const previous = [...selected], previousAnchor = anchorId;
    selected = [it.id];
    anchorId = it.id;
    if (!showSelection()) { selected = previous; anchorId = previousAnchor; return; }
    hideCard();
    updateSelection();
  }
  const chosen = selected.map(itemById).filter(Boolean);
  const one = chosen.length === 1 ? chosen[0] : null;
  const groupable = chosen.length > 1 && chosen.every((x) => !x.missing);
  const hasItemList = chosen.some((it) => it.kind === "item_list");
  const sequences = chosen.every((x) => ["sequence", "list"].includes(x.kind));
  const contigCount = chosen.reduce((n, x) => n + (x.kind === "list" ? childrenOf(x).length : 1), 0);  // (a graph needs 2 or more)
  const entries = it && chosen.length ? [
    ["Open in new window", openInNewWindow],
    "-",  // organise
    ["Rename", () => startRename(), !one || one.missing, null, "F2"],
    ["Duplicate", duplicateSelected, hasItemList, null, "Ctrl+D"],
    ["Move to folder…", () => openFolderMenu(e, selected)],
    groupable && ["Group into a list", () => groupSelected()],
    one?.kind === "list" && ["Split into sequences", splitSelected],
    one?.kind === "item_list" && ["Ungroup items", splitSelected],
    chosen.some(listOwner) && ["Take out of list", () => takeOutOfLists(selected)],
    "-",  // what's in them
    one?.kind === "sequence" && ["Import annotations or a data track…", () => pickAnnotations(one)],
    sequences && ["Remove annotations…", removeAnnotations],
    sequences && ["Import metadata from a table…", openMetaImport],
    sequences && ["Circularize from overlap…", circularizeSelected],
    sequences && contigCount > 1 && ["Make assembly graph from contigs…", makeContigGraph],
    one?.trace && ["Trim ends by quality…", () => trimRead(one)],
    one?.kind === "mapping" && ["Consensus as new sequence", () => consensusAsSequence(one.id)],
    one?.kind === "reads" && one.read_files?.length && ["Delete the read files (keep the report)…", () => dropReadFiles(one)],
    "-",  // out of Cladia
    !chosen.some((x) => ["blast", "primers", "reads", "item_list"].includes(x.kind)) && ["Export…", openExport],
    one?.original && !one.parent && ["Export to original location…", () => writeBack(one), !one.original.format],
    one?.original && ["Show original in Explorer", () => revealOriginal(one)],
    "-",
    ["Paste as new sequence", pasteFromMenu, false, null, "Ctrl+V"],
    "-",
    ["Delete", removeSelected, false, "danger", "Del"],
  ] : [
    ["Paste as new sequence", pasteFromMenu, false, null, "Ctrl+V"],
    ["Import…", addFilesClicked],
  ];
  showMenu(e, entries);
}

// ── Metadata: the side panel's tab for the one item selected ──────────────
let metadataItem = null, metadataInitial = null, metadataRequest = 0, metadataHistory = [], metadataFuture = [];
let metadataStamp = "";  // the sequences' versions when the form was last edited: Undo goes to whichever was edited last
const metadataFields = ["description", "organism", "accession", "strain", "isolate", "country", "collection_date", "host", "organelle"];
function metadataDirty() {
  const form = $("metadata-form");
  return !!metadataInitial && metadataFields.some((key) => form.elements[key].value !== (metadataInitial[key] || ""));
}
const docsStamp = () => shownDocs().map((d) => d.version).join(",");
// The Metadata tab is showing (the side panel is open on it)
const metadataShown = () => sideTabNow() === "metadata";
// The form was edited after the last edit of the sequence: Undo and Redo act on it
const metadataLast = () => metadataItem?.kind === "sequence" && (metadataHistory.length > 0 || metadataFuture.length > 0)
  && metadataStamp === docsStamp();
function clearMetadata() {
  metadataItem = null; metadataInitial = null; metadataRequest++;
  metadataHistory = []; metadataFuture = [];
}
function metadataFact(container, label, value) {
  const row = document.createElement("div"); row.className = "metadata-fact";
  row.dataset.fact = label;
  const strong = document.createElement("strong"); strong.textContent = `${label}: `;
  row.append(strong, document.createTextNode(value || "Not recorded")); container.append(row);
}
function metadataEditState(editing) {
  const form = $("metadata-form");
  for (const key of metadataFields) form.elements[key].disabled = !editing;
}
async function openMetadataTab(item) {
  if (metadataItem?.id === item.id) { metadataEditState(editable() && !viewIds); renderEditButtons(); return; }
  metadataItem = item;
  const request = ++metadataRequest;
  $("metadata-title").textContent = item.label;
  $("metadata-figure-history").hidden = !(["tree", "comparison"].includes(item.kind) || item.kind === "sequence" && !item.parent && !item.trace && !item.protein);
  const details = $("metadata-details"), provenance = $("metadata-provenance"), form = $("metadata-form");
  details.replaceChildren(); provenance.replaceChildren(); form.hidden = true;
  $("metadata-record").replaceChildren();
  delete $("mapping-summary").dataset.of;
  renderMappingSummary();  // a mapping's numbers, gaps and differences (once its data is loaded)
  metadataFact(details, "Type", item.kind);
  metadataFact(details, "Folder", item.folder || "Library");
  if (item.original?.path) metadataFact(details, "Original file", item.original.path);
  const p = item.provenance;
  $("metadata-log").hidden = !p?.job_id;
  if (p) {
    metadataFact(provenance, "Job", p.title); metadataFact(provenance, "Status", p.status);
    metadataFact(provenance, "Completed", p.finished ? new Date(p.finished * 1000).toLocaleString() : "");
    metadataFact(provenance, "Inputs", (p.inputs || []).map((x) => x.label).join(", "));
    metadataFact(provenance, "Job ID", p.job_id);
    metadataFact(provenance, "Parameters", p.parameters ? JSON.stringify(p.parameters) : "Not recorded");
    metadataFact(provenance, "Tool versions", p.versions || "Not recorded");
    metadataFact(provenance, "Commands attempted", p.commands?.join("; ") || "Not recorded");
    metadataFact(provenance, "Citations", p.citations?.map(c => `${c.tool}: ${c.reference} — ${c.url}`).join("; ") || "Not recorded");
    $("metadata-log").onclick = async () => { try { await openLog(p.job_id); } catch (e) { toast("The job log is no longer available", true); } };
  } else metadataFact(provenance, "Job information", "Not recorded");
  if (item.kind !== "sequence") {
    try {
      const info = await api("GET", `/api/info/${item.id}`);
      if (request !== metadataRequest) return;
      for (const [key, value] of Object.entries(info)) {
        if (key === "kind" || value === null || value === undefined || typeof value === "object") continue;
        metadataFact(details, key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()), String(value));
      }
    } catch (e) { if (request === metadataRequest) metadataFact(details, "Details", e.message); }
    return;
  }
  try {
    const data = await api("GET", `/api/library/${item.id}/metadata`);
    if (request !== metadataRequest) return;
    metadataInitial = data; form.hidden = false;
    for (const key of metadataFields) form.elements[key].value = data[key] || "";
    const record = $("metadata-record");
    const facts = await api("GET", `/api/info/${item.id}`);
    if (request !== metadataRequest) return;
    if (facts.gc != null) metadataFact(record, "GC content", `${(facts.gc * 100).toFixed(1)} %`);
    if (data.taxonomy?.length) metadataFact(record, "Taxonomy", data.taxonomy.join("; "));
    if (data.date) metadataFact(record, "Record date", data.date);
    for (const [key, values] of Object.entries(data.source_qualifiers || {})) {
      if (["organism", "strain", "isolate", "country", "geo_loc_name", "collection_date", "host", "organelle"].includes(key)) continue;
      metadataFact(record, `Source /${key}`, values.join("; "));
    }
    for (const ref of data.references || []) metadataFact(record, "Reference", [ref.authors, ref.title, ref.journal, ref.pubmed_id ? `PubMed ${ref.pubmed_id}` : ""].filter(Boolean).join(" · "));
    form.querySelector(".error").hidden = true; metadataHistory = []; metadataFuture = [];
    metadataEditState(editable() && !viewIds); renderEditButtons();
  } catch (e) { if (request === metadataRequest) metadataFact(details, "Metadata", e.message); }
}
// Metadata belongs to the open viewer, independently of sidebar navigation.
function showMetadata() {
  const item = shownIds.length === 1 ? itemById(shownIds[0]) : null;
  const none = !item || item.missing;
  $("metadata-empty").hidden = !none;
  $("metadataview").querySelector(".metadata-content").hidden = none;
  if (none) clearMetadata(); else openMetadataTab(item);
}
async function restoreSavedVersion(item, index) {
  if (hasUnsavedChanges()) return toast("Save or undo unsaved changes first", true);
  try {
    await api("POST", `/api/library/${item.id}/history`, { index });
    clearMetadata(); seqKey = treeKey = null;
    await loadList(); showSelection(shownIds);
    toast("Restored saved version");
    return true;
  } catch (e) { toast(`Couldn't restore: ${e.message}`, true); return false; }
}
async function savedUndo(redo) {
  if (hasUnsavedChanges()) return toast("Save or undo unsaved changes first", true);
  const item = shownIds.length === 1 && itemById(shownIds[0]);
  if (!item) return;
  try {
    const data = await api("GET", `/api/library/${item.id}/history`);
    const index = data.current + (redo ? 1 : -1);
    if (index < 0 || index >= data.versions.length) return toast("No saved version in that direction");
    await restoreSavedVersion(item, index);
  } catch (e) { toast(e.message, true); }
}
async function showSavedHistory(figure = false) {
  const item = metadataItem; if (!item) return;
  try {
    if (figure && figureEditor) await figureEditor.flush();
    const url = figure ? `/api/figure/${item.id}/history` : `/api/library/${item.id}/history`;
    const data = await api("GET", url);
    $("history-note").textContent = figure ? "These versions contain figure settings, separate from the underlying sequence, tree or comparison."
      : data.whole_list ? "These versions cover the entire sequence list, not just this record." : "Choose a saved state to restore.";
    const box = $("history-versions"); box.replaceChildren();
    if (!data.versions.length) box.textContent = "No saved versions yet.";
    for (const version of [...data.versions].reverse()) {
      const row = document.createElement("p");
      row.append(document.createTextNode(new Date(version.time * 1000).toLocaleString() + (version.index === data.current ? " · Current" : " ")));
      if (version.index !== data.current) {
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "Restore";
        button.onclick = async () => {
          if (!confirm("Restore this saved version?")) return;
          if (!figure) { if (await restoreSavedVersion(item, version.index)) $("history-dialog").close(); return; }
          try {
            if (figureEditor) await figureEditor.flush();
            await api("POST", url, { index: version.index, expected_hash: data.versions[data.current].hash,
              target_hash: version.hash, expected_current: data.current, expected_count: data.versions.length });
            $("history-dialog").close();
            clearMetadata(); figureShown = null;
            await loadList(); showSelection(shownIds);
            toast("Restored figure settings");
          } catch (e) { toast(`Couldn't restore the figure: ${e.message}`, true); }
        };
        row.append(button);
      }
      box.append(row);
    }
    $("history-dialog").showModal();
  } catch (e) { toast(e.message, true); }
}
$("metadata-history").onclick = () => showSavedHistory();
$("metadata-figure-history").onclick = () => showSavedHistory(true);
const metadataValues = () => Object.fromEntries(metadataFields.map((key) => [key, $("metadata-form").elements[key].value]));
$("metadata-form").addEventListener("beforeinput", () => { if (editable()) { metadataHistory.push(metadataValues()); metadataStamp = docsStamp(); } });
$("metadata-form").addEventListener("input", () => { metadataFuture = []; renderEditButtons(); });
$("metadata-form").onsubmit = (e) => { e.preventDefault(); saveShown(); };
$("metadata-form").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveShown(); } });
$("metadataview").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && ["z", "y"].includes(e.key.toLowerCase()) &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) {
    e.preventDefault(); undoRedo(e.key.toLowerCase() === "y" || e.shiftKey);
  }
});

// A menu at the mouse: entries are [label, action, disabled, class] or "-"
// entries: [label, action, disabled, class, key] (key: its shortcut, shown at the
// right), "-" between groups, { heading } over a group; null/false are left out.
// Separators that end up doubled or at an end (their group had nothing) go.
function showMenu(e, entries) {
  const menu = $("list-menu");
  menu.innerHTML = "";
  const kept = [];
  for (const entry of entries.filter(Boolean)) {
    if (entry === "-" && (!kept.length || kept[kept.length - 1] === "-")) continue;
    kept.push(entry);
  }
  while (kept[kept.length - 1] === "-" || kept[kept.length - 1]?.heading) kept.pop();
  for (const entry of kept) {
    if (entry === "-") { menu.append(document.createElement("hr")); continue; }
    if (entry.heading) {
      const heading = document.createElement("div");
      heading.className = "menu-heading";
      heading.textContent = entry.heading;
      menu.append(heading);
      continue;
    }
    const [label, action, disabled, cls, key] = entry;
    const button = document.createElement("button");
    button.textContent = label;
    if (key) {
      const shortcut = document.createElement("span");
      shortcut.className = "key";
      shortcut.textContent = key;
      button.append(shortcut);
    }
    button.disabled = !!disabled || !action;
    if (cls) button.className = cls;
    button.onclick = () => { closeListMenu(); action(); };
    menu.append(button);
  }
  menu.hidden = false;
  const { innerWidth: w, innerHeight: h } = window;
  menu.style.left = `${Math.min(e.clientX, w - menu.offsetWidth - 4)}px`;
  menu.style.top = `${Math.min(e.clientY, h - menu.offsetHeight - 4)}px`;
}

function closeListMenu() {
  $("list-menu").hidden = true;
}

// The file an item was imported from (Cladia works on its copy): shown in
// Explorer, or replaced by the item (in the file's own format)
async function revealOriginal(it) {
  try { await api("POST", `/api/library/${it.id}/reveal`); } catch (e) { toast(e.message, true); }
}

async function writeBack(it) {
  const { path, format } = it.original;
  const lost = format === "fasta" && it.kind !== "alignment" ? "\n\nIt is a FASTA file: annotations aren't kept in it." : "";
  const unsaved = hasUnsavedChanges() ? "\n\nUnsaved changes aren't included (save first)." : "";
  if (!confirm(`Replace the original file with Cladia's ${it.label}?\n\n${path}${lost}${unsaved}`)) return;
  try {
    await api("POST", `/api/library/${it.id}/write-back`);
    toast(`Written to ${path}`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function duplicateSelected() {
  const ids = [...selected];
  if (!ids.length) return;
  if (ids.some(id => itemById(id)?.kind === "item_list")) return toast("Select the items inside the list to duplicate them", true);
  const { ids: made } = await api("POST", "/api/library/duplicate", { ids });
  await loadList();
  const unsaved = hasUnsavedChanges() && seqShown?.items.some((it) => ids.includes(it.id)) ? " (as last saved)" : "";
  toast((made.length === 1 ? `Made ${itemById(made[0])?.label}` : `Duplicated ${made.length} items`) + unsaved);
}

// ── Pasting into the list: new items ─────────────────────────────────────
// What Cladia copied last (Copy / Ctrl+C in the viewer) is pasted with what the
// text alone doesn't carry: a region of a sequence becomes a new sequence with
// the features that lie inside it (and the source's organism, organelle...), a
// block of an alignment a new alignment. Any other text is imported as FASTA
// (bases without a header become one sequence).
let lastCopy = null;  // { text, make: async () => new item ids }

// The features of `data` (editor JSON) that lie inside start..end, moved to the region
function sliceRegion(data, start, end) {
  const features = [];
  for (const f of data.features || []) {
    const parts = SeqEdit.partsOf(f);
    if (parts.some((p) => p.start > p.end)) continue;  // across the origin
    if (Math.min(...parts.map((p) => p.start)) < start || Math.max(...parts.map((p) => p.end)) > end) continue;
    const { id, ...rest } = f;  // a new feature for the new file
    features.push(SeqEdit.withParts(rest, parts.map((p) => ({ start: p.start - start, end: p.end - start }))));
  }
  return { sequence: data.sequence.slice(start, end + 1), features };
}

// How to paste the current viewer selection as new items (null: as text only)
function copyMaker() {
  const s = seqView.selection;
  const named = (name) => async () =>  // bases copied without a name: pasted as a sequence named so
    (await api("POST", "/api/library/upload", { filename: "Pasted.fasta", paste: true, text: `>${name}\n${lastCopy.text}\n`,
                                                folder: seqShown.items[0]?.folder || null })).ids;  // where it came from
  if (s?.consensus) return named("Consensus");
  if (s?.parts) {  // a feature's parts: their bases joined (e.g. a CDS's exons), named after it
    const row = seqView.rows[s.r0];
    const f = (row.features || []).find((x) => x.c0 === s.c0 && x.c1 === s.c1 && x.parts.length === s.parts.length);
    return named(`${row.name} ${f?.name || `${s.c0 + 1}-${s.c1 + 1}`} (joined parts)`);
  }
  if (seqShown.mode === "alignment" && s.r0 === s.r1) {  // a row: its name, with the columns if only part of it
    const r = seqView.rows[s.r0];
    return named(s.c0 <= r.first && s.c1 >= r.last ? r.name : `${r.name}_${s.c0 + 1}-${s.c1 + 1}`);
  }
  if (seqShown.mode === "alignment") {
    if (s.wholeRows) {
      const item = seqShown.alignment.id;
      const rows = seqView.selectedRows(s).map((i) => ({ ...seqShown.alignDoc.rows()[i] }));
      return async () => (await api("POST", `/api/alignment/${item}/copy-rows`, { rows })).ids;
    }
    if (seqShown.alignDoc?.dirty) return null;  // paste the copied text, not stale saved rows
    if (s.rows) return null;  // rows picked apart: FASTA text
    const aln = seqShown.alignment;
    return async () => [(await api("POST", "/api/library/extract", { id: aln.id, start: s.c0, end: s.c1, rows: [s.r0, s.r1] })).id];
  }
  // one sequence each selected row, with its features (several rows were pasted as bare FASTA: the user's report);
  // several rows: one new list, "<list>_extract" (or "<first row>_extract"), one undo step
  const rows = seqView.selectedRows(s);
  const regions = rows.map((r) => regionOf(s, r));
  if (regions.some((x) => x === null)) return null;  // e.g. a comparison's genomes: FASTA text
  const sequences = regions.filter(Boolean);
  if (!sequences.length) return async () => [];
  const first = seqShown.items[rows[0]] || seqShown.items[0];
  const parent = first?.parent && itemById(first.parent);
  const listName = sequences.length > 1 ? `${parent ? parent.name : sequences[0].name}_extract` : null;
  return async () => {
    const inputs = sequences.map(s => itemById(s.source)).filter(Boolean);
    const folder = listName ? await chooseResultFolder(inputs) : null;
    if (listName && folder === undefined) return [];
    return (await api("POST", "/api/library/new-sequences", { sequences, list_name: listName,
      ...(listName ? {folder} : {}) })).ids;
  };
}

// Row r's part of the selection as a new sequence ({name, data, source}), or
// undefined when the row is shorter than where the selection starts (null: the text only)
function regionOf(s, r) {
  const source = seqShown.mapping
    ? { data: seqShown.mapping.reference, item: itemById(seqShown.mapping.reference_item) || seqShown.items[0] }
    : seqShown.mode === "single"
    ? { data: { ...seqShown.doc.data, sequence: seqShown.doc.sequence, features: seqShown.doc.features, geneticCode: seqShown.fileCode },
        item: seqShown.items[0] }
    : seqShown.docs?.[r] && { data: { ...seqShown.docs[r].data, sequence: seqShown.docs[r].sequence,
                                      features: seqShown.docs[r].features }, item: seqShown.items[r] };
  if (!source) return null;
  const end = Math.min(s.c1, source.data.sequence.length - 1);
  if (end < s.c0) return undefined;
  const region = s.wraps  // across the origin: the sequence turned to start at c0, then cut
    ? sliceRegion(SeqEdit.rotate(source.data, s.c0), 0, seqView.selectionLength(s) - 1)
    : sliceRegion(source.data, s.c0, end);
  const it = source.item;
  const whole = s.wraps ? seqView.selectionLength(s) >= source.data.sequence.length
    : s.c0 === 0 && end === source.data.sequence.length - 1;
  const name = (it.parent ? it.name : it.label) + (whole ? "" : ` ${s.c0 + 1}-${end + 1}`);  // a part: its range
  const geneticCode = codeChoices[it.id] || source.data.geneticCode;
  const from = it.kind === "sequence" ? it.id : null;  // metadata from the source sequence (not a mapping)
  return { name, data: { ...region, geneticCode }, source: from };
}

async function pasteIntoList(text) {
  if (viewIds) return;
  let ids = [], errors = [];
  try {
    const plain = (t) => (t || "").replace(/\r\n/g, "\n").trim();  // Windows' clipboard gives lines back with \r\n
    if (lastCopy?.make && plain(text) === plain(lastCopy.text)) {
      ids = await lastCopy.make();
    } else {
      let fasta = (text || "").trim();
      if (!fasta.startsWith(">")) {
        const bases = SeqEdit.cleanBases(fasta) || SeqEdit.cleanBases(fasta, true);  // DNA, or a protein
        if (!bases) return toast("Nothing to paste: copy DNA bases or FASTA first", true);
        fasta = `>Pasted_sequence\n${bases}`;
      }
      ({ ids, errors } = await api("POST", "/api/library/upload", { filename: "Pasted.fasta", paste: true, text: fasta + "\n",
                                                                    folder: currentFolder }));  // the folder shown
    }
  } catch (e) {
    return toast(`Paste failed: ${e.message}`, true);
  }
  if (errors.length) toast(errors.join("\n"), true);
  if (!ids.length) return;
  await loadList();
  toast(`${ids.length === 1 ? `New: ${itemById(ids[0])?.label}` : `${ids.length} new items`} (Ctrl+Z in the list undoes it)`);
  const previous = [...selected];
  selected = [ids[0]];
  anchorId = ids[0];
  if (!showSelection()) selected = previous;
  updateSelection();
}

// The menu's Paste: the clipboard if the browser lets us read it, else what Cladia copied
async function pasteFromMenu() {
  try {
    return pasteIntoList(await navigator.clipboard.readText());
  } catch (e) {
    if (lastCopy) return pasteIntoList(lastCopy.text);
    toast("Press Ctrl+V in the list to paste", true);
  }
}

// ── Hover cards in the list ──────────────────────────────────────────────
// After a short pause on an item: what is in it (a sequence's organism and
// description, a list's or alignment's size and organisms...), from /api/info.
const infoCache = new Map();  // "id:mtime" -> info
let cardTimer = null;

function cardFor(li, it) {
  li.onmouseenter = () => {
    clearTimeout(cardTimer);
    cardTimer = setTimeout(() => showCard(li, it), 450);
  };
  li.onmouseleave = hideCard;
}

function hideCard() {
  clearTimeout(cardTimer);
  $("hover-card").hidden = true;
}

async function showCard(li, it) {
  if (it.missing || renaming === it.id) return;
  const key = `${it.id}:${it.mtime}:${it.kind === "item_list" ? it.count : ""}`;
  if (!infoCache.has(key)) {
    try { infoCache.set(key, await api("GET", `/api/info/${it.id}`)); } catch (e) { return; }
  }
  if (!li.isConnected || !li.matches(":hover")) return;
  const info = infoCache.get(key);
  const card = $("hover-card");
  card.innerHTML = "";
  const line = (text, cls) => {
    if (!text) return;
    const div = document.createElement("div");
    div.textContent = text;
    if (cls) div.className = cls;
    card.append(div);
  };
  const n = (x) => Number(x).toLocaleString();
  const organisms = (list, count) => list.length
    ? list.map(([o, k]) => `${o}${k > 1 ? ` ×${k}` : ""}`).join(", ") + (count > list.length ? `, ${count - list.length} more` : "")
    : "";
  line(it.parent ? it.name : it.label, "title");
  if (it.folder !== (currentFolder || undefined) && it.folder) line(`📁 ${it.folder}`, "muted");
  // break long paths after their separators, not inside folder names
  if (it.original && !it.parent) line(`From ${it.original.path.replace(/([\\/])/g, "$1​")}`, "muted path");
  if (info.kind === "sequence") {
    line(info.organism, "organism");
    line(info.description);
    line([`${n(info.length)} ${info.unit}`, info.unit === "bp" ? (info.circular ? "circular" : "linear") : "protein",
          info.gc != null ? `GC ${(info.gc * 100).toFixed(1)} %` : "",
          info.features ? `${n(info.features)} features (${n(info.cds)} CDS)` : "no features", info.accession]
      .filter(Boolean).join(" · "), "muted");
  } else if (info.kind === "list") {
    line(`${n(info.count)} sequences · ${n(info.total)} ${info.unit} in all · ${n(info.min)}–${n(info.max)} ${info.unit} each`);
    line(organisms(info.organisms, info.organism_count), "organism");
  } else if (info.kind === "item_list") {
    line(`${n(info.count)} item${info.count === 1 ? "" : "s"}`);
  } else if (info.kind === "alignment") {
    line(`Alignment of ${n(info.count)} sequences · ${n(info.columns)} columns`);
    line(organisms(info.organisms, info.organism_count), "organism");
  } else if (info.kind === "tree") {
    line(`Tree with ${n(info.tips)} tips`);
  } else if (info.kind === "graph") {
    line(`Assembly graph · ${n(info.nodes)} nodes, ${n(info.links)} links`);
    line(`${n(info.total)} bp in all · longest ${n(info.longest)} bp · N50 ${n(info.n50)} bp` +
         (info.sequences ? "" : " · some nodes without their sequence"), "muted");
  } else if (info.kind === "mapping") {
    line(`Reads mapped to ${info.reference}`);
    line(`${n(info.reads)} reads, ${info.mapped_percent} % mapped · mean depth ${info.mean_depth}× · ` +
         `${info.covered_percent} % covered · ${n(info.difference_count)} differences`);
  } else if (info.kind === "comparison") {
    line(`Comparison of ${info.genomes.length} genomes · ${info.blocks} shared block${info.blocks === 1 ? "" : "s"}`);
    line(info.genomes.slice(0, 6).join(", ") + (info.genomes.length > 6 ? `, ${info.genomes.length - 6} more` : ""), "muted");
  } else if (info.kind === "blast") {
    line(`BLAST of ${info.query} · ${info.program}, ${info.database} · ${info.date}`);
    line(`${n(info.hits)} hits` + (info.top ? ` · best: ${info.top} (${info.top_identity} %)` : ""), "muted");
  } else if (info.kind === "primers") {
    if (info.mode === "design") {
      line(`Primer design for ${info.template} · ${n(info.pairs)} pair${info.pairs === 1 ? "" : "s"}`);
      line(`Product ${info.product_min}–${info.product_max} bp · Tm ${info.tm_min}–${info.tm_max} °C`, "muted");
    } else {
      line(`In-silico PCR with ${info.forward} / ${info.reverse}`);
      line(`${n(info.products)} product${info.products === 1 ? "" : "s"} · ${info.date}`, "muted");
    }
  }
  const box = li.getBoundingClientRect();
  card.hidden = false;
  card.style.left = `${box.right + 8}px`;
  card.style.top = `${Math.min(box.top, window.innerHeight - card.offsetHeight - 8)}px`;
}

// ── Importing files ──────────────────────────────────────────────────────
// Importing copies files into Cladia. In the app window the Open dialog and
// drag-and-drop give real paths; in a normal browser (development) the page
// can't see paths, so the file's text is uploaded instead.
// Sequences from several files: a popup offers to put them in one list.
async function addFilesClicked() {
  if (inPywebview()) {
    const paths = await window.pywebview.api.pick_files();
    if (paths.length) addPaths(paths);
  } else {
    $("file-input").click();
  }
}

async function addPaths(paths) {  // also called from Python when files are dropped
  const target = dropTarget;
  dropTarget = null;
  const folder = paths[0].split(/[\\/]/).slice(-2, -1)[0];  // a list of them is named after it
  const gffs = paths.filter(isGff);
  if (gffs.length) {  // annotations: onto the sequence they were dropped on, or the ones they name
    for (const path of gffs) await importAnnotations({ path }, target, path.split(/[\\/]/).pop());
    paths = paths.filter((p) => !isGff(p));
    if (!paths.length) return;
  }
  toast(paths.length === 1 ? `Importing ${paths[0].split(/[\\/]/).pop()}…` : `Importing ${paths.length} files…`);
  try {
    const result = await api("POST", "/api/library/add", { paths, folder: currentFolder });
    for (const j of result.jobs || []) toast(j.message);  // BAM / SAM: a job makes the mappings
    if (result.jobs?.length) pollJobs();
    afterAdd(result.ids, result.errors, folder, result.notes);
  } catch (e) {
    toast(`Import failed: ${e.message}`, true);
  }
}

// A file's text; a gzipped one (.gz) unpacked first (the upload is text)
async function fileText(file) {
  if (!/\.gz$/i.test(file.name)) return file.text();
  return new Response(file.stream().pipeThrough(new DecompressionStream("gzip"))).text();
}

async function base64Of(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

const isReads = (name) => /\.(fastq|fq)(\.gz)?$/i.test(name);

async function uploadFiles(fileList) {
  const target = dropTarget;
  dropTarget = null;
  const ids = [], errors = [], notes = [];
  const batch = Math.random().toString(36).slice(2);
  const lastReads = [...fileList].filter((f) => isReads(f.name)).pop();
  for (const file of fileList) {
    if (isGff(file.name)) {
      await importAnnotations({ text: await file.text() }, target, file.name);
      continue;
    }
    try {
      const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
      const binary = String.fromCharCode(...head) === "ABIF" || /\.(bam|sam|cram)$/i.test(file.name) || isReads(file.name);  // a Sanger trace, a BAM, reads: bytes, as base64
      const body = binary ? { filename: file.name, data: await base64Of(file), folder: currentFolder }
        : { filename: file.name.replace(/\.gz$/i, ""), text: await fileText(file), folder: currentFolder };
      if (isReads(file.name)) Object.assign(body, { batch, last: file === lastReads });  // one drop's reads pair up
      const r = await api("POST", "/api/library/upload", body);
      for (const j of r.jobs || []) { toast(j.message); pollJobs(); }
      ids.push(...r.ids);
      errors.push(...r.errors);
      notes.push(...(r.notes || []));
    } catch (e) {
      errors.push(e.message);
    }
  }
  afterAdd(ids, errors, undefined, notes);
}

// ── Annotations from GFF3 files ──────────────────────────────────────────
// Dropped on a sequence in the list, or picked with its right-click menu: the
// file's features are added to that sequence. Dropped elsewhere: each sequence
// named in the file (seqid) gets its features, matched by name or accession; a
// GFF3 with its sequences (##FASTA) becomes new sequences instead.
const isGff = (name) => /\.(gff3?|gtf|bed|bedgraph|bg|wig)$/i.test(name);  // (and data tracks: bedGraph / WIG)
let dropTarget = null;  // the sequence a dragged file is over

async function pickAnnotations(it) {
  if (inPywebview()) {
    const path = await window.pywebview.api.pick_annotations();
    if (path) importAnnotations({ path }, it.id, path.split(/[\\/]/).pop());
  } else {
    const input = $("gff-input");
    input.onchange = async () => {
      const file = input.files[0];
      input.value = "";
      if (file) importAnnotations({ text: await file.text() }, it.id, file.name);
    };
    input.click();
  }
}

async function importAnnotations(source, id, filename) {
  if (hasUnsavedChanges()) return toast("Save (or undo) the changes to the open sequence first", true);
  try {
    const r = await api("POST", "/api/annotations/import", { ...source, id, folder: currentFolder, name: filename });
    if (r.ids) return afterAdd(r.ids, r.errors);  // a GFF3 with its sequences: imported as new sequences
    if (r.tracks) toast(`${filename}: track ${r.tracks.join(", ")} added (Show ▾ › Data tracks)`);
    const done = r.results.map((x) => `${x.label}: ${x.added} feature${x.added === 1 ? "" : "s"} added` +
                                      (x.skipped ? ` (${x.skipped} already there)` : ""));
    if (done.length) toast(`${filename}\n${done.join("\n")}`);
    if (r.errors.length) toast(`${filename}\n${r.errors.join("\n")}`, true);
    await loadList();
    showSelection(shownIds);  // the shown sequence may have new features
  } catch (e) {
    toast(`${filename}: ${e.message}`, true);
  }
}

// notes: files taken in that made no item (a submission template kept as a submitter)
async function afterAdd(ids, errors, listName = "Imported sequences", notes = []) {
  if (errors.length) toast([...errors, ...notes].join("\n"), true);
  else if (notes.length && !ids.length) toast(notes.join("\n"));
  if (!ids.length) return;
  await loadList();
  // several items: one list (sequences only), a new folder (all items shown), or as they are
  const sequences = ids.every((id) => ["sequence", "list"].includes(itemById(id)?.kind) && !itemById(id)?.trace);  // (reads: no list)
  // (a folder that had nothing in it needs no new folder: the files are alone there already)
  const others = items.some((it) => !it.parent && (it.folder || null) === currentFolder && !ids.includes(it.id)) ||
                 folders.some((path) => path.startsWith(currentFolder + "/"));
  const folderToo = currentFolder === null || others;
  if (ids.length > 1 && (sequences || folderToo)) {
    const { how, name } = await askGroup(ids, listName, sequences, folderToo);
    if (how === "list") return groupSelected(ids, name, false);
    if (how === "folder") {
      await moveToFolder(ids, joinPath(currentFolder, name), true);  // a new folder in the one shown
      return;
    }
  }
  if (!errors.length) toast([ids.length === 1 ? `Imported ${itemById(ids[0]).label}` : `Imported ${ids.length} files`, ...notes].join("\n"));
  selected = [ids[0]];
  anchorId = ids[0];
  showSelection();
  renderList();
}

// After importing several items: { how: "list" | "folder" | null (as they are), name }.
// One list only for sequences; a new folder (inside the one shown) for anything.
function askGroup(ids, name, sequences, folderToo = true) {
  const dialog = $("group-dialog"), form = dialog.querySelector("form");
  const count = ids.reduce((n, id) => n + (itemById(id).kind === "list" ? itemById(id).count : 1), 0);
  $("group-question").textContent = sequences
    ? `The files you imported have ${count} sequences. Put them in one sequence list, as if they had come in one file` +
      (folderToo ? ", or keep them as they are in a new folder?" : "?")
    : `You imported ${ids.length} items. Put them in a new folder?`;
  $("group-list").hidden = !sequences;
  $("group-folder").hidden = !folderToo;
  $("group-name-label").textContent = sequences && folderToo ? "Name (of the list or folder)" : sequences ? "Name of the list" : "Name of the folder";
  form.name.value = name;
  const enter = sequences ? "list" : "folder";
  form.name.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); dialog.close(enter); } };
  dialog.returnValue = "";  // Esc: as they are
  dialog.showModal();
  form.name.select();
  return new Promise((resolve) => {
    dialog.onclose = () => resolve({ how: dialog.returnValue === "separate" ? null : dialog.returnValue || null,
                                     name: form.name.value.trim() || name });
  });
}

let draggedFolder = null;  // a folder row being dragged

// Dragging items in the list: onto the folder button (its menu opens) and a folder
let draggedIds = null;

function itemDragStart(e, it) {
  draggedIds = selected.includes(it.id) ? [...selected] : [it.id];
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", draggedIds.map((id) => itemById(id)?.label).join("\n"));
}

// Drag and drop: the overlay shows while dragging; preventDefault stops the
// window from handing the file to Windows (which would open Geneious).
// In the app window, Python receives the drop with real paths (window.py).
let dragDepth = 0;
const isFileDrag = (e) => e.dataTransfer && e.dataTransfer.types.includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!isFileDrag(e)) return;
  e.preventDefault();
  if (dragDepth++ === 0) $("drop-overlay").hidden = false;
});
window.addEventListener("dragleave", (e) => {
  if (isFileDrag(e) && --dragDepth <= 0) {
    dragDepth = 0;
    $("drop-overlay").hidden = true;
    document.querySelector("#lists li.drop-target")?.classList.remove("drop-target");
    dropTarget = null;
  }
});
window.addEventListener("dragover", (e) => {
  if (!isFileDrag(e)) return;
  e.preventDefault();
  // over a sequence in the list: a GFF3 file dropped there annotates it
  const li = e.target.closest?.("#lists li[data-id]");
  const it = li && itemById(li.dataset.id);
  const id = it?.kind === "sequence" ? it.id : null;
  if (id !== dropTarget) {
    document.querySelector("#lists li.drop-target")?.classList.remove("drop-target");
    if (id) li.classList.add("drop-target");
    dropTarget = id;
  }
});
window.addEventListener("drop", (e) => {
  if (!isFileDrag(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $("drop-overlay").hidden = true;
  document.querySelector("#lists li.drop-target")?.classList.remove("drop-target");
  if (!inPywebview()) uploadFiles(e.dataTransfer.files);
});

// ── Main area ────────────────────────────────────────────────────────────
// Sequences, lists and alignments open in Cladia's viewer (seqview.js):
//   one sequence       -> one row, both strands, features, translations
//   several sequences  -> one row each, not aligned
//   an alignment       -> consensus, identity, only the differences coloured
// Trees open in the tree view.
// Several sequences: as many rows as fit MAX_BASES of sequence, MAX_ROWS at most
// (opening takes ~0.1 s per Mb, measured: 400 mitochondria of 26 kb 1.8 s, 100
// genomes of 150 kb 1.5 s; zooming and scrolling stay quick, only rows in view are
// drawn). It was 50 rows, after a 5000-genome list crashed Cladia.
const MAX_ROWS = 1000, MAX_BASES = 30e6;
const ROWS_PER_REQUEST = 100;
function rowsToShow(sequences) {
  let bases = 0, n = 0;
  while (n < sequences.length && n < MAX_ROWS && (n === 0 || bases + (sequences[n].length || 0) <= MAX_BASES)) {
    bases += sequences[n].length || 0;
    n++;
  }
  return sequences.slice(0, n);
}
// Feature types hidden in the viewer (View > Show: a tick per type in what is
// shown); mRNAs start hidden, as in Geneious
const hiddenType = (type) => seqShow.hiddenTypes.includes(type);

// Returns false if the user chose to keep unsaved changes (nothing changes then).
// Only sequences and lists open together. Any other selection of several items shows what
// is selected instead of one of them (it used to be whichever kind came first in a fixed order).
const KIND_NOUNS = { sequence: ["sequence", "sequences"], list: ["sequence list", "sequence lists"], item_list: ["list", "lists"], alignment: ["alignment", "alignments"],
                     comparison: ["genome comparison", "genome comparisons"], mapping: ["mapping", "mappings"], variants: ["variant set", "variant sets"],
                     reads: ["read set", "read sets"], tree: ["tree", "trees"], graph: ["assembly graph", "assembly graphs"],
                     figure: ["figure", "figures"], blast: ["BLAST result", "BLAST results"], primers: ["primer result", "primer results"] };
const PLACEHOLDER = $("placeholder").innerHTML;
function showSummary(chosen, list) {
  const counts = {};
  for (const it of chosen) counts[it.kind] = (counts[it.kind] || 0) + 1;
  const parts = Object.keys(KIND_NOUNS).filter((kind) => counts[kind]).map((kind) => `${counts[kind]} ${KIND_NOUNS[kind][counts[kind] === 1 ? 0 : 1]}`);
  const p = document.createElement("p"), b = document.createElement("b");
  b.textContent = list ? `${list.label} · ${chosen.length} item${chosen.length === 1 ? "" : "s"}` : `${chosen.length} items selected`;
  p.append(b, document.createElement("br"), parts.join(", "), document.createElement("br"),
           list ? "Expand the list and select an item to open it."
             : "Select one to open it; sequences and sequence lists open together. Right-click for what can be done with all of them.");
  $("placeholder").replaceChildren(p);
  showView("placeholder");
}

function showSelection(selection = selected) {
  const picked = selection.map(itemById).filter((it) => it && !it.missing);
  const itemList = picked.length === 1 && picked[0].kind === "item_list" ? picked[0] : null;
  const chosen = itemList ? listMembers(itemList) : picked;
  const summary = itemList || (chosen.length > 1 && !chosen.every((it) => ["sequence", "list"].includes(it.kind)));
  if (!chosen.some((it) => it.kind === "mapping")) stopMappingReads();  // the reads lane goes with the mapping
  // A sequence list stands for its sequences
  const sequences = [];
  for (const it of chosen.flatMap((x) => (x.kind === "list" ? childrenOf(x) : x.kind === "sequence" ? [x] : []))) {
    if (!sequences.includes(it)) sequences.push(it);
  }
  const alignment = chosen.find((it) => it.kind === "alignment");
  const tree = chosen.find((it) => it.kind === "tree");
  const graphItem = chosen.find((it) => it.kind === "graph");
  const mappingItem = chosen.find((it) => it.kind === "mapping");
  const blastItem = chosen.find((it) => it.kind === "blast");
  const primersItem = chosen.find((it) => it.kind === "primers");
  const comparison = chosen.find((it) => it.kind === "comparison");
  const variantsItem = chosen.find((it) => it.kind === "variants");
  const onlyList = chosen.length === 1 && chosen[0].kind === "list" ? chosen[0] : null;

  // edited in the viewer, and going somewhere else
  const ids = (list) => list.map((it) => it.id).join(",");
  const staying = !summary && seqShown && (seqShown.mode === "single" ? sequences.length === 1 && sequences[0].id === seqShown.items[0].id
    : seqShown.mode === "sequences" ? ids(rowsToShow(sequences)) === ids(seqShown.items)
    : !sequences.length && alignment?.id === seqShown.items[0].id);
  if (hasUnsavedChanges() && !staying) {
    const what = seqShown.mode === "sequences" ? `${shownDocs().filter((d) => d.dirty).length} sequences` : seqShown.items[0].label;
    if (!confirm(`Discard unsaved changes to ${what}?`)) return false;
    seqShown.doc = seqShown.docs = seqShown.alignDoc = null;
    seqKey = null;
  }

  if (metadataItem && (picked.length !== 1 || picked[0].id !== metadataItem.id)) clearMetadata();

  markSeen(picked);  // (the title stays until the new item's replaces it: no blank in between)

  if (summary) {
    showSummary(chosen, itemList);
  } else if (sequences.length === 1) {
    showSingle(sequences[0]);
  } else if (sequences.length > 1) {
    showSequences(rowsToShow(sequences), sequences.length, onlyList);
  } else if (alignment) {
    showAlignment(alignment);
  } else if (comparison) {
    showComparison(comparison);
  } else if (mappingItem) {
    showMapping(mappingItem);
  } else if (variantsItem) {
    showVariantsItem(variantsItem);
  } else if (chosen.some((it) => it.kind === "reads")) {
    showReads(chosen.find((it) => it.kind === "reads"));
  } else if (tree) {
    showTree(tree);
  } else if (graphItem) {
    showGraph(graphItem);
  } else if (chosen.some((it) => it.kind === "figure")) {
    showFigure(chosen.find((it) => it.kind === "figure"));  // a figure item: the editor, drawing its source
  } else if (blastItem || primersItem) {
    showTable(blastItem || primersItem);
  } else {
    $("placeholder").innerHTML = PLACEHOLDER;
    showView("placeholder");
  }
  shownIds = picked.map(it => it.id);
  syncSidePanel(picked);
  scheduleToolStates();
  shownVersion = versionOf(shownIds);
  return true;
}

// Linear / Circular map of a single sequence
function setSeqMap(map) {
  if (!seqShown || seqShown.mode !== "single") return;
  seqShown.map = map;
  seqView.setCircular(map === "circular");
  if (map === "circular" && !seqView.circularSequence) {
    toast("This sequence is linear: drawn as a circle, its ends aren't joined. If it is circular: right-click → Mark as a circular sequence");
  }
  renderSeqControls("single");
}

// Views are hidden with visibility, not display:none: they keep their size,
// so the viewer and the tree view don't have to re-measure when shown again
function showView(id) {
  if (id !== "treeview") treeUnlocked = null;
  if (id !== "treeview") rememberTreeViewport();
  requestAnimationFrame(updateViewerZoom);
  for (const view of ["placeholder", "seqview", "treeview", "graphview", "tableview", "figureview", "readsview"]) $(view).classList.toggle("inactive", view !== id);
  if (id !== "figureview") figureShown = null;  // shown again from the file next time
  $("viewbar").hidden = id === "placeholder" || id === "tableview";  // result tables have their own bar
  $("tree-controls").hidden = id !== "treeview";
  // Controls that the new item's own render sets (renderGraphControls, renderFigureSwitch,
  // renderSeqControls) are hidden here only when the new view can't have them: hiding and
  // showing them again while the item loads made the bar jump.
  if (id !== "graphview") $("graph-controls").hidden = true;
  if (id !== "seqview" && id !== "graphview") $("graph-back").hidden = true;
  $("seq-controls").hidden = id !== "seqview";
  $("figure-controls").hidden = id !== "figureview";
  renderTreeEditButtons();
  if (id !== "figureview") $("figure-back").hidden = true;
  if (!["seqview", "treeview", "figureview"].includes(id)) $("view-figure").hidden = true;
  scheduleSidePanel();  // the Annotations tab goes with the sequence viewer
}

// ── Genetic code ─────────────────────────────────────────────────────────
// A sequence opens with the code from its file (a CDS /transl_table or
// /organelle, see genbank.genetic_code); a code picked in the tools bar is
// remembered for that sequence.
let geneticCodes = [];  // NCBI codes from /api/genetic-codes
const codeChoices = loadJSON("cladia.codes");  // item id -> code picked by the user

const IUPAC = { A: "A", C: "C", G: "G", T: "T", U: "T", R: "AG", Y: "CT", K: "GT", M: "AC",
                S: "CG", W: "AT", B: "CGT", D: "AGT", H: "ACT", V: "ACG", N: "ACGT", X: "ACGT" };

function loadJSON(key, allowString = false) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    if (allowString && typeof value === "string") return value;
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (e) { return {}; }
}

async function loadGeneticCodes() {
  geneticCodes = await api("GET", "/api/genetic-codes");
}

// Codon -> amino acid for every codon, ambiguous ones (e.g. TGR) included:
// the amino acid if all possible readings agree, X otherwise
function codonTable(number) {
  const codons = geneticCodes.find((c) => c.id === number).codons;
  const table = {};
  const letters = Object.keys(IUPAC);
  for (const a of letters) for (const b of letters) for (const c of letters) {
    const aminoAcids = new Set();
    for (const x of IUPAC[a]) for (const y of IUPAC[b]) for (const z of IUPAC[c]) aminoAcids.add(codons[x + y + z]);
    table[(a + b + c).toLowerCase()] = aminoAcids.size === 1 ? [...aminoAcids][0] : "X";
  }
  return table;
}

function hasUnsavedChanges() {
  return shownDocs().some((doc) => doc.dirty) || metadataDirty();
}
