"""MUSCLE 5: multiple alignment of DNA or protein sequences.

An optional tool: Settings › Tools › Install downloads MUSCLE 5.3 from bioconda
into its own environment (conda.py; lock: locks/muscle-5.3.lock). MUSCLE writes
rows in its own order; they go back in the input order, checked (align_io.py).
"""
import re

from cladia import engine_api as api

VERSION = "5.3"
ENGINE = {
    "id": "muscle", "label": "MUSCLE", "order": 15,
    "blurb": "Accurate multiple alignment of DNA or protein sequences (MUSCLE 5).",
    "supports": ["align.new"], "optional": True, "lane": "compute", "cores": None, "jobKind": "muscle",
    "conda": {"version": VERSION, "spec": "muscle=5.3"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/muscle-*"],
                "estimateBytes": 31_000_000},
    "options": [
        {"key": "algorithm", "label": "Algorithm", "type": "choice", "default": "align", "choices": [
            {"value": "align", "label": "PPP (most accurate, up to a few hundred sequences)"},
            {"value": "super5", "label": "Super5 (large sets, thousands of sequences)"}]},
    ],
}
CITATIONS = [{"tool": "MUSCLE", "reference": "Edgar (2022), Nature Communications 13:6968",
              "url": "https://doi.org/10.1038/s41467-022-34630-w"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["muscle", "-version"]), workdir)
    found = re.search(r"muscle (\d+\.\d+)", text)
    if not found or found.group(1) != VERSION:
        raise RuntimeError(f"Expected MUSCLE {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "MUSCLE", found.group(1), CITATIONS)


def _watch(text, progress):
    """MUSCLE rewrites a progress line with \\r: the latest one, and its percentage."""
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    if not lines:
        return None
    percent = re.search(r"(\d+(?:\.\d+)?)%", lines[-1])
    return {"at": 1, "fraction": float(percent.group(1)) / 100 if percent else None, "detail": lines[-1][-160:]}


def run(job, workdir, operation, items, output, add_to=None, algorithm="align"):
    if operation != "align.new" or add_to is not None or len(items) < 2:
        raise ValueError("MUSCLE needs at least two sequences for a new alignment")
    if algorithm not in ("align", "super5"):
        raise ValueError("Choose PPP or Super5")
    api.steps(job, "Preparing sequences", "Aligning", "Saving alignment")
    records, rows = api.alignment_from_items(items)
    _version(job, workdir)
    api.write_fasta(records, workdir / "input.fa")
    api.step(job, 1)
    api.watch_log(job, workdir / "run.log", _watch)
    args = ["muscle", f"-{algorithm}", "/data/input.fa", "-output", "/data/aligned.fa",
            "-threads", str(max(1, int(job.get("cores") or 1)))]
    try:
        api.run_container(job, api.tools_image(),
                          api.conda_command(ENGINE, ["bash", "-c", 'exec "$@" > /data/run.log 2>&1', "sh", *args]), workdir)
    except RuntimeError as exc:
        log = workdir / "run.log"
        detail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
        raise RuntimeError(f"{exc}\n{detail}".strip()) from None
    api.step(job, 2, detail="Checking aligned sequences")
    aligned = api.alignment_back(workdir / "aligned.fa", rows, {r.id: str(r.seq) for r in records}, "MUSCLE")
    api.write_fasta(aligned, workdir / "result.fa")
    api.add_output(job, workdir / "result.fa", output, kind="alignment", extra={"sources": api.alignment_sources(rows)})
