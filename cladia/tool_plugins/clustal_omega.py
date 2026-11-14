"""Clustal Omega: multiple alignment of DNA, RNA or proteins.

An optional tool: Settings › Tools › Install downloads Clustal Omega 1.2.4 from
bioconda into its own environment (conda.py; lock: locks/clustal_omega-1.2.4.lock).
Rows are given opaque ids for the run and put back in the input order; the
result is checked (every row back, same residues, one length) before saving.
"""
import re
from copy import copy

from cladia import engine_api as api

VERSION = "1.2.4"
ENGINE = {
    "id": "clustal_omega", "label": "Clustal Omega", "order": 20,
    "blurb": "Multiple alignment of DNA, RNA or protein sequences with Clustal Omega.",
    "supports": ["align.new"], "optional": True, "lane": "compute", "cores": None,
    "conda": {"version": VERSION, "spec": "clustalo=1.2.4"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/clustal_omega-*"],
                "estimateBytes": 34_000_000},
    "options": [
        {"key": "sequence_type", "label": "Sequence type", "type": "choice", "default": "auto", "choices": [
            {"value": "auto", "label": "Detect automatically"}, {"value": "DNA", "label": "DNA"},
            {"value": "RNA", "label": "RNA"}, {"value": "Protein", "label": "Protein"}]},
        {"key": "iterations", "label": "Refinement iterations", "type": "int", "default": 0, "min": 0,
         "help": "Protein alignments only. Zero uses the standard progressive alignment."},
        {"key": "full", "label": "Use the full distance matrix", "type": "bool", "default": False,
         "advanced": True, "help": "Slower and uses more memory for large datasets; otherwise use mBed clustering."},
    ],
}
CITATIONS = [{"tool": "Clustal Omega", "reference": "Sievers et al. (2011)",
              "url": "https://doi.org/10.1038/msb.2011.75"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    observed = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["clustalo", "--version"]), workdir).strip()
    if observed != VERSION:
        raise RuntimeError(f"Expected Clustal Omega {VERSION}, got {observed!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "Clustal Omega", observed, CITATIONS)
    return observed


def _watch(text, progress):
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    return {"at": 1, "fraction": None, "detail": lines[-1][-160:] if lines else "Aligning sequences"}


def run(job, workdir, operation, items, output, add_to=None, sequence_type="auto", iterations=0, full=False):
    if operation != "align.new" or add_to is not None or len(items) < 2:
        raise ValueError("Clustal Omega needs at least two sequences for a new alignment")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 0 or not isinstance(full, bool):
        raise ValueError("Invalid Clustal Omega options")
    if sequence_type not in {"auto", "DNA", "RNA", "Protein"}:
        raise ValueError("Invalid sequence type")
    records, names, originals, sources, proteins = [], {}, {}, {}, []
    used = set()
    for index, item in enumerate(items):
        record = copy(api.records(item)[0])
        sequence = str(record.seq).replace("-", "").replace(".", "").upper()
        if not sequence:
            raise ValueError(f"{item['label']}: empty sequence")
        proteins.append(bool(item["protein"]) if "protein" in item else api.looks_protein(sequence))
        name = re.sub(r"\s+", "_", api.safe_name(item["name"])) or "sequence"
        while name in used:
            name += "_"
        used.add(name)
        key = f"seq{index + 1:06d}"
        names[key], originals[key], sources[name] = name, sequence, {"item": item["id"]}
        record.id, record.description = key, ""
        records.append(record)
    if sequence_type == "auto":
        # FASTA metadata heuristics can misclassify IUPAC ambiguity codes as protein.
        nucleotide = all(set(seq) <= set("ACGTRYSWKMBDHVNU") for seq in originals.values())
        if nucleotide:
            sequence_type = "RNA" if any("U" in seq for seq in originals.values()) else "DNA"
        elif any(proteins) and not all(proteins):
            raise ValueError("Choose either nucleotide sequences or proteins, not a mixture")
        else:
            sequence_type = "Protein"
    if sequence_type in {"DNA", "RNA"} and any(not set(seq) <= set("ACGTRYSWKMBDHVN" + ("U" if sequence_type == "RNA" else "T"))
                                               for seq in originals.values()):
        raise ValueError(f"An input contains letters incompatible with {sequence_type}")
    if sequence_type == "RNA" and any("T" in seq for seq in originals.values()):
        raise ValueError("Choose either DNA or RNA sequences, not a mixture")
    if sequence_type != "Protein" and iterations:
        raise ValueError("Clustal Omega refinement iterations require proteins; use zero for DNA/RNA")
    api.steps(job, "Preparing sequences", "Aligning", "Saving alignment")
    _version(job, workdir)
    job["log"] += f"Sequence type: {sequence_type}\n"
    api.write_fasta(records, workdir / "input.fa")
    api.step(job, 1)
    api.watch_log(job, workdir / "run.log", _watch)
    args = ["clustalo", "--infile=/data/input.fa", "--outfile=/data/aligned.fa", "--outfmt=fasta",
            "--output-order=input-order", "--dealign", f"--seqtype={sequence_type}",
            f"--threads={max(1, int(job.get('cores') or 1))}", f"--iterations={iterations}", "--verbose", "--force"]
    if full:
        args.append("--full")
    try:
        api.run_container(job, api.tools_image(),
                          api.conda_command(ENGINE, ["bash", "-c", 'exec "$@" > /data/run.log 2>&1', "sh", *args]), workdir)
    except RuntimeError as exc:
        log = workdir / "run.log"
        detail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
        raise RuntimeError(f"{exc}\n{detail}".strip()) from None
    if job.get("cancel"):
        raise RuntimeError("cancelled")
    api.step(job, 2, detail="Checking aligned sequences")
    aligned = api.read_records(workdir / "aligned.fa", "fasta")
    by_id = {record.id: record for record in aligned}
    if len(by_id) != len(aligned) or set(by_id) != set(names) or len({len(record.seq) for record in aligned}) != 1:
        raise RuntimeError("Clustal Omega returned incomplete or inconsistent alignment rows")
    aligned = [by_id[key] for key in names]
    for key, record in zip(names, aligned):
        if str(record.seq).replace("-", "").replace(".", "").upper() != originals[key]:
            raise RuntimeError(f"Clustal Omega changed the residues of {names[key]}; no result was saved")
        record.id, record.description = names[key], ""
    api.write_fasta(aligned, workdir / "result.fa")
    api.add_output(job, workdir / "result.fa", output, kind="alignment", extra={"sources": sources})
