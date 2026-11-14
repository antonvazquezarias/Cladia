"""ClipKIT: trims an alignment, keeping the phylogenetically informative columns.

An optional tool: Settings › Tools › Install downloads ClipKIT 2.14.0 from
bioconda into its own environment (conda.py; lock: locks/clipkit-2.14.0.lock).
The result is a new alignment.
"""
from cladia import engine_api as api

VERSION = "2.14.0"
MODES = {"smart-gap": "Smart-gap (gap threshold chosen from the data; the default)",
         "kpic-smart-gap": "Keep informative and constant sites, then smart-gap",
         "kpi-smart-gap": "Keep parsimony-informative sites, then smart-gap",
         "gappy": "Gappy (columns above a gap threshold)",
         "kpic": "Keep parsimony-informative and constant sites only",
         "kpi": "Keep parsimony-informative sites only"}
ENGINE = {
    "id": "clipkit", "label": "ClipKIT", "order": 20,
    "blurb": "Trim columns while keeping the phylogenetically informative ones.",
    "supports": ["align.trim"], "optional": True, "lane": "compute", "cores": 1, "jobKind": "clipkit",
    "conda": {"version": VERSION, "spec": "clipkit=2.14.0"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/clipkit-*"],
                "estimateBytes": 291_000_000},
    "options": [
        {"key": "mode", "label": "Mode", "type": "choice", "default": "smart-gap",
         "choices": [{"value": key, "label": label} for key, label in MODES.items()]},
        {"key": "gaps", "label": "Gap threshold (fraction of rows, 0-1)", "type": "float", "default": 0.9,
         "min": 0, "max": 1, "visibleWhen": {"key": "mode", "values": ["gappy"]}},
    ],
}
CITATIONS = [{"tool": "ClipKIT", "reference": "Steenwyk et al. (2020), PLOS Biology 18:e3001007",
              "url": "https://doi.org/10.1371/journal.pbio.3001007"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["clipkit", "--version"]), workdir)
    if f"clipkit {VERSION}" not in text:
        raise RuntimeError(f"Expected ClipKIT {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "ClipKIT", VERSION, CITATIONS)


def run(job, workdir, operation, items, output, add_to=None, mode="smart-gap", gaps=0.9):
    if operation != "align.trim" or len(items) != 1:
        raise ValueError("ClipKIT trims one alignment")
    if mode not in MODES or not isinstance(gaps, (int, float)) or not 0 <= gaps <= 1:
        raise ValueError("Invalid ClipKIT options")
    api.steps(job, "Trimming", "Saving alignment")
    records, rows = api.alignment_from_item(items[0])
    before = len(records[0].seq)
    api.write_fasta(records, workdir / "input.fa")
    _version(job, workdir)
    args = ["clipkit", "/data/input.fa", "-o", "/data/trimmed.fa", "-of", "fasta", "-m", mode]
    if mode == "gappy":
        args += ["-g", f"{gaps:g}"]
    api.run_container(job, api.tools_image(), api.conda_command(ENGINE, args), workdir)
    trimmed = api.alignment_back(workdir / "trimmed.fa", rows, program="ClipKIT")
    after = len(trimmed[0].seq)
    if not after:
        raise RuntimeError("ClipKIT removed every column; try another mode")
    job["log"] += f"{MODES[mode].split(' (')[0]}: kept {after:,} of {before:,} columns ({100 * after / before:.1f} %)\n"
    api.step(job, 1)
    api.write_fasta(trimmed, workdir / "result.fa")
    api.add_output(job, workdir / "result.fa", output, kind="alignment")
