# Plan: clean engine boundary and one tool registry (for another agent)

## Implementation note, 3 Nov 2026

The generated tool path now uses `cladia/engine_api.py`; the plugin import
boundary is enforced by `tests/test_engine_boundary.py`. Operation-specific
submission moved to core `prepare` hooks in `cladia/tool_jobs.py`, and the
generic HTTP endpoint has no operation-ID branches. `tool_specs.py` and
`GET /api/tools` were removed. Engine metadata supplies scheduler resources;
`jobs.JOB_KINDS` covers jobs without engines. The registry is cached and
refreshes when plugins, installations, or settings change. The page's only
remaining `TOOL_INPUTS` entry is the graph Compare picker.

The alpha no longer needs compatibility HTTP endpoints (step 8, option b).
The unused `/api/jobs/{tool}` routes, handlers, and request models were removed;
their tests now use the generated tool endpoint. BLAST-result downloads use it
too. Active APIs for GenBank validation, graph comparison, read pairing, and
BLAST database management stay because they serve distinct page workflows.
The follow-up [engine ownership plan](PLAN_ENGINE_OWNERSHIP.md) is now implemented:
engines own their workflows and progress readers, the public host API has
explicit signatures and no algorithm-specific job bridges, and the HTTP app
assembles domain routers. Host execution/publication/progress and plain input
validation are separate services. See the follow-up plan's delivery record
for actual validation and remaining biological assembly limitations.

Written 3 Nov 2026, after evaluating the modular tool system (`cladia/tool_registry.py`,
`cladia/tool_plugins/`, `cladia/static/app-tool-registry.js`). Read `CLAUDE.md` (*Modular tools*, *Legacy
backend job registry*, *Working with the user*) and `docs/FEATURE_GUIDE.md` (*Adding an engine*) first.
The system works: this plan is about two structural problems, not about behaviour. **Nothing the user
sees should change.**

## The two problems

**1. Engines (plugins) reach into Cladia's internals.**
- Eleven plugins `from cladia import jobs` and call its job functions directly (`jobs.orfs_job`,
  `jobs.tree_job`, ...).
- Seven plugins' `dispatch` does `from cladia.server import XRequest, start_x` (`orfs.py`,
  `reference_annotation.py`, `primer3.py`, `primer_test.py`, `genbank_download.py`, `ncbi_blast.py`,
  `local_blast.py`). So the dependency runs engine -> server -> registry -> engine: a cycle held together
  by imports done inside functions, and the "plugin" is really an adapter over server code.
- Consequences: a new engine author must know `server.py` and `jobs.py`; circular-import breakage waits for
  the next refactor; a later sandbox or a declarative (container + description file) plugin kind is
  harder because the contract is "whatever `server` and `jobs` happen to offer".
- `run_registered_tool` in `server.py` (~line 1980-2140) also knows specific operations: a ladder of
  `if req.operation == "align.compare"`, `"assemble.batch"`, `"assemble.one"`, `"read_files" in resolved`...
  that builds titles, extra parameters and sample groups. The generic endpoint should not know them.

**2. Two registries still describe the same tools.**
- `cladia/tool_specs.py` (`ToolSpec`, `catalog()`) says, per job kind, its label, endpoint, lane, cores,
  inputs, outputs, citation key and runner. `ENGINE` entries in the plugins say lane, cores, options,
  `jobKind` again. `jobs.submit` (~line 374) reads **lane, cores and runner from `tool_specs.catalog()`**;
  `/api/tools` publishes it (nothing in the page calls it); `run_details` / citations use `citation_key`.
- The page-side input declarations: `TOOL_INPUTS` in `static/app-picker.js` (line ~10) is a third
  description of inputs; the picker already prefers the registry's operation inputs (line ~47) and falls
  back to `TOOL_INPUTS`, which is now only really needed for the graph's Compare dialog
  (`app-tools.js` ~640).
- Fourteen legacy endpoints (`/api/jobs/mafft|annotate-from-reference|orfs|primers|blast|map|mauve|
  assemble|tree|download|validate`, `/api/graph/{id}/blast`, `/api/blastdbs/*`) still exist. The page calls
  only three (`validate`, `download` from a BLAST result, `cancel`); nine test files call the others.
- `build_registry()` is called per request (it re-executes every plugin file; ~6 ms, harmless, but
  module-level state in a plugin is lost on every call).

## Target

- **One engine API.** A small module, `cladia/engine_api.py`, is the only Cladia module a plugin may
  import (besides the standard library and third-party packages such as Biopython). It exports exactly what
  engines need: progress (`steps`, `step`), running programs in Cladia's Linux (`run_container`,
  `run_program`), the job log, adding results to the library (`add_output` and friends), reading items,
  `config`, citations. Everything else stays private. This is the contract future engines (and a
  sandbox) are held to.
- **Engines are self-contained.** `run(...)` does the work using only the engine API. `dispatch` goes
  away: starting a job is generic (the registry endpoint builds the request, `jobs.submit`s it). The
  `start_*` logic and request models move out of `server.py` into plain modules the endpoints and the
  engines both use.
- **The generic endpoint is generic.** Operation-specific preparation (titles, `groups`, `samples`) lives
  with the operation (core), as a `prepare` hook in its registry entry, not as `if` branches in the
  endpoint.
- **One place for scheduling and metadata.** Lane, cores, runner and citation come from the engine (or,
  for jobs that are not engines, a small table of internal job kinds). `tool_specs.py` and `/api/tools`
  are deleted.
- **Fewer input declarations.** `TOOL_INPUTS` shrinks to what is not a registered operation (the graph
  Compare) or goes.

## Order of work (each step ends with the suite green and a commit)

0. **Baseline.** Note the test count (303 on 3 Nov). Save a headless check that opens every generated
   dialog and runs Align and Find ORFs through the page (what the evaluation did: Playwright in a scratch
   venv, the app run with the Cladia venv's python and `CLADIA_DATA` at an empty scratch folder with
   `.cladia/upload` and `library` created; dialogs open without console errors, the toolbar's dimmed /
   off states, a MAFFT job in ~3 s, `orfs` done, logs with run details). Run it after every step.
1. **Boundary test first.** `tests/test_engine_boundary.py`: parse each plugin with `ast` and list its
   imports; fail on any `cladia.*` import other than `cladia.engine_api` (start by *listing* the
   violations, with an allow-list that you shrink to empty as you migrate; the test is the progress bar).
2. **Create `engine_api.py`** and move the plugins over one at a time, simplest first (`fasttree`,
   `iqtree`, `mafft`, `mauve`, `minimap2`, `sanger`), then the ones with `dispatch`. If a plugin needs
   something the API lacks, add it to the API deliberately (name it, document it), don't import around it.
3. **Move `start_*` and the request models out of `server.py`** into a module such as
   `cladia/tool_jobs.py` (plain functions taking plain values; the helpers they borrow from `server.py`:
   `_sequences`, `_item`, `_free`, locks, folder handling, go to a shared helper module). The legacy
   endpoints become thin wrappers over them, still returning what they return today. Watch for circular
   imports: the new modules must not import `server`.
4. **Make `run_registered_tool` generic.** Add `prepare` (and, where needed, `title`) to operations in
   `tool_registry.py`; move the branches for `align.compare`, `assemble.one/batch`, map `groups` and the
   titles there. The endpoint then: validate inputs and options against the contract, `prepare`, and
   `jobs.submit` with the engine's runner and resources.
5. **One metadata source.** `jobs.submit` takes lane / cores / runner / citation from the engine
   (`ENGINE.lane`, `cores`, `jobKind`, plus `citation`) or from `jobs.JOB_KINDS` for non-engine jobs
   (`validate`, `graphblast`, `blastdb`, `pcr` if it isn't an engine, project jobs). Delete
   `tool_specs.py`, `/api/tools`, `tests/test_tool_specs.py` (replace with a test that every job kind the
   app submits resolves to a runner and resources). `run_details.capture` keeps working: note the `_map_job`
   special case in `jobs.submit`.
6. **Cache the registry.** Build once at startup and rebuild only when an optional engine is installed or
   removed (`save_installed_engine`), plus a fixture for tests that need a fresh one.
7. **Page side.** Delete the `TOOL_INPUTS` entries of tools the registry now describes; keep or inline
   what the graph Compare dialog needs; the picker reads operation inputs only.
8. **Legacy endpoints (decide with the user first, it is the costly one).** Options: (a) keep them as thin
   wrappers (cheap, leaves ~15 endpoints only tests use), or (b) migrate the nine test files to the
   service functions / the registry endpoint and delete the endpoints. Recommend (b) in a *separate*
   commit series, one endpoint at a time, only after steps 1-7 are done; ask the user.
9. **Docs.** `CLAUDE.md`: replace *Legacy backend job registry* with a short description of the engine API
   and the single job-kind source, and update *Modular tools*. `docs/FEATURE_GUIDE.md` *Adding an engine*:
   the engine API, no `dispatch`, how a non-engine job kind is registered. Say what was deleted.

## Acceptance

- `tests/test_engine_boundary.py` passes with an empty allow-list: no plugin imports `cladia.server`,
  `cladia.jobs`, `cladia.library` or anything but `engine_api`.
- `grep -rn "tool_specs" cladia tests` finds nothing; `GET /api/tools` is gone.
- `run_registered_tool` has no operation-id branches.
- The full suite passes (at least the 303 of the baseline, plus the new tests); the baseline headless
  check gives the same result as before every step; no visible change in any dialog.
- A new, trivial engine added as one plugin file (try one in a test: a fake FastTree-like engine writing
  a result) works using only `engine_api`, with no edit to `server.py`, `jobs.py` or the page.

## Things that will bite

- **Circular imports.** `server` -> `tool_registry` -> plugins -> `engine_api` -> `jobs` -> `library`;
  `jobs` must not import `server` (it doesn't today; keep it so). Run `python -c "import cladia.server"`
  and each plugin on its own after every move.
- **Tests that monkeypatch** `server.jobs...`, `jobs.run_container` or the container program
  (`CLADIA_CONTAINER` stand-in): moving functions changes what they patch. Keep `jobs.run_container` the
  single call site for running programs so those tests keep working.
- **Job kinds are strings in many places** (`"map"`, `"mafft"`, `"orfs"`, queue lanes, saved logs, the
  page's job rows, `run_details`, citations): don't rename any.
- **The in-place annotation behaviour** (`apply`, `into`, results going back to their source folder, the
  destinations code) was just reworked by another agent; moving `start_*` must keep it byte for byte.
- **`dispatch` and `run` differ today** for the eight engines that have both; read each pair before merging
  them. Where `dispatch` builds several jobs (one per list), that behaviour moves into the operation's
  `prepare`, not into the engine.
- Another session may have uncommitted changes in the tree: `git status` first, **stage only your own
  files** (`git add <paths>`, never `git add -A`).

## Out of scope

Downloadable plugins, a sandbox, a declarative plugin kind (decide before any catalogue exists; the engine
API is what makes it possible later); the real GetOrganelle test with Illumina reads; new engines;
changing any dialog's look.

## Housekeeping

Commit when a step is done and tested; push to `main`; tell the user in a line. Ask the user before step 8.
Don't be sycophantic; reproduce before fixing; the user tests on their own Windows PC and reports bugs.
