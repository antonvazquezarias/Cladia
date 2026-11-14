# Plan: GC content and GC skew tracks (for another agent)

Written 2 Nov 2026. Read `CLAUDE.md` first (*Sequence viewer and editor*, *Working with the user*), then
this file. Implemented 2 Nov 2026; the plan below is retained as the design record.

## Decision: a general feature of the viewer, not only of the figure

GC% (and GC skew) along a genome is useful while looking at any sequence, not only in a paper's figure:
spotting a horizontally transferred or misassembled stretch, a bacterial contaminant contig, an AT-rich
intron, the plastid's inverted repeats (higher GC in the rRNA). So it is an **optional track of the
sequence viewer** (linear and circular), off by default, and the genome map figure
(`docs/PLAN_GENOME_MAP_FIGURE.md`) **draws its own GC ring from the same function**: one computation,
two drawers. The figure's ring is not a reuse of the viewer's drawing code (SVG there, canvas here).

## What exists

- **`SeqEdit.gcWindows(sequence, size)`** in `cladia/static/seqedit.js`: non-overlapping windows,
  `[{ start, end, called, gc }]`, 0-based half-open, ambiguous bases out of the denominator, `gc` a
  fraction or `null`. It was added to the working tree by another session (the genome figure's) and may
  be uncommitted: **run `git status` / `git log` first, reuse it, extend it (skew), don't write a second
  one**; if it isn't there, write it with that signature.
- The model to copy for a track: the **mapping view** in `seqview.js` (`setMapping`, `COVERAGE` 46 px,
  `mappingBins` (per-pixel bins when zoomed out), `drawMapping`, `mappingTrack(y)` / `mappingTrackTip` /
  `mappingTrackSummary` for the hover text and the names column's summary, `hoverTrack`) and, for the
  circle, `seqview-circular.js` (`circRings`, `circBins`, `circHover`: rings inside the circle for
  coverage / agreement / clipped reads). Layout arithmetic lives in `layout`, `headerHeight`, `layoutKey`
  (it must include anything that changes the heights), `mappingTop(row)`.
- The **Show panel** (`#show-panel`, `renderTypes`, `seqShow` in localStorage `cladia.view`, `setShow`,
  `changeSeqShow`) is where options live; its checkboxes are in `index.html`.
- The viewer's status line text for what is under the mouse (no browser tooltips over the canvas).
- `genbank.record_meta` / `/api/info/{id}` (the sidebar's hover cards) for facts about a sequence.

## What to build

**A. The computation** (`seqedit.js`, pure, tested in node)
- `gcWindows(sequence, size)` as it is.
- `gcSkew(sequence, size)`: per window `(G - C) / (G + C)` (`null` when G + C = 0), and a cumulative
  series (running sum of the windows' `G - C` over the sequence length) because that is the form people
  plot to find origins and terminators.
- `autoWindow(length)`: a round window giving about 300-800 windows (100, 200, 500, 1000, 2000, 5000...).
- Sequences edited in the viewer change: recompute on the document's change (debounced, ~150 ms), cached by
  the document version. 500 kb is trivial (a few ms); don't worry about workers.

**B. The linear track** (single-sequence viewer, `mode === "single"`, DNA only)
- Show ▾ gets **GC content** and **GC skew** ticks, a **Window** select (Auto, 50 ... 5,000 bp; remembered
  in `seqShow`), and for skew *per window | cumulative*.
- A header track like the coverage graph: GC% as a filled graph with a thin line at the sequence's mean
  (the mean's value written at the axis, the y range from the data's min to max padded, so a 30-45 % genome
  isn't a flat line at the bottom of a 0-100 axis; say the range in the names column's label), the track's
  name at the left (it opens on hover like a row's name). Skew: bars above (green) and below (red) a
  zero line, or the cumulative curve.
- Zoomed out: per-pixel bins (min, mean, max or just the mean) as `mappingBins` does; zoomed in: the
  windows as bars. Hover: `GC 41.2 % · 1,501-1,600` in the status line; over the track's name, the
  whole sequence's GC and the visible stretch's, as `mappingTrackSummary` does.
- Clicking the track selects that window's bases (as a click on a coverage position would; optional).
- Not for alignments, proteins, mappings (they have their own tracks), comparisons (later: one per genome).

**C. The circular map**
- Rings inside the circle (below the feature lanes, above the centre) as the mapping rings: GC as an
  area above and below the mean (OGDRAW's style: black where above the mean), skew as a second ring, each
  with the same window. Hover text as `circHover`. Layout: `circRings` currently knows the mapping's
  rings; generalise it to "the tracks this view has" rather than adding a second code path.

**D. Whole-sequence GC in the facts** (small and useful on its own)
- `record_meta`'s pass already reads each record once: add `gc` (a fraction, over called bases) to what
  `/api/info/{id}` returns and show "GC 38.2 %" on the sequence's hover card and in the Metadata tab's
  facts. Cached with the rest (the index is keyed by mtime and size). Don't slow the 5,000-genome import:
  count from the sequence string you already have; measure (the notes say 0.8 s for 5,000 genomes today).

**E. Optionally, GC by feature class** (a table: GC of CDS, rRNA, tRNA, introns, intergenic): a button in
the Annotations tab. Only if A-D are done and the user asks; say so in the final report instead of building it.

## Order of work

1. Pure functions + node tests (`tests/test_seqedit.js` or a new `tests/test_gc.js`): a known sequence with
   known windows, an all-N window, a window shorter than `size` at the end, skew signs, cumulative sum.
2. Linear track + Show panel options + hover; headless screenshots on `tests/data/Clarei_mt.gb` and a
   synthetic 150 kb genome with a planted AT-rich stretch (`tests/make_big_genome.py`): **look at them**.
3. Circular rings.
4. Facts (D), docs, commit.

## Tests and checks

- `python -m pytest tests` stays green (it runs the node tests too: node at `%LOCALAPPDATA%\node\node.exe`).
- Headless Playwright (scratch `tvenv`; the app run with the Cladia venv's python and `CLADIA_DATA` at a
  scratch folder): toggle the track on and off, change the window, edit a base and see it update, zoom,
  circular and linear, a protein (no track offered), a list of sequences (decide: one track per row?
  **Don't**: sequences mode has rows; offer it only in single mode and say so).
- `layoutKey` must include the new tracks (the viewer re-lays out when heights change, or the rows jump).

## Docs and housekeeping

- `CLAUDE.md`: a paragraph under *Sequence viewer and editor* (the tracks, the window rule, where the
  function lives and who else uses it), and update the *Future additions agreed on 2 Nov 2026* list.
  `README.md`: a line in the viewer's description.
- Commit when a stretch is done and tested, push to `main`, tell the user in a line. Another session may
  have uncommitted changes in the same tree (`git status` before staging): **stage only your own files**
  (`git add <paths>`, never `git add -A`).
- Ask the user before a design choice that would be costly to redo, with a screenshot; they answer
  questions before you build.

## Out of scope

Sliding-window overlap settings, GC by codon position (GC3), per-genome tracks in comparisons, GC in the
alignment viewer, exporting the values as CSV (easy to add later: the table view already does CSV).

## Completion — 2 November 2026

Implemented and checked. See CLAUDE.md, “GC tracks and long reads”, for implementation
choices, synthetic Linux mapping results, performance measurements and limitations.
The retained Chrome check is `scripts/test_composition_ui.cjs`; it uses a temporary
library. GC-by-feature-class, assembly and polishing remain outside this work.
