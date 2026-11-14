# Project status and next priorities

Updated 5 November 2026. This is the current backlog summary and roadmap. Older design plans
and dated implementation notes remain available as historical records.

## Implemented foundation

Cladia has a copied local library, saved edit history, metadata-table import,
project ZIP transfer with recovery checks, sequence/alignment/mapping/tree/graph
viewers, GC/skew tracks, long-read mapping and publication figure editors.

Tool workflows live in engine modules. The host supplies validation, scheduling,
execution, result publication and a public engine API. Generated tool dialogs
are implemented. Optional tools (Clustal Omega, GetOrganelle, Krait repeat finder, MUSCLE, MACSE, RAxML-NG, MrBayes, tRNAscan-SE, trimAl, ClipKIT)
ship as engine code and download their tool from bioconda when installed in
Settings → Tools ([how](PLAN_BIOCONDA_TOOLS.md)); there are no external plugins.

One managed WSL distribution hosts the Linux tools, with separate dependency
roots where needed. Settings lists both built-in and optional tools, grouped by
type, with management controls and storage information.

Python/API and Node model tests are complemented by retained Chrome regression
scripts for viewers, figures, settings and project transfers, and
`scripts/check_conda_tools.py` installs, runs and removes the optional tools for real. See
[Contributing](../CONTRIBUTING.md) for commands and their coverage limits.

## Before a public release

1. **Validate representative real workflows.** In particular, GetOrganelle still
   needs a real assembly run with suitable Illumina data. Keep tool-install,
   cancellation, failure and result-publication checks alongside scientific
   output validation; mocked jobs do not establish biological correctness.
2. **Polish installation and updates.** Add update discovery, an installer icon,
   signing if available, and clearer explanations of WSL setup and first-use
   downloads. Recheck migration on a clean Windows setup before distributing.

## Roadmap (agreed 5 November 2026)

The order: first the kinds of data and the formats Cladia can open and show,
then the tools that produce them, then tools that edit them. The lab already has
data Cladia can't open (BAMs from the HPC, FASTQ, collaborators' VCF and BED);
once a kind of data has an item and a viewer, a tool that makes it only has to
write that format, and its output can be checked in the same viewer as an
imported file. This replaces the earlier "bcftools first" priority: variant
calling comes after reads and BAM/VCF import, so it also works on imported mappings.

Tools whose results land in views that already exist need not wait for phase 1
(listed at the end of phase 2).

### Viewers today

| Viewer | Shows |
| --- | --- |
| Sequence viewer (linear and circular) | sequences, lists, alignments, Sanger reads, mappings, genome comparisons, graph tracks |
| Tree viewer | trees (Newick, NEXUS, MrBayes output) |
| Graph viewer | assembly graphs (GFA, FASTG, contigs) |
| Figure editor | genome, tree and comparison figures |
| Result table | BLAST hits, primer designs, in-silico PCR |
| Side panel tabs | annotations, variants (list and matrix), identity matrix, checks, metadata |

### Phase 1: kinds of data, formats and viewers

| Format | Shown in | Work |
| --- | --- | --- |
| FASTQ (and read FASTA, gzip), paired files and lanes | **new reads viewer** | **done 6 Nov 2026** (FASTQ; read FASTA still imports as sequences; lanes beyond R1/R2 pairs are separate samples; the tools take reads items or files from disk): reads as library items: paired files and lanes form one sample; derived reads go in the library; reads stay out of saved versions; Storage can remove them; project ZIPs have an *Include reads* option, off by default; provenance records size and checksum |
| BAM / SAM (CRAM if feasible) | sequence viewer, mapping mode | **done 6 Nov 2026** (a job; references matched by name and length; no CRAM): import as a mapping item: coverage, consensus and agreement computed once on import, the reads lane read from the BAM; the reference must be in the library or in the file |
| VCF | sequence viewer, side panel Variants (list and matrix) | **done 6 Nov 2026**: a *variants* item linked to its reference sequence (REF alleles checked), sites as marks on the reference; linking to a mapping not done |
| BED | sequence viewer, as features | **done 6 Nov 2026**: like GFF3 import, onto the matching sequences (BED12 blocks as parts) |
| bedGraph / WIG | sequence viewer, as a data track | **done 6 Nov 2026**: tracks kept beside a sequence (not items), drawn like GC content; bigWig not read |
| SnapGene .dna | sequence viewer | **done 6 Nov 2026**: import (Biopython reads it); features and topology kept |
| phyloXML | tree viewer | **done 6 Nov 2026**: import (Biopython reads it); confidences as supports |
| tabular BLAST output (outfmt 6) | result table | **done 6 Nov 2026**: one BLAST result item per query; lengths, titles and organisms are not in the file |

New viewers:

1. **Reads viewer** (**done 6 Nov 2026**): a report page for a reads item (files and pairing, read
   count, length distribution, quality by position, base composition, a sample
   of reads); fastp's report later fills the same page.
2. **Dot plot** (**done 6 Nov 2026**): a side panel tab for any DNA shown (a sequence
   against itself, two of a list, an alignment's rows, a comparison's genomes), and a
   dot plot figure item.
3. **Protein properties** (**done 6 Nov 2026**): a side panel tab for proteins (pI, molecular weight,
   composition, a hydrophobicity plot along the sequence).

Phase 1 is done (6 Nov 2026) apart from the notes in the table. Reads items now feed Map reads; long-read structural variants (Sniffles2) belong later, not in this phase.

### Phase 2: tools that produce those kinds

New tools come from bioconda (adapters in Cladia, the tool downloaded at an exact
version when installed), as Clustal Omega, GetOrganelle and Krait already do. See
[PLAN_BIOCONDA_TOOLS.md](PLAN_BIOCONDA_TOOLS.md).

In order of value for effort:

1. **fastp**: read QC, trimming, filtering, merging pairs; under a **Reads**
   toolbar button (tabs: Map, Check, Trim), which replaces Map reads; Map and
   Assemble also offer "trim first".
2. **bcftools**: read-based SNP/indel calling on Cladia's and imported mappings
   (ploidy, quality and depth settings; reference identity and sample names
   checked; VCF, parameters and citation kept); then VCF normalization/filtering
   and sample consensus export.
3. **Assemblers**: SPAdes/Unicycler for organelles, Flye for long reads (under Assemble).
4. **More read mappers**: Bowtie2 and BWA beside minimap2.
5. **Whole-genome alignment**: LastZ or minimap2 on assemblies, beside Mauve.

Done 7 Nov 2026 (as they need no new viewer): **MUSCLE 5** and **MACSE** (codon-aware),
**RAxML-NG**, **MrBayes**, built-in **neighbour-joining / UPGMA**, **tRNAscan-SE**, **trimAl**
and **ClipKIT** (Align › Trim columns), and removing tips from a tree. Still open: other
finders (barrnap for rRNAs), T-Coffee, PhyML.

Later scientific proposals, each needing its own scope, licensing and real-data
validation: microsatellite genotyping (HipSTR), long-read structural variants
(Sniffles2), assembly-to-assembly comparison (MUMmer), a plastid inverted-repeat
finder. Krait finds tandem-repeat loci; it does not genotype samples or find
distant plastid inverted repeats.

### Phase 3: editing, curation and platform

- Alignment and read curation (in the viewer bar's Curate ▾).
- Codon-aware alignment; degenerate primers and probes.
- **Workflows**: chained steps (trim → map → call variants) saved as a recipe.
- Library-wide search inside sequences and metadata; UniProt and PubMed searches.
- CRISPR guide design and cloning simulation last.
- Toolbar groups (Data / Analysis / Figures) with one-word buttons; a toolbar
  Figure group for multi-item figures.

Left out of parity for now: RNA-seq expression analysis, microsatellite allele
calling, cloud sharing.

## Ongoing maintenance

The [folder interaction audit](FOLDER_AUDIT.md) records the original ten issues
and their implemented fixes. Folder navigation now opens new folders, exits global
search, separates sidebar selection from the open viewer, preserves expanded paths,
saves app-level preferences, supports keyboard navigation and rejects stale or
colliding mutations. A retained browser check covers these workflows.

The latest maintenance pass hardens settings saves and input validation, recovers
from malformed browser preferences, handles disappearing job logs during storage
measurement, and refreshes tool status after installs. Settings browser checks avoid machine-specific
storage assumptions and wait for asynchronous UI updates.

- Record complete parameters, actual tool versions and immutable input identities
  in persistent provenance, including in-place updates. Current provenance is
  useful but not complete for every workflow; logs may be deleted.
- Keep browser regressions focused on interactions that have broken: zoom anchors,
  horizontal/vertical scrolling, read placement, hover, selection and saved choices.
- Maintain library/history/attachment consistency as new operations are added.
- Update user-facing guides with behavior changes rather than appending unrelated
  paragraphs to the repository front page.

## Limits and deferred scope

- Genetic code is set per sequence rather than per CDS.
- Large alignments can still need performance work during repeated gap editing;
  older timing measurements are not a current performance guarantee.
- Project ZIPs exclude tools, settings, credentials, local BLAST databases and
  original external reads. Saved history is kept with the library, not as an
  independent backup.
- Frontend scripts share global state and depend on script order. Extract cohesive
  responsibilities when necessary; a wholesale rewrite is not a current goal.
- HPC orchestration, metabarcoding and alignment-column curation remain deferred
  by the user. Do not treat them as release requirements.
