# Cladia

A desktop workbench for sequences, alignments, genome maps and phylogenetic trees.
Keep a local sequence library, run analysis tools, inspect results and prepare
figures in one application.

Cladia was previously called **SeqLab**.
The supported desktop setup is **Windows**, with **WSL 2** for Linux analysis
tools. Docker Desktop is not required.

## What you can do

- **Organize sequences:** import GenBank, EMBL, FASTA, GFF3 and Sanger traces;
  manage folders, annotations and metadata in a local library.
- **Inspect and edit:** linear and circular sequence views, alignment gap editing,
  GC-content and GC-skew tracks, read mappings and assembly graphs.
- **Run analyses:** alignment with MAFFT, de novo annotation with MFannot, annotation from a reference,
  trees with IQ-TREE or FastTree, mapping with minimap2 and samtools, local or
  NCBI BLAST, primer design and in-silico PCR.
- **Prepare figures:** edit tree, genome-map and genome-comparison figures;
  export SVG, PDF, PNG or TIFF.
- **Share work:** export a project ZIP with saved library items and attachments,
  optionally including saved versions and Trash.

Imports are copied into Cladia’s library. Editing works on those copies;
writing back to an original file is an explicit export action. Saved versions
help recover edits, but are stored alongside the library and are not a backup.

## Setup on Windows

From a copy or clone of this repository:

1. Run **[setup.bat](setup.bat)** to prepare Python and the application dependencies.
2. Run **[run.bat](run.bat)** to open Cladia.
3. Import files, select an item and choose a tool or viewer.

Setup offers to install WSL if it is missing; that step may require administrator
approval and a restart. The first Linux analysis prepares its tool environment
and may download several hundred megabytes. Cladia manages one WSL distribution
with isolated dependency environments where needed.

If you have a lab-provided `Cladia-setup-<date>.exe`, you can use that instead.
See the **[installation guide](docs/INSTALLATION.md)** for installer updates,
setup checks, storage locations and migration from SeqLab.

## Using it

The **[user guide](docs/USER_GUIDE.md)** covers importing, editing, viewers,
analysis tools, figures, jobs and sharing projects.

Your library normally lives in `%USERPROFILE%\Cladia`. Analysis tools and the
Python environment are managed separately under `%LOCALAPPDATA%\Cladia`.
Project ZIPs exclude settings, credentials, tool installations, local BLAST
databases and original external reads. Bring those reads separately if you need
to rerun a mapping.

## Optional tools

Some tools are optional: Cladia includes their code, and **Settings → Tools** downloads
the tool itself when you install it, from bioconda at an exact version with every
package checked (**Clustal Omega**, **GetOrganelle**, **Krait repeat finder**, **MUSCLE**, **MACSE**, **RAxML-NG**, **MrBayes**, **tRNAscan-SE**, **ARAGORN**, **trimAl**, **ClipKIT**). Once
installed they appear in their tool dialogs; they can be updated or uninstalled there.
Built-in tools are listed too, with details and storage information. Adding a tool:
see [the integration guide](docs/FEATURE_GUIDE.md) and
[optional tools from bioconda](docs/PLAN_BIOCONDA_TOOLS.md).

## Development and project status

- [Contributing and running checks](CONTRIBUTING.md)
- [Current priorities and limitations](docs/PROJECT_REVIEW.md)
- [Tool and viewer integration guide](docs/FEATURE_GUIDE.md)
- [Tool versions, sources and checks](docs/TOOL_VERSION_AUDIT.md)
- [Documentation index](docs/README.md)

Detailed design plans and implementation history are linked from the
[documentation index](docs/README.md); they are not all outstanding work.

## License

Cladia is released under the [MIT License](LICENSE). It runs other programs
(MAFFT, IQ-TREE, BLAST+, minimap2, samtools, the optional bioconda tools and
others) as separate programs, downloaded on your PC under their own licenses;
none of them is included in this repository. See
[the license audit](docs/LICENSE_AUDIT.md) for each one, and cite the tools you
use: every job's log lists their citations.
