"""trimAl: removes gappy or poorly aligned columns from an alignment.

An optional tool: Settings › Tools › Install downloads trimAl 1.5.1 from bioconda
into its own environment (conda.py; lock: locks/trimal-1.5.1.lock). The result
is a new alignment; every row is kept (-keepseqs), even one left with only gaps.
"""
import re

from cladia import engine_api as api

VERSION = "1.5.1"
METHODS = {"gappyout": "Gappyout (from the gap distribution; the usual choice)",
           "strict": "Strict (gaps and similarity)", "strictplus": "Strict plus (stricter, for trees)",
           "automated1": "Automated (chooses by the alignment; for ML trees)",
           "gt": "Gap threshold (keep columns with enough rows without a gap)",
           "nogaps": "No gaps (remove every column with a gap)"}
ENGINE = {
    "id": "trimal", "label": "trimAl", "order": 10,
    "blurb": "Trim columns by gaps and similarity, with automatic or set thresholds.",
    "supports": ["align.trim"], "optional": True, "lane": "compute", "cores": 1, "jobKind": "trimal",
    "conda": {"version": VERSION, "spec": "trimal=1.5.1"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/trimal-*"],
                "estimateBytes": 30_000_000},
    "options": [
        {"key": "method", "label": "Method", "type": "choice", "default": "gappyout",
         "choices": [{"value": key, "label": label} for key, label in METHODS.items()]},
        {"key": "gap_threshold", "label": "Rows without a gap (fraction, 0-1)", "type": "float", "default": 0.8,
         "min": 0, "max": 1, "visibleWhen": {"key": "method", "values": ["gt"]}},
    ],
}
CITATIONS = [{"tool": "trimAl", "reference": "Capella-Gutiérrez et al. (2009), Bioinformatics 25:1972",
              "url": "https://doi.org/10.1093/bioinformatics/btp348"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["trimal", "--version"]), workdir)
    if not re.search(r"trimAl v1\.5\.rev1\b", text):
        raise RuntimeError(f"Expected trimAl {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "trimAl", VERSION, CITATIONS)


def run(job, workdir, operation, items, output, add_to=None, method="gappyout", gap_threshold=0.8):
    if operation != "align.trim" or len(items) != 1:
        raise ValueError("trimAl trims one alignment")
    if method not in METHODS or not isinstance(gap_threshold, (int, float)) or not 0 <= gap_threshold <= 1:
        raise ValueError("Invalid trimAl options")
    api.steps(job, "Trimming", "Saving alignment")
    records, rows = api.alignment_from_item(items[0])
    before = len(records[0].seq)
    api.write_fasta(records, workdir / "input.fa")
    _version(job, workdir)
    args = ["trimal", "-in", "/data/input.fa", "-out", "/data/trimmed.fa", "-fasta", "-keepseqs", f"-{method}"]
    if method == "gt":
        args.append(f"{gap_threshold:g}")
    api.run_container(job, api.tools_image(), api.conda_command(ENGINE, args), workdir)
    if not (workdir / "trimmed.fa").exists() or not (workdir / "trimmed.fa").read_text(encoding="utf-8").strip():
        raise RuntimeError("trimAl removed every column; try a less strict method")
    trimmed = api.alignment_back(workdir / "trimmed.fa", rows, program="trimAl")
    after = len(trimmed[0].seq)
    job["log"] += f"{METHODS[method].split(' (')[0]}: kept {after:,} of {before:,} columns ({100 * after / before:.1f} %)\n"
    api.step(job, 1)
    api.write_fasta(trimmed, workdir / "result.fa")
    api.add_output(job, workdir / "result.fa", output, kind="alignment")
