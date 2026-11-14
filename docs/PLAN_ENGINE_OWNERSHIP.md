# Finish engine ownership and split host responsibilities

Status: implemented, 3 November 2026. The assessment and numbered stages below
record the approved design and pre-migration evidence. The delivery record
at the end describes the resulting implementation and verification.

## Verified assessment

| Concern | Pre-migration evidence | Assessment |
| --- | --- | --- |
| Engines remain adapters | 15 engine modules; 13 delegate execution to algorithm-specific `engine_api.*_job` bridges. MAFFT contains its 94-line workflow; GetOrganelle owns its run/install workflow. | Substantially true, but MAFFT and GetOrganelle are exceptions. |
| API depends on private names | engine_api.py is 47 lines, with 24 late-bound forwards. Six targets are `_named`, `_set_option`, `_tool_watched`, `_add_output`, `_ensure`, `_tool`. | Confirmed. Plugins call public aliases; the private coupling is inside their implementation. |
| Files remain large | jobs.py: 1,691 lines / 72 top-level functions; server.py: 2,175 lines / 130 top-level functions. | Confirmed. Responsibility and dependency boundaries, rather than a line quota, should determine the split. |

The existing import-boundary, job-registry and tool-registry tests pass:
29 tests on the inspected version. This was focused verification, not a full-suite
baseline run. The working tree was clean at the start of the assessment.

The previous refactor did deliver useful changes: plugins no longer import server
or jobs directly, operation preparation lives in tool_jobs.py, the generated
submission endpoint has no operation-ID ladder, duplicate registries and legacy
submission routes are gone, and registry discovery is cached. Preserve those gains.

Additional coupling to handle during extraction:

- The import-boundary test checks imports in top-level plugin files only. It does
  not verify forwarded target existence/signatures, public API attribute use, or
  helper files below a plugin package.
- engine_api imports jobs/config/mapping/blastdb as accessible module attributes.
  An import-only check would allow `api.jobs.some_internal_function()`. That is an
  architectural escape hatch; fixing it is not a Python sandbox/security guarantee.
- MAFFT still obtains strategy constants and its progress plan through the host;
  jobs.mafft_job imports the plugin back as a compatibility entry point.
- jobs.submit explicitly passes `_map_job` to provenance capture for mapping.
  Moving the implementation without replacing that shortcut would lose defaults
  or reintroduce a scheduler dependency on one engine.
- tool_jobs.submit_blast reads jobs.BLAST_PROGRAMS and jobs.LOCAL_DB_TYPE. Core
  preparation still depends on engine-specific constants in the scheduler module.
- Documentation says plugins can read settings through tool_registry.tool_setting,
  while the enforced import rule permits engine_api only. Publish a real settings
  accessor through the engine API before recommending it to plugin authors.

## Intended ownership

```text
HTTP routes -> core request/operation services -> scheduler + registry
registry -> engine metadata and run function
engine -> published engine API -> host services
scheduler -> execution lifecycle and generic progress-reader protocol
```

An engine owns its command construction, engine defaults, multi-pass workflow,
progress interpretation, tool-output interpretation and engine-specific cleanup.
It must run without calling a host function named after that engine's whole job.

The host owns scheduling, cancellation, workspaces, process/container execution,
volume lifetime primitives, library access and publication, destination rules,
locks, saved versions, provenance storage and settings. Shared record formats,
coordinate transformations and genuinely reused pure scientific helpers need not
be copied into each engine simply to reduce jobs.py. They must have an explicit,
narrow contract, not expose an entire internal module or another job facade.

Core operation preparation remains core: selection expansion, reference exclusion,
per-list/per-query batching, destination choice and edit-lock policy. It must not
become an alternate implementation of an engine's scientific workflow.

## Sequence of work

### 0. Establish a migration baseline and close the immediate blind spot

First implementation commit; do not move scientific code in this commit.

- Run the full isolated suite and record its actual count. Refresh/retain a browser
  smoke covering every generated category dialog and registry submission of Align
  and Find ORFs. Existing old-dialog smoke scripts must not be assumed to exercise
  the generated tool path.
- Add a contract test that inventories every `_forward` target and verifies it
  exists and is callable. Also inventory plugin-used public API attributes, including
  attributes assigned to local aliases as in MAFFT. Missing names must fail tests
  before a user reaches an affected workflow.
- Freeze the public services and their current return/error semantics in small
  contract tests. In particular, engine_api.run_container currently calls the
  checked job-aware `_tool` and returns stdout; it is not the lower-level
  jobs.run_container returning a process tuple. Preserve this distinction.
- Document algorithm-specific job bridges as temporary; record which engine owns
  each bridge's replacement. An explicit forwarding map is acceptable during this
  migration, but do not treat a passing existence test as engine independence.

Exit: baseline green; deliberately removing any forwarded target fails a focused
contract test; this commit changes no commands, files, scientific defaults or UI.

### 1. Give the engine API real, explicit host services

Extract service implementations out of jobs.py before moving their callers.
Suggested modules (combine small ones if that keeps the design simpler):

- execution.py: checked process/container calls, image preparation and volume
  operations, with existing Windows/WSL/container behavior and cancellation.
- job_progress.py: generic progress state and live-log reading. Engine-specific
  parsers will migrate with their engine, not stay in a central switch.
- job_outputs.py: input/record readers, result registration and annotation commit
  services, retaining destination tracking, history, locks and provenance.

engine_api stays a deliberate public facade with documented signatures and an
explicit export list. Its public names must not expose jobs/config or other host
modules. A settings accessor and image/config values should be explicit services
or immutable values. Do not add more `*args, **kwargs` job-shaped wrappers.

Preserve the existing mutable job context initially; do not combine this migration
with an IPC/sandbox redesign. A progress-reader registration function can attach
an engine parser to an internal job field; callbacks must never enter JSON job
responses, saved logs or provenance. Existing non-engine jobs may keep their own
parsers until moved to their proper core worker modules.

Update test instrumentation to patch the actual host execution provider, not old
jobs.py implementation names. Temporary compatibility names must have a tracked
removal point. No runtime fallback to an old scientific implementation is allowed
once its engine is migrated.

Exit: host service contracts pass directly; engine_api does not dynamically look
up private jobs names for host primitives; cold imports succeed without server.

### 2. Prove extraction with FastTree, then complete MAFFT

FastTree is small, but its body is currently inside tree_job's IQ-TREE/FastTree
branch. Split ownership rather than moving that entire branching function behind
another alias. Keep shared input-name normalization explicit and test restored
names, DNA/protein flags, core budgets and output metadata.

Complete MAFFT ownership by moving its strategy table and progress parser/plan
with the engine. Preserve the recent fixed phase totals, reference/fragment
counters, actual run details, source-row metadata and result destinations.

Replace signature-based provenance shortcuts with a deliberate capture contract
for effective engine options/defaults. Test mapping's existing defaults before
removing jobs.submit's `_map_job` special case. Provenance storage/redaction stays
in the host; method/version interpretation belongs with the engine or its declared
metadata. Do not guess an installed version.

Exit: these engines call host primitives only; their old job bridges have no
production callers. Real native FastTree and ordinary/reference MAFFT smoke passes,
alongside generated-dialog submission and provenance tests.

### 3. Migrate the remaining engines in small, reviewable batches

Use the same pattern, without changing options, naming or output formats:

1. IQ-TREE and Mauve: branch removal, thread/model policy, origin alignment,
   progress and name/source restoration.
2. GenBank download and local/NCBI BLAST: queries, polling, retries/cancellation,
   parsing and one-result-per-query behavior. Remove BLAST constants from jobs.py;
   any compatibility checks needed by prepare hooks must use a shared explicit
   contract or engine declarations, not reach back into the scheduler.
3. Primer3/PCR, ORFs, de novo and reference annotation: keep reusable pure helpers
   where genuinely shared with interactive features. Move each engine workflow,
   while preserving host-owned transactional annotation commits, per-list batching,
   apply-versus-copy rules and per-source destinations.
4. Sanger, then minimap2/samtools: sample grouping/results, extension rounds,
   long-read presets, BAM/read sidecars and mapping summaries. Preserve cleanup in
   a finally path for unpacked-read volumes on success, failure and cancellation.

Move the relevant progress reader with each engine in the same commit. If an
engine needs helper packages, extend discovery and boundary tests to cover them
recursively before introducing them; do not bypass the import rule with a shared
backdoor into core internals. Internal graph-BLAST and submission-validation workers
belong in dedicated core worker modules, not in the scheduler or fake plugins.

After each batch: focused scientific tests, existing failure/cancellation checks,
full isolated suite and the generated-path browser smoke. Run real tools where
command construction/output interpretation changed; use deterministic fixtures for
network failures and cancellation instead of depending on live NCBI for regression.
A biological GetOrganelle assembly validation remains a separate, explicitly
unverified task; an install/mock smoke is not evidence of assembly correctness.

### 4. Split the HTTP layer by domain

Follow the existing project_api router pattern. Suggested groups: library/folders,
sequences/alignments, graphs, mappings, results/figures, tools/registry/jobs, and
settings/import-export. Co-locate request models with the router that owns them.
Adjust grouping to avoid both another monolith and one-function files.

Before extracting routers, move shared request/window context, edit locks,
library action history, item resolution and destination error translation into
small core services. Routers must not import helpers from server.py or from each
other. server.py becomes app creation, middleware, exception handlers, static
mounts and router registration. Keep every active URL and response shape intact;
do not recreate the removed legacy tool endpoints.

The generic registry submission code should be split into plain input/option
validation and a thin HTTP adapter. Preserve registry-driven preparation rather
than adding operation-ID or engine-ID branches to the endpoint.

Exit: route/middleware inventory unchanged, except implementation module names;
lock/window undo isolation, missing/stale items and destination errors still have
the same behavior. Full suite plus project transfer, viewer and generated-tool
browser checks pass. Tests patch shared services rather than server re-exports.

### 5. Remove bridges and make the boundary enforceable

- Remove algorithm-specific `*_job` functions from engine_api and their obsolete
  jobs.py wrappers once callers/tests have migrated. Remove unused imports/constants.
- Remove the temporary forwarding implementation when its last use disappears.
- Expand the AST check recursively and validate plugin-used API attributes against
  the declared public surface; reject private/module-qualified host access.
- Add cold-import checks and a minimal test engine that can produce a result using
  host services with no server import or edits to jobs.py. Exercise a migrated
  engine with its old jobs compatibility name absent, proving it is not a fallback.
- Keep package/version discovery and optional-plugin reload behavior working; do not
  add a second registry as part of service extraction.
- Update FEATURE_GUIDE, CLAUDE and the old boundary plan's status to distinguish
  completed import-boundary work from completed implementation ownership.

Exit: jobs.py owns job lifecycle, not scientific command construction; server.py
owns HTTP assembly, not domain operations. No engine calls an algorithm-specific
host job facade. No helper merely relocates the same monolith under a new name.

## Invariants and delivery

Each numbered stage (and each engine batch) is a separately tested, reversible
commit. Start with the contract-test commit, then host services and the pilot;
do not attempt a simultaneous rewrite of all engines and routers.

Keep job kinds, operation IDs, citations, saved settings, file formats, metadata,
resource budgets and tool defaults stable. Explicitly protect the recent progress,
folder relocation, annotation rollback and map cleanup fixes. Use private temporary
libraries for every test and smoke; never write test results to the user's library.

This work does not add engines, redesign dialogs, promise sandboxing or force a
new plugin packaging format. File sizes are supporting evidence: success is that
an engine can own and change its workflow without editing the scheduler or HTTP
application, and host changes have a testable public contract.

## Delivery record — 3 November 2026

- Baseline: 316 tests passed before extraction. The first implementation commit
  (`86f2283`) added forwarding-target and plugin-capability checks without moving
  scientific code. Those temporary checks are now replaced by explicit API,
  recursive import/capability and cold-import contract tests.
- All 15 engine modules own their workflows. FastTree and IQ-TREE have separate
  implementations; engine-specific progress readers moved with their workflows.
  No algorithm-specific engine API/job compatibility bridge remains.
- Host services live in execution.py (154 lines), job_progress.py (28) and
  job_outputs.py (74). Shared pure scientific/format helpers remain reusable
  through explicit public API functions. The API has explicit signatures and an
  export list, including settings access; it exposes no host module objects.
- jobs.py fell from 1,691 to 287 lines and owns lifecycle, scheduling, cancellation,
  logs, destination relocation and provenance publication. The scheduler captures
  the submitted runner's declared defaults uniformly; no mapping-specific
  signature shortcut remains. Mapping's cleanup wrapper now declares those
  defaults itself. FastTree default-method citations have a regression test.
- server.py fell from 2,175 to 38 lines. Ten domain routers plus the existing
  project router preserve the original active URL/method set, frozen in a test
  fixture. Shared context/locks/history, item resolution, export preparation,
  generic tool validation and optional installation have their own services.
  Plain services have no FastAPI or router dependencies; routers cannot import
  the app or peer routers. The installer calls the plain installation service.
- The complete isolated suite passed: **324 tests**. After the final dependency
  assertions, the 12 boundary/provenance tests also passed. Tests cover annotation
  transactions, destination relocation, cancellation and mapping-volume cleanup.
  A small test engine publishes a real result through host services with no
  scheduler change; progress callbacks stay out of job responses and saved logs.
- Native analysis smoke passed: ordinary/reference MAFFT, DNA/protein IQ-TREE,
  FastTree, Mauve, Primer3, local BLAST programs and bundled-tool version checks.
  Native mapping smoke passed: ONT, ONT HQ, CLR, HiFi FASTQ/FASTA, Illumina and HiFi
  extension; synthetic long-read consensus matched the reference exactly.
- Retained Chrome smoke `scripts/test_engine_ui.cjs` passed: eight available
  category dialogs, real generated-endpoint MAFFT and ORF submissions, job logs
  and result placement, no page errors. Project transfer/metadata and genome
  figure viewer/export smoke passed (SVG/PDF/PNG/TIFF), also without page errors.
  Every check used a private temporary library.

No tool versions, scientific defaults or file formats were changed by this
refactor. Live NCBI behavior and a biological GetOrganelle assembly were not
validated here; deterministic network tests and existing installation/dispatch
checks are not substitutes for those external validations.
