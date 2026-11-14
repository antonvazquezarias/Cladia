# Plan: figures as library items

Status: stages 1–3 done 5 November 2026 (figure items, migration, API, the
Figures section, Figure ▾, the figure's bar with ◀ source, deleting asks,
figures of a selection). Not done: a genome stretch across the origin; stage 4's
real-browser script runs (Playwright isn't installed on the development PC).

## Why change, and what this reverses

Figures were library items once (October 2026). The user then chose to make a
figure a view of its item: one figure per tree, comparison or genome, kept in a
`.figure.json` file beside it and opened with the Tree / Figure switch
(`move_old_figures` converted the early items). That kept figures attached to
what they show, but it allows only one figure per item, cannot make a figure of
part of an item (a clade, a stretch of a genome), and needs a switch in the
viewer bar.

On 5 November 2026 the user chose figures as items again, with a **Figure ▾**
button in the viewer bar. This plan keeps what made the "view" design good:
a figure stays with its source, follows it between folders and is listed with it.
It adds what the old items lacked: several figures per source, figures of a
selection, and a clean migration.

## The model

- New item kind **`figure`**. Its file `<name>.figure.json`:
  `{ "version": 1, "of": "tree" | "comparison" | "genome", "source": "<item id>",
     "part": null | {...}, "settings": {...} }`.
  `settings` is exactly what the editor saves today. `part` is the selection
  it was made from (below).
- The entry carries `source` (the item it draws) and sits in its source's folder.
- **Drawn live from the source**, as today: edits to the tree or genome appear
  in the figure. Settings that name things (clades by tip names, features by
  `type|name|start|end`, genes by name) already survive edits.
- **Sources:** nucleotide sequences, trees and comparisons. A sequence inside a
  list is referred to by the list's id and the record's name (`source_record`),
  since member ids are renumbered when the list changes; if the record is renamed
  or removed the figure says "source missing".
- **Part** (a figure of a selection):
  - tree: `{ "tips": [...] }`, the subtree spanned by those tips;
  - genome: `{ "start": 1-based, "end": 1-based }`, origin-crossing allowed,
    using the linear layout's existing range;
  - comparison: `{ "genomes": [indices] }`, using the existing hide/order settings.
  If a part no longer matches (tips removed, sequence shorter), the figure draws
  what still exists and says what is missing.

## In the library list

- Figures have a **Figures section** of their own, like Sequences, Alignments
  and Trees (agreed 5 Nov 2026: nested under their source they were hard to
  find, above all for a sequence inside a list). Each row says what it shows
  underneath ("of Mt"). Search finds them by their name too, and the source's
  Figure ▾ lists its figures, so they are reachable both ways.
- A new figure goes in its source's folder; after that it is an ordinary item
  (moving the source does not move its figures). **Duplicating** a source does
  not copy its figures; **Duplicate** on a figure makes a second figure of the
  same source.
- **Deleting** a source asks: "Also delete its 2 figures?" Yes: they go to the
  trash with it and come back together on Restore. No: they stay, marked
  "source missing", and can't be drawn until the source is restored.
- **Rename** works on figures like any item.

## In the viewer

- **Figure ▾** at the right end of the bar for every item that can have figures,
  replacing the Sequence / Tree / Comparison | Figure switch:
  - *New figure*: of the whole item, opens it.
  - *New figure of the selection*: enabled with a selected clade, a range on a
    genome, or chosen genomes in a comparison.
  - Then the item's existing figures, one per line, to open them.
- **A figure item's own bar**, the same layout as every viewer:
  - title: the figure's name; underneath, "Figure of <source>" (or "Source missing");
  - **◀ <source>** first, to go back to what it shows;
  - groups **Zoom** (the bar's icons) · **Edit** (↶ ↷) · **Export** (SVG, PDF, PNG,
    TIFF and dpi), with the editor's side panel of settings unchanged.
  - The editor's separate `fig-bar` goes; its buttons move into the viewer bar.
- Opening the source from the list or ◀ shows the source as usual.

## Migration

- At startup (and after a project import), each existing sidecar figure
  (an entry with a `figure` field) becomes a figure item named
  "<source> figure" in the source's folder, with the same settings.
- Its saved versions move with it: the `<id>_figure` history becomes the new
  item's own history, so figure undo history is kept.
- The `figure` field is then removed from the source. This replaces
  `move_old_figures`, which converted the other way.
- Project ZIPs from older versions import, then migrate the same way.
- The step is idempotent and journaled like other library writes: a crash
  during migration leaves either the old sidecar or the new item, never neither.

## API

- Figures use the ordinary item endpoints (rename, move, duplicate, delete,
  history, trash, export of the item record not offered).
- `POST /api/figures {source, part?, name?}` creates one and returns its id.
- `GET /api/figure/{figure id}` → `{of, source, part, settings, missing}`;
  `PUT /api/figure/{figure id}` saves settings (as now, keyed by the figure, not the source).
- `/api/figure/export` unchanged.
- `GET /api/library` items gain `source` for figures and `figures` (a count)
  for sources, for the list and Figure ▾.

## Stages

1. **Items and migration (backend):** kind, file format, create/save/read,
   trash cascade, duplicate, move with source, project ZIP, migration from
   sidecars with history. Tests in `tests/test_figure_items.py`; existing
   `test_genome_figure.py` and project tests updated.
2. **List and viewer:** figures under their source, Figure ▾, figure items opening
   in the editor, the editor's bar merged into the viewer bar, ◀ source.
3. **Figures of a selection:** `part` for clades, genome ranges and comparison
   subsets, with "New figure of the selection".
4. **Docs and checks:** CLAUDE.md, USER_GUIDE, FEATURE_GUIDE; browser checks on
   a private library (tree, genome, comparison, migration of an old library).

Each stage is committed on its own; stage 1 changes nothing visible except that
existing figures still open (through a temporary path) until stage 2 lands.

## Decisions (agreed 5 November 2026)

1. A **Figures section** in the list (not nested under the source).
2. Drawn **live** from the source.
3. Deleting a source **asks** about its figures.
4. Later, not in this plan: a toolbar **Figure** group for multi-item figures
   (several trees side by side).
