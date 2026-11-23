// Cladia's page, continued from app-tools.js: startup: the page's controls wired up, the list loaded.
// The page's scripts share one global scope and run in the order index.html loads them,
// so code here can use what earlier files declare; at load time it mustn't call into later ones.

installViewerZoom();
installIcons();

// A complete backdrop click dismisses only its modal, using Escape's cancel path.
// Remember where the gesture began so dragging out of a field never closes it.
let backdropDialog = null;
function outsideDialog(dialog, e) {
  const r = dialog.getBoundingClientRect();
  return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
}
document.addEventListener("pointerdown", e => {
  const dialog = e.target instanceof HTMLDialogElement ? e.target : null;
  backdropDialog = e.button === 0 && dialog?.open && outsideDialog(dialog, e) ? dialog : null;
}, true);
document.addEventListener("pointercancel", () => { backdropDialog = null; }, true);
document.addEventListener("click", e => {
  const dialog = backdropDialog;
  backdropDialog = null;
  if (!dialog?.open || e.target !== dialog || !outsideDialog(dialog, e)) return;
  e.preventDefault(); e.stopPropagation();
  if (dialog.requestClose) dialog.requestClose("");
  else if (dialog.dispatchEvent(new Event("cancel", {cancelable: true}))) dialog.close("");
}, true);

// ── Startup ──────────────────────────────────────────────────────────────
$("filter").oninput = () => { anchorId = null; renderFolderButton(); renderList(); scheduleToolStates(); };
$("library-sort").onclick = openLibrarySortMenu;
$("library-sort").title = librarySortTitle();
$("open-archive").onclick = openArchive;
$("clear-jobs").onclick = clearJobs;
$("trash-restore").onclick = restoreTrash;
$("trash-delete").onclick = () => deleteTrash(false);
$("trash-empty").onclick = () => deleteTrash(true);
$("archive-filter").oninput = renderArchive;
$("archive-delete").onclick = deleteArchived;
$("add-files").onclick = addFilesClicked;
$("add-download").onclick = () => openTool("download");
$("settings-button").onclick = openSettings;
const openGuide = () => api("POST", "/api/help/guide").catch((e) => toast(e.message, true));
$("help-button").onclick = openGuide;
$("help-guide").onclick = openGuide;
$("help-logs").onclick = () => api("POST", "/api/help/logs").catch((e) => toast(e.message, true));
$("settings-dialog").querySelector("form").addEventListener("submit", (e) => settingsDone(e.target, e));
const positionOpenSettings = () => { if ($("settings-dialog").open) positionSettings(); };
new ResizeObserver(positionOpenSettings).observe($("toolbar"));
window.addEventListener("resize", positionOpenSettings);
const storagePanel = $("settings-dialog").querySelector('.tab-panel[data-tab="space"]');
const storageSizeObserver = new ResizeObserver(() => requestAnimationFrame(fitSettingsStorage));
for (const element of [storagePanel, ...storagePanel.children]) storageSizeObserver.observe(element);
for (const b of $("settings-tabs").children) b.onclick = () => settingsTab(b.dataset.tab);
$("tool-engine-confirm").onclick = installSelectedToolEngines;
$("tool-engine-save-settings").onclick = saveToolEngineSettings;
$("blastdb-browse").onclick = async () => {
  const path = await window.pywebview.api.pick_blastdb();
  if (path) $("settings-dialog").querySelector("[name=blastdb_path]").value = path;
};
// Submitters: the fields edit the working copy; the dialog's Save keeps it
// Metadata from a table
$("meta-choose").onclick = () => $("meta-input").click();
$("meta-input").onchange = (e) => { const file = e.target.files[0]; e.target.value = ""; if (file) metaFileChosen(file); };
$("meta-dialog").addEventListener("change", (e) => { if (e.target.closest("#meta-body")) previewMeta(); });
$("meta-dialog").querySelector("form").addEventListener("submit", (e) => {
  if (e.submitter?.value !== "run") return;
  e.preventDefault();
  runMetaImport();
});
$("sub-select").onchange = (e) => { subAt = Number(e.target.value); renderSubmitters(); };
for (const input of $("sub-form").querySelectorAll("[data-f]")) {
  input.oninput = () => {
    subs[subAt][input.dataset.f] = input.value;
    if (input.dataset.f === "name") $("sub-select").selectedOptions[0].textContent = input.value || "(unnamed)";
  };
}
$("sub-new").onclick = () => addSubmitter({ name: freeSubName("New submitter"), authors: "", contact: "", email: "", phone: "",
  institution: "", department: "", street: "", city: "", state: "", postal_code: "", country: "", title: "",
  citation: "unpublished", hold: "" });
$("sub-copy").onclick = () => addSubmitter({ ...subs[subAt], name: freeSubName(`${subs[subAt].name} copy`) });
$("sub-delete").onclick = () => {
  if (!confirm(`Delete the submitter ${subs[subAt].name || "(unnamed)"}?`)) return;
  subs.splice(subAt, 1);
  renderSubmitters();
};
$("sub-import").onclick = () => $("sbt-input").click();
$("sbt-input").onchange = (e) => { const file = e.target.files[0]; e.target.value = ""; if (file) importSbt(file); };
$("sub-export").onclick = exportSbt;
$("blastdb-add").onclick = addBlastDb;
$("blastdb-estimate").onclick = estimateBlastDb;
$("blastdb-download").onclick = downloadBlastDb;
// Enter in these fields does their own thing, not the dialog's Save
for (const [name, action] of [["blastdb_path", addBlastDb], ["blastdb_add_name", addBlastDb], ["blastdb_query", estimateBlastDb], ["blastdb_name", estimateBlastDb]]) {
  $("settings-dialog").querySelector(`[name=${name}]`).onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); action(); } };
}
$("sidebar-edge").onmousedown = dragSidebarEdge;
$("sidebar-edge").ondblclick = () => { setSidebarWidth(290); try { localStorage.removeItem("cladia.sidebar"); } catch (e) {} };
// The sidebar takes items dragged out of a list; each list handles its own drops.
$("lists").addEventListener("dragover", (e) => {
  if (!takesOut()) return;
  const li = e.target.closest?.("#lists li[data-id]");
  const it = li && itemById(li.dataset.id);
  if (it && draggedIds.every((id) => {
    const owner = listOwner(itemById(id));
    return owner && [it.id, listOwner(it)].includes(owner);
  })) {
    $("lists").classList.remove("drop-out");  // over its own list: nothing happens there
    return;
  }
  e.preventDefault();
  $("lists").classList.add("drop-out");
});
$("lists").addEventListener("dragleave", (e) => { if (!$("lists").contains(e.relatedTarget)) $("lists").classList.remove("drop-out"); });
$("lists").addEventListener("drop", (e) => {
  if (!$("lists").classList.contains("drop-out")) return;
  e.preventDefault();
  $("lists").classList.remove("drop-out");
  takeOutOfLists(draggedIds);
});
window.addEventListener("dragend", () => {
  $("lists").classList.remove("drop-out");
  draggedIds = draggedFolder = null;
  for (const el of document.querySelectorAll(".drop")) el.classList.remove("drop");
  cancelFolderSpring();
});
// The folder button: a click opens the tree; dragged items open it too
$("folder-button").onclick = (e) => { e.stopPropagation(); $("folder-popup").hidden ? openFolderPopup() : closeFolderPopup(); };
$("folder-button").ondragenter = (e) => {
  if (!draggedIds && !draggedFolder) return;
  e.preventDefault();
  if ($("folder-popup").hidden) openFolderPopup();
};
$("folder-button").ondragover = (e) => { if (draggedIds || draggedFolder) e.preventDefault(); };
$("folder-filter").oninput = renderFolderTree;
$("folder-filter").onkeydown = folderFilterKeydown;
$("folder-tree").onkeydown = folderTreeKeydown;
window.addEventListener("mousedown", (e) => {
  if (!$("folder-popup").contains(e.target) && !$("folder-button").contains(e.target) && !$("list-menu").contains(e.target)) closeFolderPopup();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeFolderPopup(); });
// Copy the file list first: clearing the input empties it while the uploads still run
$("file-input").onchange = (e) => { uploadFiles([...e.target.files]); e.target.value = ""; };
const exportForm = $("export-dialog").querySelector("form");
exportForm.addEventListener("submit", (e) => runExport(exportForm, e));
$("lists").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && ["z", "y"].includes(e.key.toLowerCase()) &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) {
    e.preventDefault(); e.stopPropagation();
    if (hasUnsavedChanges()) return toast("Save or undo the open changes first", true);
    (async () => {
      try {
        const result = await api("POST", "/api/library/action-history", { redo: e.key.toLowerCase() === "y" || e.shiftKey });
        await loadList(); selected = result.ids.filter((id) => itemById(id));
        seqKey = treeKey = null; showSelection(); updateSelection();
        toast(`${e.key.toLowerCase() === "y" || e.shiftKey ? "Redid" : "Undid"} ${result.kind}`);
      } catch (err) { toast(err.message, true); }
    })();
    return;
  }
  // Ctrl+A selects top-level rows; a list has its own row.
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) {
    e.preventDefault();
    const all = Object.keys(LIST_ELEMENTS).flatMap(visibleRows).filter((it) => !listOwner(it) && !it.missing).map((it) => it.id);
    const previous = [...selected], previousAnchor = anchorId;
    selected = all;
    anchorId = all[0] || null;
    if (!showSelection()) { selected = previous; anchorId = previousAnchor; }
    updateSelection();
    return;
  }
  if (LIST_KEYS.includes(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) {
    e.preventDefault();
    moveInList(e);
    return;
  }
  if (e.key === "Delete") removeSelected();
  if (e.key === "F2" && selected.length === 1) startRename();
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") { e.preventDefault(); duplicateSelected(); }
});
$("lists").addEventListener("contextmenu", (e) => openListMenu(e, null));  // empty space
// A click on empty space (not an item, a button or a field) clears the selection,
// and so the view, as in a file explorer; unsaved edits ask first (showSelection)
$("lists").addEventListener("click", (e) => {
  if (e.target.closest("li[data-id], button, input, select, a, summary") || (!selected.length && !shownIds.length)) return;
  const previous = [...selected], previousAnchor = anchorId;
  selected = [];
  anchorId = null;
  if (!showSelection([])) { selected = previous; anchorId = previousAnchor; return; }
  lastClick = {id: null, at: 0};
  renderList();
});
for (const event of ["mousedown", "blur", "resize"]) {
  window.addEventListener(event, (e) => { if (!(e.target instanceof Node && $("list-menu").contains(e.target))) closeListMenu(); });  // (resize, blur: the window is no Node)
}
// Escape closes whatever floats: menus, the folder tree, the Show panel, the hover card
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  closeListMenu();
  $("show-panel").hidden = true;
  hideCard();
});
// Ctrl+F: the search box of what is shown (the viewer's Find, the tree's, the
// BLAST filter); from the list, or with nothing shown, the list's filter
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "f" || document.querySelector("dialog[open]")) return;
  e.preventDefault();
  const shown = (id) => !$(id).classList.contains("inactive");
  const box = document.activeElement?.closest?.("#sidebar") ? $("filter")
    : shown("seqview") ? $("seq-find") : shown("treeview") ? $("tree-search") : shown("graphview") ? $("graph-find") : shown("tableview") ? $("table-filter")
    : $("filter");
  box.focus();
  box.select();
});
// Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) with a tree shown: its reroots undone, redone
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || !["z", "y"].includes(e.key.toLowerCase()) || document.querySelector("dialog[open]")) return;
  if (document.activeElement?.closest?.("#sidebar")) return;
  if ($("treeview").classList.contains("inactive") || !treeShown || viewIds) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  e.preventDefault();
  treeUndo(e.key.toLowerCase() === "y" || e.shiftKey);
});
// Enter in a dialog's field means its main button (OK, Run...): the browser would
// take the first button, Cancel. Fields with their own Enter (searches) come first.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.defaultPrevented || e.isComposing || e.target.tagName !== "INPUT") return;
  const form = e.target.closest("dialog form");
  const main = form?.querySelector(".actions button:not(.secondary):not([hidden])");
  if (!main || main.value === "close") return;
  e.preventDefault();
  form.requestSubmit(main);
});


const codesLoaded = loadGeneticCodes();

renderRegisteredToolbar();
for (const key of ["align", "support", "lengths", "ladderize"]) {
  $(`tree-${key}`).onchange = (e) => changeTreeLayout({ [key]: e.target.checked });
}
// Find: typing searches; Enter goes to the next match, Shift+Enter the previous
$("seq-find").addEventListener("input", seqFind);
$("seq-find").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); seqFindStep(e.shiftKey ? -1 : 1); }
});
$("seq-prev").onclick = () => seqFindStep(-1);
$("seq-next").onclick = () => seqFindStep(1);


$("seq-show").onclick = (e) => {
  e.stopPropagation();
  const panel = $("show-panel");
  panel.hidden = !panel.hidden;
  if (panel.hidden) return;
  renderTypes();
  panel.style.left = "0px";  // under the button; moved left if it would go past the window's edge
  const over = panel.getBoundingClientRect().right - (window.innerWidth - 8);
  if (over > 0) panel.style.left = `${-over}px`;
};
document.addEventListener("mousedown", (e) => { if (!e.target.closest(".dropdown")) $("show-panel").hidden = $("tree-show-panel").hidden = true; });
$("tree-show").onclick = () => { $("tree-show-panel").hidden = !$("tree-show-panel").hidden; };
$("seq-features").onchange = (e) => { changeSeqShow({ features: e.target.checked }); renderTypes(); };
$("seq-variants").onchange = (e) => changeSeqShow({ variants: e.target.checked });
$("seq-reads").onchange = (e) => setMappingReadsMode(e.target.checked ? "all" : "none");
$("seq-translations").onchange = (e) => changeSeqShow({ translations: e.target.checked });
$("seq-complement").onchange = (e) => changeSeqShow({ complement: e.target.checked });
$("seq-gc-size").onchange = (e) => changeSeqShow({ gcSize: e.target.value });
$("seq-gc").onchange = (e) => changeSeqShow({ gc: e.target.checked });
$("seq-gc-skew").onchange = (e) => changeSeqShow({ gcSkew: e.target.checked });
$("seq-gc-window").onchange = (e) => changeSeqShow({ gcWindow: Number(e.target.value) });
$("seq-gc-mode").onchange = (e) => changeSeqShow({ gcCumulative: e.target.value === "cumulative" });
$("seq-curate").onclick = openCurateMenu;
$("seq-sort").onclick = openSortMenu;
$("mapping-save-consensus").onclick = () => consensusAsSequence();
$("tree-curate").onclick = openTreeCurateMenu;
$("tree-lock").onclick = () => setTreeEditMode(!treeEditable());
$("tree-undo").onclick = () => treeUndo(false);
$("tree-redo").onclick = () => treeUndo(true);
$("lists").addEventListener("scroll", hideCard);
$("seqview").addEventListener("contextmenu", openViewerMenu);
{
  const codeForm = $("code-dialog").querySelector("form");
  codeForm.addEventListener("submit", (e) => codeDialogDone(codeForm, e));
}
$("matrix-body").addEventListener("mouseover", matrixTip);
for (const b of document.querySelectorAll("[data-matrixshows]")) b.onclick = () => { matrixShows = b.dataset.matrixshows; if (matrix) renderMatrix(); };
$("matrix-copy").onclick = copyMatrix;
$("table-body").onclick = tableClick;
$("table-filter").oninput = () => renderTable();
$("table-copy").onclick = copyTable;
$("table-csv").onclick = exportCsv;
$("table-action").onclick = () => tableView?.spec.actions?.[0]?.run();
$("table-action2").onclick = () => tableView?.spec.actions?.[1]?.run();
$("matrix-export").onclick = exportMatrix;
$("seq-code").onchange = (e) => changeSeqCode(Number(e.target.value));
for (const b of document.querySelectorAll("[data-seqmap]")) b.onclick = () => setSeqMap(b.dataset.seqmap);
$("seqview").addEventListener("keydown", (e) => {
  if (e.key === "Escape") seqView?.clearSelection();
  if ((e.ctrlKey || e.metaKey) && e.key === "c") { e.preventDefault(); copySeqSelection(); return; }
  if (canEdit()) editKey(e);
});
document.addEventListener("paste", (e) => {  // Ctrl+V in the list: new items; on the viewer: bases
  if (document.activeElement?.closest?.("#lists")) {
    e.preventDefault();
    return pasteIntoList(e.clipboardData.getData("text"));
  }
  if (!seqShown?.docs || document.activeElement !== seqView?.canvas) return;
  e.preventDefault();
  if (!editable()) return lockedHint();
  pasteBases(e.clipboardData.getData("text"));
});
$("seq-save").onclick = saveShown;
$("seq-lock").onclick = () => setEditMode(!editable());
$("seq-undo").onclick = () => undoRedo(false);
$("seq-redo").onclick = () => undoRedo(true);
$("at-add").onclick = () => seqView?.selection && openFeatureDialog("new", rowSelection());
const featureForm = $("feature-dialog").querySelector("form");
featureForm.addEventListener("submit", (e) => featureDialogDone(featureForm, e));
$("tree-search").oninput = findTips;
$("tree-search").onkeydown = (e) => { if (e.key === "Enter") findTipsStep(e.shiftKey ? -1 : 1); };
$("tree-prev").onclick = () => findTipsStep(-1);
$("tree-next").onclick = () => findTipsStep(1);
$("graph-min-length").onchange = $("graph-min-depth").onchange = $("graph-only-hits").onchange = filterGraph;
$("graph-compare").onclick = openGraphCompare;
$("graph-reference-clear").onclick = clearGraphComparison;
$("graph-export").onclick = openGraphExport;
$("graph-mode").onclick = (e) => { const b = e.target.closest("[data-gmode]"); if (b) setGraphMode(b.dataset.gmode); };
$("graph-gaps").onchange = (e) => { if (graphView) { graphView.allowGaps = e.target.checked; graphView.draw(); } };
$("graph-export-dialog").querySelector("form").addEventListener("submit", runGraphExport);
$("graph-show").onclick = () => {
  const panel = $("graph-show-panel");
  panel.hidden = !panel.hidden;
  $("graph-show").setAttribute("aria-expanded", String(!panel.hidden));
  panel.style.left = "0px";
  const over = panel.getBoundingClientRect().right - (window.innerWidth - 8);
  if (over > 0) panel.style.left = `${-over}px`;
};
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#graph-view-group")) closeGraphShow();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeGraphShow(); });
for (const id of ["graph-depth-colours", "graph-labels", "graph-links", "graph-compact"]) $(id).onchange = changeGraphDisplay;
$("graph-order").onchange = filterGraph;
$("graph-reset").onclick = resetGraphDisplay;
$("graph-find").oninput = () => findGraphNode(0);
$("graph-find").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); findGraphNode(e.shiftKey ? -1 : 1); } };
$("graph-to-scale").onchange = filterGraph;
$("graph-back").onclick = backToGraph;
$("graph-blast-dialog").querySelector("form").addEventListener("submit", runGraphCompare);

$("view-figure-button").onclick = openFigureMenu;
$("figure-back").onclick = () => figureShown?.data?.source && openItem(figureShown.data.source);
window.addEventListener("beforeunload", (e) => {
  if (hasUnsavedChanges() || projectJobId) e.preventDefault();
});

// Saves in another window show here at once (and on focus: Cladia's copies may
// also have been deleted outside it)
windows?.addEventListener("message", () => refreshShown());
window.addEventListener("focus", () => refreshShown({ focus: true }));
if (viewIds) {
  startViewWindow();
} else {
  loadLibraryView().then(() => loadList()).then(() => checkUpdates()).catch(e => toast(e.message, true));
  pollJobs();
}
$("update-reveal").onclick = () => api("POST", "/api/updates/reveal").catch((e) => toast(e.message, true));
