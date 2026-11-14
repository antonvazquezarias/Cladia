# Plan: mapping long reads (Nanopore, PacBio) (for another agent)

Written 2 Nov 2026. Read `CLAUDE.md` first (*Mapping reads* under *Jobs and tools*, *Cladia's own Linux*,
*Testing*, *Working with the user*), then this file. Implemented 2 Nov 2026; the plan below is retained as the design record.

## Decision: minimap2 is enough, and no assembler is added

- **Mapping**: minimap2 is the standard aligner for long reads too; only its **preset** changes
  (`-ax sr` today). Nothing else needs to be added for mapping.
- **Assemblers (Flye, hifiasm, Canu, Oatk, MitoHiFi...) are not part of this**: Cladia doesn't assemble
  (the user's lab assembles on an HPC and brings contigs and graphs in; see *Graph from contigs* and the
  GFA import). Flye and hifiasm write GFA, which the graph view already opens; that is the integration.
  If the user later wants local long-read assembly or polishing (Medaka / Racon for a better ONT
  consensus), it is a separate plan.

## What exists

- `cladia/mapping.py`: `pair_reads(paths)` (pairs Illumina files by name), the shell the job runs (`map.sh`
  built around line 144: `minimap2 -t $T -ax sr /data/ref.fasta <files> | samtools sort ...`, `SORT_MEMORY`,
  `threads(cores)`), `ends_script` (extension rounds, line ~189, also `-ax sr`), `clipped_reads`,
  `extend_ends`, `read_reads` / `write_reads` (the reads lane: `READS_MAX` 200k kept, `samtools view
  -F 0xF04` primary reads only), the depth / agreement / variable-position code (`COUNT_AWK`,
  `read_counts`).
- `jobs.map_job` (~line 1423): reads files are mounted read-only from the user's folders (not copied),
  `_unpacked` (reads gunzipped once into a volume for jobs with extension rounds), progress through
  `_watch_map`, `NEEDS` (the mapping takes the whole core budget).
- The dialog: `TOOLS.map` / `#map-dialog` in `app-tools.js` (reads picked with `pickReads`, reference
  through the picker, extension rounds, min depth / agreement), `map.reads` through `/api/tool-registry/run`.
- The viewer: reads lane in `seqview.js` (`setReads`, `packReads`, `readTip`...), mapping JSON
  `<name>.mapping.json` with `reads`, `agreement`, `variable`, `clips`, `extension`, `summary`.
- The tools Linux (`containers/lab-tools/setup.sh`, `config.TOOLS_VERSION`) is Debian bookworm: its
  minimap2 was **2.24**, now upgraded to **2.31-r1302**. The `lr:hq` preset
  (accurate Nanopore Q20+) is now offered; upstream documents it for 2.27+.
  If that matters, build a newer minimap2 in `setup.sh` (repeatable, bump `TOOLS_VERSION`, note it in
  CLAUDE.md); otherwise use `map-ont` for all Nanopore and say so.

## What to build

1. **A reads type** in the Map reads dialog (a select, remembered in `cladia.map`):
   *Illumina (short, paired)* (today's behaviour, the default), *Nanopore* (`-ax map-ont`),
   *PacBio HiFi* (`-ax map-hifi`), *PacBio CLR / older* (`-ax map-pb`), and *Nanopore, high accuracy*
   (`-ax lr:hq`) available with the upgraded tools (upstream documents 2.27+). Each with a one-line "about" under it (the *A tab per
   method* idea: only the shown fields are relevant).
2. **No pairing** for long reads: every file is one input (FASTQ or FASTA, gzipped or not); several files
   are each mapped and merged as the pairs are today. `pair_reads` is not used.
3. **The mapping script** takes the preset as a parameter (one place: the `-ax` argument, in `map.sh` and in
   `ends_script`); everything after minimap2 (`samtools sort`, depth, counts) is unchanged. Keep `-F 0xF04`
   (primary only): supplementary pieces of chimeric reads are dropped; a read crossing a circular
   reference's origin maps whole thanks to the 1000 bp `EXTEND`.
4. **Quality and noise.** Long reads have 1-10 % errors, mostly indels, so the numbers built for Illumina
   need other defaults when the type is long: *variable positions* (second base >= 5 % and >= 3 reads) would
   list noise everywhere; offer 20 % for ONT / CLR (the Summary's select exists: `cladia.variable`), keep 5 %
   for HiFi. `samtools mpileup -B -Q 20` filters by base quality: FASTA reads have none (check it doesn't
   drop everything). Say in the Summary which preset was used.
5. **Extension rounds** (growing a linear contig's ends from clipped reads): **off and hidden for ONT and
   CLR at first** (the agreement rule can't work at 8 % error), allowed for HiFi if a test shows it works
   (a simulated 0.5 % error HiFi set must extend as the Illumina test did). Don't promise more than the
   test shows; write down what you found.
6. **The consensus** (`samtools consensus`, called twice for the insertions): fine for HiFi; for ONT it
   is only as good as the depth allows. Warn in the Summary ("consensus from raw Nanopore reads: check
   indels, polish with Medaka if you need accuracy") rather than pretending.
7. **The reads lane** for reads of 5-50 kb: `packReads`, `READS_LIMIT` 500 per window and the row heights
   were built for 150 bp; check it stays readable (zoomed out: a bar per read; zoomed in: letters with
   many mismatch colours: maybe default to the thin bar view for long types), and that the sidecar
   (`reads.tsv`, `_split_read`'s per-base walk) isn't slow for tens of thousands of long reads.
8. **Memory and time**: FASTQ of several GB; `samtools sort -m` as it is; the `_unpacked` step only for
   jobs that need it (extension rounds); progress stage names stay (`_watch_map`).

## Order of work

1. Tools image: check the minimap2 version; decide about `lr:hq`.
2. The preset as a parameter through `map_job` / `mapping.py`, and the dialog's select; no pairing.
   pytest with the container call stubbed (the project's pattern: `CLADIA_CONTAINER` stand-in, see
   `tests/test_mapping.py`): the command line carries the right `-ax`, files aren't paired.
3. **A simulator** `tests/make_long_reads.py` (plain Python, no new dependency): reads of 3-20 kb from both
   strands of a real genome (`tests/data/Clarei_mt.gb`), errors at a given rate split into substitutions
   / insertions / deletions (ONT-like ~8 %, HiFi-like 0.5 %), a FASTQ with plausible qualities, 50x.
4. A **real run on the user's PC** (WSL): map the ONT-like and the HiFi-like sets, check coverage, depth,
   agreement, the consensus against the truth (HiFi: ~99.9 %; ONT: say what it is), time and memory;
   look at the reads lane in the app (headless screenshot).
5. Defaults for the variable-position threshold and the extension-rounds decision from those results.
6. Docs, commit.

## Tests and checks

- `python -m pytest tests` stays green (it includes the JS tests).
- Real WSL job on this PC (the notes have the pattern in earlier mapping tests: a Kappaphycus-like
  genome with planted differences). Report the numbers in `CLAUDE.md`, as the other mapping notes do.
- The existing Illumina tests and the user's real runs must be unaffected: the default type is Illumina.

## Docs and housekeeping

- `CLAUDE.md` under *Mapping reads*: the types, what each does and doesn't (extension rounds, consensus
  caveat, variable threshold), the simulator, measured results. `README.md`: the Map reads description.
  Update the *Future additions agreed on 2 Nov 2026* list (item 4).
- Commit when a stretch is done and tested, push to `main`, tell the user in a line. Another session may
  have uncommitted changes in the same tree: `git status` first and **stage only your own files**
  (`git add <paths>`, never `git add -A`).
- Ask the user before a design choice that would be costly to redo; they answer questions before you build.

## Out of scope

Assembly, polishing, variant calling, basecalling, read QC and trimming (Porechop, Filtlong, NanoPlot):
possible later, none asked for. Paired-end linkage views, BAM export are as they are.

## Completion — 2 November 2026

Implemented and checked. See CLAUDE.md, “GC tracks and long reads”, for implementation
choices, synthetic Linux mapping results, performance measurements and limitations.
The retained Chrome check is `scripts/test_composition_ui.cjs`; it uses a temporary
library. GC-by-feature-class, assembly and polishing remain outside this work.

Minimap2 was subsequently upgraded to pinned upstream 2.31-r1302 (tools 0.7),
with a separate high-accuracy Nanopore lr:hq choice. See CLAUDE.md's upgrade note
for documentation review and validation results; all five genomic presets were
checked on the real Linux toolchain, with circular-origin and paired-read coverage.
