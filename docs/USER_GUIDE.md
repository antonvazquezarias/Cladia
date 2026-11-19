# Cladia user guide

[Overview](../README.md) · [Installation](INSTALLATION.md)

- [Help, backups and updates](#help-backups-and-updates)
- [Importing files](#importing-files)
- [Settings and databases](#settings-and-databases)
- [Library and file management](#library-and-file-management)
- [Figures](#figures)
- [Viewer controls](#viewer-controls)
- [Side panel](#side-panel)
- [Composition tracks](#composition-tracks)
- [Editing sequences and alignments](#editing-sequences-and-alignments)
- [Analysis tools](#analysis-tools)
- [Read mappings](#read-mappings)
- [Assembly graphs](#assembly-graphs)
- [Result folders and jobs](#result-folders-and-jobs)
- [Trees](#trees)
- [Sharing projects and recovering saved work](#sharing-projects-and-recovering-saved-work)

## Help, backups and updates

- **This guide** opens from the **?** beside the title (or Settings → General); it is
  the guide of the version installed. Most controls also explain themselves when the
  mouse rests on them.
- **Your library is only on this PC**, in `%USERPROFILE%\Cladia`. Trash and saved
  versions live in the same folder, so they protect against a mistake, not against a
  lost or broken disk. Now and then, and before anything risky, make a copy:
  **Settings → Storage → Export project ZIP…** and keep the ZIP somewhere else (a
  shared or cloud folder). Importing it later brings everything back as a new folder.
  Read files (FASTQ) are left out unless you tick *Include read files (FASTQ)*.
- **Updates**: when a newer installer is in the folder yours came from, Cladia says so
  at start; Settings → General shows your version and the new installer. Close Cladia,
  run it: the library and saved versions are kept.
- **Reporting a problem**: say what you did, what you expected, and the version
  (Settings → General). *Show the log files* there opens the folder with `cladia.log`:
  send that file. If a job failed, open its log from the Jobs list (or *Archive…*) and
  send its text too. A file that Cladia can't import or shows wrongly is the most
  useful thing to attach.
- **The first analysis takes longer**: Cladia prepares its own Linux for the tools
  (a download of a few hundred MB, once). Jobs show it as *preparing Cladia's Linux*.

## Importing files

Imported items keep their names even when an item with the same name exists
in another folder (or the same folder). Cladia gives its internal copies unique
filenames without adding those storage suffixes to the displayed names. Existing
items keep their current names; use Rename to change an older suffixed name.

- **Import…** (top of the sidebar) or drag files onto the window. **Download…**
  beside it fetches records from GenBank. Cladia copies them into its
  library and works only on the copies; your original files are never changed.
  Sequences are stored as GenBank, so annotations can always be saved.
  Importing several files at once asks what to do with them: sequences can go
  into one sequence list, as if they had come in one file; anything can go into
  a new folder (while all items are shown); or keep them as they are. The name
  starts as the files' folder name.
- **Sanger reads** (`.ab1` files, from any ABI sequencer): imported as sequences
  with their trace kept beside them, trimmed by quality on the way in (the bad
  start and end cut off: bases with an error probability over 0.05, as Geneious
  does; a read without qualities, as from old 310 machines, is kept whole). The
  description says what was kept, e.g. "bases 19-697 of 795". Opened, a read
  shows its trace (the four dye curves, each base's under its letter) and quality
  bars above the bases, zoomed in at its start; zoomed out, only the qualities.
  Fix a doubtful base by typing over it (Editable): the trace stays; adding or
  removing bases hides it, as it no longer lines up. Right-click a read, *Trim
  ends by quality…*, to trim it again with another limit (0 = the whole read;
  edits made since are lost). Reads stay sequences of their own: they can't go
  into a sequence list (it couldn't keep their traces), so several imported at
  once are offered a folder instead. Assemble a sample's reads with *Assemble*.

### Formats, annotations and proteins

- **Formats** are recognised by what's in the file, not its ending:
  - sequences: GenBank, EMBL (ENA; stored as GenBank), FASTA (`.fasta`, `.fa`,
    `.faa`, `.fna`, `.fas`, `.afa`, `.txt`…), and GFF3 with its sequences (a
    `##FASTA` section: new sequences with those annotations);
  - SnapGene (`.dna`): imported as a GenBank sequence with its features and
    topology (SnapGene's own enzyme sites and history are not kept);
  - Sanger reads (ABI `.ab1`): see *Sanger reads* above;
  - alignments (stored as aligned FASTA): Clustal (`.aln`), NEXUS, PHYLIP
    (sequential or interleaved, short or long names) and Stockholm (Pfam/Rfam,
    HMMER, Infernal; its structure lines are left out); NEXUS missing data (`?`)
    becomes a gap;
  - trees: Newick (`.nwk`, IQ-TREE's `.treefile`/`.contree`…) and NEXUS (MrBayes
    `.con.tre`, BEAST/TreeAnnotator, FigTree), with posteriors written in
    comments (`[&prob=0.97]`, `[&posterior=0.99]`) shown as the supports. A NEXUS
    file with an alignment and a tree becomes two items. A file of many trees
    (`.ufboot`, a MrBayes `.t` sample) is refused: make a consensus tree first;
    phyloXML (`.xml`, `.phyloxml`; confidence values become the supports);
  - BLAST+ tabular output (`-outfmt 6`, its 12 default columns): one BLAST results
    item per query. The file has no query or subject length, titles or organisms, so
    those columns stay empty (—); a subject's scores are its best hit's and the sum
    of its hits' bit scores;
  - any of these gzipped (`.gz`, as NCBI and ENA often send them);
  - **reads** (FASTQ, gzipped or not): see *Reads* below;
  - **BAM and SAM** files: see *Imported mappings (BAM)* under *Read mappings*;
  - **VCF**: a *Variants* item (see *Variants (VCF)* below);
  - **bedGraph and WIG**: data tracks on a sequence (see *Data tracks* below).
- **Annotations (GFF3, GTF, BED)** without sequences are added to sequences already in
  Cladia: drop the file on a sequence in the list (it lights up while you drag;
  the file's only sequence, or the one named like it, goes onto it), or
  right-click a sequence > *Import annotations (GFF3)…*. Dropped anywhere else,
  each sequence named in the file (first column) gets its features if a sequence
  in Cladia has that name or accession (with or without the version, e.g.
  `NC_001677.1`). Features already there (same type and place) aren't added
  twice. Lines sharing an ID become one feature in parts (a CDS over exons);
  Name/gene become `/gene` (`/label` for other types), product, locus_tag and
  Note are kept, a CDS without a name takes its gene's (through Parent), the
  phase becomes `/codon_start`, and parts past the end of a circular sequence
  cross its origin. Save or undo changes to the open sequence first. **BED** files
  work the same way: each line is a `misc_feature` named by its fourth column, on
  the strand in its sixth (BED starts at 0; Cladia shows 1-based positions), and the
  blocks of a BED12 line are the parts of one feature.
- **Data tracks (bedGraph, WIG)**: a number along a sequence from another program
  (coverage, methylation, expression, any score). Drop the file on the sequence, or
  right-click it > *Import annotations or a data track…*. The file's only chromosome,
  or the one named like the sequence, is used; values outside the sequence are left
  out, and more than 200,000 intervals are averaged into 200,000. The track is drawn
  under the ruler like GC content (bars from zero, a ring on the circular map), with
  its name, file and range at the left; hover gives the value at a position, and the
  names column the visible stretch's mean and highest. *Show ▾ › Data tracks* ticks
  each track on or off and removes one. A sequence may have several; they are kept
  with it (Duplicate, Trash and project ZIPs carry them), not as items of their own.
  Only sequences of their own take tracks (not a list's sequences).
- **Variants (VCF)**: a VCF becomes a *Variants* item (a section of the list), linked
  to the library sequence it was called against: the one named like the VCF's
  `CHROM` whose bases agree with the file's REF alleles (the first 500 sites are
  checked; more than 5 % disagreeing, or a `##contig` length that differs, means
  it isn't that sequence). Import the reference first. Opening the item shows the
  reference (read only) with each site as an orange *variation* mark, and the side
  panel's **Variants** tab: the sites as a list (filters by type, missing calls,
  samples) and the sample × site matrix, as for an alignment's variants. Click a site
  to go to it. Sites-only VCFs (no samples) work too. A VCF covering several
  sequences shows the one with most sites, and says so. Without a match the item is
  still kept, with the reason in its title. A sequence may have several VCFs (other
  callers or samples). Export gives the file as imported.
- **Reads (FASTQ)**: FASTQ files (`.fastq`, `.fq`, gzipped or not) become *Reads*
  items, one per sample: `_R1`/`_R2` files of one drop are paired as Map reads pairs
  them, other files are a sample each. The files are copied into the library as they
  came (still gzipped), with their size and SHA-256, and read once: a job (*Import
  reads …*), so the item appears when it's done. The item opens a report: the files
  and their read and base counts, the read length distribution, mean quality along
  the reads (one line per file, over green/yellow/red zones), base composition along
  the reads, the mean quality of each read, GC per read, and the first reads. Counts
  and lengths are from every read; the per-position charts from the first 200,000
  reads of each file, up to position 1,000. Right-click > *Delete the read files (keep
  the report)…* frees the space (not to the Trash). Read files take no saved
  versions; project ZIPs leave them out unless *Include read files* is ticked; reads
  items aren't duplicated. Tools that take reads (Map reads, GetOrganelle's *Organelle from reads*) offer **From the library** (reads items: each is one sample, its files already paired) or **From disk** (file paths, paired by name, read where they are); the last choice is remembered.
- **Encodings**: UTF-8 (with or without a BOM), UTF-16 ("Unicode" in old Notepad)
  and older Windows files (Windows-1252) are all read correctly, so accents in
  notes or author names come through; Cladia saves and exports UTF-8.
- **Proteins** (amino acid FASTA, protein GenBank) are recognised by their letters
  and stored as protein. They're shown in Clustal colours (hydrophobic blue,
  positive red, negative magenta, polar green, C pink, G orange, P yellow,
  aromatic cyan), linear only, without a complementary strand, translations or
  genetic code; Find looks for the motif as written; Editable accepts amino acid
  letters. Protein alignments work like DNA ones (MAFFT, trees with protein
  models, gap editing). Annotation and Map reads take DNA only. The side panel's
  **Properties** tab (shown while a protein is open) gives its molecular weight,
  isoelectric point, charge at pH 7, GRAVY, aromaticity, instability index,
  extinction coefficient and helix/turn/sheet fractions (Biopython's ProteinAnalysis),
  the amino acid composition and a Kyte-Doolittle hydrophobicity plot (window 5 to
  21). It follows the sequence as shown, edits included. Letters other than the 20
  standard amino acids (X, B, Z…) are counted and left out; gaps and stops are ignored.

## Settings and databases

- **Settings** (⚙ beside *Cladia*), in tabs: *General* (how many cores computing
  jobs may use; *Text size*, 90-175 %: everything in Cladia's windows larger, as a
  browser's zoom), *NCBI* (your e-mail and API key; the key makes GenBank downloads
  and searches faster, not BLAST), *BLAST databases*, *Submitters* (see below) and
  *Storage* (what Cladia's files take, with buttons to delete old job logs and old tool
  images; the trash, to restore or delete what you deleted).
  *Tools* shows a compact grid of tool names, grouped by type. **Manage…** opens details, settings,
  measured footprints or installation estimates, and the actions (install, update or uninstall an
  optional tool). Shared dependencies and local databases are identified separately;
  online and in-app tools need no separate tool download.
  Click outside a dialog to cancel and close it. Settings stays anchored below
  the toolbar when switching tabs. Its height follows the active panel and grows
  with its content, up to the available window height; only overflowing panels
  scroll. This also adapts while resizing the window. In Storage,
  the Trash list scrolls independently, so long lists do not expand the dialog.
- **Submitters** (Settings), for GenBank submissions: one entry per collaboration, with
  the authors (one per line, "Last, First"), the contact, the affiliation, the paper's
  title and an optional release date. They make NCBI's submission template (.sbt).
  *Import .sbt…* reads one you already have (dropping a .sbt file into Cladia does the same);
  *Export .sbt…* writes one. In the side panel's *Checks* tab, *Export for GenBank…* and
  *Validate with NCBI* ask which submitter to use, with this submission's own authors,
  title and release date filled in from it (change them there if they differ from the
  preset's), and write the `.sbt` beside the `.tbl` and `.fsa`; "No template" leaves it out.
- **BLAST databases** (Settings), for BLAST on this PC (the BLAST tool lists them
  under *Local*). *Add one you have*: pick any file of a BLAST database (.nal, .nsq,
  .pal, .psq…); it is used where it is, not copied. A FASTA file is made into a
  database instead. *Download from GenBank*: type a GenBank search (NCBI's own
  syntax, e.g. `Rhodophyta[organism] AND mitochondrion[filter]`); *Estimate size
  and time* says how many records it finds, how big the database would be and
  how long downloading and making it would take (from a sample of the records:
  within ~10 % on the size); *Download* fetches every record as FASTA and makes
  the database (a job, in Jobs). Cladia's own databases are kept in
  `%USERPROFILE%\Cladia\Blast databases`, one folder each; *Delete* removes
  them, *Remove* only takes one you added off the list.
## Library and file management

- **Folders** can contain other folders. The 📁 button above the search box
  shows your current path (*Mito › Red*); the sidebar lists that folder's own
  items. Click the button to browse the tree. Arrows expand branches, and the
  search field finds folders by name or path when there are many. Counts show
  items directly inside; hover a count for the number in subfolders.
- **New folder…** creates a folder inside the current one and opens it. Right-click
  a folder for *New folder inside…*, *Rename…*, *Move to…* or *Delete folder…*.
  Moving or renaming your current folder keeps you in it; expanded branches
  follow the change. Your location and expansion choices survive app restarts.
- **Search all folders** searches the entire library. The folder button says
  *Search · all folders* while searching. Choosing a folder clears the query and
  shows that folder. Browsing clears the sidebar selection and range anchor;
  the open viewer, its side panel and any unsaved edits stay open. Tools and
  library actions use the visible sidebar selection or the inputs shown in
  their dialog, rather than an invisible selection from another folder.
- **Keyboard navigation:** open the folder picker with its button. From folder
  search, Down focuses the first match and Enter opens it. In the tree, Up/Down
  move between rows, Right/Left expand or collapse branches or move to a child
  or parent, Home/End jump to the first/last row, and Enter opens a folder.
  F2 renames the focused folder; Delete asks to remove it. Escape closes the picker.
- **Move items** by dragging them onto the 📁 button, then onto a folder in the
  tree. Hover a closed branch briefly to expand it. Drop on *New folder…* to
  create a destination and open it after the move. Moving to an existing folder
  keeps the source folder open. Right-click an item → *Move to folder…* offers
  the same destinations. A list moves with its sequences; moving an individual
  member extracts it as a standalone sequence. Save or undo edits before
  extracting members. Folder names that changed in another window must be
  selected again if the previous destination no longer exists.
- **Delete folder…** removes only the folder: its items and subfolders move up
  one level. It keeps you in a surviving descendant when possible. If this would
  merge same-named child folders, removal is blocked with an explanation; move
  or rename the conflicting child first. No sequence items are deleted.
- Files imported while a folder is shown go into it. Copies stay beside their
  sources; combined results use the shared input folder or an explicit
  destination (see [Result folders and jobs](#result-folders-and-jobs)). Hovering
  an item found in another folder shows its location.
- A file with several sequences becomes one **sequence list** (as in Geneious): one
  entry with a count, e.g. `reads (12)`. The arrow shows its sequences; clicking the
  list shows them all together; clicking one opens it. **Split into sequences**
  (right-click) turns a list into separate sequences; **Group into a list**
  (right-click several sequences or lists) moves them into one new list; dragging
  sequences (or a list, or a list's sequences) onto a list moves them into it, at
  its end; dragging a list's sequences out of it (anywhere in the list but on a
  list), or onto a folder, makes each a sequence of its own. A list
  or an alignment can be **sorted** by name, length (an alignment's: bases, not
  gaps), organism or description with **Sort ▾** in the viewer's bar while it is
  shown. The current order is ticked with its direction; choosing it again
  reverses it (A to Z / Z to A, longest / shortest first). The order is saved, so
  tools and Export follow it. In an alignment, select one row and choose
  **Distance to selected sequence** to put the closest sequences first (choose
  it again for farthest first). Distance is the proportion of mismatches at
  aligned positions with a known base or amino acid in both sequences; gaps
  and ambiguous sites are ignored. Rows with no comparable positions go last.
- **Group into a list** also works with alignments, trees, and other library
  items. A mixed list shows the same breakdown by type as selecting several
  items. Expand it to open each item in its usual viewer. Dropping another
  kind of item onto a sequence list puts both into a mixed list. Use **Take
  out of list** on a member to move it out, or **Ungroup items** to remove the
  list and return its members to their previous folders.
- **Right-click** an item in the list for what can be done with the selection:
  *Open in new window*, *Rename*, *Metadata* (one item), *Duplicate*,
  *Group into a list* (several),
  *Split into sequences* (lists), *Remove annotations…* (sequences or lists: every
  feature taken off, saved at once),
  *Import metadata from a table…* (sequences or lists: a CSV, TSV or Excel table with one
  row per sequence; choose the column with the names and the column of each field: country,
  collection date, host, voucher, coordinates and so on; a preview says what matches and what would
  change before anything is written),
  *Import annotations (GFF3)…* (one sequence), *Export…*, *Paste as new sequence*
  and *Delete*. Results of tools are marked **new** (bold, with a badge) until you
  open them, also after closing Cladia. Right-clicking empty space
  offers *Paste* and *Import…*. Keys in the list: F2 renames, Ctrl+D
  duplicates, Delete deletes, Ctrl+V pastes.
- **Paste as new sequence** (or Ctrl+V in the list): select part of a sequence in
  the viewer and copy it (Ctrl+C or Copy), then paste in the list. It becomes a new
  sequence named e.g. `Mt 501-1700`, with the features that lie fully inside the
  region and the source's organism and organelle. Several sequences selected
  (rows of a list, also Ctrl+clicked ones) become a new sequence each, with their
  features. A block copied from an alignment
  becomes a new alignment. Text copied elsewhere is imported too: bases become
  `Pasted_sequence`, FASTA becomes a sequence or a list.
- **Duplicate** makes a separate copy, named `<name> copy` (a sequence of a list
  becomes a sequence of its own).
- **Export…** writes the selected items to a new file, GenBank, FASTA, GFF3
  (annotations only) or GFF3 + FASTA (annotations and sequences in one `.gff3`, with
  a `##FASTA` section), where you choose. An alignment: FASTA, NEXUS or PHYLIP
  (relaxed: long names, spaces become `_`); trees: Newick or NEXUS. Several items go into one file. This is the only
  way anything leaves Cladia. *CDS proteins* writes the protein of every CDS as
  FASTA, translated with the genetic code shown for each sequence.
- **The original file**: an imported item remembers the file it was copied from
  (its hover card says where). Right-click it: *Show original in Explorer*, or
  *Export to original location…*, which replaces that file with Cladia's version,
  in the file's own format, after asking (a FASTA file keeps no annotations).
  Not for a list's single sequence, nor for EMBL, Stockholm, Clustal or .gz
  originals (use Export).
- Saving updates the stored `/translation` of every CDS whose DNA you changed;
  CDSs you didn't touch keep theirs as they were.
- **New window** opens the selection (a sequence, a list, several sequences or an
  alignment) in a second window, to see two things side by side. It edits like
  the main window. One window edits an item at a time: while it is Editable in
  one window (or has unsaved changes there), another window showing it can't be
  switched to Editable, nor rename, sort, annotate or delete it; when it's saved,
  the other windows show the change at once. Closing the main window closes the
  others; if any window has unsaved changes, Cladia asks first (and so does
  closing that window).
- **Rename** (or F2, or double-click an item) gives an item a new name;
  Enter keeps it, Esc cancels. A sequence's record is renamed too (its LOCUS name;
  spaces become `_`), so the new name is what Export writes. Lists, alignments and
  trees only change their name in the list.
- **Metadata** (in the viewer toolbar, or right-click > Metadata) shows
  its details and recorded provenance. For a sequence, switch to **Editable** to change
  the record's header: description (DEFINITION), organism/species (ORGANISM and
  the source's `/organism`), accession (ACCESSION; with or without version) and
  the source's strain, isolate, location, collection date, host and organelle
  (cleared by emptying them; a record without a
  source feature gets one). Written into the file, so the list, the viewer's title
  and Export follow. Refused while the item has unsaved edits or another window
  is editing it. The same Save button saves pending sequence and metadata edits
  together; the toolbar's undo and redo follow the active view. Taxonomy, other source qualifiers and references are shown as
  recorded. The name is still changed with Rename.
- **Descriptions** (the button next to the filter, remembered): each sequence's
  description under its name, small and grey. A FASTA header's `[Organism]` goes
  to the organism, not the description.
- **The list's width**: drag the sidebar's right edge (remembered; double-click it
  for the usual width). Long names end in "…"; a list's count and the *new* badge
  stay visible.
- The list shows only the sections that have something (Sequences, Alignments,
  Mappings, Trees, BLAST results, Primer designs, In-silico PCR).
- **Hovering an item** in the list shows a card: for a sequence its organism,
  description, length, topology, features and accession; for a list or alignment
  how many sequences, their lengths and organisms; for a tree its tips; for a mapping
  its numbers. The **Filter** box also finds sequences by organism and description,
  and alignments by the names, organisms and descriptions of their rows.
- **Show ▾ › Names** (bar above the viewer) chooses what the viewer's names column shows:
  any of name, organism and description, joined by " | " (also in the identity
  matrix); it only offers what the sequences shown have (a FASTA header with only
  a name has no description). Drag the column's edge to
  make it wider or narrower; longer labels end in "…", and hovering the names opens
  the column over the sequences to show them in full. Both are remembered.
  Organism and description come from the GenBank header (ORGANISM, DEFINITION) or a
  FASTA header ("name description [Organism]"); alignment rows made by MAFFT take
  them from the sequence they came from.
- **Delete** (or the Delete key in the list) moves Cladia's copy to the **trash**,
  after asking. The files you imported from are not affected. *Settings > Storage*
  lists the trash (what, where it was, when, its size): tick items to *Restore*
  them (back in the folder they were in; sequences deleted from a list come back
  beside it, as a sequence or a list of them) or *Delete for good*; *Empty the
  trash* deletes everything in it.
## Figures

- **Figures for papers** are items of their own, in the list's *Figures* section,
  each drawing a tree, a genome comparison or a nucleotide sequence (a list's too).
  Make one with **Figure ▾** at the right of the viewer bar (*New figure*); the
  same menu lists the figures already made of that item. *New figure of the
  selection* makes one of part of it: a tree's selected clade (or the clade
  spanning the selected tips), a selected stretch of a genome (a linear map of
  it), or the selected genomes of a comparison. A figure is drawn from
  what it shows each time, so later edits appear in it; **◀ <name>** at the start
  of its bar goes back. Deleting an item asks whether to delete its figures too;
  kept, they say their source is missing until it is restored. Figures made before
  November 2026 were converted into figure items, with their saved versions.
  The figure editor shows the page as it will be exported, and
  settings beside it: page width (84 mm for one
  journal column, 174 mm for two), height, font, font size and line width in
  points; tip names (name, organism, organism and name, or description, from
  your sequences of that name; in one column); italics for species names as the
  rules have them (*Gracilaria* sp. 5, *Gracilaria* cf. *tenuistipitata*,
  *Mastocarpus papillatus* var. *cristatus*, strain codes upright), all, or
  none; in a text you type, `*...*` is italic and `**...**` bold; phylogram or
  cladogram, ladderizing; support values or filled circles from a threshold
  and up to a maximum (e.g. up to 99 leaves out the 100s of a well supported
  tree); a scale bar. *Layout*: rectangular, mirrored (the root at the right,
  names at the left) or circular (names pointing outwards, an opening at the
  right; clade backgrounds, bars and collapsed clades follow the circle). Click a branch to set its clade: branch colour, background
  colour, a clade label with a bar (bold, normal or italic; or a bar alone),
  collapse it into a triangle, swap its sides. Click a name to change that
  tip's text, italics or colour; Shift+click another name to select the tips
  between and *Add a bar for these tips* (a group that isn't one clade). Bars
  inside others go in their own columns, nearer the tips. Undo/Redo; the
  figure is saved as you go and reopens as you left it (the tree itself isn't
  changed; for a second version, make another figure or duplicate this one). **Export**: SVG (to finish in Inkscape or Illustrator), PDF (vector),
  PNG or TIFF at 300, 600 or 1200 dpi.
  A **genome comparison's** figure: each genome a line with its features as arrows, at one scale, names
  at the left; bands between neighbouring genomes for the blocks progressiveMauve
  found, or for genes of the same name (ND5 and nad5, CYTB and cob count as the
  same), or none. Choose which feature types show and their colours, gene names
  (slanted when crowded), arrow height, spacing, a scale bar. Hide features
  shorter than a length, or leave names off short ones (e.g. from 300 bp: no
  tRNA names). The *Genes* list (with a filter) has every gene in the figure
  once, with a tick for showing it and one for its name, in every genome at
  once (ND5 and nad5 are one gene); a gene ticked for its name gets it even
  when the length or type rules would leave it out. Click a genome's
  name to flip it (drawn reverse complemented), move it up or down, hide or
  rename it; click a feature to recolour or rename it, or hide its gene (or its
  gene's name) in every genome; click a band to recolour its block.
  A **single genome's** figure (Figure ▾ on a nucleotide sequence) draws a circular or linear map with
  strand arrows, names, a kb scale and an optional GC-content track; the circular
  map can also show a GC ring. Organellar genes are coloured by editable function
  classes, with per-feature overrides, optional regions (including origin-crossing
  regions), and an editable centre label and legend. Linear maps can focus on a
  coordinate range.
  Circular names default to an upright, compact layout; choose Radial for rotated
  names or Upright · all names for a taller, two-column map. Compact layouts report
  names omitted to avoid collisions. Linear names stay near their feature lanes.
  Circular connectors stay near their feature's angle and start on the correct
  inner/outer track. **Fit** shows the whole page, including the legend; use −/+
  to zoom out/in further.
  GC tracks use the genome mean as their baseline and state their scale/direction.
  Settings are saved in the figure without changing the sequence. The figure exports
  as SVG, vector PDF, PNG or TIFF at the selected resolution.
## Viewer controls

- **Tool dialogs** with more than one method show them as tabs under the title,
  each with a line on what it does and when to use it: Annotate (De novo, From a
  reference, Find ORFs), Build tree (IQ-TREE, FastTree), Download (Accessions, Search). The
  tab you used last opens next time.
- **The bars at the top**: the tinted tool bar (Annotate, Align, Build tree, Map
  reads, Assemble, Variants, Primers, BLAST: jobs that update sequences or create
  results; it says how many are running; a dimmed button still opens, its tooltip
  says what the selection lacks). *Download* is in the sidebar beside *Import*.
  Below it, the viewer's own bar: the title in two lines (name and numbers; then
  *organism* · description, or what kind of item it is), and the controls in groups,
  always in this order: **Zoom** (−, +, fit all, fit the selection), **Edit** (the
  lock: read only / editable; Save; ↶ ↷; **Curate ▾** for changes to the whole
  item), **Find**, and **View** (Circular | Linear; *Show ▾*: features,
  translations, complementary strand, a tick for each feature type with its colour
  and count, the names column's fields, composition and data tracks, the genetic
  code). **Figure ▾** at the right makes or opens figures of what is shown. Groups
  that don't apply aren't shown; items that can't be edited (mappings, comparisons)
  have no Edit group.
  There is no Copy button: **Ctrl+C** or right-click. *Extract*, *Save consensus* and
  the other actions on a selection are in the viewer's right-click menu.
- **Cladia's viewer** shows whatever you click, in the style of Geneious, with the
  same mouse and tools everywhere:
  - **One sequence:** a ruler, the bases in colour with the complementary strand
    underneath when zoomed in, CDS translations, and the features in lanes with
    their names (mRNAs are hidden, as in Geneious). **Circular / Linear** switches
    the map (a circular sequence opens circular); the selection stays. On the
    circle, Ctrl+wheel zooms in (the selection, or else the cursor, turns to the top with a brief animation)
    until the bases and translations show along it, and the wheel rotates it. On a
    circular sequence a drag can go across the origin (e.g. 15,500–15,789 + 1–300):
    Copy, *New feature* (a `join`), pasting it into the list as a new sequence,
    typing over it and deleting it work on it (typed bases go before the origin). A linear sequence can be shown as a circle too: its ends are marked
    with a red break and the centre says "linear sequence".
  - **Right-click on the viewer**: Copy, *New feature from the selection*, and (in
    Editable mode, undoable) *Reverse complement the selection* / *the whole
    sequence* (features inside it turn round with it), *Make position N the origin* (the cursor or the
    selection's start becomes position 1; features across the new origin become
    joins), *Mark as a circular / linear sequence* (saved in the GenBank file) and
    *Genetic code of the sequence…*: saved as `/transl_table` on every CDS (as GenBank
    stores it), so it goes with the file; the CDSs' `/translation` are redone in that
    code when you save. The genetic code under **Show ▾** only changes what is shown.
  - **Several sequences** (Ctrl+click or Shift+click, or a sequence list): one row
    each, with their features. They aren't aligned, so differences aren't marked.
    Up to 30 Mb of sequence or 1,000 sequences show at once (about 1,000
    mitochondria or 200 plastids, a few seconds to open); for a bigger list the
    title says how many of how many are shown.
  - **An alignment:** the consensus and an identity bar on top (green where all
    sequences agree, olive where they don't, red under 30 %), each sequence in grey
    with only the bases that differ from the consensus coloured (A red, C blue, G
    yellow, T green). Gaps at the ends of a sequence are left blank and don't count
    against identity. Double-click a name to open that sequence.
  - **Mouse:** Ctrl+wheel zooms to centre the selection or cursor with a brief sliding animation
    (following the mouse when neither is set, or when the selection no longer fits), the wheel
    moves along; with more rows than fit, the wheel over the names (or Shift+wheel,
    or the scrollbar on the right) scrolls up and down. Drag to select (over the ruler: whole
    columns; over the names: whole rows; over an alignment's consensus, or a click on
    "Consensus": the consensus alone, which Copy copies as a sequence without its
    gaps); Shift extends, Ctrl+click on a name adds or removes that row (rows
    apart from each other: Copy, BLAST and the identity matrix take just those;
    Extract and gap editing need a block of rows). In an alignment, copying several
    whole rows and pasting them into the library creates a new alignment; columns
    that are gaps in every copied row are trimmed at the ends. Right-click the
    selected names and choose *Save selected rows as sequences* to create one list
    of ungapped sequences. Copying a selected block keeps its alignment gaps. Esc clears; click a feature
    to select it: a feature in parts (a CDS's exons) selects just its parts, so Copy
    and BLAST take their bases joined, and pasting it in the list gives them as a
    sequence (typing or deleting needs one stretch: drag). The line under the rows gives the selection in each sequence's own
    positions; the overview at the bottom shows the whole length (click or drag it
    to jump).
  - **Find** (Ctrl+F) looks for a motif (gaps ignored, both strands), a sequence name
    (including accessions) or a feature by name. Names match without regard to case;
    Enter or ▶ for the next match, Shift+Enter or ◀ for the previous. Ctrl+F goes
    to the search box of what is shown (also a tree's, the BLAST filter); from the
    list, to the list's filter.
  - **Copy** (or Ctrl+C) copies the selection: the bases alone (no name, no gaps)
    of one sequence, one row of a list or alignment, or the consensus; FASTA for
    several rows. **Extract** (alignments) makes the selected block a new
    alignment in the list.
  - **Identity matrix** (alignments: the side panel's *Identity* tab, beside the alignment): the
    pairwise identity of every two sequences, as Geneious counts it (a base against
    a gap is a difference, gap against gap isn't compared, gaps at a sequence's ends
    don't count), coloured from red (least alike) to green. *Differences* shows the
    number of differing positions instead; hovering a cell gives both and the
    positions compared. A selection in the alignment limits the matrix to its
    columns and rows (e.g. one gene). *Copy* pastes into Excel; *Export CSV…*.
  - **Dot plot** (side panel tab, for DNA shown in the sequence viewer: a sequence,
    a list's sequences, an alignment's rows without their gaps, a comparison's
    genomes): a dot wherever the two sequences share a word of *Word* bases (Auto:
    8 for short sequences up to 14 for genomes), blue on the same strand, red as
    the reverse complement. A diagonal is the two running alike; breaks are
    insertions and deletions, shifted pieces rearrangements or another origin, red
    lines inversions, parallel lines repeats. One sequence against itself (A = B)
    shows its repeats; a plastid's inverted repeats are a red line across.
    Choose the two in *A* and *B* (a selection of two rows picks them). Hover for
    both positions; click to show that place of A in the viewer. It follows edits.
    Up to 2 Mb per side; very repetitive words (poly-A runs) are thinned. ⤢ gives it
    the work area. **New figure** makes a dot plot figure item of the same two
    sequences (SVG, PDF, PNG, TIFF; page width, word size, resolution, colours, axis
    names and ticks in its panel).
  - **Stop codons inside a CDS** (in the genetic code shown) are marked as in
    Geneious: the CDS is lighter from the stop to its 3' end, with a dark red mark at
    the stop; hovering the CDS says where it is.
  - **Features** and **Translations** switch those on and off. Each CDS shows its
    own translation under its arrow when zoomed in, so overlapping CDSs don't mix;
    **Complement** switches the complementary strand; each feature type can be
    hidden or shown (mRNAs start hidden, as in Geneious; the choice is kept). In an
    alignment they come from the library sequence each row was aligned from (MAFFT
    remembers it, also for reversed `_R_` rows; for an imported alignment, a
    sequence with the same name); a row whose sequence changed after aligning shows
    none.
  - **Genetic code** (under **Show ▾**, one sequence) is the code used for translations. A sequence
    opens with the code from its file: a CDS `/transl_table`, otherwise `/organelle`
    (mitochondrion 4, plastid 11), otherwise 1. Picking another one is remembered.
## Side panel

The tabs at the right edge of the work area open a panel beside the viewer (click
the open tab again to close it; drag its left edge; ⤢ gives it the whole area).
Only the tabs that apply to what is shown appear.

- **Annotations**: the features as a table (of every sequence of a list, an
  alignment's rows or a comparison's genomes too). Click a row to select the feature
  in the viewer, and the other way round; Ctrl+click and Shift+click select several.
  Filter by text or type, sort by a header, choose columns in *Columns ▾*. When the
  sequence is editable, double-click a cell to change the name, type, start, end,
  strand, gene, product, note or location; *Set…* changes a field on all selected
  rows, *Delete* removes them, **+ Feature** adds one from the viewer's selection.
  These are ordinary edits: Ctrl+Z undoes them and Save saves them. *Copy* and
  *Export CSV* give the table as shown.
- **Metadata** (one item selected): description, organism, accession, strain and the
  other source fields, saved with Save; where a result came from (job, inputs, log);
  **View saved versions**; a mapping's **Summary**.
- **Checks** (editable sequences): what GenBank would reject or question (CDS frame,
  start, stop and internal stops, missing products, genes without a name, features
  outside the sequence, qualifiers that aren't GenBank's…). Click a problem to go to
  its feature; rows with errors are red in the Annotations table. *Export for
  GenBank…* writes the `.tbl`, `.fsa` and, with a submitter, the `.sbt`; *Validate
  with NCBI* runs NCBI's table2asn on them as a job and shows its messages.
- **Identity** and **Variants** (alignments), **Dot plot** (DNA), **Properties**
  (one protein): described under *Viewer controls* and *Analysis tools*.

## Composition tracks

GC content and GC skew are optional **Show** tracks for a single DNA sequence,
with linear graphs and circular rings. Choose Auto or a window size, and per-window
or cumulative skew, and Compact or Large tracks; choices persist. Composition has
its own section in Show, separate from annotation types and translations. GC bars
extend above/below the genome mean; positive skew means more G than C, negative
means more C than G. Circular rings use outward/inward for the same directions.
Cumulative skew is a curve of running (G−C) / genome length.
Hover a track for its value and coordinates,
or its name for whole-sequence and visible-region GC. Ambiguous bases are excluded.
GC content is also shown in sequence hover cards and Metadata. Alignments, multiple
sequence rows, mappings, graph tracks and proteins do not offer these tracks.

## Editing sequences and alignments

- **Read only / Editable**: everything opens read only;
  switch to **Editable** in the bar to make changes. Showing something else locks
  it again. Unsaved changes can still be saved after locking. Double-clicking a
  feature while read only shows its details without changing anything.
- **Editing several sequences** (a list or several selected, Editable): each row
  edits as one sequence does; the row with the cursor, or the one a selection is
  in, is the one edited (the right-click menu says which). Undo and redo go back
  through the last edits whichever row they were in; **Save** saves every changed
  sequence (the title says how many are changed).
- **Editing** (one sequence, in the viewer): click on the bases to place the cursor
  and type (A, C, G, T and IUPAC codes); typing over a selection replaces it.
  Backspace/Delete remove bases, Ctrl+X/C/V cut, copy and paste (a FASTA header in
  the pasted text is ignored), the arrow keys move the cursor, Ctrl+Z/Ctrl+Y undo
  and redo, **Ctrl+S** or **Save** saves (an asterisk in the title means unsaved
  changes; leaving the sequence asks first). Features follow the edits: bases
  inserted inside a feature lengthen it, before it move it; deleting part of a
  feature shortens it, all of it removes it. Double-click a feature to change its
  name, type, location (GenBank style: `100..200`, `complement(...)`, `join(...)`)
  and qualifiers, or delete it; **+ Feature** (under the side panel's Annotations
  table) makes one from the selection.
  Right-click: *Delete the features in the selection* (those wholly inside it) or
  *Delete all features* (undoable, saved with Save). A CDS
  whose DNA changed gets a new `/translation` when saved.
- **Gap editing** (an alignment, Editable): click in a row to place the cursor (the
  arrow keys move it, also up and down), or select a block. **Space** or **-**
  pushes the block (or the bases after the cursor, up to the next gap) one column
  right; **Backspace** pulls it left into the gap before it; **Delete** removes the
  gap after the cursor, or moves the gaps inside a selection to its end. Bases never
  change, only gaps; columns left with only gaps disappear. Select one or more
  names and press **Delete** to remove those whole rows from the alignment; at
  least one row must remain. The source library sequences are not changed.
  Ctrl+Z/Ctrl+Y and **Ctrl+S** as for sequences, including restoring removed rows.
- A file with one sequence is named after the sequence (its LOCUS name or FASTA
  header); a list or alignment after the file.
- **Zoom** (Ctrl+wheel) centres the selected region or clicked cursor through a
  quick 170 ms transition. Circular maps take the shortest turn, including across
  the origin; linear maps slide. Search, annotation-table and overview jumps use
  the same transition. Further input retargets it immediately; manual scrolling
  and dragging stay direct. With no selection/cursor, or once a selection no longer
  fits, zoom keeps the mouse position anchored. Reduced-motion settings skip the animation.
## Analysis tools

### Optional tools

Some tools are optional: their code comes with Cladia, but the tool itself is
downloaded only when you install it in **Settings → Tools** (from bioconda, at an
exact version, every package checked by its checksum). Once installed it appears
in its tool dialog; until then it isn't offered. Installing is a job (see Jobs);
it needs an internet connection that once. *Manage…* on an installed tool offers
**Uninstall** (its downloaded files go; results made with it stay). When a newer
Cladia comes with a different version of a tool, its card says **Update…**: the
tool isn't used until it is updated. The Windows installer can also queue them
for the first start.

| Tool | What it adds | Downloaded |
| --- | --- | --- |
| Clustal Omega | An alternative aligner for DNA, RNA and proteins | clustalo 1.2.4, about 35 MB |
| GetOrganelle | Organelle assembly from suitable Illumina reads; the seed/label databases you choose | GetOrganelle 1.7.7.1 with SPAdes, Bowtie2 and BLAST+, about 1.4 GB, plus databases |
| Krait repeat finder | Perfect, compound, imperfect and generic tandem-repeat searches in DNA | pytrf 1.5.0 and pyfastx 2.3.1, about 170 MB |
| MUSCLE | Aligner for DNA and proteins (Align › New alignment) | MUSCLE 5.3, about 31 MB |
| MACSE | Codon-aware alignment of coding DNA, allowing frameshifts and stops; can also save the protein alignment | MACSE 2.07 with Java, about 520 MB |
| RAxML-NG | Maximum-likelihood trees with bootstrap supports (Build tree) | RAxML-NG 2.0.4, about 160 MB |
| MrBayes | Bayesian trees with posterior probabilities (Build tree); runs can take hours | MrBayes 3.2.9, about 560 MB |
| tRNAscan-SE | Finds tRNA genes (Annotate › tRNAs), with organellar, bacterial or general models | tRNAscan-SE 2.0.13 with Infernal, about 110 MB |
| ARAGORN | Finds tRNA and tmRNA genes (Annotate › tRNAs), including tRNAs with introns and across a circular origin | ARAGORN 1.2.41, about 6 MB |
| trimAl | Trims gappy or poorly aligned columns (Align › Trim columns) | trimAl 1.5.1, about 30 MB |
| ClipKIT | Trims columns but keeps the informative ones (Align › Trim columns) | ClipKIT 2.14.0, about 290 MB |

**Neighbour-joining / UPGMA** (Build tree) is built in and needs nothing
downloaded: Kimura, Jukes-Cantor, p or Poisson distances, bootstrap supports and an
optional outgroup (neighbour-joining).

**Removing tips from a tree**: select tips, then *Curate ▾ › Remove the selected
tips* (or right-click). It is saved like rerooting, so ↶ / Ctrl+Z undoes it; a tree
keeps at least three tips. **Removing sequences from an alignment**: switch to
Editable, select whole rows by their names and press Delete.

**Alignment variants** (SNP and small-indel detection against a reference row of
a nucleotide alignment) is built in: it needs nothing downloaded.

Alignment variants attaches a haploid VCF to its alignment. Use the **Variants**
side tab and **Variant matrix** view to inspect, filter and export calls.
Alignment edits make attached calls stale until detection is rerun. Terminal
gaps and ambiguous bases are missing calls rather than inferred variants.

Krait saves repeat loci as `repeat_region` features, either on the selected
sequence with saved history or as a copy. It does not genotype samples or scan
repeats crossing the circular origin. Its four searches: perfect SSRs (pytrf
`findstr`, a minimum number of repeats per motif length), compound SSRs (perfect
ones grouped when closer than the maximum gap; components in the feature's notes),
imperfect SSRs (`findatr`; identity and edits in the notes) and generic tandem
repeats (`findgtr`, longer motifs). Replacing results removes only earlier
features of the same search. pytrf: Du et al. (2025), *BMC Bioinformatics* 26:151.

Alignment variants calls internal gaps as indel alleles anchored to a reference
base (a shared flank where possible, so an adjacent SNP isn't lost); simple indels
in a short tandem repeat get an `RU` motif tag. The reference row always has
genotype 0.

### Running analyses

- **Tools** (bar at the top) take their input from the list's selection when it fits,
  and every dialog has a **picker** to choose or change it: the sequences (a selected
  list stands for all its sequences) are shown at the top of the dialog; *Add…* opens
  your library by folder, with lists that expand, a filter (name, organism, description,
  accession), and ticks for lists and folders; items the tool can't use are left out
  (*Show unusable* lists them with the reason). Single choices (the reference, the
  alignment) have a *Change…* button. A tool button that doesn't fit the current
  selection is **dimmed** (it still opens, and you choose in the dialog; the tooltip
  says why); one that nothing in the library could use (a tree with no alignment) is
  **greyed out**. Results are saved in the library:
  - *Download*: GenBank records from NCBI by accession (nucleotide; e.g. `NC_001677`,
    `MK000001.1`; paste as many as you like, separated by spaces, commas or lines),
    with their annotations and sequences, into one sequence list (named as you
    choose) or a separate sequence each. Accessions NCBI doesn't have are listed
    in the job's log. Needs the PC to be online. The **Search** tab takes anything
    NCBI's search understands: plain words (`rbcL Gracilaria`) or its fields
    (`Fucus[organism] AND complete genome`, `[gene]`, `AND`/`OR`/`NOT`). Matches
    show 100 at a time (accession, description, organism, length, date; *Show 100
    more*); tick the ones you want (a click on a row ticks it; the box at the top
    ticks all shown) and Download. The list is named after the search.
  - *BLAST*: choose **Local** or **Online** in the tabs at the top, before choosing
    a program. Local searches databases on this PC; Online sends the queries to
    NCBI. Switching tabs preserves the query and each tab’s options.
    Online searches NCBI's databases (as the BLAST web page) with the selection
    in the viewer (part of a sequence, or of several rows, or of the consensus),
    else the whole of each sequence selected in the library (up to 10 queries).
    Each whole sequence list runs as one job. Its successful results appear in
    a new list named `<list>_blast`, in source order. The job log names any failed
    queries. Individually selected queries produce separate result items.
    Programs: megablast (highly similar), discontiguous megablast (more distant),
    blastn, blastx (translated, against proteins; with the sequence's genetic
    code); for proteins blastp and tblastn. Databases: core nucleotide (NCBI's
    default), nt, RefSeq RNA; nr, RefSeq proteins, Swiss-Prot. *Limit to* takes an
    NCBI query, e.g. `Rhodophyta[organism]`. A search takes from half a minute to
    several minutes (NCBI's queue; Cladia asks once a minute, as NCBI wants).
    Databases in **Local** (Settings > BLAST databases, those of the program's
    kind) are searched here with BLAST+, in seconds, with no limit on how many
    queries; *Limit to* doesn't apply to them, and their hits have no organism
    (it's in the description).
    Each query's result opens as a table (`<query> BLAST`) with accession,
    description, organism, max and total score, query cover, E value, identity,
    length, in NCBI's order or sorted by a column (click its header), with a
    filter (the columns keep their width). Click a hit (or its box) to tick it.
    **Download ticked** fetches the ticked hits from GenBank as a sequence list;
    **Copy** / **Export CSV…** the table.
  - *Annotate*, three methods. If the sequences already have annotations, the
    dialog asks: **replace** them (the results have only the new ones) or **keep**
    them: yours stay as they are, and only what you don't have is added (a new
    feature counts as one you have when it's of the same type, on the same strand,
    and overlaps one of yours by at least 80 % of the shorter; a de novo method's
    additions get a note such as "added by MFannot"; the job's log gives the counts). By default the
    selected sequences are updated, with their previous states in saved versions.
    Select **Save as a copy** to create new items instead. If a source is
    changed or being edited while a job runs, its result becomes a copy instead.
    **De novo, MFannot**: the genetic code (mitochondrion 4, plastid 11, each
    sequence's own, or any other); genes, CDSs with their introns and exons, rRNAs
    and tRNAs, named as MFannot names them (e.g. `trnM(cat)`). It runs in the
    MFannot authors' own image, downloaded and unpacked the first time (about
    510 MB, 1.7 GB on disk). It reads each sequence as linear, so a gene across a
    circular sequence's origin isn't found: move the origin between genes first.
    About 25 s per mitochondrial genome.
    A de novo engine that rotates a circular sequence (to start it at a chosen gene) has its result saved as a copy rather than changing your sequence's origin.
    **Quick, from a reference**: pick an annotated
    sequence and the most a feature may differ (default 30 %); BLAST finds each of
    the reference's features in the sequence and copies it there (to every place it
    is found within the limit, e.g. both copies of an inverted repeat), with a note
    saying how identical it was. A copied CDS's ends are then moved to the nearest
    start and stop codons in its frame (the reference's own start codon first, then
    ATG, then the genetic code's others); the note says how far. A copied gene then
    takes its ends from its copied CDS (or RNA), as far from them as in the
    reference: equal ends stay equal, UTRs keep their length. CDS translations
    are made for the new sequence.
    Unaligned ends count as differences. Very short features (under ~20 bp) can't be
    found; the job's log lists what wasn't copied.
    **Find ORFs**: minimum length in codons; the sequence's own genetic code or one
    chosen; both strands; any start codon; and whether to leave out ORFs that
    overlap an existing CDS. Each result has an ORF feature from
    a start codon to the next stop (orange, named with its length in amino acids).
    Keep (the default here) adds them to the sequence's own annotations; Replace
    leaves only the ORFs.
    With any method, a whole sequence list is one job with one bar (how many
    sequences are done) and updates the list in place, preserving its sequence order.
    When creating copies, including fallback copies, results from a whole list
    go into a new sequence list with a suffix such as `_mfannot`, `_annotated`
    or `_orfs`.
    Sequences selected one by one are one job too. MFannot annotates them
    one after another (about 25 s per mitochondrial genome); the quick annotation
    searches them all at once (21 contigs in about 2 s); the ORF finder is immediate.
  - *Align (MAFFT)*: strategy, orientation adjustment, name; under *More options*,
    max iterations and gap penalties. *Reverse-complement sequences in the wrong
    orientation* is on unless you turned it off last time (sequences it turns get
    `_R_` before their name); not shown for proteins. Three tabs: *New alignment*;
    *Add to existing*: the selected sequences added to an alignment in the list
    (`--add`, or `--addfragments` for fragments; optionally keeping its length), or
    aligned to one *reference sequence* (as fragments, in the reference's
    coordinates, by default; it opens on this tab when one sequence or an
    alignment is selected); and *Compare genomes*.
    MAFFT progress keeps fixed Preparing / Aligning / Finishing phases, with
    direction checking when enabled. Counters describe MAFFT work units, not
    completed input sequences; refinement and combining can be indeterminate.
    Automatic strategy selection does not change the displayed phase total.
  - *Align › Compare genomes* (progressiveMauve): whole genomes that may be
    rearranged or inverted. It finds the blocks they share wherever they are and
    aligns their bases. The result goes under *Genome comparisons*: the genomes
    one under another with their annotations, each with a lane of coloured blocks
    (the same colour in every genome; above the line on the + strand, below it
    when inverted), and bands linking each block to the same block in the next
    genome (twisted where one of the two is inverted). Click a block to line the
    genomes up on it, as in Mauve: each genome that has it moves along so the
    block starts in the same place (positions shown stay each genome's own), the
    block highlighted everywhere and selected in the genome clicked. Right-click
    → *Each genome from its start* undoes it. Right-click a block to *Extract it as
    an alignment* (its rows get their genomes' annotations; reversed ones are
    named `_R_`), e.g. to build a tree from it. *Seed size*: empty lets
    progressiveMauve choose; smaller (e.g. 11) finds more between distant or
    AT-rich genomes. *Start the genomes where the first one starts* (on by
    default): progressiveMauve reads each genome from its first base, so circular
    genomes starting at different genes would show blocks cut at their origins;
    each circular genome is turned to start where the first selected one does, and
    a genome on the other strand is reverse complemented (the log says which; your
    sequences aren't changed). Seven red algal mitochondria, two of them reversed:
    6 blocks as they were, 1 block lined up. The comparison is read only: it keeps
    the genomes as they were compared (turned, if lined up), so its positions are
    those; extracted blocks still get the annotations of your sequences.
  - *Build tree*: from an alignment in the list. **IQ-TREE** (maximum likelihood; the
    model is picked by ModelFinder unless you type one, ultrafast bootstrap with 1000
    replicates, optional outgroup and threads) or **FastTree** (fast, approximate).
    The tree goes to the *Trees* list; IQ-TREE's report is in the job's log.
    Threads *AUTO*: 4 for small and medium alignments (IQ-TREE's own measuring of
    the best number costs about a minute every time, more than a small tree takes);
    for large ones (distinct columns x sequences from 300,000, e.g. 30 organelle
    genomes) IQ-TREE measures it itself. Type a number to choose.
  - *Assemble* (Sanger reads): a sample's reads, forward and reverse (and
    internal ones), put together. Each read is aligned to the longest one, and
    reverse complemented if it fits better that way; a read overlapping too little
    is left out (the log says so). The consensus takes, at each position, the base
    with the highest summed quality (equal sums give an IUPAC code); where the
    reads disagree it gets a *reads disagree* feature saying which read had what
    at what quality. Results: the reads aligned (`<name> reads`, in the direction of
    the first read selected, their traces under them when zoomed in) and the
    consensus (`<name>`, named from the reads: `R6574_F` + `R6574_R` -> `R6574`).
    **By sample name** (the dialog's second tab; it opens there when the selected
    reads are several samples): select all the reads of a run and each sample is
    assembled as above. Reads are grouped by name: the sample is what comes before
    the primer, the last word ending in F or R (`AG123_rbcLF` + `AG123_rbcLR`;
    `AG124-F_A01` + `AG124-R_B01`, where the plate well after the primer is
    ignored; also F1/R1, fwd/rev). The dialog lists the samples it found and the
    reads left without a partner before anything runs. Results go in a new
    folder: each sample's reads aligned, and one list of every consensus. A sample
    that can't be assembled is noted in the log; the others go on.
  - *Primers*: two tabs. **Design** runs Primer3 on one sequence: pick a target
    region (the viewer selection fills it), product-size range, and optionally Tm,
    GC and the number of pairs. **Test** runs a small in-silico PCR of a primer
    pair against the selected sequences: binding sites, mismatches (and how many
    at the 3' end), product size and orientation, including both orientations and
    circular sequences. Of the mismatches allowed, *in each primer's last 5 bases*
    limits those at the 3' end, which is what stops a PCR (default at most 1; none,
    2 or any). Each run makes a *Primer results* item, beside its
    sequence: a sortable, filterable table of the pairs (both primers, positions,
    Tm, GC, product, penalty) or the products, with the parameters used in its
    title, *Copy* and *Export CSV…*. **Save primers** makes a list of the forward
    and reverse primer of each ticked pair, named after the template, for ordering
    or a record. The Primers tool's **Test** tab loads a pair from a primer design
    (any of its pairs) or from those saved lists (*Load a pair from…*), then runs
    the in-silico PCR against the sequences selected in the list. Primer design
    needs the tools image 0.4
    (Primer3 2.6.1).
  - *Map reads (minimap2)*: choose Illumina, Nanopore, PacBio HiFi or PacBio CLR.
    Long reads use their matching minimap2 preset and each file maps independently
    (FASTA or FASTQ, optionally gzipped). Minimap2 2.31 uses `map-ont` for noisy Nanopore reads and offers
    `lr:hq` for high-accuracy Nanopore reads (Q20+, roughly 99% accuracy or better).
    The dialog initially chooses high-accuracy Nanopore, then remembers your choice. End extension is available for Illumina
    and HiFi. Raw Nanopore/CLR consensus needs indel checks and suitable polishing.
    Illumina reads (FASTQ, `.fastq.gz` or not) are mapped to a
    sequence in the list. Pick any number of files (*Choose files…*, or paste their
    paths, one per line); R1/R2 files are paired by name (`_R1`/`_R2`, `_1`/`_2`,
    e.g. `S1_L001_R1_001.fastq.gz`), lanes or runs of the same sample are merged,
    a file without its mate is single-end. The dialog shows the pairs it found.
    The read files stay where they are: Cladia only reads them, nothing is copied.
    Options: minimum depth for the consensus (N below it, 3), minimum mapping
    quality (0: reads that fit two places equally well, as in a plastid's inverted
    repeat, are kept), and *Keep the mapped reads (BAM)*. A circular reference is
    mapped across its origin too.
    **Extend the ends from the reads** (a linear contig, e.g. from a broken
    assembly): reads that hang off an end are added to it (the consensus of their
    overhangs), round after round, as Geneious's "iterate": each round adds up to
    about a read length at each end (~100 bp with 150 bp reads). Options: the most
    rounds (5) and the share of reads that must agree on a new base (80 %); the
    minimum depth counts too. A side stops growing where the reads disagree or run
    out. For gaps of several kb an assembler that grows a seed (NOVOPlasty,
    GetOrganelle) does better. Gzipped reads are unpacked once at the start (into
    Cladia's Linux disk, deleted when the job ends) when there's room: every round
    then reads them faster (about 15 % quicker per round on real data). That disk
    (*Cladia's Linux: Cladia* in Settings > Storage) grows to fit them and
    keeps that size, reusing the space for later jobs.
## Read mappings

- **Imported mappings (BAM)**: a BAM or SAM made elsewhere (BWA, Bowtie2, the HPC's
  minimap2) imports as mappings, through Import or by dropping it. A BAM names its
  references but doesn't hold their bases, so each reference must be in the library
  first: the sequence of that name (list name, LOCUS name or accession) and the same
  length. A job then reads the file (sorting and indexing it when needed, with the
  Linux tools' samtools) and makes one mapping per matched reference, with coverage,
  agreement, clipped reads, consensus and the reads lane exactly as Map reads makes
  them; references not found are listed in the job's log. The BAM is kept with each
  mapping (Export: BAM). CRAM isn't read (convert it to BAM first).
- **Mappings** (the Map reads results) open like a sequence: the reference and its
  features, with a **coverage graph** above it (red underneath where the depth is
  below the minimum; the stretches extended from the reads tinted orange, and
  marked as *extension* features), **Agreement** (the share of reads with the
  commonest base at each position: green from 95 %, olive to 80 %, red below;
  zoomed out, the lowest in each pixel), **Clipped** (reads that stop matching and
  go on as overhang: tall bars at an end say reads want to continue; in the middle
  they suggest a misassembly or a repeat border) and the **consensus** (grey where
  it agrees with the reference, the differences coloured, deletions black,
  insertions as purple marks, N light grey). Then the **sequence** itself, and below
  it the **reads**, which fill the rest of the viewer: the mapped reads of the
  window shown, packed side by side when they don't overlap, drawn exactly as the
  alignment draws its rows (the same letters and grey matches with coloured
  mismatches zoomed in; the same grey bars and coloured ticks zoomed out;
  deletions as gaps, soft clips left out). Hover a read for its position, strand,
  MAPQ, mismatches and CIGAR. Click it to put a cursor in it (Copy, or Ctrl+C,
  then copies the whole read), drag within it to select part of it (Copy copies
  those bases as FASTA) or double-click it to select it whole. The sequence, the
  consensus and a read are selected one at a time: a selection is drawn on its own
  row only (the consensus framed, the sequence's row banded, the read's part on
  its row), so it is always clear what is selected. Zooming keeps a read's
  selection or cursor centred, as for a sequence (and its row centred in the lane).
  As in an alignment, bases that differ from the consensus are coloured, the
  reference's too. The wheel over the lane pans along as elsewhere, Shift+wheel
  scrolls the read rows (also over the names column) and Ctrl+wheel zooms; a small
  bar at the right edge shows where you are. Its label
  says which rows are shown when there are more than fit, on two lines (the count,
  then the rows), shortened to the names column; hover the column's edge to open
  it, as with a sequence's name. No read is dropped.
  A mapping made before this has no
  reads lane. On the circular map, coverage,
  agreement and clipped reads are rings inside the circle. Hovering a track gives
  that track's numbers at the position: the coverage (and whether it is below the
  minimum), the agreement with the base counts at a variable position, or the
  clipped reads; over the consensus row it gives all of them and the consensus base.
  Over a track's *name* it gives that track's numbers for the visible window
  (mean/max coverage, agreement range, clipped reads), and a track hover is shown
  even while a selection is active.
  Bases under quality 20 aren't counted. The **Summary** (side panel, Metadata tab) gives the numbers (reads, %
  mapped, properly paired, insert size, depth, % covered, how much was extended)
  and lists the coverage gaps, the differences and the **variable positions**
  (where a second base is in at least 5, 10, 20 or 30 % of the reads, you choose,
  and 3+ reads, with the counts): click one to go there. *Consensus as new sequence* (Summary, or
  right-click the mapping) makes it a sequence, with the reference's features moved
  onto it. Export: the consensus as GenBank or FASTA, or the BAM (with its `.bai`
  index) if you also want the reads outside Cladia (IGV, Tablet).
## Assembly graphs

- **Import** a GFA (SPAdes, GetOrganelle, Unicycler, Flye) or SPAdes' FASTG like any
  file; it opens in the graph viewer. Or select the contigs of an assembly (a list)
  and right-click *Make assembly graph from contigs…*: contigs whose ends overlap
  exactly (30 to 1,000 bases) are linked.
- **The view**: each connected group is drawn as parallel lines, its longest path in
  the middle; circular groups as rings; contigs linked to nothing together at the
  end. Colour is depth (yellow low to purple high, the legend is at the top right).
  Dashed links are loops back or joins between two ends (an inverted repeat). The
  wheel moves, Ctrl+wheel zooms, **Find** goes through node names. *Show ▾*: lengths
  to scale, colours, labels, the order of the groups, rings in a row (Compact), and
  hiding small groups, low depth, or groups that don't match the reference.
- **Selecting nodes**: click, Ctrl+click, Shift+click (the shortest way between two
  nodes). Ctrl+C copies their names. *Export selected as a sequence…* saves them one
  by one or joined along their path (overlaps written once; circular if it closes).
  The BLAST tool takes the selected nodes as queries.
- **Path mode** (*Select: Nodes | Path*), for a route the nodes alone can't say, such
  as a plastid's repeat used twice: click the nodes in order, each at the end you
  enter it by; only ends linked to the last step are offered. The strip under the bar
  lists the steps and says when the path closes; Backspace removes the last step,
  Esc clears. *Export path…* saves it as one sequence.
- **A node's track**: double-click a node to see the sequence of its track in the
  sequence viewer, with depth as coverage; **◀ Graph** goes back. Its right-click menu
  saves the track or a selection of it, turns a node round, or takes another branch.
- **Compare with a reference**: pick a library sequence; each node is then marked
  where it matches, coloured along the reference (red at its start to purple at its
  end). The × beside its name removes the comparison.
- **Circularize from overlap…** (right-click sequences in the list): a linear sequence
  whose last 30 to 1,000 bases exactly repeat its start loses the repeat and is
  marked circular. Others are left as they are. The earlier version stays in its
  saved versions.

## Result folders and jobs

- **Result folders:** individual annotation copies stay beside each source, including
  copies made when an in-place update cannot be applied. Combined results default
  to the inputs' shared folder; inputs from different folders require a destination.
  **Save result in** is shown for alignment/comparison, assembly, mapping, trees and
  primer jobs. A reference does not choose the output folder; mapping external FASTQ
  files defaults to the folder currently open. Grouping, contig graphs and combined
  pasted extracts also ask when sources span folders. Taking members out of lists
  preserves their source folders; dropping them into a folder uses that destination.
  Queued/running jobs follow folder renames and moves. Deleting a destination follows
  the library's move-up rule, instead of recreating the deleted folder.
- **Jobs run side by side**: downloads and BLAST searches never wait for computing
  jobs; one-core jobs (de novo annotation, comparing genomes) run several at once, as many
  as the PC has cores (less one); MAFFT, trees, mapping and the quick annotation
  of many sequences use several or all of them. Each kind keeps its order.
- **Jobs** (bottom left) shows every running and queued job of this session (in
  blue; with more than 3 they scroll), then the latest finished ones (done green,
  failed red; a message pops up when one finishes), at most 3 rows in all but
  always the last one finished,
  with when they ended and links to their results; older ones are in the archive. *log* shows a job's log (a
  running one's, e.g. IQ-TREE's progress, updates by itself); *cancel* skips a
  queued job or stops a running one. Every log is saved when its job ends, in
  `.cladia/logs`; **Archive…** lists them all, with a filter (tool, sequence name,
  status, date), how long each took and its results (struck through if deleted
  since). Click a row to read its log; tick rows (the box in the header ticks
  every row shown, e.g. after filtering "failed") and **Delete** them.
  A running job has a **progress bar**: one block per stage when it has stages
  (finished ones filled); the current stage fills as it goes when Cladia can tell
  how far it is (downloads: records fetched; IQ-TREE: models tested; MAFFT:
  sequences compared and alignment steps; extension rounds), else it shimmers
  (BLAST at NCBI, the tree search, MAFFT's refinement, de novo annotation). Comparing genomes
  shows reading, seeds, pairs compared and the merges of its alignment. Under it: the stage
  and what it's doing (e.g. *2/3 Searching trees · iteration 220*, *6/7 Aligning
  (2nd pass) · 80 of 119*, *NCBI expects about 40 s*).
## Trees

- **Trees** (from a job, or imported `.nwk` / `.tre` / `.treefile` files) open in
  Cladia's tree viewer (rectangular), initially fitted to the whole tree.
  During the session, each tree remembers its row zoom, branch zoom, pan position
  and collapsed clades when you switch items. Fit whole resets the view explicitly;
  an edited tree starts fitted again.
  - Mouse: the wheel scrolls up and down, Shift+wheel sideways; **Ctrl+wheel**
    spreads the tips apart or brings them closer (out to the whole tree; names keep
    their size and show once there's room), **Ctrl+Shift+wheel** stretches the
    branches; dragging moves the tree; scrollbars on the right and bottom.
  - Click a branch or name to select that clade (or tip), double-click to collapse
    or expand it (a grey triangle with its number of tips).
    **Ctrl/Cmd+click** toggles tips, **Shift+click** selects a range, and
    **Ctrl/Cmd+C** copies their names. Fit selection frames all selected tips;
    right-clicking a selected tip keeps the selection. The right-click
    menu: *Reroot* (saved in the tree: the root goes halfway along that branch, and
    support values stay with their branches; Ctrl+Z undoes it, Ctrl+Y redoes it,
    several steps back), *Collapse / Expand*, *Copy the tip
    names*. The status line under the tree describes what the mouse is on (support,
    branch length, distance from the root). Trees open **Read only**; click the
    lock in the Edit group to enable rerooting, removing tips and undo/redo.
    These changes are saved at once. Lock it again to return to Read only;
    selecting, copying, collapsing and changing the display work in either mode.
  - **Find**: tips whose names contain the text (Enter or ◀ ▶ go through them).
    When zoomed out past readable labels, orange dots mark every hit and a larger
    ring marks the current result, including matches inside collapsed clades.
    **View**: *Align tips* (names in one column with dotted guides), *Support*,
    *Branch lengths*, *Ladderize* (largest clades at the bottom; view only); these
    choices are remembered across app restarts, in Cladia’s settings. The **distance scale** is under the tree.
  - **Figure ▾** makes a figure of the tree, to prepare and export SVG, PDF, PNG or TIFF.
    *Export…* in the library list saves the tree as Newick.


## Sharing projects and recovering saved work


Open **Settings → Storage → Project ZIP**. **Export project ZIP…** packages the
entire saved library, folders, annotations, provenance, and attached files
(kept BAM/index files, reads lanes, traces, graph hits, comparison alignments and
figure settings). Saved versions and Trash are optional. Save or undo unsaved
viewer changes first; running jobs are included only once their results reach
the library.

ZIP export, checking and import run as **Jobs**, with stages, logs and cancellation.
Closing the desktop app while they run shows the normal jobs warning. If you
choose to close, Cladia cancels the transfer and waits for its cleanup. In the
browser, completed exports can also be downloaded from their job row. The latest
four downloads are retained for up to an hour or until Cladia exits.

Transfers allow other library writes during copying, compression and validation.
If the saved library changes while an export snapshot is being copied, export
stops and asks you to retry. After an interrupted import, startup uses its
publication journal to remove verified, unregistered files or preserve a committed
import. Files changed since the interruption are left alone and reported under
Settings → Storage.

**Import project ZIP…** validates the archive and shows a summary. Choose a new
folder name: imported objects receive fresh IDs and filenames, links point to
the imported copies, and an existing folder name gains a number. Existing items
are not replaced. Included Trash items appear in the normal Trash, with their
project folder recorded. Included saved versions remain restorable. Original
paths are retained as historical information, not enabled as export-to-original
destinations on the receiving machine.

External FASTQ/source files, local BLAST databases, tools/WSL installations,
settings, credentials and job logs are excluded. Mappings retain saved results
and any kept BAM/reads sidecars; bring original reads separately to rerun an
analysis. Allow disk space for the ZIP and staged contents. Current limits:
50 GiB uncompressed, 100,000 payload files, and 512 MiB uncompressed per saved
history snapshot. This is a versioned Cladia format, not a generic ZIP importer.

Metadata-table import refuses ambiguous matches (including accession aliases)
rather than using the first row. The preview counts all changes and rechecks
saved inputs before applying. Overlapping list/member selections count once;
large-list imports retain per-record saved versions. Tables above 50,000 rows,
256 columns or 20 MiB are rejected explicitly.

Metadata imports roll back the whole batch if a file or saved-history write fails.
Sequence, alignment, tree and figure saves also protect the previous file and
history index on write failure. These rollback copies are temporary and require
working disk space; they do not provide an independent backup.

New job results show the producing job, completion time and identifiable library inputs in the Metadata tab. Open its log there while the log is retained. Older results do not have this information; it does not yet record every parameter or tool version.

**Check library** in Settings → Storage reports missing or unreadable registered files and unreferenced files without changing them. An unreferenced file is not necessarily safe to delete.

Cladia records saved file versions for sequence, alignment and tree edits made after this feature was added. Open **Metadata → View saved versions** to inspect or restore them. For a sequence in a list, versions cover the entire list file; in a big list (over 20 MB) they cover just that sequence, so saving stays quick. Tree and comparison figure settings have a separate **View figure versions** button in Metadata; their changes are saved automatically, and versions exist from the first save after this feature was added. In a read-only sequence or alignment viewer, Ctrl+Z and Ctrl+Y move between saved versions; unsaved edits must be saved or undone first. Saved versions follow whole items into and out of Trash. They are stored alongside the library and are not an independent backup. Each item keeps its last 20 versions (and at most 300 MB of them); *Settings > Storage* shows how much they take and can delete them all (the items stay as they are). Other actions and item types do not yet have persistent version history.

With focus in the library list, Ctrl+Z and Ctrl+Y undo and redo rename, move, delete, group and split actions in this window's current session. They refuse to overwrite newer changes. Undoing deletion of a list member restores its original list and order. Group and split keep the source items in Trash for undo; emptying or changing those Trash items makes the corresponding undo unavailable. Other unsupported structural actions clear this session's action stack.
