# Adding tools and visualizations

The generated tool contract is in `cladia/tool_registry.py`. Core categories
are fixed toolbar purposes. Core operations define dialog tabs, input slots and
outputs. Engine modules in `cladia/tool_plugins/` declare supported operations,
option schemas, job resources and a runner; `GET /api/tool-registry` supplies
the UI. Fixed toolbar categories and their dialogs use this registry; categories
without an available engine remain hidden.
`cladia/engine_api.py` is the only Cladia module an engine may import. Core
`prepare` hooks in `cladia/tool_jobs.py` handle operation-specific submission.
Tools launch through `POST /api/tool-registry/run`. Specialized APIs remain for
submission validation, graph comparison, read pairing and database management.

Every engine ships with Cladia; there are no separately installed plugins. An
**optional** engine (`ENGINE["optional"]`, with `install` and `uninstall`
functions and optionally `installChoices`) has its tool downloaded only when the
user installs it. Most declare `ENGINE["conda"] = {"version", "spec"}` and a lock
file `cladia/tool_plugins/locks/<id>-<version>.lock` (made with
`scripts/make_conda_lock.py`); `install` calls `api.conda_install(job, workdir,
ENGINE)` and the runner wraps its programs in `api.conda_command(ENGINE, [...])`.
See [optional tools from bioconda](PLAN_BIOCONDA_TOOLS.md) and
`cladia/conda.py`. Clustal Omega, GetOrganelle and Krait are examples.

A tool published as a Docker image (one too awkward for bioconda) is declared
with `ENGINE["image"] = {"name", "check", "version", "legacy"?}`: the registry
calls `linux.add_environment`, and the first job unpacks the image into its own
root, `/var/lib/cladia/environments/<engine id>`, checked with the `check` shell
command. The runner calls `api.run_container(job, <image name>, args, workdir)`.
Pin the image by digest; changing `version` unpacks it again. An engine may
also declare `ENGINE["citations"]` as `[tool, reference, url]` entries for its
job logs.

An optional engine It appears in the Settings → Tools list, grouped by core category (`GET /api/tool-engines`),
but its operation/engine is omitted from dialogs until an installation job succeeds.
`POST /api/tool-engines/install` queues selected engines and choices on the
download lane, and records completed installs in `.cladia/installed-engines.json`
(and the lock's hash in `installed-locks.json`: a changed lock marks the tool
*outdated* until it is updated). `POST /api/tool-engines/{id}/uninstall` removes it.
Tools appear in a compact grid grouped by type, including built-in engines.
**Manage…** opens details, settings, measured/estimated storage and available
installation/update/removal actions. Already installed choices are marked and
additional choices can be queued. The Windows installer generates its optional-tool and package
tasks from the engines' metadata at build time, then queues the selected installs for
the first app run. An optional engine keeps its setup idempotent and its data outside the project ZIP;
per-data-folder state goes in `api.private_volume(name)` (GetOrganelle's databases),
so another library's install or uninstall never touches it.
Optional engines may also declare `settings` fields of type `text`, `bool`, or
`secret`. Their installed-tool dialog renders these fields automatically. Values
are stored locally in `.cladia/tool-settings.json`; the settings API reports
only whether a secret is present. Engine code reads values with
`engine_api.tool_setting(engine_id, key, default=None)`. Never put credentials in job logs,
result provenance, or the public registry.
Core annotation preparation preserves the existing per-list jobs and in-place
history behavior; those operations have `outputMode: update`
and the generated dialog shows **Save as a copy** in place of a result name.

Use this as a project-specific contributor checklist. Confirm actual APIs and neighboring implementations before coding; filenames and behavior can change. A visualization of an existing item need not create a duplicate simply because it is opened. A tool producing a result should create a persistent new library object, including table results, unless a deliberate product decision specifies otherwise.

## Integration checklist

Current mutation convention: annotators update selected sequences by default;
**Save as a copy** opts into new objects. In-place tools and bulk metadata edits
must checkpoint history, respect edit locks, detect stale inputs, and explain
partial failures. Object-producing analyses still create new library items.

1. **Define the operation.** Describe accepted item kinds, selection count, DNA/protein and circular behavior, parameters/defaults, external dependencies, expected outputs, errors, and whether it creates one or several objects. Validate on the server as well as in the UI. Never silently overwrite inputs.
2. **Find its home.** Choose the existing core category and operation that match its purpose. A new operation needs a core input/output contract; a new engine for an existing operation needs one engine module and no dialog changes. Related operations may share a top-level dialog tab through `group`; the generated dialog then shows their methods in a selector within that tab. For example, Assemble groups one and multiple Sanger samples under **Sanger reads**, and GetOrganelle under **Organelles**. New toolbar categories are a deliberate core product decision and remain hidden until an engine is available. Keep the viewer's controls for viewing/editing, not job launchers without a clear reason.
3. **Run through the job system where appropriate.** Add a validated endpoint in the appropriate `api_*` router, submit through `jobs.submit`, choose the appropriate lane/core usage, implement a runner and cancellation checks, and use a work directory. Register external dependencies and container image/version as needed. Fast synchronous view operations may not need a job.
4. **Report honest progress.** Define meaningful stages with `steps`/`step`; add a watcher only when the program emits reliable progress. Supply a fraction only when measurable, otherwise show an indeterminate stage and useful detail. Support queued/running/done/failed/cancelled states, logs and cancellation; do not invent percentages or claim cancellation succeeded until work stops.
5. **Publish results to the library.** Add or reuse a suitable item kind and persistence function; put the new item in the source folder, name it clearly, register output IDs on the job, and show it as new. For multi-input jobs specify folder choice. Persist required sidecars and metadata, handle partial failure and cleanup, and verify reopening after restart. Add support for rename, duplicate, delete/trash/restore and export appropriate to the type. Treat inputs and originals as read-only unless the user explicitly applies an edit.
6. **Make results useful.** Reuse a viewer when its data model fits; otherwise create a dedicated view with clear labels, units, empty/error states, selection and export as appropriate. Preserve coordinates, provenance, tool version and parameters where needed to interpret or reproduce results. A result table should remain available after the job completes and after restart.
7. **Test and document.** Cover validation, output kind/content and folder, errors, cancellation, persistence and representative scientific edge cases. Test progress parsing against representative output where applicable, and smoke-test the actual tool and GUI. Update README and this guide when conventions change.

Job footers and result provenance are built by `run_details.py`. Register a
verified citation for each new external tool, capture meaningful submitted
parameters, and record actual versions when reliably available. A missing
citation or version is reported as unrecorded; never infer a runtime version
from the installed-tools plan. Both new results and in-place annotation updates
retain the details after the job log is deleted.
Include method-specific citations only when the job selects those methods, and
choose version-specific citations from observed output rather than configuration.
External engines can call `api.record_tool(job, name, observed_version, citations)`
to retain runtime versions and citations in logs and result provenance without
adding tool-specific branches to the host. Citation entries have `tool`,
`reference` and `url` fields. Do not record a pinned build version as observed
until the executable has actually reported it.

## Adding an engine

For an existing operation, add one Python module in `cladia/tool_plugins/` with
`ENGINE` and `run`. Import only `cladia.engine_api` for host services.
`ENGINE.supports` names core operation IDs; `options` uses
`choice`, `bool`, `int`, `float`, `text`, or `file` schemas. `run` receives the
operation, validated inputs, options and job context. The core generates the
engine selector and controls. See `mafft.py` or `getorganelle.py`. Each engine owns its command construction, progress parsers and output
interpretation. Use the API's progress, container, reader and output functions;
there are no algorithm-specific job bridges. `run_container` returns stdout
and raises on failure; `execute_process` returns the lower-level process tuple.
Register live-log parsers with `watch_log(job, path, reader, offset=0)`; callbacks
are internal and never serialized. Declare effective option defaults in `run`
and the option schema so provenance captures the selected method and defaults. If an operation needs per-list or per-query jobs, add a core `prepare`
hook in `tool_jobs.py`. Engines do not call HTTP handlers.

For a new operation, add its tab (or group within a tab), input slots and output contract to
`tool_registry.py`. The core owns the fixed categories, their icons,
toolbar order and operation tabs. Document types and output modes are rendered
by `app-tool-registry.js` and validated in `tool_validation.py`; add a shared input
widget only if no existing contract fits. The generated toolbar hides a
category with no available engine. Picker and toolbar state use the registry's
first applicable input slot. Download has no library input and remains active.

Use a scratch library to check selection prefill, incompatible selections,
tab and engine switching, remembered options, disabled Run, job submission,
result persistence and errors. `tests/test_tool_registry.py` covers engine
discovery, invalid registration, optional installation and submission.

## Existing examples to inspect

| Feature | Entry points and output/view | Progress example |
| --- | --- | --- |
| MAFFT / genome comparison | `mafft_job`, `mauve_job`; alignment / comparison and sequence or figure view | MAFFT and Mauve watchers |
| Trees | `tree_job`; tree viewer and figure view | FastTree / IQ-TREE watchers |
| Annotation / ORFs | de novo engines, `reference_job`, `orfs_job`; update with history, or new copies | stages and sequence counts where available |
| Read mapping / assembly | `map_job`, `assemble_job`; mapping or consensus sequence | mapping watcher and stages |
| Tool inputs and pickers | `tool_registry.py`, `app-tool-registry.js`, `ToolPicker`, `toolState` in `app-picker.js` | none |
| Primers / BLAST | primer and BLAST jobs; persistent table items, `resultview.js` | stages and available tool progress |
| Sequence and alignment views | `seqview.js`, `seqview-circular.js`, `seqedit.js`; alignment identity matrix in `matrix.js` | interactive views, no job required |
| Alignment variants | `cladia/tool_plugins/alignment_snps.py`; attached VCF in `library.py`, site table in `app-panel.js`, sample × site matrix in `variant-matrix.js` | alignment column scan and VCF save |
| Tree and publication figures | `treeview.js`, `figure.js`; figure settings and export | interactive views, no job required |

When adding a new item kind, trace it through `library.py`, the domain HTTP router, library list and selection handling in `app.js`, the viewer, and export and trash paths. Consult existing tests rather than assuming that a new kind is automatically supported everywhere.

## Persistent files and project ZIPs

Declare file attachments in `library.FILE_FIELDS` / `_files_of`; update the
relevant copy operation and test Trash restore and project round trips. Add new
kinds to `projects.KINDS` and the shared `library.check_file_content` validator.
Library references embedded in JSON must be remapped on project import;
`projects._links` recognizes `item`, `id`, `reference_item` and `from_id`, including
list-member IDs. Add explicit handling and tests for other reference fields.
Never remap biological names or accessions. Saved JSON history snapshots need
the same remapping and fresh content hashes. Do not change ZIP version 1's
meaning incompatibly; introduce a new version/reader when necessary.

Project transfers use `projects.py`, `project_api.py`, and `app-projects.js`.
They package saved state, not settings, secrets, installations or external reads.
See [the implementation plan](PORTABILITY_PLAN.md) for format and validation.

Saved-file mutations checkpoint their baseline, then put the write and final
checkpoint inside `history.protect(path, owners)` under the library write lock.
The guard restores the file and timeline indices on failure and defers blob
pruning until commit. Metadata batches use nested guards for all affected files.
Save APIs return JSON storage errors (507); don't hide them behind a plain 500.

Project APIs submit jobs by default (`background: false` is the synchronous
compatibility/testing path; raw upload uses a query parameter). They use a local
transfer lane, cooperative cancellation, and completion events that the native
close handler waits for. Keep internal job fields out of API serialization and
preserve imported provenance rather than replacing it with the transfer job.
Export checks a copied snapshot instead of holding the lock while compressing;
import validates/remaps before its brief publication lock. New publication
attachments must be covered by the transaction journal and startup recovery.

## Result destinations

Use `library.result_folder` for a combined result. Omitted destination means the
common source folder; mixed folders require a choice. Explicit `null` means Library
root and must remain distinguishable from an omitted field (`ResultDestination` in
request_context.py). Exclude auxiliary references when computing the default. Tool dialogs
share app-destinations.js; library commands use chooseResultFolder for mixed inputs.

For one-copy-per-input annotation outputs, call `_add_output(..., source=item)`.
It records the source owner and a fallback folder, so the job's common destination
cannot gather all copies into the first source's folder. In-place updated outputs
are excluded from placement. Whole-list outputs keep their list's folder.

Folder edits notify jobs while holding the library write lock. Jobs register their
destination and publish their outputs under that same lock, without holding it
across external computation. Missing explicit folders produce HTTP 400. A job is
reported done after output placement; project imports preserve their own layout.
Tests: test_destinations.py and scripts/test_destinations_ui.cjs (isolated libraries).

## Host boundaries and regression checks

`server.py` assembles the HTTP application; `api_*` modules own domain routes.
Share window, lock and undo state through `request_context.py`, and use
`item_inputs.InputError` for HTTP-independent validation (translated centrally).
Routers must not import `server` or other routers. `tool_install.py` is shared
by HTTP installation and first-run installer requests. Scientific engines use
only the published `engine_api.__all__` capabilities, never host module objects.

Keep input caches local to the request: validation shares one library snapshot
and reuses parsed alignment rows; BLAST preparation takes one fresh snapshot
per batch and reads each graph once. Downloads need no library scan. Live job
polling caches only parsed progress summaries, invalidating them when the log,
reader, offset or host progress changes. Do not globally cache library inputs
or retain complete log contents. `test_tool_validation.py` and
`test_job_progress.py` check read counts and invalidation without timing limits.

Treat saved preferences as recoverable state.
Settings writes merge under a lock and replace the file atomically; failed writes
must leave the previous settings intact. Validate the complete request before
saving any fields. Tree preferences retain only recognized boolean options.
Browser preference readers expect objects and reject JSON scalars, arrays and null.

Invalidate both operation and management caches after an optional tool is installed,
updated or uninstalled; that also invalidates storage measurements. Browser checks
should wait for the displayed state after asynchronous jobs and use fixtures for
machine-specific disk measurements, rather than requiring a particular WSL install.

Run `python -m pytest -q` in the application environment. The boundary tests
check every referenced capability, cold imports without scheduler/HTTP, and the full active URL/method inventory. Retain generated-tool
coverage with `node scripts/test_engine_ui.cjs` (Playwright/Chrome and
`CLADIA_PYTHON`); it opens every available category and submits real MAFFT and
ORF jobs. `test_project_ui.cjs` and `test_genome_figure_ui.cjs` check project
transfer and viewer/export paths. All use private scratch libraries. Native
scientific checks are `scripts/check_analysis_tools.py` and `check_long_reads.py`.


## Folder navigation and viewer identity

`app-folders.js` owns folder UI and path remapping. Explicit navigation exits the
all-folder filter, clears sidebar selection/anchor and keeps the viewer open.
`selected` belongs to library actions; `shownIds` belongs to viewer refresh,
metadata, checks and saved-version actions. Call `showSelection(ids)` to refresh
an existing viewer without manufacturing a hidden sidebar selection. View-only
windows keep their explicit IDs independently of sidebar visibility.

Folder creation/move/delete validates stale targets under the library write lock.
Removal must reject colliding child destinations before writing. Internal import
helpers can still intentionally create ancestor paths; interactive folder creation
requires an existing parent. Preserve background-job destination relocation.
After taking records out of a list, remap both extracted IDs and renumbered
survivors before refreshing the viewer. Never retain an old record-index ID for a
different sequence. Folder navigation and expansion live in `library_view` app
settings; saving those preferences must not reapply desktop text scaling.

Retain the folder browser regression alongside API tests. It exercises generated
private data, actual mouse dragging, keyboard navigation, unsaved document
preservation, stale dialogs, rejected merges and empty-browser-profile persistence.
