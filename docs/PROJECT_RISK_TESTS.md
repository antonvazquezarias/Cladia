# Project risk experiments — 2 November 2026

Application revision tested: `9dc74af`. Tests and documentation only; no fixes
were applied. All experiments used temporary libraries and synthetic data on the
local Windows machine, with the installed Cladia Python 3.14 environment.

**This is the historical audit. The follow-up fixes and verification below
supersede its open issues and expected-failure status.**

## Findings

| Concern | Observed result | Priority |
| --- | --- | --- |
| History write fails after sequence write | Metadata edit remains, but restoring the previous saved version raises “The file changed outside its history; reload before restoring”, even after looking up the item again. | High: repair save/history failure handling first. |
| Folder created during project import | Creation succeeds and the folder initially exists; the import subsequently publishes its captured folder list and the new empty folder disappears. No exception is reported. | High: serialize folder creation with other library writes. |
| Large-transfer write blocking | 256 MiB incompressible attachment: export ~3.2 s; concurrent protected folder rename waits ~3.2 s. Import ~1.5–1.8 s; concurrent rename waits ~1.5–1.8 s. Reads of this small index take ~1–8 ms. | Medium: shorten write-lock duration after correctness fixes. |
| History import memory | A 127.9 MiB GenBank history snapshot, in a ~0.76 MiB ZIP, raises worker peak working set from ~46 MiB to ~318 MiB. Import succeeds (~0.3–0.5 s). | Medium: avoid whole-snapshot JSON decoding for sequence histories. |
| Forced exit before index publication | Import worker deliberately exits with code 77 after files have moved but before items.json is written. Real server startup leaves one unregistered GenBank file and one project staging directory. Existing item bytes remain unchanged; Check library reports the orphan. | Medium: interrupted-transfer recovery/cleanup. |
| Failure on second metadata file | First file remains edited, second stays unchanged. Error names the completed file. Restoring the first file's previous version succeeds. | Existing incremental behavior confirmed; operation-level undo is a usability improvement. |
| Native ZIP dialogs | Actual WindowApi save/open methods exercised in a separate WebView: export creates a ZIP, open selects it and validation finds one item; Cancel returns cleanly for both. | Passed for this machine/configuration. |
| Existing regression suite | 235 passed; three existing Biopython warnings about the partial-GenBank fixture's malformed LOCUS line. | Fixture cleanup is low priority; tests still verify the relevant behavior. |

## Reproduction and scope

`scripts/probe_project_risks.py` prepares fixtures and launches separate workers
for timing/memory/crash measurements. Windows process counters report peak working
set. A real library write (rename_folder) measures lock contention. The initial
exploration used add_folder and found that it bypasses the lock; this led to the
separate deterministic folder race test. Measurements above use rename_folder.

The transfer attachment is 256 MiB of random bytes registered as a kept BAM plus
an index. This measures ZIP transport, not BAM validity or read mapping. The
history fixture is valid GenBank list text representing an earlier large list;
repeated content deliberately makes storage small while testing raw memory cost.
These are bounded probes, not validation of 50 GiB projects or all storage speeds.

`scripts/probe_native_zip.py` opens only a temporary diagnostic WebView and calls
the production WindowApi ZIP methods. Computer-use automation selected the
diagnostic window, saved/opened its scratch ZIP, cancelled both dialogs and closed
the window. The temporary library and ZIP were removed afterward. This complements
the retained browser end-to-end check; it is not an end-to-end run of the entire
production app inside WebView.

Expected-failure regression tests are retained with `strict=True`:

- `tests/test_projects.py::test_folder_created_during_import_is_preserved`
- `tests/test_metatable.py::test_history_write_failure_still_allows_recovery`

Focused run: **26 passed, 2 xfailed**. These xfails represent reproduced defects,
not passing behavior. Remove the markers when implementing and verifying fixes;
strict mode flags an unexpected pass so the markers cannot silently linger.

Recommended order: save/history consistency, folder write serialization,
history memory use, transfer lock duration, interrupted-transfer cleanup, then
optional bulk-operation undo and fixture-warning cleanup.

## Follow-up implementation and repeated experiments

- Both strict xfail markers have been removed: folder creation survives import,
  and a failed history checkpoint restores the original metadata and timeline.
  File guards also cover alignment, tree, annotation and figure saves. Blob
  pruning now happens after index publication and is deferred during a save.
- A second metadata-file failure now rolls the entire batch back; the two hosts
  both remain empty, and restoring the baseline succeeds. Save APIs return useful
  JSON storage errors rather than an unhandled response.
- For the same 256 MiB transfer fixture: export ~3.63 s, import ~1.85 s, while a
  concurrent library write waits only ~1–2 ms. Export copying/compression and
  import validation/remapping no longer hold the library write lock. Concurrent
  source changes during snapshot copying abort export safely for a retry.
- The same 127.9 MiB history snapshot imports with ~51 MiB peak working set,
  down from ~318 MiB. A bounded prefix check skips JSON decoding of sequence
  histories. JSON result histories still need decoding to remap references.
- Forced exit before index publication, followed by real server startup, now
  leaves no orphan files and no staging directories. The recovery report shows
  one cleaned transfer, no issues, and the existing item remains byte-identical.
  Additional subprocess tests cover forced exit after index commit: imported
  objects are preserved. Changed or registered recovery destinations are retained.
  A forced exit during native export also cleans its destination-side temporary
  file on recovery while preserving the previously existing ZIP.
- ZIP checking/export/import run as local jobs with stages, cancellation and the
  normal close warning. Tests verify No keeps the app/job open, and Yes cancels
  and waits for cleanup. Export cancellation preserves an existing destination.
  Queued cancellation removes its staged upload. Imported provenance is preserved.
- The partial-GenBank fixture LOCUS column spacing is corrected; its biological
  content and assertions are unchanged.
- The Chrome smoke passes with asynchronous jobs/downloads and no page errors.
  Original native ZIP dialog checks remain applicable; close-dialog logic is
  tested with the production handler and a fake window, not by automating its UI.

The probes remain bounded local experiments, not claims about all hardware or
50 GiB transfers. Rollback protects ordinary metadata/save failures; arbitrary
power-loss atomicity across a metadata batch is not promised. Transfer journals
provide recovery for interrupted project publication.

Final follow-up verification: **253 passed, no warnings and no xfails** in the
full Python/JavaScript suite. Chrome asynchronous job/ZIP smoke also passes.
This includes native-export crash cleanup and workspace-creation failure handling.
