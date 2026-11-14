// ── What each tool takes, the tool buttons' state, and the picker ────────
// Registered operations describe toolbar and dialog inputs. Graph comparison still uses
// this picker contract because it is a viewer action rather than a toolbar tool.
//
// A slot: { label, noun, kinds: ["sequence" | "alignment"...], accepts(item) -> null | why not,
//           min, max, single }. `kinds` are the leaf items to choose (a sequence list is a
//           folder of sequences, chosen whole or in part); `accepts` sees one leaf.

const noProtein = (it) => (it.protein ? "a protein" : null);
const TOOL_INPUTS = {
  graphcompare: {
    reference: { label: "Related genome", noun: "DNA sequence", kinds: ["sequence"], single: true, accepts: noProtein, min: 1 },
  },
};

const KIND_NAMES = { sequence: "a sequence", list: "a list", alignment: "an alignment", tree: "a tree", mapping: "a mapping",
                     blast: "BLAST results", primers: "primer results", comparison: "a comparison", graph: "an assembly graph", reads: "reads" };
const sizeText = (it) => (it.length ? (it.length >= 10000 ? `${(it.length / 1000).toFixed(1)} kb` : `${it.length.toLocaleString()} bp`) : "");
const folderOf = (it) => (it.parent ? itemById(it.parent)?.folder : it.folder) || null;

// Where an item lives, when another item of its kind has the same name (told apart in the chips and rows)
const twinName = (it) => items.some((o) => o !== it && !o.missing && o.kind === it.kind && o.label === it.label)
  ? (it.parent ? "" : it.folder || "Library") : "";

// The library's items a slot could take, each with why not (null: it fits)
const slotLeaves = (spec) => items.filter((it) => !it.missing && spec.kinds.includes(it.kind));
const slotReason = (spec, it) => (spec.accepts ? spec.accepts(it) : null);

// ── The toolbar buttons ──────────────────────────────────────────────────
// normal: the selection fits; dim: it doesn't, but the library has items that would
// (the dialog's picker chooses them); off: nothing in the library could be used

// The items of the current selection a slot could take (lists open into their sequences)
function selectionFor(spec) {
  const out = [];
  for (const it of selected.map(itemById).filter((x) => x && !x.missing)) {
    const leaves = it.kind === "list" && spec.kinds.includes("sequence") ? childrenOf(it) : [it];
    for (const leaf of leaves) if (spec.kinds.includes(leaf.kind) && !slotReason(spec, leaf) && !out.includes(leaf)) out.push(leaf);
  }
  return out;
}

function toolState(name) {
  const operations = registeredToolsData?.operations.filter((o) => o.category === name) || [];
  const firstSlot = operations.flatMap((o) => o.inputs)
    .find((slot) => !["file_paths", "text", "accessions", "ncbi_search"].includes(slot.docType));
  const slots = operations.length ? (firstSlot ? { [firstSlot.key]: inputSpec(firstSlot) } : null) : TOOL_INPUTS[name];
  if (!slots) return { state: "ok" };
  const [first] = Object.values(slots), spec = first;
  const viewerSource = name === "blast" && (viewerQueries?.() || graphQueries?.());
  if (viewerSource) return { state: "ok" };
  const fit = selectionFor(spec).length, min = Math.max(1, spec.min || 1);
  if (fit >= (name === "align" ? 1 : min)) return { state: "ok" };
  if (!slotLeaves(spec).some((it) => !slotReason(spec, it))) {
    return { state: "off", reason: `no ${spec.noun}s in the library yet${name === "tree" ? ": create an alignment first" : ""}` };
  }
  const have = selected.length;
  const why = have ? `the selection has no ${spec.noun} that fits` : `select ${min > 1 ? `at least ${min}` : /^[aeiou]/i.test(spec.noun) ? "an" : "a"} ${spec.noun}${min > 1 ? "s" : ""}, or choose in the dialog`;
  return { state: "dim", reason: `${why} (choose in the dialog)` };
}

let toolStateFrame = 0;
function scheduleToolStates() {  // (the viewer's selection changes many times a second)
  if (toolStateFrame) return;
  toolStateFrame = requestAnimationFrame(() => { toolStateFrame = 0; renderToolStates(); });
}

function renderToolStates() {
  for (const button of document.querySelectorAll("#toolbar [data-tool]")) {
    button.dataset.title ??= button.title;
    const { state, reason } = toolState(button.dataset.tool);
    button.classList.toggle("dim", state === "dim");
    button.classList.toggle("off", state === "off");
    button.disabled = state === "off";
    button.title = state === "ok" ? button.dataset.title : `${button.dataset.title}\n— ${reason}`;
  }
}

// ── The picker ───────────────────────────────────────────────────────────
// What is chosen is a set of leaf item ids; a list is "chosen whole" when all its members are.
class ToolPicker {
  constructor(host, spec, options = {}) {
    this.host = host;
    this.spec = spec;
    this.options = options;      // { exclude(id), onChange(picker) }
    this.chosen = new Set();
    this.skipped = [];
    this.open = new Set();       // lists expanded in the chips
    host.replaceChildren();
    host.classList.add("picker");  // (a host keeps its own class: the dialog finds it again when reopened)
    host.classList.toggle("single", !!spec.single);
    host.innerHTML = '<div class="picker-head"><span class="picker-label"></span><span class="picker-count muted"></span>' +
      '<button type="button" class="secondary picker-add"></button><button type="button" class="secondary picker-clear">Clear</button></div>' +
      '<ul class="picker-chips"></ul><p class="picker-note muted"></p>';
    host.querySelector(".picker-label").textContent = spec.label;
    host.querySelector(".picker-add").textContent = spec.single ? "Change…" : "Add…";
    host.querySelector(".picker-clear").hidden = !!spec.single;
    host.querySelector(".picker-add").onclick = () => openPickerDialog(this);
    host.querySelector(".picker-clear").onclick = () => { this.chosen.clear(); this.changed(); };
  }

  // The current selection in the list, as far as it fits; what doesn't is listed with why
  fillFromSelection(ids = selected) {
    const spec = this.spec, why = new Map();
    const skip = (reason) => why.set(reason, (why.get(reason) || 0) + 1);
    this.chosen.clear();
    for (const id of ids) {
      const it = itemById(id);
      if (!it || it.missing) continue;
      const leaves = it.kind === "list" && spec.kinds.includes("sequence") ? childrenOf(it) : [it];
      for (const leaf of leaves) {
        if (!spec.kinds.includes(leaf.kind)) skip(KIND_NAMES[leaf.kind] || leaf.kind);
        else if (this.options.exclude?.(leaf.id)) continue;
        else if (slotReason(spec, leaf)) skip(slotReason(spec, leaf));
        else if (spec.single && this.chosen.size) skip("only one can be used");
        else this.chosen.add(leaf.id);
      }
    }
    this.skipped = [...why].map(([reason, n]) => `${n} ${reason}`);
    this.render();
    return this;
  }

  set(ids) { this.chosen = new Set(ids); this.changed(); }
  changed() { this.skipped = []; this.render(); this.options.onChange?.(this); }

  // ── what the tools read ──
  leafItems() { return items.filter((it) => this.chosen.has(it.id)); }   // in the library's order
  ids() { return this.leafItems().map((it) => it.id); }
  get value() { return this.ids()[0] || ""; }

  // The choice as the server takes it: a list chosen whole stays a list (its results make one list),
  // a list in part its chosen sequences, the rest as they are
  asPicked() {
    const out = [], seen = new Set();
    for (const it of this.leafItems()) {
      if (!it.parent) { out.push(it.id); continue; }
      if (seen.has(it.parent)) continue;
      seen.add(it.parent);
      const members = childrenOf(itemById(it.parent));
      out.push(...(members.every((m) => this.chosen.has(m.id)) ? [it.parent] : members.filter((m) => this.chosen.has(m.id)).map((m) => m.id)));
    }
    return out;
  }

  problem() {
    const n = this.chosen.size, { min = 0, max, noun } = this.spec;
    if (n < min) return `Choose ${min > 1 ? `at least ${min} ${noun}s` : `a ${noun}`} (Add…).`;
    if (max && n > max) return `At most ${max} ${noun}s.`;
    return null;
  }

  // ── drawing ──
  render() {
    const spec = this.spec, host = this.host, ul = host.querySelector(".picker-chips");
    // drop what is gone from the library
    for (const id of [...this.chosen]) if (!itemById(id) || this.options.exclude?.(id)) this.chosen.delete(id);
    ul.replaceChildren();
    const chip = (text, sub, onRemove, extra) => {
      const li = document.createElement("li");
      li.className = "picker-chip";
      const name = document.createElement("span");
      name.className = "picker-name";
      name.textContent = text;
      li.append(name);
      if (sub) li.append(Object.assign(document.createElement("span"), { className: "muted", textContent: sub }));
      if (onRemove && !spec.single) {
        const x = document.createElement("button");
        x.type = "button";
        x.textContent = "×";
        x.title = "Take it out";
        x.onclick = onRemove;
        li.append(x);
      }
      li.prepend(extra || Object.assign(document.createElement("span"), { className: "picker-arrow" }));
      return li;
    };
    const leaves = this.leafItems(), shown = new Set();
    let drawn = 0;
    const room = () => drawn++ < 60;
    for (const it of leaves) {
      if (shown.has(it.id)) continue;
      if (it.parent) {  // a list: whole, or the part chosen
        const list = itemById(it.parent), members = childrenOf(list), mine = members.filter((m) => this.chosen.has(m.id));
        mine.forEach((m) => shown.add(m.id));
        if (!room()) continue;
        const whole = mine.length === members.length, opened = this.open.has(list.id);
        const arrow = document.createElement("button");
        arrow.type = "button";
        arrow.className = "picker-arrow";
        arrow.textContent = opened ? "▾" : "▸";
        arrow.onclick = () => { opened ? this.open.delete(list.id) : this.open.add(list.id); this.render(); };
        ul.append(chip(list.label, whole ? `list, ${members.length} sequences` : `list, ${mine.length} of ${members.length}`,
                       () => { mine.forEach((m) => this.chosen.delete(m.id)); this.changed(); }, arrow));
        if (opened) for (const m of mine.slice(0, 200)) {
          const row = chip(m.name, sizeText(m), () => { this.chosen.delete(m.id); this.changed(); });
          row.classList.add("member");
          ul.append(row);
        }
        if (opened && mine.length > 200) ul.append(Object.assign(document.createElement("li"), { className: "muted member", textContent: `and ${mine.length - 200} more` }));
      } else if (room()) {
        shown.add(it.id);
        ul.append(chip(it.label, [twinName(it), it.organism, sizeText(it)].filter(Boolean).join(" · "), () => { this.chosen.delete(it.id); this.changed(); }));
      }
    }
    if (drawn > 60) ul.append(Object.assign(document.createElement("li"), { className: "muted", textContent: `and ${drawn - 60} more` }));
    const n = this.chosen.size;
    host.querySelector(".picker-count").textContent = n ? (spec.single ? "" : `${n.toLocaleString()} ${spec.noun}${n === 1 ? "" : "s"}`) : "";
    const note = host.querySelector(".picker-note");
    const parts = [];
    if (!n) parts.push(`None chosen yet. ${spec.single ? "Change…" : "Add…"} picks from the library.`);
    if (this.skipped.length) parts.push(`Left out of the selection: ${this.skipped.join(", ")}.`);
    const problem = n && this.problem();
    if (problem) parts.push(problem);
    note.textContent = parts.join(" ");
    host.classList.toggle("problem", !!(problem || !n && spec.min));
    // a hidden select keeps its value in step, for the tool's request
    const select = this.options.select;
    if (select) {
      select.replaceChildren();
      const it = this.leafItems()[0];
      select.append(new Option(it ? it.label : "", it ? it.id : ""));
      select.value = it ? it.id : "";
    }
    const form = this.host.closest("form");
    if (form?._toolName) renderResultDestination(form);
  }
}

// ── The popup: folders, lists, a filter ──────────────────────────────────
let pickerState = null;

function openPickerDialog(picker) {
  const dialog = $("picker-dialog"), spec = picker.spec;
  pickerState = { picker, spec, work: new Set(picker.chosen), openFolders: new Set(), openLists: new Set(), limit: 400 };
  // what is open to begin with: the folder shown, and the lists that hold a chosen sequence
  for (let path = currentFolder; path; path = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : null) pickerState.openFolders.add(path);
  for (const id of picker.chosen) { const it = itemById(id); if (it?.parent) pickerState.openLists.add(it.parent); }
  $("picker-title").textContent = spec.single ? `Choose the ${spec.noun}` : `Choose ${spec.noun}s`;
  $("picker-filter").value = "";
  $("picker-all-folders").checked = !currentFolder;
  $("picker-all-folders").parentElement.hidden = !currentFolder;
  $("picker-unusable").checked = false;
  renderPickerTree();
  dialog.showModal();
  $("picker-filter").focus();
}

// The leaves a folder (and its subfolders) holds, for the folder's tick and count
const insideFolder = (path, it) => {
  const f = folderOf(it);
  return path === null ? true : f === path || (f && f.startsWith(`${path}/`));
};

function renderPickerTree() {
  const st = pickerState, spec = st.spec, box = $("picker-tree");
  const text = $("picker-filter").value.trim().toLowerCase();
  const scope = $("picker-all-folders").checked || !currentFolder ? null : currentFolder;
  const showUnusable = $("picker-unusable").checked;
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const leaves = slotLeaves(spec).filter((it) => insideFolder(scope, it));
  const reason = (it) => slotReason(spec, it) || (st.picker.options.exclude?.(it.id) ? "already used in another input" : null);
  const usable = (it) => !reason(it);
  const matches = (it) => !text || `${it.label} ${it.organism || ""} ${it.description || ""} ${it.accession || ""} ${it.parent ? itemById(it.parent)?.label : ""}`.toLowerCase().includes(text);
  const frag = document.createDocumentFragment();
  let rows = 0, hidden = 0;
  const add = (depth, build) => { if (rows >= st.limit) { hidden++; return; } rows++; const li = build(); li.style.paddingLeft = `${8 + depth * 16}px`; frag.append(li); };

  const tick = (state, onChange, disabled) => {  // a tri-state box (a radio for a single choice)
    const input = document.createElement("input"); input.autocomplete = "off";
    input.type = spec.single ? "radio" : "checkbox";
    input.checked = state === "all";
    input.indeterminate = state === "some";
    input.disabled = !!disabled;
    input.onclick = (e) => { e.stopPropagation(); onChange(); };
    return input;
  };
  const row = (className, ...parts) => {
    const li = document.createElement("li");
    li.className = `picker-row ${className}`;
    for (const p of parts) li.append(p);
    return li;
  };
  const label = (t, muted) => Object.assign(document.createElement("span"), { className: muted ? "muted" : "picker-name", textContent: t });
  const toggleLeaf = (it) => {
    if (spec.single) { st.work = new Set([it.id]); return finishPicker(); }
    st.work.has(it.id) ? st.work.delete(it.id) : st.work.add(it.id);
    renderPickerTree();
  };
  const setMany = (list, on) => { for (const it of list) on ? st.work.add(it.id) : st.work.delete(it.id); renderPickerTree(); };
  const stateOf = (list) => { const u = list.filter(usable), n = u.filter((it) => st.work.has(it.id)).length; return !u.length || !n ? "none" : n === u.length ? "all" : "some"; };
  const leafRow = (it, depth, withList) => add(depth, () => {
    const why = reason(it), li = row(`leaf${why ? " unusable" : ""}`,
      tick(st.work.has(it.id) ? "all" : "none", () => toggleLeaf(it), !!why),
      label(it.parent ? (withList ? `${itemById(it.parent)?.label} › ${it.name}` : it.name) : it.label),
      label([twinName(it), it.organism, sizeText(it), why ? `(${why})` : ""].filter(Boolean).join(" · "), true));
    if (!why) li.onclick = () => toggleLeaf(it);
    return li;
  });
  const listRow = (list, depth) => {
    const members = childrenOf(list).filter(matches);
    if (text && !members.length) return;
    if (!showUnusable && !childrenOf(list).some(usable)) return;  // nothing in it could be chosen
    const open = st.openLists.has(list.id) || !!text, all = childrenOf(list);
    add(depth, () => {
      const arrow = label(open ? "▾" : "▸", true);
      arrow.className = "picker-arrow";
      const li = row("list", arrow, spec.single ? label("") : tick(stateOf(all), () => setMany(all.filter(usable), stateOf(all) !== "all")),
                     label(list.label), label(`list, ${all.length} sequences`, true));
      li.onclick = () => { open && !text ? st.openLists.delete(list.id) : st.openLists.add(list.id); renderPickerTree(); };
      return li;
    });
    if (open) for (const m of members) if (usable(m) || showUnusable) leafRow(m, depth + 1, !!text);
  };
  const folderRow = (path, depth) => {
    const inside = leaves.filter((it) => insideFolder(path, it) && (usable(it) || showUnusable));
    if (!inside.length) return;
    const open = st.openFolders.has(path), name = path.slice(path.lastIndexOf("/") + 1);
    add(depth, () => {
      const arrow = label(open ? "▾" : "▸", true);
      arrow.className = "picker-arrow";
      const li = row("folder", arrow, spec.single ? label("") : tick(stateOf(inside), () => setMany(inside.filter(usable), stateOf(inside) !== "all")),
                     label(name), label(`${inside.length.toLocaleString()}`, true));
      li.onclick = () => { open ? st.openFolders.delete(path) : st.openFolders.add(path); renderPickerTree(); };
      return li;
    });
    if (open) children(path, depth + 1);
  };
  // a folder's contents: subfolders, then its lists and sequences on their own
  const children = (path, depth) => {
    for (const f of folders.filter((f) => (path === null ? !f.includes("/") : f.startsWith(`${path}/`) && !f.slice(path.length + 1).includes("/"))).sort(collator.compare)) folderRow(f, depth);
    const here = items.filter((it) => !it.missing && !it.parent && (it.folder || null) === path).sort((a, b) => collator.compare(a.label, b.label));
    for (const it of here) {
      if (it.kind === "list" && spec.kinds.includes("sequence")) listRow(it, depth);
      else if (spec.kinds.includes(it.kind) && (usable(it) || showUnusable)) leafRow(it, depth);
    }
  };

  if (text) {  // a filter: the matching ones, whatever folder they are in
    for (const list of items.filter((it) => it.kind === "list" && !it.missing && spec.kinds.includes("sequence") && insideFolder(scope, it))) listRow(list, 0);
    for (const it of leaves.filter((it) => !it.parent && matches(it) && (usable(it) || showUnusable))) leafRow(it, 0, false);
  } else if (scope) {
    children(scope, 0);
  } else {
    children(null, 0);
  }
  box.replaceChildren(frag);
  if (hidden) {
    const more = Object.assign(document.createElement("li"), { className: "picker-row muted", textContent: `${hidden.toLocaleString()} more: narrow the filter, or show more` });
    more.onclick = () => { st.limit += 600; renderPickerTree(); };
    box.append(more);
  }
  if (!box.children.length) box.append(Object.assign(document.createElement("li"), { className: "picker-row muted", textContent: text ? "Nothing matches." : `No ${spec.noun}s here.` }));
  const n = st.work.size;
  $("picker-foot").textContent = spec.single ? "" : `${n.toLocaleString()} chosen` + (spec.min && n < spec.min ? ` (needs at least ${spec.min})` : "");
}

function finishPicker() {
  const st = pickerState;
  st.picker.chosen = new Set(st.work);
  $("picker-dialog").close();
  st.picker.changed();
}

// The tool dialogs' pickers: one for the sequences slot (`.seq-picker`), more made by tools for single choices
function mountPicker(form, name, slot = "sequences", host = form.querySelector(".seq-picker"), options = {}) {
  const spec = TOOL_INPUTS[name]?.[slot];
  if (!host || !spec) return null;
  form._pickers ??= {};
  const picker = form._pickers[slot] = new ToolPicker(host, spec, options);
  return picker;
}

// A single choice replacing a select: the select stays (hidden) holding the chosen id for the request
function mountSingle(form, name, slot, select, { initial, onChange } = {}) {
  select.hidden = true;
  select.parentElement.querySelector(".picker")?.remove();
  const host = document.createElement("div");
  select.insertAdjacentElement("afterend", host);
  const picker = mountPicker(form, name, slot, host, { select, onChange: (p) => { select.dispatchEvent(new Event("change")); onChange?.(p); } });
  picker.set(initial ? [initial] : []);
  return picker;
}

// The dialog's picker for the sequences, and what the tool code reads from it
let activePicker = null;
const pickedSequences = () => activePicker?.leafItems() || [];
const pickedIds = () => activePicker?.asPicked() || [];

$("picker-filter").oninput = () => { pickerState.limit = 400; renderPickerTree(); };
$("picker-all-folders").onchange = $("picker-unusable").onchange = () => renderPickerTree();
$("picker-dialog").querySelector("form").addEventListener("submit", (e) => {
  if (e.submitter?.value === "ok") finishPicker();
});
