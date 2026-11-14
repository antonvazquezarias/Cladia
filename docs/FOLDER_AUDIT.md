# Folder interaction audit

Audited 4 November 2026 against commit `44c45df`. The findings below record the
original behavior. The follow-up folder rework is implemented; see the delivery
record below. They are no longer an outstanding bug list.

## Delivery record

- `app-folders.js` owns folder navigation, the picker, keyboard/drag handlers and
  mutation flows. Creating a folder opens it; creating a destination for items
  opens it after the move succeeds.
- Choosing a folder exits global search and clears sidebar selection and range
  anchors. `shownIds` tracks the open viewer separately, preserving documents,
  unsaved edits, metadata, submission checks and saved-version actions.
- A shared path remapper handles the active folder, descendants and expanded
  branches across moves, renames and parent removal. Folder preferences persist
  in application settings rather than relying on the local server port.
- Picker search targets actual matches on Enter, reports no matches, supports
  arrow/Home/End/Enter/Escape navigation, F2 rename and Delete. It scrolls the
  active folder into view. Counts describe own items, with descendant totals in
  tooltips.
- Drag cancellation resets the expansion timer and path; successful hover
  expansion is saved. Same-destination moves are disabled. Folder submission
  prevents duplicate requests and cancellation while its write is in progress.
- Stale parents/sources/destinations are rejected before mutation. Parent removal
  rejects child-folder collisions, preserves surviving descendant navigation and
  reports errors visibly. No implicit folder merge is offered.
- Moving list members remaps the viewer to extracted or renumbered record IDs.
  Unsaved edits block extraction before creating a destination. Partial failures
  refresh the library and are reported as incomplete moves.

Verification: `tests/test_folders.py`, `tests/test_view_settings.py`, and
`scripts/test_folders_ui.cjs` retain the mutation, persistence and browser
regressions. The browser script uses a private library and covers all reproduced
issues plus real dragging, dirty/clean viewer preservation, list-member identity,
keyboard interaction and fresh-profile persistence. The composition smoke check
was also updated to use generated tool dialogs rather than the retired TOOLS API.

## Method

Traced the frontend folder/navigation/selection handlers and backend relocation
rules. Reproduced the cases below in Chrome against a private temporary library,
using generated sequences and folders. The audit exercised the real folder API
and UI handlers; it did not access the normal library or run scientific tools.
No browser exceptions occurred in the successful-path checks.

The scratch observations are in `build/folder-audit/observations.json` when run
in this checkout. They are diagnostic artifacts, not a retained regression suite.

## Confirmed findings

### 1. Creating a folder leaves you in its parent

Create `Alpha/Created` while browsing `Alpha`: the folder exists, but the sidebar
still shows `Alpha`. The same dialog can create a destination and move items
into it without opening the destination.

**Recommendation:** open newly created folders, including a folder created for
selected items after their move succeeds. Keep rename operations in the current
location. Preserve the existing unsaved-edit safeguards.

**Code:** `app.js:folderDialog`, `moveToFolder`.

### 2. Moving the currently open folder jumps to the library root

With `MoveMe` open, move it into `Beta`. The move succeeds, but the sidebar opens
the root rather than `Beta/MoveMe`. The frontend joins the new path with an empty
suffix, producing `Beta/MoveMe/`; the next refresh treats that trailing-slash path
as missing and falls back to the root.

**Recommendation:** use one path-remapping helper that handles both the folder
itself and descendants without introducing trailing separators.

**Code:** `app.js:moveFolder`, `loadList`.

### 3. A global filter makes folder navigation appear ineffective

Search for `matching`, then choose `Beta`. The button says `Beta`, but matching
items from both `Alpha` and `Beta` remain visible. Folder navigation keeps the
query, and any nonempty query bypasses folder filtering.

**Recommendation:** explicit folder navigation should leave global-search mode
and show that folder. Make global-search scope visible while a query is active.

**Code:** `app.js:inFolder`, `setFolder`, `visibleRows`.

### 4. The sidebar retains an invisible selection from the previous folder

Select a sequence in `Alpha`, then browse `Beta`. No row is visibly selected,
but `selected` still contains the sequence in `Alpha`. Library actions consume
that selection, and the old item can remain open in the viewer.

Keeping an open viewer while browsing is useful. Retaining an undisclosed
sidebar selection makes the target of tools, Copy or Delete unclear.

**Recommendation:** distinguish the viewed item from sidebar selection. Reset
sidebar selection on folder navigation, retain the open viewer and its unsaved
state deliberately, and make each action's target visible.

**Code:** `app.js:setFolder`, `renderList`, `showSelection`, `removeSelected`.

### 5. Shift-click can select nothing after changing folders

Select an item in `Alpha`, browse `Beta` containing three sequences, then
Shift-click its first row. The selection becomes empty. `anchorId` still points
to an item outside the visible rows, so the range calculation uses index `-1`.

**Recommendation:** reset the anchor on navigation and validate that both range
endpoints are visible. An invalid anchor should select the clicked item normally.
The same validation should cover changes to the sidebar filter.

**Code:** `app.js:clickItem`, `setFolder`.

### 6. Enter in folder search can open an ancestor instead of the match

Search for `Needle` when it is inside `ZParent`. The filtered tree correctly
shows `ZParent` and `Needle`; Enter opens `ZParent`. The handler clicks the first
non-root row, including ancestors shown only to provide context.

**Recommendation:** distinguish actual matches from contextual ancestors and
activate the first match. Add arrow-key navigation and visible keyboard focus.

**Code:** `app-start.js:folder-filter.onkeydown`, `app.js:renderFolderTree`.

### 7. Cancelling a drag can break the next hover expansion

Start hovering over a closed branch, close the popup before its expansion timer
fires, then hover over the same branch again. It does not open. Closing cancels
the timer but leaves `springPath`, causing the next attempt to return early.

**Recommendation:** reset both timer and hover path on popup closure and drag
completion, and save expansion state consistently after successful hover opening.

**Code:** `app.js:springOpen`, `closeFolderPopup`; `app-start.js:dragend`.

### 8. Expansion state does not follow renamed or moved folders

Expand `Alpha`, rename it to `Renamed`, and inspect the expansion state. It still
contains `Alpha`. Moves leave the same stale paths. Opening the picker re-expands
the active folder's ancestors, masking part of the problem, but other expanded
branches lose their state and stale entries accumulate.

**Recommendation:** remap expanded paths together with folder navigation, remove
paths that no longer exist, and persist the updated state.

**Code:** `app.js:openBranches`, `folderDialog`, `moveFolder`, `saveBranches`.

### 9. Deleting a parent loses your place and can silently merge children

With `DeleteMe/Survivor` open and another root-level `Survivor` already present,
delete `DeleteMe`. Both children's items end up in one `Survivor`; the sidebar
jumps to the root. The confirmation describes moving contents up but does not
identify the collision or explain the merge. Both test sequences survived.

**Recommendation:** preflight destination collisions and explain or reject
merges before writing. Follow the surviving nested location when possible.
Report API failures visibly; the current delete handler has no error catch.

**Code:** `library.py:delete_folder`, `_moved`; `app.js:deleteFolder`.

### 10. A stale creation dialog can recreate a renamed parent

Open New folder inside `PendingParent`, rename that parent through another
window/API request, then submit the dialog. It creates `PendingParent/Child`,
implicitly recreating the old parent alongside the renamed one.

**Recommendation:** validate that an explicitly chosen parent still exists at
submission, under the library write lock. Ask the user to choose the destination
again if it changed. Preserve any intentional ancestor-creation behavior in
import/project workflows separately.

**Code:** `app.js:folderDialog`; `library.py:add_folder`, `folders`.

## Suggested implementation order

1. Fix active-path remapping, opening newly created folders, global-filter exit,
   selection/anchor behavior, and expansion-state remapping as one navigation pass.
2. Fix folder-search keyboard targeting and drag-hover cleanup. Retain a browser
   regression script covering these interactions with a private library.
3. Add backend checks for stale parents and delete collisions, then reflect those
   errors and choices in the UI. Cover successful moves as well as rejected writes.

## Additional polish identified by inspection

- Folder rows and branch arrows lack keyboard focus/navigation support.
- Parent counts include descendants, but opening a folder shows only its own
  items; label counts or empty states so these do not appear contradictory.
- Opening the picker expands ancestors but does not scroll the active row into
  view, making large trees harder to navigate.
- The current folder and expansion state use browser storage. The default random
  local server port means this is not reliable persistence across app launches;
  app-level settings now provide a model for fixing that.

These are separate from the ten reproduced cases above. They do not require
replacing the folder model or changing how files are stored.
