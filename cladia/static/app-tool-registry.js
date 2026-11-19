// Generated tool dialog: core owns operations, inputs, output and job submission.
// Engine modules (cladia/tool_plugins) contribute data and option schemas through /api/tool-registry.
let registeredToolsPromise = null;
let registeredToolsData = null;
const registeredDialogs = new Map();
const registeredToolIcons = {
  annotate: '<path d="M2 2h6l6 6-6 6-6-6z"/><circle cx="5.5" cy="5.5" r="1.2"/>',
  align: '<path d="M2 4h8M4 8h10M2 12h6M11 12h3"/>',
  tree: '<path d="M1.5 8.5h3M4.5 3.5v10M4.5 3.5h4M4.5 13.5h10M8.5 1.5v4M8.5 1.5h6M8.5 5.5h6"/>',
  map: '<path d="M1 13.5h14" stroke-width="2.6"/><path d="M2 9.5h5M8 9.5h6M4 6h6M3 2.5h5M9 2.5h4"/>',
  assemble: '<path d="M2 4h6l3 4h4M2 12h6l3-4"/>',
  variants: '<path d="M2 3h12M2 8h12M2 13h12M5 1.5v3M10 6.5v3M7 11.5v3"/>',
  primers: '<path d="M1.5 8h13M4 4.5h5.5M7.5 2.7L9.5 4.5L7.5 6.3M12 11.5H6.5M8.5 9.7L6.5 11.5l2 1.8"/>',
  crispr: '<path d="M3 3l10 10M13 3L3 13"/><circle cx="3" cy="3" r="1"/><circle cx="13" cy="3" r="1"/><path d="M7 7h2"/>',
  blast: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
  download: '<path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M2.5 11v2.5h11V11"/>',
};

// Categories whose button sits in the sidebar beside Import instead of the toolbar:
// they bring data in rather than analyse it (index.html #add-download).
const SIDEBAR_TOOLS = new Set(["download"]);

async function renderRegisteredToolbar() {
  try {
    const registry = await registeredTools();
    const bar = $("toolbar");
    for (const old of bar.querySelectorAll("[data-tool]")) old.remove();
    const before = $("jobs-running");
    for (const category of registry.categories) {
      if (SIDEBAR_TOOLS.has(category.id)) continue;
      const button = document.createElement("button"); button.className = "tool";
      button.dataset.tool = category.id; button.title = category.title || category.label;
      button.innerHTML = `<svg class="icon" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${registeredToolIcons[category.icon] || ""}</svg>`;
      button.append(document.createTextNode(category.toolbarLabel || category.label));
      button.onclick = () => openTool(category.id);
      bar.insertBefore(button, before);
    }
    scheduleToolStates();
  } catch (error) { toast(`Could not load tools: ${error.message}`, true); }
}

const toolMemory = (key) => loadJSON(`cladia.tool.${key}`, true);
function saveToolMemory(key, value) {
  try { localStorage.setItem(`cladia.tool.${key}`, JSON.stringify(value)); } catch (e) {}
}

function inputSpec(slot) {
  const kinds = slot.docType === "alignment_or_sequence" ? ["alignment", "sequence"]
    : slot.docType === "alignment" ? ["alignment"] : ["sequence"];
  const noun = slot.docType === "alignment_or_sequence" ? "alignment or sequence" : slot.docType === "alignment" ? "alignment"
    : slot.docType === "annotated_sequence" ? "annotated reference"
    : slot.docType === "blast_queries" ? "query sequence"
    : slot.docType === "dna_sequence" ? "genome" : slot.docType === "sanger_read" ? "Sanger read" : "sequence";
  return { label: slot.label, noun, kinds, min: slot.min, max: slot.max,
    single: slot.max === 1, accepts: slot.docType === "annotated_sequence" ? (it) => noProtein(it) || (!it.features ? "without annotations" : null)
      : slot.docType === "dna_sequence" ? noProtein
      : slot.docType === "sanger_read" ? (it) => noProtein(it) || (it.length > 3000 ? "longer than 3000 bp" : null) : undefined };
}

async function registeredTools() {
  registeredToolsPromise ??= api("GET", "/api/tool-registry").then((data) => {
    registeredToolsData = data; return data;
  }).catch((error) => { registeredToolsPromise = null; throw error; });
  return registeredToolsPromise;
}

let toolEngineCatalog = [];
let toolEnginesPromise = null;
let toolStoragePromise = null;

function toolStorageText(storage) {
  if (!storage) return "Disk size not reported";
  if (storage.kind === "builtin") return "Included in Cladia · no additional tool download";
  if (storage.kind === "remote") return "Runs online · no local tool download";
  const amount = storage.bytes !== null && storage.bytes !== undefined ? `${bytes(storage.bytes)} on this PC`
    : storage.estimateBytes !== null && storage.estimateBytes !== undefined ? `About ${bytes(storage.estimateBytes)} to install` : "Disk size not reported";
  return storage.kind === "shared" ? `No additional tool files · uses ${storage.sharedWith || "another installed tool"}` : amount;
}

async function renderToolEngines() {
  const host = $("tool-engines-list");
  if (!host.children.length) host.textContent = "Loading tools…";
  const error = $("tool-engines-error"); error.hidden = true;
  try {
    toolEnginesPromise ??= api("GET", "/api/tool-engines").catch((e) => { toolEnginesPromise = null; throw e; });
    const catalog = await toolEnginesPromise;
    toolEngineCatalog = catalog;
    const expanded = new Set([...host.querySelectorAll("details[open]")].map(e => e.dataset.type));
    host.replaceChildren();
    const sorted = [...catalog].sort((a, b) => a.typeOrder - b.typeOrder || a.type.localeCompare(b.type) || a.label.localeCompare(b.label));
    let lastType = null, group = null, cards = null;
    for (const engine of sorted) {
      if (engine.type !== lastType) {
        group = document.createElement("details"); group.className = "tool-group";
        group.dataset.type = engine.type; group.open = expanded.has(engine.type);
        const heading = document.createElement("summary"); heading.className = "tool-type-heading";
        heading.textContent = engine.type;
        cards = document.createElement("div"); cards.className = "tool-grid";
        group.append(heading, cards); host.append(group); lastType = engine.type;
      }
      const row = document.createElement("div"); row.className = "tool-card";
      row.dataset.engine = engine.id;
      const title = document.createElement("strong"); title.textContent = engine.label;
      title.title = `${engine.type} · ${engine.blurb}`;
      const action = document.createElement("button"); action.type = "button"; action.className = "secondary";
      action.textContent = engine.outdated ? "Update…" : engine.installed ? "Manage…" : "Install";
      action.setAttribute("aria-label", `${engine.outdated ? "Update" : engine.installed ? "Manage" : "Install"} ${engine.label}`);
      action.onclick = () => openToolEngineDetails(engine.id);
      row.append(title, action); cards.append(row);
    }
    if (!sorted.length) host.textContent = "All tools are included with Cladia.";
  } catch (e) {
    host.textContent = "Could not load tools.";
    error.textContent = e.message; error.hidden = false;
  }
}

async function openToolEngineDetails(engineId) {
  const engine = toolEngineCatalog.find((item) => item.id === engineId);
  if (!engine) return;
  const dialog = $("tool-engine-dialog"), host = $("tool-engine-packages");
  const settingsHost = $("tool-engine-settings"), save = $("tool-engine-save-settings");
  dialog.dataset.engine = engine.id;
  $("tool-engine-title").textContent = engine.builtin || engine.installed ? engine.label : `Install ${engine.label}`;
  const storage = engine.storage;
  const shared = storage?.kind === "shared" && storage.bytes != null
    ? `Shared ${storage.sharedWith || "tool"} installation: ${bytes(storage.bytes)} (counted once).` : "";
  $("tool-engine-description").textContent = [engine.blurb, toolSourceText(engine), engine.diskText || toolStorageText(storage), shared, storage?.note].filter(Boolean).join("\n");
  renderToolEngineActions(engine);
  $("tool-engine-error").hidden = true;
  host.replaceChildren();
  settingsHost.replaceChildren(); save.hidden = true;
  if (engine.choices.length) {
    const caption = document.createElement("p"); caption.className = "muted";
    caption.textContent = engine.installed ? "Installed packages are marked. Choose any others to add." : "Choose the packages to install with this tool.";
    host.append(caption);
    for (const choice of engine.choices) {
      const label = document.createElement("label"); label.className = "check";
      const checkbox = document.createElement("input"); checkbox.type = "checkbox";
      checkbox.value = choice.value;
      const already = engine.installedChoices.includes(choice.value);
      checkbox.checked = already; checkbox.disabled = already;
      checkbox.onchange = updateToolEngineInstallButton;
      label.append(checkbox, document.createTextNode(` ${choice.label}${already ? " (installed)" : ""}`));
      host.append(label);
    }
  } else {
    host.textContent = engine.builtin ? (["builtin", "remote"].includes(engine.storage?.kind)
      ? "Included with Cladia. No separate tool installation is needed." : "Included with Cladia. Linux tools are prepared automatically on first use.")
      : engine.outdated ? "This version of Cladia comes with a different version of the tool: update it to use it again."
      : engine.installed ? "Installed." : "Installing downloads it; it then appears in its tool dialog.";
  }
  updateToolEngineInstallButton();
  dialog.showModal();
  if (!toolStoragePromise) {
    toolStoragePromise = api("GET", "/api/tool-engines/storage").catch((e) => { toolStoragePromise = null; throw e; });
  }
  toolStoragePromise.then((sizes) => {
    if (!dialog.open || dialog.dataset.engine !== engine.id) return;
    engine.storage = sizes[engine.id] || engine.storage;
    const measured = engine.storage;
    const sharedMeasured = measured?.kind === "shared" && measured.bytes != null
      ? `Shared ${measured.sharedWith || "tool"} installation: ${bytes(measured.bytes)} (counted once).` : "";
    $("tool-engine-description").textContent = [engine.blurb, toolSourceText(engine), toolStorageText(measured), sharedMeasured, measured?.note].filter(Boolean).join("\n");
  }).catch(() => {});
  if (engine.installed && engine.settings?.length) {
    try {
      const values = await api("GET", `/api/tool-engines/${encodeURIComponent(engine.id)}/settings`);
      if (!dialog.open || dialog.dataset.engine !== engine.id) return;
      const heading = document.createElement("h4"); heading.textContent = "Tool settings"; settingsHost.append(heading);
      for (const field of engine.settings) {
        const label = document.createElement("label"); label.textContent = field.label;
        const input = document.createElement("input"); input.autocomplete = "off"; input.dataset.setting = field.key;
        input.type = field.type === "secret" ? "password" : field.type === "bool" ? "checkbox" : "text";
        if (field.type === "bool") input.checked = !!values[field.key];
        else if (field.type === "secret") {
          input.placeholder = values[field.key] ? "Saved; leave blank to keep" : "";
          input.autocomplete = "off";
          if (values[field.key]) {
            const clear = document.createElement("button"); clear.type = "button"; clear.className = "secondary";
            clear.textContent = "Clear"; clear.onclick = () => { input.dataset.clear = "true"; input.placeholder = "Will be cleared"; input.value = ""; };
            label.append(clear);
          }
        } else input.value = values[field.key] ?? "";
        label.append(input); settingsHost.append(label);
      }
      save.hidden = false;
    } catch (e) { $("tool-engine-error").textContent = e.message; $("tool-engine-error").hidden = false; }
  }
}

async function saveToolEngineSettings() {
  const dialog = $("tool-engine-dialog"), changes = {};
  for (const input of $("tool-engine-settings").querySelectorAll("[data-setting]")) {
    const field = toolEngineCatalog.find((item) => item.id === dialog.dataset.engine)?.settings.find((item) => item.key === input.dataset.setting);
    if (field.type === "bool") changes[field.key] = input.checked;
    else if (field.type !== "secret") changes[field.key] = input.value.trim();
    else if (input.dataset.clear) changes[field.key] = null;
    else if (input.value) changes[field.key] = input.value;
  }
  const error = $("tool-engine-error"); error.hidden = true;
  try {
    await api("PUT", `/api/tool-engines/${encodeURIComponent(dialog.dataset.engine)}/settings`, changes);
    dialog.close(); toast("Tool settings saved.");
  } catch (e) { error.textContent = e.message; error.hidden = false; }
}

function updateToolEngineInstallButton() {
  const engine = toolEngineCatalog.find((item) => item.id === $("tool-engine-dialog").dataset.engine);
  const confirm = $("tool-engine-confirm");
  confirm.hidden = !!engine?.builtin || (!!engine?.installed && !engine.outdated && !engine.choices.length);
  confirm.textContent = engine?.outdated ? "Update" : engine?.installed ? "Install selected packages" : "Install tool";
  confirm.disabled = !engine || (engine.outdated ? false : engine.installed && !engine.choices.length) || (!engine.outdated &&
    (engine.choices.length > 0 && !$("tool-engine-packages").querySelector("input:not(:disabled):checked")));
}

// Where an optional tool comes from (most: bioconda, downloaded when installed)
function toolSourceText(engine) {
  if (engine.builtin || !engine.packages) return "";
  return `Downloaded from bioconda when you install it: ${engine.packages.replaceAll("=", " ")} (exact versions, checked).`;
}

// Uninstall, for an installed optional tool
function renderToolEngineActions(engine) {
  const host = $("tool-engine-actions");
  host.replaceChildren();
  if (engine.builtin || !engine.installed) return;
  const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary danger";
  remove.textContent = "Uninstall";
  remove.onclick = async () => {
    if (!confirm(`Uninstall ${engine.label}? Its downloaded files are removed; results made with it stay.`)) return;
    try {
      await api("POST", `/api/tool-engines/${encodeURIComponent(engine.id)}/uninstall`);
      $("tool-engine-dialog").close();
      toast(`Uninstalling ${engine.label}; follow it in Jobs.`);
      pollJobs();
    } catch (e) { $("tool-engine-error").textContent = e.message; $("tool-engine-error").hidden = false; }
  };
  host.append(remove);
}

async function installSelectedToolEngines() {
  const dialog = $("tool-engine-dialog");
  const engine = toolEngineCatalog.find((item) => item.id === dialog.dataset.engine);
  if (!engine) return;
  const choices = engine.outdated ? engine.installedChoices  // an update: the same packages, the new version
    : [...$("tool-engine-packages").querySelectorAll("input:not(:disabled):checked")].map((input) => input.value);
  const error = $("tool-engine-error"); error.hidden = true;
  try {
    $("tool-engine-confirm").disabled = true;
    await api("POST", "/api/tool-engines/install", { engines: [{ id: engine.id, choices }] });
    dialog.close();
    toast(`Installing ${engine.label}; follow its progress in Jobs.`);
    pollJobs();
  } catch (e) {
    error.textContent = e.message; error.hidden = false;
    updateToolEngineInstallButton();
  }
}

function makeRegisteredDialog(category) {
  const dialog = document.createElement("dialog");
  dialog.className = "tool-dialog registered-tool-dialog";
  dialog.id = `${category.id}-registered-dialog`;
  const form = document.createElement("form"); form.autocomplete = "off"; form.method = "dialog"; form._toolName = category.id;
  const heading = document.createElement("h3"); heading.textContent = category.label;
  const main = document.createElement("div"); main.className = "registered-main";
  const navigation = document.createElement("div"); navigation.className = "tabs tool-tabs registered-tabs";
  const content = document.createElement("div"); content.className = "registered-content";
  const error = document.createElement("p"); error.className = "error"; error.hidden = true;
  const actions = document.createElement("div"); actions.className = "actions";
  const cancel = document.createElement("button"); cancel.value = "cancel"; cancel.className = "secondary";
  cancel.formNoValidate = true; cancel.textContent = "Cancel";
  const run = document.createElement("button"); run.value = "run"; run.textContent = category.label;
  actions.append(cancel, run); main.append(navigation, content); form.append(heading, main, error, actions);
  dialog.append(form); document.body.append(dialog);
  form.addEventListener("submit", (event) => submitRegisteredTool(form, event));
  form.addEventListener("input", (event) => {
    if (event.target.name === "output") form._nameEdited = true;
    updateRegisteredForm(form);
  });
  form.addEventListener("change", () => {
    updateRegisteredForm(form);
    for (const slot of form._operation?.inputs || []) previewRegisteredFiles(form, slot);
  });
  form.addEventListener("change", () => renderResultDestination(form));
  return { dialog, form, navigation, content, run, error };
}

function registeredName(form) {
  if (form._operation.category === "download") return form._searchName || form._operation.defaultName;
  if (form._operation.id === "assemble.batch") return `Sanger ${new Date().toISOString().slice(0, 10)}`;
  if (form._operation.id === "assemble.one") {
    const names = form._pickers?.reads?.leafItems().map((it) => it.name) || [];
    let common = names[0] || "";
    for (const name of names) while (!name.startsWith(common)) common = common.slice(0, -1);
    common = common.replace(/[_\-. ]+$/, "");
    return names.length > 1 && common.length >= 2 ? common : `${names[0] || "reads"} consensus`;
  }
  if (form._operation.defaultName === "reference_mapping") {
    return `${form._pickers?.reference?.leafItems()[0]?.name || "reads"} mapping`;
  }
  if (form._operation.defaultName === "alignment_tree") {
    const alignment = form._pickers?.alignment?.leafItems()[0];
    if (!alignment) return "tree";
    return `${alignment.name}${alignment.name.includes(" ") ? " " : "_"}tree`;
  }
  if (form._operation.defaultName === "alignment_trimmed") {
    const alignment = form._pickers?.alignment?.leafItems()[0];
    if (!alignment) return "trimmed";
    return `${alignment.name}${alignment.name.includes(" ") ? " " : "_"}trimmed`;
  }
  const picker = form._pickers?.sequences;
  const picked = picker?.asPicked().map(itemById) || [];
  const base = picked.length === 1 && picked[0]?.kind === "list" ? picked[0].name : null;
  const n = picker?.ids().length || 0;
  if (form._operation.id === "align.compare") return `${base || `${n} genome${n === 1 ? "" : "s"}`} compared`;
  const target = form._pickers?.add_to?.leafItems()[0];
  return target ? `${base || `${n} sequence${n === 1 ? "" : "s"}`} added to ${target.name}` : base || `${n} sequences`;
}

function updateRegisteredForm(form) {
  if (!form._operation) return;
  const queries = form._specialInputs?.queries;
  const queryType = queries?.length ? (seqView?.protein && queries[0].sequence ? "protein" : "dna")
    : form._pickers?.queries?.leafItems().length && form._pickers.queries.leafItems().every((it) => it.protein) ? "protein" : "dna";
  for (const spec of form._engine?.options || []) {
    if (spec.type !== "choice" || !spec.choices.some((choice) => choice.queryType || choice.when)) continue;
    const field = form.querySelector(`.registered-engine-options [name="${spec.key}"]`);
    if (!field) continue;
    const before = field.value;
    const allowed = spec.choices.filter((choice) => (!choice.queryType || choice.queryType === queryType) &&
      (!choice.when || choice.when.values.includes(form.querySelector(`.registered-engine-options [name="${choice.when.key}"]`)?.value)));
    field.replaceChildren(...allowed.map((choice) => new Option(choice.label, String(choice.value))));
    field.value = allowed.some((choice) => String(choice.value) === before) ? before : String(allowed[0]?.value ?? "");
  }
  const invalid = Object.entries(form._pickers || {}).some(([key, picker]) =>
    !(key === "queries" && queries?.length) && picker.problem()) ||
    form._operation.inputs.some((slot) => slot.docType === "blast_queries" &&
      !(queries?.length || form._pickers?.queries?.ids().length)) ||
    form._operation.inputs.some((slot) => slot.docType === "text" &&
      (form._textInputs?.[slot.key]?.value.trim().length || 0) < slot.min) ||
    form._operation.inputs.some((slot) => ["accessions", "ncbi_search"].includes(slot.docType) &&
      (form._downloadInputs?.[slot.key]?.().length || 0) < slot.min) ||
    form._operation.inputs.some((slot) => slot.docType === "file_paths" &&
      (form._fileInputs?.[slot.key]?.value.split(/\r?\n/).filter((path) => path.trim()).length || 0) < slot.min) ||
    (form._engine?.options || []).some((spec) => spec.required &&
      !form.querySelector(`.registered-engine-options [name="${spec.key}"]`)?.value);
  form.querySelector(".actions button[value=run]").disabled = invalid || !form._engine;
  const output = form.elements.namedItem("output");
  if (output && !form._nameEdited) output.value = registeredName(form);
  const sequences = form._pickers?.sequences?.leafItems() || [];
  const protein = sequences.length > 0 && sequences.every((it) => it.protein);
  for (const spec of form._engine?.options || []) {
    const field = form.querySelector(`.registered-engine-options [name="${spec.key}"]`);
    if (field && spec.dnaOnly) field.closest("label").hidden = !!protein;
    if (field && spec.visibleWhen) {
      const other = form.querySelector(`.registered-engine-options [name="${spec.visibleWhen.key}"]`);
      const value = other?.type === "checkbox" ? other.checked : other?.value;
      field.closest("label").hidden = !spec.visibleWhen.values.includes(value);
    }
    if (field?.dataset.autoDefault === "true" && spec.defaultWhen) {
      const target = form._pickers?.[spec.defaultWhen.slot]?.leafItems()[0];
      if (spec.type === "bool") field.checked = target?.kind === spec.defaultWhen.kind ? spec.defaultWhen.value : spec.default;
    }
  }
}

function previewRegisteredFiles(form, slot) {
  if (slot.preview === "sanger_samples") {
    const box = form.querySelector(`[data-preview="${slot.key}"]`);
    if (!box) return;
    const ids = form._pickers?.[slot.key]?.asPicked() || [];
    if (ids.length < 2) { box.textContent = "Choose at least two reads."; return; }
    api("POST", "/api/sanger/samples", { ids }).then(({ samples, alone }) => {
      if (!box.isConnected) return;
      box.textContent = samples.length
        ? `${samples.length} sample${samples.length === 1 ? "" : "s"}: ${samples.map((s) => `${s.sample} (${s.reads.length} reads)`).join(" · ")}${alone.length ? ` · Left out: ${alone.join(", ")}` : ""}`
        : "No two reads share a sample name.";
    }).catch((error) => { box.textContent = error.message; });
    return;
  }
  if (slot.preview !== "read_pairs") return;
  clearTimeout(form._previewTimer);
  form._previewTimer = setTimeout(async () => {
    const box = form.querySelector(`[data-preview="${slot.key}"]`);
    if (!box) return;
    const paths = form._fileInputs?.[slot.key]?.value.split(/\r?\n/).map((p) => p.trim()).filter(Boolean) || [];
    if (!paths.length) {
      const fromDisk = !form.querySelector(`textarea[name="${slot.key}"]`)?.parentElement.hidden;
      box.textContent = fromDisk ? "Pairs are found by file name: …_R1/…_R2 or …_1/…_2."
        : "Each reads item is one sample (its files already paired). Import FASTQ files to add reads to the library.";
      return;
    }
    const readType = form.querySelector('.registered-engine-options [name="read_type"]')?.value || "illumina";
    try {
      const groups = await api("POST", "/api/reads/pairs", { paths, read_type: readType });
      box.textContent = groups.map((g) => `${g.name}: ${g.files.length === 2 ? "paired" : "single-end"}, ${Math.ceil(g.size / 1e6)} MB`).join(" · ");
    } catch (error) { box.textContent = error.message; }
  }, 300);
}

async function refreshInputChoices(form) {
  for (const spec of form._engine?.options || []) {
    if (spec.choicesFrom?.kind !== "alignment_rows") continue;
    const field = form.querySelector(`.registered-engine-options [name="${spec.key}"]`);
    const id = form._pickers?.[spec.choicesFrom.slot]?.value;
    if (!field) continue;
    const before = field.value;
    field.replaceChildren(...spec.choices.map((c) => new Option(c.label, c.value)));
    if (!id) continue;
    const at = id;
    try {
      const rows = await api("GET", `/api/alignment/${encodeURIComponent(id)}`);
      if (form._pickers?.[spec.choicesFrom.slot]?.value !== at || !field.isConnected) continue;
      for (const row of rows) field.append(new Option(row.name, row.name));
      field.value = [...field.options].some((o) => o.value === before) ? before : spec.default;
    } catch (error) { /* the server validates again when the job is submitted */ }
  }
}

function renderOption(spec, value) {
  const label = document.createElement("label");
  label.className = spec.type === "bool" ? "check" : "";
  let field;
  if (spec.type === "choice") {
    field = document.createElement("select");
    for (const choice of spec.choices) field.append(new Option(choice.label, choice.value));
    field.value = value;
  } else {
    field = document.createElement("input"); field.autocomplete = "off";
    field.type = spec.type === "bool" ? "checkbox" : ["int", "float"].includes(spec.type) ? "number" : spec.type;
    if (spec.type === "bool") field.checked = value;
    else field.value = value ?? "";
    if (spec.type === "int") field.step = spec.step ?? "1";
    if (spec.type === "float") field.step = spec.step ?? "any";
    if (spec.min !== undefined) field.min = spec.min;
    if (spec.max !== undefined) field.max = spec.max;
  }
  field.name = spec.key;
  if (spec.type === "bool") label.append(field, document.createTextNode(` ${spec.label}`));
  else label.append(document.createTextNode(spec.label), field);
  if (spec.help) { const help = document.createElement("small"); help.className = "muted"; help.textContent = spec.help; label.append(help); }
  return label;
}

function currentOptions(form) {
  if (!form._engine) return {};
  const result = {};
  for (const spec of form._engine.options.filter((o) => !o.operations || o.operations.includes(form._operation.id))) {
    const field = form.querySelector(`.registered-engine-options [name="${spec.key}"]`);
    if (!field) continue;
    const choice = spec.type === "choice" ? spec.choices.find((c) => String(c.value) === field.value) : null;
    result[spec.key] = spec.type === "bool" ? field.checked
      : ["int", "float"].includes(spec.type) ? (field.value === "" ? null : Number(field.value))
      : spec.type === "choice" ? (choice ? choice.value : field.value) : field.value;
  }
  return result;
}

function rememberRegisteredEngine(form) {
  if (!form._engine || !form._operation) return;
  saveToolMemory(`options.${form._operation.id}.${form._engine.id}`, currentOptions(form));
}

function showRegisteredEngine(form, registry, engineId) {
  rememberRegisteredEngine(form);
  const engines = registry.engines.filter((e) => e.supports.includes(form._operation.id));
  form._engine = engines.find((e) => e.id === engineId) || engines[0];
  const zone = form.querySelector(".registered-engine-options"); zone.replaceChildren();
  if (!form._engine) return;
  for (const tab of form.querySelectorAll('.engine-tabs [role="tab"]')) {
    const active = tab.dataset.engine === form._engine.id;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    if (active) form.querySelector('.registered-engine').setAttribute('aria-labelledby', tab.id);
  }
  const remembered = toolMemory(`options.${form._operation.id}.${form._engine.id}`);
  const advanced = document.createElement("details");
  const summary = document.createElement("summary"); summary.textContent = "More options"; advanced.append(summary);
  for (const spec of form._engine.options.filter((o) => !o.operations || o.operations.includes(form._operation.id))) {
    const field = renderOption(spec, remembered[spec.key] ?? spec.default);
    const control = field.querySelector(`[name="${spec.key}"]`);
    control.dataset.autoDefault = Object.hasOwn(remembered, spec.key) ? "false" : "true";
    control.addEventListener("change", () => { control.dataset.autoDefault = "false"; });
    (spec.advanced ? advanced : zone).append(field);
  }
  if (advanced.children.length > 1) zone.append(advanced);
  form.querySelector(".registered-blurb").textContent = form._engine.blurb;
  saveToolMemory(`engine.${form._operation.id}`, form._engine.id);
  refreshInputChoices(form);
  for (const slot of form._operation.inputs) previewRegisteredFiles(form, slot);
  updateRegisteredForm(form);
}

function renderRegisteredNcbiSearch(form, host, slot) {
  const state = form._ncbiSearchState ??= { query: "", rows: [], count: 0, ticked: new Set() };
  const controls = document.createElement("div"); controls.className = "dl-query";
  const query = document.createElement("input"); query.autocomplete = "off"; query.type = "search"; query.name = "query";
  query.value = state.query;
  query.placeholder = "e.g. rbcL Gracilaria, or Fucus[organism] AND complete genome";
  const search = document.createElement("button"); search.type = "button"; search.textContent = "Search";
  controls.append(query, search);
  const count = document.createElement("p"); count.className = "muted";
  const results = document.createElement("div"); results.className = "registered-ncbi-results";
  const more = document.createElement("button"); more.type = "button"; more.className = "secondary";
  more.textContent = "Show 100 more"; more.hidden = true;
  host.append(controls, count, results, more);
  form._downloadInputs[slot.key] = () => [...state.ticked];
  const update = () => {
    count.textContent = state.count ? `${state.count.toLocaleString()} found · ${state.ticked.size} ticked` : "Nothing found.";    more.hidden = state.rows.length >= state.count;
    updateRegisteredForm(form);
  };
  const render = () => {
    const scroll = results.scrollTop; results.replaceChildren();
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const title of ["", "Accession", "Description", "Organism", "Length", "Date"]) {
      const th = document.createElement("th"); th.textContent = title; head.append(th);
    }
    const all = document.createElement("input"); all.type = "checkbox"; all.title = "All shown";
    all.checked = state.rows.length > 0 && state.rows.every((row) => state.ticked.has(row.accession));
    all.onchange = () => { for (const row of state.rows) all.checked ? state.ticked.add(row.accession) : state.ticked.delete(row.accession); render(); };
    head.firstChild.append(all); table.append(head);
    for (const row of state.rows) {
      const tr = document.createElement("tr"); const td = document.createElement("td");
      const box = document.createElement("input"); box.type = "checkbox"; box.checked = state.ticked.has(row.accession);
      box.onchange = () => { box.checked ? state.ticked.add(row.accession) : state.ticked.delete(row.accession); update(); };
      td.append(box); tr.append(td);
      for (const value of [row.accession, row.title, row.organism, `${Number(row.length).toLocaleString()} bp`, row.date]) {
        const cell = document.createElement("td"); cell.textContent = value; tr.append(cell);
      }
      tr.onclick = (event) => { if (event.target !== box) box.click(); };
      table.append(tr);
    }
    results.append(table); results.scrollTop = scroll; update();
  };
  const fetch = async (append = false) => {
    const term = append ? state.query : query.value.trim(); if (!term) return;
    count.textContent = "Searching…";
    try {
      const data = await api("GET", `/api/ncbi/search?query=${encodeURIComponent(term)}&start=${append ? state.rows.length : 0}`);
      if (!append) {
        state.query = term; state.rows = []; state.ticked.clear();
        form._searchName = term.slice(0, 60);
        if (!form._nameEdited) form.elements.namedItem("output").value = form._searchName;
      }
      state.count = data.count; state.rows.push(...data.results); render();
    } catch (error) { count.textContent = error.message; }
  };
  search.onclick = () => fetch(); more.onclick = () => fetch(true);
  query.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); fetch(); } };
  render();
}

function renderRegisteredOperation(form, registry, operation, previous = {}) {
  rememberRegisteredEngine(form);
  form._operation = operation; form._engine = null;
  form.querySelector(".actions button[value=run]").textContent = operation.action || form.closest("dialog").querySelector("h3").textContent;
  const content = form.querySelector(".registered-content"); content.replaceChildren();
  if (form._groupOperations?.length > 1) {
    const method = document.createElement("label");
    method.textContent = operation.category === "assemble" ? "Samples" : "Method";
    const choices = document.createElement("select"); choices.name = "operation_mode";
    for (const candidate of form._groupOperations) choices.append(new Option(candidate.label, candidate.id));
    choices.value = operation.id;
    choices.onchange = () => form._activateOperation(choices.value);
    method.append(choices); content.append(method);
  }
  const about = document.createElement("p"); about.className = "muted tab-about"; about.textContent = operation.description;
  content.append(about);
  form._pickers = {}; form._fileInputs = {}; form._textInputs = {}; form._specialInputs = {}; form._downloadInputs = {};
  if (operation.presets === "primer_pairs") {
    const label = document.createElement("label"); label.textContent = "Load a pair from…";
    const select = document.createElement("select"); select.name = "primer_source";
    select.append(new Option("Type them below", "")); label.append(select); content.append(label);
  }
  for (const slot of operation.inputs) {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend"); legend.textContent = slot.label;
    const host = document.createElement("div"); fieldset.append(legend, host); content.append(fieldset);
    if (slot.docType === "accessions") {
      const textarea = document.createElement("textarea"); textarea.name = slot.key; textarea.rows = 5;
      textarea.placeholder = "NC_001677\nMK000001.1, PX399654";
      textarea.value = previous.accessionsText || "";
      host.append(textarea);
      form._downloadInputs[slot.key] = () => textarea.value.split(/[\s,;]+/).filter(Boolean);
      continue;
    }
    if (slot.docType === "ncbi_search") {
      renderRegisteredNcbiSearch(form, host, slot);
      continue;
    }
    if (slot.docType === "text") {
      const input = document.createElement("input"); input.autocomplete = "off"; input.type = "text"; input.name = slot.key;
      input.setAttribute("aria-label", slot.label);
      input.autocomplete = "off"; input.spellcheck = false; input.value = previous[slot.key]?.[0] || "";
      host.append(input); form._textInputs[slot.key] = input; continue;
    }
    if (slot.docType === "blast_queries") {
      const special = viewerQueries() || graphQueries();
      const pickerHost = document.createElement("div"); host.append(pickerHost);
      const picker = form._pickers[slot.key] = new ToolPicker(pickerHost, inputSpec(slot), {
        onChange: () => updateRegisteredForm(form),
      });
      picker.fillFromSelection();
      if (special?.length) {
        form._specialInputs[slot.key] = special.map(({ label, ...query }) => query);
        const note = document.createElement("div"); note.className = "registered-special-queries";
        const title = document.createElement("p"); title.className = "muted";
        title.textContent = special[0].sequence ? "The selection in the viewer:" : "The nodes selected in the graph:";
        const list = document.createElement("ul");
        for (const query of special.slice(0, 20)) {
          const row = document.createElement("li"); row.textContent = query.label; list.append(row);
        }
        const other = document.createElement("button"); other.type = "button"; other.className = "secondary";
        other.textContent = "Use sequences from the library instead";
        other.onclick = () => { form._specialInputs[slot.key] = null; note.remove(); pickerHost.hidden = false; updateRegisteredForm(form); };
        note.append(title, list, other); host.prepend(note); pickerHost.hidden = true;
      }
      continue;
    }
    if (slot.docType === "file_paths") {
      // Reads from the library (reads items, sent as "item:<id>") or files on disk (their paths):
      // form._fileInputs[slot.key].value is either, one per line, so the rest of the form reads it the same way
      const before = (previous[slot.key] || []).map((v) => v.trim()).filter(Boolean);
      const fromItems = before.filter((v) => v.startsWith("item:")).map((v) => v.slice(5));
      const haveReads = items.some((it) => it.kind === "reads" && !it.missing);
      let mode = fromItems.length ? "library" : before.length ? "disk" : toolMemory("readsFrom").from || (haveReads ? "library" : "disk");
      const changed = () => { updateRegisteredForm(form); previewRegisteredFiles(form, slot); };
      const switcher = document.createElement("div"); switcher.className = "segmented reads-from";
      switcher.innerHTML = `<button type="button" data-from="library">From the library</button><button type="button" data-from="disk">From disk</button>`;
      const libraryHost = document.createElement("div"), diskHost = document.createElement("div");
      const picker = new ToolPicker(libraryHost, { label: "Reads items", noun: "reads", kinds: ["reads"], min: slot.min, max: slot.max,
        accepts: (it) => (it.read_files?.length ? null : "its read files were deleted") }, { onChange: changed });
      if (fromItems.length) picker.set(fromItems.filter((id) => itemById(id)));
      else picker.fillFromSelection();
      const textarea = document.createElement("textarea"); textarea.name = slot.key; textarea.rows = 4;
      textarea.placeholder = "One file per line; Illumina pairs are found by name.\nC:\\runs\\S1_L001_R1_001.fastq.gz";
      textarea.value = before.filter((v) => !v.startsWith("item:")).join("\n");
      textarea.addEventListener("input", changed);
      diskHost.append(textarea);
      if (inPywebview()) {
        const choose = document.createElement("button"); choose.type = "button"; choose.className = "secondary";
        choose.textContent = "Choose files…";
        choose.onclick = async () => {
          const paths = await window.pywebview.api.pick_reads();
          textarea.value = [textarea.value.trim(), ...paths].filter(Boolean).join("\n");
          changed();
        };
        diskHost.append(choose);
      }
      form._fileInputs[slot.key] = {
        get value() { return mode === "library" ? picker.ids().map((id) => `item:${id}`).join("\n") : textarea.value; },
      };
      const show = (next) => {
        mode = next;
        saveToolMemory("readsFrom", { from: mode });
        libraryHost.hidden = mode !== "library"; diskHost.hidden = mode !== "disk";
        for (const b of switcher.children) b.classList.toggle("active", b.dataset.from === mode);
        changed();
      };
      switcher.onclick = (e) => { const b = e.target.closest("button"); if (b) show(b.dataset.from); };
      const preview = document.createElement("p"); preview.className = "muted registered-file-preview";
      preview.dataset.preview = slot.key;
      host.append(switcher, libraryHost, diskHost, preview);
      show(mode);
      continue;
    }
    const picker = form._pickers[slot.key] = new ToolPicker(host, inputSpec(slot), {
      exclude: slot.key === "sequences" ? (id) => id === form._pickers.add_to?.value
        : slot.docType === "annotated_sequence" ? (id) => form._pickers.sequences?.ids().includes(id) : undefined,
      onChange: () => {
        if (slot.key === "add_to") form._pickers.sequences?.render();
        if (slot.key === "sequences") form._pickers.reference?.render();
        updateRegisteredForm(form);
        refreshInputChoices(form);
        previewRegisteredFiles(form, slot);
      },
    });
    const compatible = previous[slot.key]?.filter((id) => {
      const item = itemById(id);
      return item && inputSpec(slot).kinds.includes(item.kind);
    });
    if (compatible?.length) picker.set(compatible);
    else if (slot.key === "add_to") {
      const own = new Set(form._pickers.sequences?.ids() || []);
      const selectedTarget = selected.map(itemById).find((it) => it?.kind === "alignment");
      const target = selectedTarget || items.find((it) => !it.missing && inputSpec(slot).kinds.includes(it.kind) && !own.has(it.id));
      picker.set(target ? [target.id] : []);
    } else {
      picker.fillFromSelection();
      if (slot.max === 1 && !picker.value) {
        const first = items.find((it) => !it.missing && inputSpec(slot).kinds.includes(it.kind) &&
          !slotReason(inputSpec(slot), it) && (slot.docType !== "annotated_sequence" ||
            !form._pickers.sequences?.ids().includes(it.id)));
        if (first) picker.set([first.id]);
      }
    }
    if (slot.preview === "sanger_samples") {
      const preview = document.createElement("p"); preview.className = "muted registered-file-preview";
      preview.dataset.preview = slot.key; host.append(preview);
    }
  }
  if (operation.presets === "primer_pairs") loadPrimerSources(form);
  const engineSection = document.createElement("div"); engineSection.className = "registered-engine";
  const engineLabel = document.createElement("label"); engineLabel.textContent = "Engine";
  const engines = registry.engines.filter((e) => e.supports.includes(operation.id));
  if (operation.engineTabs) {
    const tabs = document.createElement("div"); tabs.className = "tabs engine-tabs";
    tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", operation.engineTabsLabel || "Method");
    engineSection.id = `${operation.id.replaceAll('.', '-')}-engine-panel`;
    engineSection.setAttribute("role", "tabpanel");
    for (const engine of [...engines].sort((a,b) => (a.tabOrder ?? a.order ?? 0) - (b.tabOrder ?? b.order ?? 0))) {
      const tab = document.createElement("button"); tab.type = "button";
      tab.dataset.engine = engine.id; tab.id = `${operation.id}-${engine.id}-tab`;
      tab.textContent = engine.tabLabel || engine.label; tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", engineSection.id);
      tab.onclick = () => showRegisteredEngine(form, registry, engine.id);
      tabs.append(tab);
    }
    tabs.onkeydown = event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const buttons = [...tabs.children], at = buttons.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
        : (at + (event.key === "ArrowLeft" ? -1 : 1) + buttons.length) % buttons.length;
      buttons[next].click(); buttons[next].focus();
    };
    content.prepend(tabs);
  } else if (engines.length === 1) {
    const plain = document.createElement("strong"); plain.textContent = engines[0].label; engineLabel.append(plain);
  } else {
    const select = document.createElement("select"); select.name = "engine";
    for (const engine of engines) select.append(new Option(engine.label, engine.id));
    select.onchange = () => showRegisteredEngine(form, registry, select.value);
    engineLabel.append(select);
  }
  const blurb = document.createElement("p"); blurb.className = "muted registered-blurb";
  const options = document.createElement("div"); options.className = "registered-engine-options";
  if (!operation.engineTabs) engineSection.append(engineLabel);
  engineSection.append(blurb, options); content.append(engineSection);
  let output;
  if (operation.outputMode === "update") {
    const label = document.createElement("label"); label.className = "check";
    const copy = document.createElement("input"); copy.type = "checkbox"; copy.name = "save_copy";
    copy.checked = !!previous.save_copy;
    label.append(copy, document.createTextNode(" Save as a copy")); content.append(label);
  } else if (!["auto", "none"].includes(operation.outputMode)) {
    const name = document.createElement("label"); name.textContent = operation.outputLabel || "Name";
    output = document.createElement("input"); output.autocomplete = "off"; output.type = "text"; output.name = "output"; output.required = true;
    output.value = previous.output || ""; name.append(output); content.append(name);
  }
  const engineId = toolMemory(`engine.${operation.id}`);
  const chosen = engines.find((e) => e.id === engineId) || engines[0];
  const select = engineLabel.querySelector("select"); if (select) select.value = chosen?.id || "";
  showRegisteredEngine(form, registry, chosen?.id);
  if (operation.prefillTargetFromViewer) {
    const shown = seqShown?.mode === "single" ? seqShown.items[0] : null;
    const selection = seqView?.selection;
    if (shown?.id === form._pickers?.template?.value && selection && !selection.wraps && !selection.parts &&
        (selection.c0 > 0 || selection.c1 < seqView.length - 1)) {
      for (const [key, value] of [["target_start", selection.c0 + 1], ["target_end", selection.c1 + 1]]) {
        const field = form.querySelector(`.registered-engine-options [name="${key}"]`);
        if (field && !field.value) field.value = value;
      }
    }
  }
  if (output && !form._nameEdited) output.value = registeredName(form);
  if (!["update", "none"].includes(operation.outputMode)) renderResultDestination(form);
  updateRegisteredForm(form);
}

// ── Dialog width ─────────────────────────────────────────────────────────
// Chosen from what all of a dialog's tabs hold, once when it opens, so switching
// tabs never resizes it; tools don't declare it. Forms read best narrow; a results
// table needs room for its columns; more than four tabs become a column of their own.
const TABLE_INPUTS = new Set(["ncbi_search"]);

function dialogSize(operations, groups) {
  if (operations.some((o) => o.inputs.some((slot) => TABLE_INPUTS.has(slot.docType)))) return "wide";
  if (groups.size > 4) return "medium";
  return "normal";
}

function selectedFitsOperation(operation) {
  return operation.inputs.every((slot) => !["file_paths", "text", "accessions", "ncbi_search"].includes(slot.docType) &&
    selectionFor(inputSpec(slot)).length >= slot.min);
}

async function openRegisteredCategory(categoryId) {
  try {
    if (categoryId === "blast") registeredToolsPromise = null; // local databases may have changed in Settings
    const registry = await registeredTools();
    const category = registry.categories.find((c) => c.id === categoryId);
    const operations = registry.operations.filter((o) => o.category === categoryId && registry.engines.some((e) => e.supports.includes(o.id)));
    if (!category || !operations.length) throw Error("No tools are available in this category.");
    if (!registeredDialogs.has(categoryId)) registeredDialogs.set(categoryId, makeRegisteredDialog(category));
    const { dialog, form, navigation, error } = registeredDialogs.get(categoryId);
    form._toolName = categoryId; form._nameEdited = false; form._searchName = ""; delete form._destinationChoice;
    navigation.replaceChildren();
    const groups = new Map();
    for (const operation of operations) {
      const label = operation.group || operation.label;
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(operation);
    }
    navigation.classList.toggle("vertical", groups.size > 4);
    navigation.parentElement.classList.toggle("vertical", groups.size > 4);
    const size = dialogSize(operations, groups);
    dialog.classList.toggle("size-medium", size === "medium");
    dialog.classList.toggle("size-wide", size === "wide");
    const matches = operations.filter(selectedFitsOperation);
    const last = toolMemory(`operation.${categoryId}`);
    const initial = matches.length === 1 ? matches[0] : operations.find((o) => o.id === last) || operations[0];
    navigation.hidden = groups.size === 1 && !!initial.engineTabs;
    const activate = (operation) => {
      const previous = { ...Object.fromEntries(Object.entries(form._pickers || {}).map(([key, p]) => [key, p.ids()])),
        ...Object.fromEntries(Object.entries(form._fileInputs || {}).map(([key, field]) => [key, field.value.split(/\r?\n/)])),
        ...Object.fromEntries(Object.entries(form._textInputs || {}).map(([key, field]) => [key, [field.value]])),
        output: form.elements.namedItem("output")?.value,
        accessionsText: form._operation?.id === "download.accessions" ? form.querySelector('textarea[name="accessions"]')?.value : form._accessionsText,
        save_copy: form.elements.namedItem("save_copy")?.checked };
      for (const button of navigation.children) button.classList.toggle("active", button.dataset.group === (operation.group || operation.label));
      form._accessionsText = previous.accessionsText;
      form._groupOperations = groups.get(operation.group || operation.label);
      renderRegisteredOperation(form, registry, operation, previous);
      saveToolMemory(`operation.${categoryId}`, operation.id);
    };
    form._activateOperation = (id) => {
      const operation = operations.find((item) => item.id === id);
      if (operation) activate(operation);
    };
    for (const [group, choices] of groups) {
      const button = document.createElement("button"); button.type = "button";
      button.dataset.group = group; button.textContent = group;
      button.onclick = () => activate(choices.find((item) => item.id === form._operation?.id) || choices[0]);
      navigation.append(button);
    }
    form._pickers = {}; form._operation = null; form._engine = null;
    activate(initial); error.hidden = true;
    const toolbar = $("toolbar");
    const top = toolbar.getBoundingClientRect().height ? toolbar.getBoundingClientRect().bottom + 8 : 8;
    dialog.style.setProperty("--tool-dialog-top", `${Math.min(top, window.innerHeight - 80)}px`);
    dialog.showModal();
  } catch (error) { toast(error.message, true); }
}

async function submitRegisteredTool(form, event) {
  if (event.submitter?.value !== "run") return;
  event.preventDefault();
  const error = form.querySelector(".error");
  if (form._operation.outputMode === "update" && !form.elements.namedItem("save_copy")?.checked && hasUnsavedChanges()) {
    error.textContent = "Save or undo open edits before updating sequences."; error.hidden = false; return;
  }
  const problem = Object.entries(form._pickers).filter(([key]) =>
    !(key === "queries" && form._specialInputs?.queries?.length)).map(([, picker]) => picker.problem()).find(Boolean);
  if (problem) { error.textContent = problem; error.hidden = false; return; }
  const body = {
    operation: form._operation.id, engine: form._engine.id,
    inputs: { ...Object.fromEntries(Object.entries(form._pickers).map(([key, picker]) => [key, picker.asPicked()])),
      ...Object.fromEntries(Object.entries(form._textInputs || {}).map(([key, field]) => [key, [field.value.trim()]])),
      ...Object.fromEntries(Object.entries(form._specialInputs || {}).filter(([, value]) => value?.length)),
      ...Object.fromEntries(Object.entries(form._fileInputs || {}).map(([key, field]) =>
        [key, field.value.split(/\r?\n/).map((p) => p.trim()).filter(Boolean)])),
      ...Object.fromEntries(Object.entries(form._downloadInputs || {}).map(([key, read]) => [key, read()])) },
    options: currentOptions(form), output: form.elements.namedItem("output")?.value.trim() || "",
    save_copy: !!form.elements.namedItem("save_copy")?.checked,
    context: { code: seqView?.rows[0]?.code || 1 },
  };
  const destination = form.querySelector(".result-destination select");
  if (destination) {
    if (!destination.value) { error.textContent = "Choose where to save the result."; error.hidden = false; return; }
    body.folder = destination.value === "/" ? null : destination.value;
  }
  try {
    rememberRegisteredEngine(form);
    await api("POST", "/api/tool-registry/run", body);
    form.closest("dialog").close(); pollJobs();
  } catch (e) { error.textContent = e.message; error.hidden = false; }
}
