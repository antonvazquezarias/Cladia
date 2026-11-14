// Saved-library ZIP transfers. Ordinary item imports and exports keep their own UI.
let projectToken = null;
let projectJobId = null;

async function waitProjectJob(submission) {
  if (!submission.job) return submission;
  projectJobId = submission.job;
  $("project-cancel").hidden = false;
  $("project-stop").hidden = !$("project-dialog").open;
  pollJobs();
  try {
    while (true) {
      const job = await api("GET", `/api/logs/${submission.job}`);
      if (job.status === "done") return job.result;
      if (job.status === "failed") throw new Error(job.error || "Project transfer failed; see its log");
      if (job.status === "cancelled") throw new Error("Project transfer cancelled");
      const p = job.progress;
      $("project-status").textContent = p ? [p.steps?.[p.at], p.detail].filter(Boolean).join(" · ") : "Waiting for the project transfer job…";
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } finally {
    projectJobId = null;
    $("project-cancel").hidden = $("project-stop").hidden = true;
    pollJobs();
  }
}

async function cancelProjectJob() {
  if (projectJobId) await api("POST", `/api/jobs/${projectJobId}/cancel`);
}
$("project-cancel").onclick = $("project-stop").onclick = cancelProjectJob;

function projectBusy(busy, text = "") {
  $("project-export").disabled = $("project-choose").disabled = busy;
  $("project-progress").hidden = !busy;
  $("project-status").textContent = text;
}

$("project-export").onclick = async () => {
  if (hasUnsavedChanges()) return toast("Save or undo your viewer changes before exporting the project", true);
  const options = { history: $("project-history").checked, trash: $("project-trash").checked, reads: $("project-reads").checked };
  try {
    if (inPywebview()) {
      options.path = await window.pywebview.api.save_file("Cladia-project.zip", "zip");
      if (!options.path) return;
    }
    projectBusy(true, "Exporting saved library…");
    const result = await waitProjectJob(await api("POST", "/api/project/export", options));
    if (result.download) {
      const a = document.createElement("a");
      a.href = `/api/project/download/${result.download}`; a.download = "Cladia-project.zip"; a.click();
    }
    projectBusy(false, "Project ZIP exported.");
  } catch (e) { projectBusy(false, e.message); }
};

async function inspectProject(source, filename) {
  try {
    projectBusy(true, "Checking project ZIP…");
    let result;
    if (typeof source === "string") result = await api("POST", "/api/project/inspect", { path: source });
    else {
      const response = await fetch("/api/project/upload", { method: "POST", body: source });
      result = await response.json();
      if (!response.ok) throw new Error(result.detail || response.statusText);
    }
    result = await waitProjectJob(result);
    projectToken = result.token;
    $("project-summary").textContent = `${result.items} items, ${result.folders} folders, ${bytes(result.bytes)}` +
      (result.history ? ", with saved versions" : "") + (result.trash ? `, ${result.trash} Trash items` : "") + ".";
    $("project-name").value = filename.replace(/\.zip$/i, "") || "Imported project";
    $("project-error").hidden = true;
    $("project-import").disabled = false;
    $("project-dialog").showModal();
    projectBusy(false);
  } catch (e) { projectBusy(false, e.message); }
}

$("project-choose").onclick = async () => {
  if (inPywebview()) {
    const path = await window.pywebview.api.pick_project();
    if (path) await inspectProject(path, path.split(/[\\/]/).pop());
  } else $("project-file").click();
};
$("project-file").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) await inspectProject(file, file.name);
};
$("project-dialog").querySelector("form").onsubmit = (e) => {
  if (e.submitter?.value !== "cancel") { e.preventDefault(); $("project-import").click(); }
};
$("project-dialog").onclose = () => {
  if (projectToken) api("DELETE", `/api/project/preview/${projectToken}`).catch(() => {});
  projectToken = null;
};
$("project-import").onclick = async () => {
  const name = $("project-name").value.trim();
  if (!name || !projectToken) return;
  const error = $("project-error");
  error.hidden = true;
  $("project-import").disabled = true;
  $("project-import-progress").hidden = false;
  // Keep this dialog open while publishing; another import must not replace its token.
  const cancel = $("project-dialog").querySelector('[value="cancel"]');
  cancel.disabled = true;
  $("project-dialog").oncancel = (e) => e.preventDefault();
  try {
    const submission = await api("POST", "/api/project/import", { token: projectToken, name });
    projectToken = null;
    const result = await waitProjectJob(submission);
    await loadList();
    setFolder(result.folder);
    $("project-dialog").close();
    await renderSpace();
    toast(`Imported ${result.ids.length} items into ${result.folder}`);
  } catch (e) {
    error.textContent = `${e.message}. Close this dialog and choose the ZIP again to retry.`;
    error.hidden = false;
  } finally {
    $("project-import-progress").hidden = true;
    cancel.disabled = false;
    $("project-dialog").oncancel = null;
  }
};
