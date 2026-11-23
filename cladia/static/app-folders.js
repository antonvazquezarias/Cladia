// Library navigation and folder mutations. The open viewer is independent of
// sidebar selection: browsing folders never discards unsaved viewer edits.
const parentOf = path => path?.includes("/") ? path.slice(0, path.lastIndexOf("/")) : null;
const baseName = path => path.slice(path.lastIndexOf("/") + 1);
const inside = (path, folder) => !!path && (path === folder || path.startsWith(`${folder}/`));
const joinPath = (parent, name) => parent ? `${parent}/${name}` : name;
const remapFolderPath = (path, old, replacement) => path === old ? replacement
  : inside(path, old) ? joinPath(replacement, path.slice(old.length + 1)) : path;
const savedBranches = loadJSON("cladia.openFolders").list;
const openBranches = new Set(Array.isArray(savedBranches) ? savedBranches.filter(p => typeof p === "string") : []);
const FOLDER_FILTER_FROM = 12;
const SPRING = 600;
let springTimer = null, springPath = null;
let libraryViewSave = Promise.resolve(), libraryViewChanged = false;

async function loadLibraryView() {
  try {
    const saved = (await api("GET", "/api/settings")).library_view;
    if (libraryViewChanged) return;
    if (saved) {
      currentFolder = saved.folder;
      openBranches.clear();
      for (const path of saved.expanded) openBranches.add(path);
      folderSorts = saved.sorts || {};
      $("library-sort").title = librarySortTitle();
    } else if (await saveLibraryView()) {  // migrate browser preferences once, then forget them
      try { localStorage.removeItem("cladia.folder"); localStorage.removeItem("cladia.openFolders"); } catch (e) {}
    }
  } catch (e) { toast(`Could not load folder preferences: ${e.message}`, true); }
}

// Settings are the one record of the folder shown and the open branches (browser
// storage is per port, and the port changes at every start); resolves to whether it saved
function saveLibraryView() {
  libraryViewChanged = true;
  const view = {folder: currentFolder, expanded: [...openBranches], sorts: folderSorts};
  libraryViewSave = libraryViewSave.then(() => api("PUT", "/api/settings", {library_view: view}))
    .then(() => true, e => { toast(`Could not save folder preferences: ${e.message}`, true); return false; });
  return libraryViewSave;
}

function pruneFolderState() {
  let changed = false;
  while (currentFolder && !folders.includes(currentFolder)) {
    currentFolder = parentOf(currentFolder);
    changed = true;
  }
  const valid = new Set(folders);
  for (const path of openBranches) if (!valid.has(path)) { openBranches.delete(path); changed = true; }
  for (const path of Object.keys(folderSorts)) if (path && !valid.has(path)) { delete folderSorts[path]; changed = true; }
  if (changed) saveLibraryView();
}

function remapFolderState(old, replacement) {
  currentFolder = remapFolderPath(currentFolder, old, replacement);
  const expanded = [...openBranches].map(path => remapFolderPath(path, old, replacement)).filter(Boolean);
  openBranches.clear();
  for (const path of expanded) openBranches.add(path);
  // each folder's sort follows it; a deleted folder's goes (its parent, where its items move, keeps its own)
  const sorts = {};
  for (const [path, sort] of Object.entries(folderSorts)) {
    const moved = path ? remapFolderPath(path, old, replacement) : "";
    if (moved === null || (path === old && replacement === parentOf(old))) continue;  // deleted
    sorts[moved] = sort;
  }
  folderSorts = sorts;
  saveLibraryView();
}

function setFolder(path, render = true) {
  currentFolder = path;
  if (render) {
    $("filter").value = "";
    selected = [];
    anchorId = null;
    lastClick = {id: null, at: 0};
    closeFolderPopup();
  }
  saveLibraryView();
  renderFolderButton();
  $("library-sort").title = librarySortTitle();
  if (render) { renderList(); scheduleToolStates(); }
}

function renderFolderButton() {
  const button = $("folder-button");
  button.innerHTML = '<span>📁</span><span class="name"></span><span>▾</span>';
  button.querySelector(".name").textContent = $("filter").value.trim() ? "Search · all folders"
    : currentFolder ? currentFolder.split("/").join(" › ") : "Library";
  button.title = `${currentFolder || "Library (no folder)"}: choose a folder; drop items here to move them`;
  button.setAttribute("aria-haspopup", "tree");
  button.setAttribute("aria-expanded", String(!$("folder-popup").hidden));
}

function* ancestors(path) { for (let p = parentOf(path); p; p = parentOf(p)) yield p; }

function openFolderPopup() {
  const popup = $("folder-popup");
  let changed = false;
  for (const path of ancestors(currentFolder)) if (!openBranches.has(path)) { openBranches.add(path); changed = true; }
  if (changed) saveLibraryView();
  $("folder-filter").hidden = folders.length < FOLDER_FILTER_FROM;
  renderFolderTree();
  popup.hidden = false;
  const box = $("folder-button").getBoundingClientRect();
  popup.style.left = `${box.left}px`;
  popup.style.top = `${box.bottom + 2}px`;
  popup.style.width = `${box.width}px`;
  $("folder-button").setAttribute("aria-expanded", "true");
  const active = [...$("folder-tree").children].find(row => row.dataset.path === (currentFolder || ""));
  active?.scrollIntoView({block: "nearest"});
  if (!draggedIds && !draggedFolder) {
    if (!$("folder-filter").hidden) $("folder-filter").focus();
    else active?.focus({preventScroll: true});
  }
}

function cancelFolderSpring() {
  clearTimeout(springTimer);
  springTimer = springPath = null;
}

function closeFolderPopup() {
  $("folder-popup").hidden = true;
  $("folder-filter").value = "";
  $("folder-button").setAttribute("aria-expanded", "false");
  cancelFolderSpring();
}

function renderFolderTree() {
  const tree = $("folder-tree"), scroll = tree.scrollTop;
  const focused = tree.contains(document.activeElement) ? document.activeElement.dataset.path : undefined;
  const filter = $("folder-filter").value.trim().toLowerCase();
  const children = new Set(folders.map(parentOf).filter(Boolean));
  const counts = new Map(), totals = new Map();
  for (const it of items) if (!listOwner(it)) {
    const path = it.folder || "";
    counts.set(path, (counts.get(path) || 0) + 1);
    for (let p = path; p; p = parentOf(p)) totals.set(p, (totals.get(p) || 0) + 1);
  }
  const matches = new Set(folders.filter(path => path.toLowerCase().includes(filter)));
  $("folder-empty").hidden = !filter || matches.size > 0;
  const shown = filter ? new Set([...matches].flatMap(path => [path, ...ancestors(path)])) : null;
  tree.replaceChildren();
  function row(path, label, depth) {
    const li = document.createElement("li");
    li.dataset.path = path || "";
    li.dataset.match = String(!filter || !!path && matches.has(path));
    li.className = path === currentFolder ? "current" : "";
    li.tabIndex = -1;
    li.title = path || "Items outside folders";
    li.setAttribute("role", "treeitem");
    li.setAttribute("aria-level", String(Math.max(1, depth)));
    li.setAttribute("aria-selected", String(path === currentFolder));
    li.style.paddingLeft = `${6 + depth * 14}px`;
    const arrow = Object.assign(document.createElement("span"), {className: "arrow"});
    arrow.setAttribute("aria-hidden", "true");
    if (path && children.has(path)) {
      const expanded = !!shown || openBranches.has(path);
      arrow.textContent = expanded ? "▾" : "▸";
      li.setAttribute("aria-expanded", String(expanded));
      arrow.onclick = e => { e.stopPropagation(); toggleBranch(path); };
    }
    const count = document.createElement("small");
    count.textContent = counts.get(path || "") || 0;
    count.title = `${count.textContent} items here; ${Math.max(0, (totals.get(path) || 0) - Number(count.textContent))} in subfolders`;
    li.append(arrow, Object.assign(document.createElement("span"), {className: "label", textContent: label}), count);
    li.onclick = () => setFolder(path);
    li.onfocus = () => { for (const sibling of tree.children) sibling.tabIndex = sibling === li ? 0 : -1; };
    if (path) {
      li.oncontextmenu = e => { e.preventDefault(); e.stopPropagation(); openFolderRowMenu(e, path); };
      li.draggable = true;
      li.ondragstart = e => {
        draggedFolder = path; draggedIds = null;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", path);
      };
    }
    dropOn(li, path, () => springOpen(path));
    tree.append(li);
  }
  row(null, "Library (no folder)", 0);
  for (const path of folders) {
    if (shown ? shown.has(path) : [...ancestors(path)].every(p => openBranches.has(p))) row(path, baseName(path), path.split("/").length);
  }
  tree.scrollTop = scroll;
  const active = [...tree.children].find(row => row.dataset.path === (focused ?? currentFolder ?? ""));
  (active || tree.firstElementChild).tabIndex = 0;
  if (focused !== undefined) active?.focus({preventScroll: true});
  const add = $("folder-new");
  add.textContent = currentFolder ? `New folder in ${baseName(currentFolder)}…` : "New folder…";
  add.onclick = () => folderDialog({parent: currentFolder});
  add.ondragover = e => { if (draggedIds?.length) { e.preventDefault(); cancelFolderSpring(); add.classList.add("drop"); } };
  add.ondragleave = () => add.classList.remove("drop");
  add.ondrop = e => {
    if (!draggedIds?.length) return;
    e.preventDefault();
    const moving = [...draggedIds];
    add.classList.remove("drop");
    folderDialog({parent: currentFolder, moving});
  };
}

function focusFolderRow(row) { row?.focus(); row?.scrollIntoView({block: "nearest"}); }

function folderFilterKeydown(e) {
  if (!["Enter", "ArrowDown", "ArrowUp", "Escape"].includes(e.key)) return;
  e.preventDefault(); e.stopPropagation();
  if (e.key === "Escape") { closeFolderPopup(); return $("folder-button").focus(); }
  const matches = [...$("folder-tree").children].filter(row => row.dataset.match === "true");
  if (e.key === "Enter") { if (matches[0]) { matches[0].click(); $("folder-button").focus(); } }
  else focusFolderRow(e.key === "ArrowUp" ? matches.at(-1) : matches[0]);
}

function folderTreeKeydown(e) {
  const row = e.target.closest('[role="treeitem"]');
  if (!row || !["Enter", " ", "ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End", "Escape", "F2", "Delete"].includes(e.key)) return;
  e.preventDefault(); e.stopPropagation();
  const rows = [...$("folder-tree").children], index = rows.indexOf(row), path = row.dataset.path || null;
  if (e.key === "Escape") { closeFolderPopup(); return $("folder-button").focus(); }
  if (e.key === "F2") { if (path) folderDialog({renaming: path}); return; }
  if (e.key === "Delete") { if (path) { closeFolderPopup(); deleteFolder(path); } return; }
  if (e.key === "Enter" || e.key === " ") { row.click(); return $("folder-button").focus(); }
  if (e.key === "Home" || e.key === "End") return focusFolderRow(e.key === "Home" ? rows[0] : rows.at(-1));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") return focusFolderRow(rows[index + (e.key === "ArrowDown" ? 1 : -1)]);
  if (e.key === "ArrowRight" && row.hasAttribute("aria-expanded")) {
    if (row.getAttribute("aria-expanded") === "false") toggleBranch(path);
    else focusFolderRow(rows[index + 1]);
  }
  if (e.key === "ArrowLeft") {
    if (row.getAttribute("aria-expanded") === "true" && !$("folder-filter").value.trim()) toggleBranch(path);
    else focusFolderRow(rows.find(r => r.dataset.path === (parentOf(path) || "")));
  }
}

function toggleBranch(path) {
  openBranches.has(path) ? openBranches.delete(path) : openBranches.add(path);
  saveLibraryView(); renderFolderTree();
}

function springOpen(path) {
  if (springPath === path && springTimer) return;
  cancelFolderSpring();
  if (!path || openBranches.has(path) || !folders.some(f => parentOf(f) === path)) return;
  springPath = path;
  springTimer = setTimeout(() => {
    cancelFolderSpring(); openBranches.add(path); saveLibraryView(); renderFolderTree();
  }, SPRING);
}

function dropOn(el, path, over = null) {
  const takes = () => draggedIds?.some(id => {
    const it = itemById(id);
    return it && (listOwner(it) || (it.folder || null) !== path);
  }) || draggedFolder && !inside(path, draggedFolder) && parentOf(draggedFolder) !== path;
  el.ondragover = e => { if (takes()) { e.preventDefault(); el.classList.add("drop"); over?.(); } };
  el.ondragleave = e => { if (!el.contains(e.relatedTarget)) { el.classList.remove("drop"); cancelFolderSpring(); } };
  el.ondrop = e => {
    if (!takes()) return;
    e.preventDefault(); e.stopPropagation(); el.classList.remove("drop");
    const [folder, ids] = [draggedFolder, draggedIds];
    closeFolderPopup();
    if (folder) moveFolder(folder, path); else moveToFolder(ids, path);
  };
}

function openFolderRowMenu(e, path) {
  showMenu(e, [
    ["Open", () => setFolder(path)],
    ["New folder inside…", () => folderDialog({parent: path})],
    ["Rename…", () => folderDialog({renaming: path})],
    ["Move to…", () => openFolderMenu(e, null, path)],
    "-", ["Delete folder…", () => deleteFolder(path), false, "danger"],
  ]);
}

function openFolderMenu(at, moving = null, movingFolder = null) {
  closeFolderPopup();
  const menu = $("list-menu");
  menu.replaceChildren(Object.assign(document.createElement("button"), {textContent: "Move to:", disabled: true}));
  for (const folder of [null, ...folders]) {
    if (movingFolder && (inside(folder, movingFolder) || folder === parentOf(movingFolder))) continue;
    const button = document.createElement("button");
    button.textContent = folder ? `${"\u2003".repeat(folder.split("/").length - 1)}📁 ${baseName(folder)}` : "Library (no folder)";
    button.title = folder || "Library (no folder)";
    button.disabled = !!moving?.length && moving.every(id => {
      const it = itemById(id); return it && !listOwner(it) && (it.folder || null) === folder;
    });
    button.onclick = () => { closeListMenu(); movingFolder ? moveFolder(movingFolder, folder) : moveToFolder(moving, folder); };
    menu.append(button);
  }
  if (moving) {
    menu.append(document.createElement("hr"));
    const add = Object.assign(document.createElement("button"), {textContent: currentFolder ? `New folder in ${baseName(currentFolder)}…` : "New folder…"});
    add.onclick = () => { closeListMenu(); folderDialog({parent: currentFolder, moving}); };
    menu.append(add);
  }
  menu.hidden = false;
  menu.style.left = `${Math.max(4, Math.min(at.clientX, window.innerWidth - menu.offsetWidth - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(at.clientY, window.innerHeight - menu.offsetHeight - 4))}px`;
}

async function moveToFolder(ids, folder, navigate = false) {
  if (!ids?.length) return false;
  const moving = ids.filter(id => !listAncestors(itemById(id)).some(owner => ids.includes(owner)));
  const members = moving.filter(id => listOwner(itemById(id)));
  const whole = moving.filter(id => !members.includes(id));
  if (members.length && hasUnsavedChanges()) { toast("Save (or undo) your changes first (Ctrl+S)", true); return false; }
  const viewed = [...shownIds], originalItems = new Map(items.map(it => [it.id, it]));
  let extracted = null, failure = null;
  try {
    if (folder && !folders.includes(folder)) await api("POST", "/api/folders", {name: baseName(folder), parent: parentOf(folder)});
    if (members.length) extracted = (await api("POST", "/api/library/take-out", {ids: members, folder})).ids;
    if (whole.length) await api("POST", "/api/library/move", {ids: whole, folder});
  } catch (e) { failure = e; }
  try { await loadList(); }
  catch (e) { toast((failure || e).message, true); return false; }
  if (extracted && viewed.join() === shownIds.join()) {
    // List-member IDs are record indices. Extraction renumbers the survivors;
    // keep the viewer on the same records rather than the new occupants of old IDs.
    const moved = new Map(members.map((id, index) => [id, extracted[index]]));
    const removed = members.map(id => originalItems.get(id));
    const remapped = viewed.flatMap(id => {
      if (moved.has(id)) return [moved.get(id)];
      const it = originalItems.get(id);
      if (it?.parent) {
        const earlier = removed.filter(row => row.parent === it.parent && row.record < it.record).length;
        return [`${it.parent}-${it.record - earlier}`];
      }
      if (!itemById(id)) return members.filter(member => originalItems.get(member)?.parent === id).map(member => moved.get(member));
      return [id];
    }).filter(id => id && itemById(id));
    seqKey = null;
    showSelection(remapped);
  }
  if (failure) { toast(`Move incomplete: ${failure.message}`, true); return false; }
  if (navigate) setFolder(folder);
  else {
    anchorId = null; updateSelection(); scheduleToolStates();
    toast(`Moved ${ids.length === 1 ? "item" : `${ids.length} items`} to ${folder || "the library's top"}`);
  }
  return true;
}

async function moveFolder(path, parent) {
  try {
    const {name} = await api("POST", "/api/folders/move", {name: path, parent});
    remapFolderState(path, name);
    await loadList();
    toast(`Moved ${baseName(path)} to ${parent || "the library's top"}`);
  } catch (e) { toast(e.message, true); }
}

function folderDialog({renaming = null, parent = null, moving = null} = {}) {
  closeFolderPopup();
  if (moving?.some(id => listOwner(itemById(id))) && hasUnsavedChanges()) return toast("Save (or undo) your changes first (Ctrl+S)", true);
  const dialog = $("folder-dialog"), form = dialog.querySelector("form"), submit = form.querySelector('[value="ok"]');
  const cancel = form.querySelector('[value="cancel"]');
  $("folder-dialog-title").textContent = renaming ? `Rename ${baseName(renaming)}` : parent ? `New folder in ${baseName(parent)}` : "New folder";
  form.name.value = renaming ? baseName(renaming) : "";
  form.querySelector(".error").hidden = true;
  submit.disabled = cancel.disabled = false;
  dialog.oncancel = e => { if (submit.disabled) e.preventDefault(); };
  form.onsubmit = async e => {
    if (e.submitter?.value !== "ok") return;
    e.preventDefault();
    if (submit.disabled) return;
    submit.disabled = cancel.disabled = true;
    try {
      const made = renaming ? await api("POST", "/api/folders/rename", {name: renaming, new: form.name.value.trim()})
        : await api("POST", "/api/folders", {name: form.name.value.trim(), parent});
      if (renaming) remapFolderState(renaming, made.name);
      await loadList();
      dialog.close();
      if (moving) await moveToFolder(moving, made.name, true);
      else if (!renaming) setFolder(made.name);
    } catch (e) {
      form.querySelector(".error").textContent = e.message;
      form.querySelector(".error").hidden = false;
    } finally { submit.disabled = cancel.disabled = false; }
  };
  dialog.showModal(); form.name.select();
}

async function deleteFolder(path) {
  const n = items.filter(it => !listOwner(it) && inside(it.folder, path)).length, up = parentOf(path);
  if (!confirm(`Remove the folder ${baseName(path)}?\n\nIts ${n} item${n === 1 ? "" : "s"} and any subfolders move up to ${up || "the library's top"}. No items are deleted. Conflicting folder names must be resolved first.`)) return;
  try {
    await api("POST", "/api/folders/delete", {name: path});
    remapFolderState(path, up);
    await loadList();
  } catch (e) { toast(e.message, true); }
}
