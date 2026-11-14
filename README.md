# Cladia

A desktop workbench for sequences, alignments, genome maps and phylogenetic trees.
Keep a local sequence library, run analysis tools, inspect results and prepare
figures in one application. Cladia runs on Windows 10 and 11.

## What you can do

- **Organize sequences:** import GenBank, EMBL, FASTA, GFF3, SnapGene and Sanger
  traces; manage folders, annotations and metadata in a local library.
- **Inspect and edit:** linear and circular sequence views, alignment gap editing,
  GC-content and GC-skew tracks, read mappings and assembly graphs.
- **Run analyses:** alignment with MAFFT, de novo annotation with MFannot, annotation
  from a reference, trees with IQ-TREE or FastTree, read mapping with minimap2,
  local or NCBI BLAST, primer design and in-silico PCR.
- **Prepare figures:** edit tree, genome-map and genome-comparison figures;
  export SVG, PDF, PNG or TIFF.
- **Share work:** export a whole project as one ZIP file for a colleague.

## Installing

Download **Cladia-setup-&lt;date&gt;.exe** from the
[latest release](https://github.com/antonvazquezarias/Cladia/releases/latest) and run it.
It installs for your user only, without administrator rights, and adds Cladia to
the Start menu. Choose **Custom** to add optional tools at the same time.
Uninstalling keeps your library.

The analysis tools run in Linux through WSL 2, which the installer sets up if your
PC doesn't have it yet. That is the one step Windows asks permission for, and it may
need a restart. Each tool is downloaded the first time you use it, so the first run
of a tool takes longer.

The installer isn't code-signed yet, so Windows SmartScreen may warn about it:
choose *More info › Run anyway*. See the
**[installation guide](docs/INSTALLATION.md)** for details and troubleshooting.

## Using it

The **[user guide](docs/USER_GUIDE.md)** (also under **?** in the app) covers
importing, editing, viewers, analysis tools, figures, jobs and sharing projects.

Imported files are copied into your library (`%USERPROFILE%\Cladia`); Cladia
works on those copies and never changes the originals. Edits keep saved versions
you can go back to, but these live beside the library: they are not a backup.

## More tools

**Settings › Tools** lists every tool. Optional ones are downloaded when you install
them there: Clustal Omega, MUSCLE, MACSE, trimAl, ClipKIT, RAxML-NG, MrBayes,
tRNAscan-SE, ARAGORN, GetOrganelle and the Krait repeat finder. Once installed,
they appear in the tool dialogs.

## Development

To run Cladia from the source code, or to contribute, see
[CONTRIBUTING.md](CONTRIBUTING.md) and the [documentation index](docs/README.md).

## License

Cladia is released under the [MIT License](LICENSE). The analysis programs it
runs are downloaded onto your PC under their own licenses; none is included in
this repository (see [the license audit](docs/LICENSE_AUDIT.md)). Please cite the
tools you use: every job's log lists their citations.
