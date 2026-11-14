# Genome map figures: implementation record

Written 2 Nov 2026 from the agreed implementation plan; completed the same day.
Read `CLAUDE.md` first for current architecture and the *Figures* section. The
design and order below are retained as a reference for the shipped implementation.

## Goal

A **figure of one annotated sequence** in the figure editor, for a paper: the circular gene map an
organelle-genome paper always has (as OGDRAW draws it), and a linear version. It is exported like the
tree and comparison figures: SVG, PDF, PNG, TIFF. The user (a phycology lab, seaweed organelle genomes
under 500 kb, mitochondria and plastids, GenBank annotations de novo or from a reference) wants it good
enough to put in a paper without redrawing it in Inkscape.

## What already exists (reuse it, don't rebuild it)

- **The editor**: `cladia/static/figure.js`, class `FigureEditor`. A *kind* is an object in `KINDS`
  (`{ tree, comparison }`, line ~1036) with `defaults`, `prepare(source)`, `select(el)`, `panel(s, sel,
  source)`, `action(name, s, sel, source)`, `draw(s, source, sel, lookup)` returning `{ body, width,
  height }` (an SVG string body, sizes in points) and `hint`. `comparisonFigure` (line ~772) is the
  model to copy: its `COMPARE_DEFAULTS` (line ~655), its *Page* section (width/height in mm, font, font
  size, line width), its arrows with a head on the 3' end and parts joined by a thin line
  (polygons, ~line 1003), its names with `markupRuns` / `speciesRuns` (italic species names), its scale
  bar, `data-*` attributes on clickable elements (`data-feature`, picked by `select(el)`).
- **Circular geometry**: the tree figure's circular layout, `treeFigure.draw` ~lines 425-530: `arc`,
  `sector`, `radial` (text along a radius, turned round on the left half), `placedRuns` (each text run
  its own `<text>` at a measured x: **svglib mis-places `tspan`s in rotated groups and has no
  `textPath`**, so never use `textPath`; copy that technique).
- **Settings storage**: `library.figure_settings / save_figure_settings` (a `<name>.figure.json` beside
  the item, entry key `figure`, versions in `history`), `GET/PUT /api/figure/{id}`, `_figured(item_id)`
  in `server.py` (~line 1787, it refuses anything but `tree` and `comparison`), duplicate / trash /
  restore / remove already carry the `figure` file (`library.py` ~line 1146, `_files_of`): check they
  do for a sequence entry too.
- **The page side** (`static/app-tools.js` ~776-870): `renderFigureSwitch`, `setFigureView`, `showFigure`,
  `figureLookup`, `exportFigure`; the title bar's `#view-figure` switch (Tree / Figure); `app.js` ~1992
  hides it by default. Export goes through `figures.py` (`/api/figure/export`): nothing to change there
  unless the PDF of the new drawing breaks (see *Checks*).
- **Colours**: `FEATURE_COLORS` in `app.js` (gene green, CDS yellow, rRNA red, tRNA purple...). The
  figure should have its own colours by *function class* (below), not those.
- `seqedit.js` has the sequence helpers and is testable under node (`tests/test_seqedit.js` shows how).

## Decisions already taken (don't reopen without a reason)

1. A figure is **of one standalone sequence** (a sequence item with no `parent`). A list's member has no
   library entry to hold the settings file: show "Duplicate it or split the list first" instead of the
   switch. Mappings, graph tracks, Sanger reads: no switch (they are other views of a sequence).
2. It is **another view of the item**, like a tree's: the title bar's switch reads *Sequence | Figure*.
   Only the user's settings are saved, the drawing is made from the sequence each time.
3. Both **circular and linear**; the default follows the record's topology (`circular`), a setting
   changes it.
4. Features are coloured by **function class**, found from the gene name by an editable table of
   {name, pattern, colour}. Defaults for organelles (match case-insensitively on the feature's gene or
   name): photosystem I/II (`psa`, `psb`), cytochrome b6f (`pet`), ATP synthase (`atp`), NADH
   dehydrogenase (`nad`, `ndh`), cytochrome b / c oxidase (`cob`, `cytb`, `cox`, `cyc`), RuBisCO (`rbc`),
   ribosomal proteins (`rps`, `rpl`), RNA polymerase (`rpo`), rRNA (type rRNA, or `rrn`/`rnl`/`rns`),
   tRNA (type tRNA), protein transport and others (`sec`, `tat`, `ycf`, `ccm`), ORFs and unknown. The user
   can rename a class, change its colour, add one, and set the colour of a single feature.
5. Settings are keyed so that they survive edits of the sequence: a single feature is identified by
   `type|name|start|end` (not its index in the file). Say in the code comment what happens when a
   feature moves (its override is dropped, quietly).
6. No new Python dependency. Text as runs (`runsWidth`, `runsSVG`, `placedRuns`) so italics work in the
   organism name in the centre.
7. Python-only for the server, but this feature is mostly JavaScript; keep it plain, with section
   headers and short comments as the rest of `figure.js` has. No build step, no third-party code.

## The drawing

**Circular** (OGDRAW's conventions, each one a setting):
- Rings from the outside in: *gene names* (outside the ring, radial text turned round on the left half,
  short leader lines when a name is pushed away from its gene, names that overlap resolved by a simple
  greedy pass: move along the ring, then outwards; never hide a name silently, say how many were left out
  in the panel), the *gene ring*, *tick marks and kb labels*, an optional *GC content ring* (window size
  and a mean line), the *centre*.
- Strand: genes on the + strand on one side of a mid line, the - strand on the other (default as OGDRAW:
  + strand outside, - strand inside; a setting). Genes are arcs with a pointed 3' end (an arrow), the
  parts of a feature (exons) joined by a thin line, a feature across the origin drawn in two pieces.
- Centre: the organism (italic by the naming rules: `speciesRuns`), the length ("12,345 bp") and the
  GC %; each editable as text with `*italic*` markup (`markupRuns`).
- Optional `regions`: user-defined named stretches (LSC, SSC, IRa, IRb; a start, an end, a colour) drawn
  as a thin outer arc with its name. A button *Add regions from the selection* is not needed: type them.
- A **legend** of the classes that occur (a swatch and the name), placed at a corner or below, off when
  asked.
- Page: width in mm (84 / 174 presets as the others, with the help text), height as needed, font, font
  size, line width in pt, exactly as `COMPARE_DEFAULTS`' *Page* section. A square drawing area with the
  legend beside or below it.

**Linear**: the sequence as a line with a kb scale and ticks, the + strand features above and - strand
below (or all on lanes if they overlap), the same colours, arrows and names (names slanted by an angle as
in the comparison), the same legend. A *region of the sequence* (from, to) can be drawn instead of the
whole.

**Interaction** (`select(el)` / `panel` / `action`): click a feature: its section appears first in the
panel (name text, colour, hide, name position nudge); click the centre text; click a class in the legend
(its colour); a region. Buttons: *Reset this feature*, *Hide this gene*, *Hide all of this class*. Every
change is one undo step and is saved (the editor does that, you only change `settings`).

## Order of work (each step is testable and can be committed on its own)

1. **Server + switch.** `_figured`: accept a `sequence` with no parent (400 with the hint for a list
   member). `app-tools.js`: `renderFigureSwitch` / `setFigureView` / `showFigure` handle a sequence shown
   in single mode (not a mapping, graph track, read), label *Sequence*, data from `GET
   /api/sequences/{id}` (the editor JSON: `sequence`, `features` with `locations`, `strand`, `notes`,
   `circular`, `organism`, `geneticCode`). `FigureEditor`'s `KINDS` gets `genome`. A stub kind that draws
   a ring and the name is enough to prove the plumbing: switch, settings saved, undo, export all four
   formats. pytest: figure settings for a sequence round-trip, survive duplicate / trash / restore, are
   refused for a list member.
2. **Circular gene ring**: classes and colours, arrows, parts, origin-crossing features, the strand
   sides, ticks, the centre text. Pure functions (`classOf`, `featureArcs`, `tickMarks`) so node can
   test them.
3. **Names**: placement without overlaps, leader lines, the left-half turn, the "N names not shown" note.
4. **GC ring**: `gcWindows(sequence, size)` as a plain function in `seqedit.js` (the user also wants GC
   content and GC skew as tracks in the viewer later, item 3 of the future list in `CLAUDE.md`: this
   function is what that will use, so put it where both can; test it in node). Mean line, size setting.
5. **Legend, regions, linear layout.**
6. **Panel polish**: the class table editor, per-feature overrides, presets for page widths, the hint
   text, defaults checked on real genomes.
7. **Docs** (below), then commit and push.

## Checks and tests

- **node** (`tests/test_genome_figure.js`, run by `tests/test_js.py`; node is at `%LOCALAPPDATA%\node\
  node.exe`): load `figure.js` in a `vm` sandbox the way `tests/test_graph_view.js` does (it needs a fake
  `document.createElement("canvas")` with `measureText`), draw a small genome, assert: no `NaN` in the SVG,
  one arrow per shown feature, the class colours, names present or counted as left out, an origin-crossing
  feature in two pieces, linear and circular both, `gcWindows` values on a known sequence.
- **pytest**: step 1's tests; the export of a real drawn SVG to PDF through `figures.export("pdf", svg)`
  must not raise and must keep the text (render the PDF page with `pypdfium2` as was done for the tree
  figure; svglib is the weak point: avoid `textPath`, `clip-path` on text, CSS in `<style>`).
- **headless browser** (Playwright in the scratch `tvenv`, the app run with the Cladia venv's python,
  `CLADIA_DATA` pointing at an empty scratch folder with `.cladia/upload` and `library` created; see how
  earlier scripts did it): `tests/data/Clarei_mt.gb` (a 15.8 kb mitochondrion, 8 CDS, rRNA, tRNA, introns)
  and a plastid (the user's `KU200463`/Probas annotated plastid is in `%USERPROFILE%\Cladia\library` on
  the user's PC; a synthetic 150 kb one from `tests/make_big_genome.py` also does). Take screenshots and
  **look at them**: names overlapping, arrows pointing the wrong way on the - strand, a feature across the
  origin, a CDS with an intron.
- The full suite must still pass: `python -m pytest tests` (213 at the time of writing).

## Things that will bite

- A minus-strand feature's arrow points to its lower coordinate; on a circle that is anticlockwise.
- Features across the origin of a circular sequence come as two `locations` (see `viewerFeatures` in
  `app-viewer.js`): draw one feature, not two.
- `partial` ends (`<`, `>`) are on the feature (`feature.partial {left, right}`): draw a flat, open end
  on the partial side instead of the point (a small thing, but it is the right thing).
- svglib: rotated text must be one `<text>` per run at a measured x (see `placedRuns`); check the PDF.
- 500 features with long names: names are the hard part; test at 300 genes (`make_big_genome.py`).
- The list in `FEATURE_COLORS` is not this feature's: don't change the viewer's colours.
- Windows scaling, WebView2: nothing special, it is an SVG in a `div`.

## Docs and housekeeping (part of done)

- `CLAUDE.md`, *Figures*: a paragraph on the genome figure (the same style as the tree and comparison
  ones: what it draws, the settings, the class table, the key of a feature's override, tested how). Update
  the *Future additions agreed on 2 Nov 2026* list (item 1 done), and the *Figures* heading text that says
  "of trees and of genome comparisons".
- `README.md`: a line in the figures part.
- Commit when a stretch is done and tested (a few finished steps, not every change) and push to `main`,
  saying so in a line to the user. Commit messages end with the co-author trailer given in the session.
- Ask the user before going far on a design choice that would be costly to redo (the class table, how
  names are placed, whether the legend sits inside the page) with a mock-up if it helps: the user judges
  by looking at screenshots and answers questions before you build. Don't be sycophantic; reproduce
  before fixing; they test on their own Windows PC and report bugs.

## Out of scope

- Several genomes on one figure (the comparison figure does that); lists.
- Editing the annotation from the figure.
- GC skew as a second ring (can follow the GC ring; the user didn't ask yet).
- Restriction sites, primers, cloning-vector styling.

## Completion and verification

Implemented in `figure-genome.js`, integrated with the existing figure editor,
sequence switch, per-item figure settings/history, and shared `SeqEdit.gcWindows`.
The JavaScript geometry tests and focused API tests pass; the full suite reports
256 passed. `scripts/test_genome_figure_ui.cjs` passed in headless Chrome on the
mitochondrial fixture and a 150 kb synthetic plastid with 301 features, exporting
SVG, PDF, PNG and TIFF in both circular and linear layouts without page errors.
The circular editor and PDF renders were visually checked with the real fixture.
