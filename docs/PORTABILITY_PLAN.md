# Metadata checks and project portability

Implementation decisions and validation, updated 2 November 2026.

## Scope and decisions

- Strengthen the existing metadata importer: deterministic matching, reject
  ambiguous matches before writing, deduplicate selections, accurate preview
  counts, reject oversized tables rather than truncate, detect stale previews,
  and retain per-record history for large lists.
- Add a versioned Cladia ZIP format for the entire saved library. Include all
  registered files and sidecars, folders and provenance; saved versions and Trash
  are optional. Exclude app settings/credentials, tool installations, local BLAST
  databases, job workspaces and external source reads.
- Import into a new, uniquely named folder with fresh item IDs and filenames.
  Preserve links between imported objects. Never replace existing library items.
- Validate the complete archive before publishing: schema/version, paths, duplicate
  names, size limits, required files and SHA-256 hashes. Stage files on the same
  disk, publish the index last, and roll back newly published files on failure.
- Keep storage rules shared with existing library operations. No broad frontend
  rewrite or storage migration.
- UI: Settings > Storage contains Export project ZIP and Import project ZIP.
  Show an import summary before applying, and indeterminate progress while busy.
  Export describes saved state only; unsaved viewer edits must be handled first.

## Verification

Metadata tests cover ambiguous aliases, overlapping selections, full preview
counts, stale previews, size limits, and restoration of large-list records.
Project tests cover round trips, linked items, sidecars, optional history/Trash,
repeated import, malformed archives, traversal, corruption and rollback. Run the
full Python/JavaScript suite and browser interaction checks against scratch data.

## Handoff

Update README (user behavior), FEATURE_GUIDE (integration rules), and PROJECT_REVIEW
(current priorities).

## Implemented and verified

Follow-up fault-injection/performance audit found two correctness defects and
confirmed several resource/cleanup limitations. See
[PROJECT_RISK_TESTS.md](PROJECT_RISK_TESTS.md); those findings qualify the passing
baseline below. The audit was initially test-only; its follow-up fixes are now
implemented, with the original probes repeated successfully.

- Dedicated projects.py / project_api.py / app-projects.js; shared attachment
  enumeration and primary-content checks in library.py. No storage migration.
- ZIP v1 manifest: format/version/created, entries, folders, Trash metadata,
  optional history flags, exclusions, and a files map with size/SHA-256 per path.
  Payload paths: library/<filename>, history/<owner>/..., trash/<trash-id>/....
  History contains index.json and content-hash gzip blobs (including record trees).
- Import remaps links in JSON and compressed snapshots, recomputes changed history
  hashes, and disables imported original-path write-back. Publication uses
  same-disk renames; index publication is last. Rollback failures explicitly name
  recovery files if necessary. Publication now has a journal: startup removes
  verified uncommitted files, preserves committed imports, and reports changed
  destinations without deleting them. Expensive preparation runs outside the lock.
- Final validation is recorded in PROJECT_RISK_TESTS.md: the full suite passes
  **253 passed without warnings or xfails**. Cases cover interrupted native-export
  destination copies, import publication and job workspace creation failures.
- Real Chrome smoke: ambiguous metadata blocked, valid metadata saved, ZIP
  downloaded/reimported with history, malformed ZIP reported, no page errors.
  Retained as scripts/test_project_ui.cjs. Native export/inspect/save-path API
  branches and native WebView ZIP file dialogs/Cancel are tested. The normal-close
  popup and wait-for-cleanup behavior have production-handler tests with fake windows.
- Limits: 50 GiB archive payload, 100,000 payload files, 512 MiB uncompressed per
  history snapshot. Need disk for ZIP plus staged files. History and Trash default
  off. Settings/secrets, tools/databases, external reads, job logs are excluded.
- Metadata batches roll back all affected files and timeline indices on ordinary
  write failures. Ambiguity is blocked before writing; UI preview hashes detect
  changed saved inputs. Temporary rollback copies need working disk space; no
  independent backup or arbitrary power-loss transaction was added.
- ZIP checking/export/import run as local jobs with progress, cancellation, and the
  existing close warning. Confirmed closure waits for local cleanup. API defaults
  are asynchronous; background:false retains a synchronous testing/compatibility
  path (upload: query parameter). Native export destination temp files also have
  a recovery journal; startup verifies the partial ZIP prefix before removing it.
  Browser exports are downloadable from job rows; previews/downloads expire after an hour (four each).
- Sequence history remapping now uses bounded prefix detection; original probes
  show ~51 MiB peak rather than ~318 MiB. Concurrent transfer writes wait ~1–2 ms
  rather than seconds. Forced-crash tests leave no orphan/staging files after restart.
