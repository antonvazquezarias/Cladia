# Cladia analysis-tool version audit — 2 November 2026

Scope: installed scientific executables in Cladia-tools, plus
samtools' HTSlib dependency. Installed versions were read from the actual Linux
environment and Debian package metadata. Latest versions refer to upstream
releases, rather than the newest package available in Debian bookworm.

## Updated and verified — tools 0.8

| Tool | Before update | Installed / latest upstream | Source |
| --- | --- | --- | --- |
| MAFFT | 7.505 | 7.526 | [Official downloads](https://mafft.ddbj.nig.ac.jp/alignment/software/) |
| IQ-TREE | 2.0.7 | 3.1.4 | [Official website](https://iqtree.github.io/), [release](https://github.com/iqtree/iqtree3/releases/tag/v3.1.4) |
| NCBI BLAST+ (blastn/blastp/blastx/tblastn/tblastx and database utilities) | 2.12.0+ | 2.17.0+ | [NCBI current downloads](https://ftp.ncbi.nlm.nih.gov/blast/executables/blast%2B/LATEST/), [release notes](https://www.ncbi.nlm.nih.gov/sites/books/NBK131777/) |
| samtools | 1.16.1 | 1.25 | [Official release](https://github.com/samtools/samtools/releases/tag/1.25) |
| HTSlib (samtools dependency) | 1.16 | 1.25 | [Official release](https://github.com/samtools/htslib/releases/tag/1.25) |

IQ-TREE also offers legacy 2.4.0; moving to 3.1.4 is a major-version upgrade and
has been checked against Cladia's command options, progress parsing and output handling
for DNA and protein trees, including ModelFinder and 1,000 UFBoot replicates.
The samtools/HTSlib 1.25 releases were published 27 October 2026; older cached
release listings still show 1.24, so latest tags were checked through GitHub's API.
All five entries above are now upgraded. Samtools links its matching bundled
HTSlib 1.25; Debian's older shared HTSlib package can remain for other packages.

## Optional tools from bioconda — 6 November 2026

Downloaded only when installed (Settings → Tools), from conda-forge and bioconda,
exactly as listed in each lock file (`cladia/tool_plugins/locks/`); micromamba
2.9.0 (pinned by SHA-256) makes the environments. Checked for real with
`scripts/check_conda_tools.py`: install, a run, uninstall.

| Tool | Version | Packages in its lock | Environment |
| --- | --- | --- | --- |
| Clustal Omega | 1.2.4 (bioconda clustalo h503566f_10) | 7 | about 34 MB |
| Krait repeat finder | pytrf 1.5.0, pyfastx 2.3.1 | 24 | about 170 MB |
| GetOrganelle | 1.7.7.1 (with SPAdes, Bowtie2, BLAST+) | 72 | about 1.4 GB |
| MUSCLE | 5.3 | 5 | about 31 MB |
| MACSE | 2.07 (with Java) | 60 | about 520 MB |
| RAxML-NG | 2.0.4 | 33 | about 160 MB |
| MrBayes | 3.2.9 | 72 | about 560 MB |
| tRNAscan-SE | 2.0.13 (with Infernal) | 7 | about 110 MB |
| trimAl | 1.5.1 | 5 | about 30 MB |
| ClipKIT | 2.14.0 | 32 | about 290 MB |

These replace the earlier plugin packages (a Clustal Omega build of our own, a
Windows Python runtime with wheels for Krait, and a separate Linux root for
GetOrganelle); the versions are the same as theirs.
## Current

| Tool | Installed / current | Evidence |
| --- | --- | --- |
| minimap2 | 2.31-r1302 | [Official latest release](https://github.com/lh3/minimap2/releases/latest) |
| FastTree / FastTreeMP | 2.2.0 | [Upstream documentation](https://github.com/morgannprice/fasttree/blob/main/index.html) |
| Primer3 / primer3_core | 2.6.1 | [Official releases](https://github.com/primer3-org/primer3/releases/tag/v2.6.1) |
| table2asn | 1.29.324 | [NCBI download directory](https://ftp.ncbi.nlm.nih.gov/asn1-converters/by_program/table2asn/); current Linux download exactly matches the installed binary |

For table2asn, SHA-256 of both the installed executable and the decompressed
current NCBI Linux download was
`88ec2acb32ab63b67d758311865b7dc0d4b98cae16c5b9f42454f28afb921d5b`.
NCBI distributes a rolling binary rather than a dependable numbered release list.

## progressiveMauve: unmaintained, not a straightforward version comparison

Installed Debian package: `1.2.0+4713+dfsg-5+b1`; the executable reports a build
date of 13 December 2020. The [upstream page](https://darlinglab.org/mauve/download.html)
states that Mauve is no longer maintained. It lists Mauve suite 2.4.0 (2014),
warns that this release can crash with Boost, and recommends 2015 snapshots.
The suite version and Debian's progressiveMauve aligner revision are different
numbering schemes. No verified newer maintained aligner release was found, so
it is not classified as an ordinary pending version upgrade.

## Mapping default

The Map reads dialog now initially selects high-accuracy Nanopore (`ont_hq`,
`lr:hq`) when no choice has been saved. Explicit saved choices remain in force.
The [minimap2 manual](https://github.com/lh3/minimap2/blob/v2.31/minimap2.1)
recommends lr:hq for accurate long reads (<1% error), including modern Nanopore
chemistry; map-ont remains available for noisy/older reads. This is not a
universal replacement for sr, map-hifi or map-pb. API defaults retain Illumina
for compatibility with existing callers that omit the read type.

Validation: retained Chrome UI check passed, including the initial lr:hq choice,
alternative read types and their extension controls.

## Installation and validation of the updates

`containers/lab-tools/upstream.sh` installs the updated releases with SHA-256
checks on versioned upstream archives. MAFFT 7.526 is built from the official
GitLab tag because the older source-download page still links 7.525; samtools
1.25 is built with its bundled HTSlib 1.25. IQ-TREE and BLAST+ use the official
architecture-specific binaries (x86-64 or ARM64). `linux.ensure` and the Dockerfile
both execute the same upgrade script. Tools version 0.8 upgrades existing Linux
installations; compiler dependencies are removed after building. Shell files use
LF line endings so clean Windows checkouts build correctly in Linux.

Defaults reviewed against current upstream manuals:

- MAFFT Auto remains the general default; its strategies and add/keep-length
  workflows are validated on 7.526.
- IQ-TREE uses `iqtree3`; ModelFinder, 1,000 UFBoot replicates, a fixed seed and
  the existing bounded thread policy remain. A compatibility `iqtree2` alias is
  provided for existing external callers. Model-specific or pathogen shortcuts
  are not selected automatically. `-bnni` is recommended for severe model
  violations and doubles runtime; no evidence justified imposing it on all jobs.
- BLAST genomic task choices and tabular output remain compatible with 2.17.0+;
  nucleotide/protein database creation and all five offered search programs pass.
- Samtools keeps explicit simple-majority consensus, the 50% call fraction,
  depth/mapping-quality policies and read-type base-count filters. Its newly
  parallel consensus uses at most T-1 additional threads, respecting the core
  budget including the main thread. It does not switch to Bayesian calling.

Validation: 264 regression tests passed. `scripts/check_analysis_tools.py` uses a
private library to check alignment/addition, DNA and protein trees, actual tree
reports/progress markers, FastTree, local BLAST database creation and five search
programs, progressiveMauve and Primer3 design. Every executable must exit
successfully and report its expected version. `scripts/check_long_reads.py`
checks all genomic presets, paired Illumina, circular references, HiFi FASTA and
HiFi extension against the new samtools/HTSlib. Fresh-container checks additionally
caught and fixed table2asn's missing SQLite runtime dependency.
