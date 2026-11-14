"""MACSE v2: codon-aware alignment of protein-coding DNA.

An optional tool: Settings › Tools › Install downloads MACSE 2.07 (Java) from
bioconda into its own environment (conda.py; lock: locks/macse-2.07.lock).
MACSE aligns the codons, allowing for frameshifts and stop codons. It marks a
frameshift with "!" in the DNA, written here as a gap, so each row keeps its
bases; the translated alignment can be saved too ("!" as X there).
"""
from Bio.Data import CodonTable

from cladia import engine_api as api

VERSION = "2.07"
ENGINE = {
    "id": "macse", "label": "MACSE (codon-aware)", "order": 30,
    "blurb": "Aligns protein-coding DNA codon by codon, allowing for frameshifts and stop codons.",
    "supports": ["align.new"], "optional": True, "lane": "compute", "cores": 1, "jobKind": "macse",
    "conda": {"version": VERSION, "spec": "macse=2.07"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/macse-*"],
                "estimateBytes": 519_000_000, "note": "Includes a Java runtime."},
    "options": [
        {"key": "code", "label": "Genetic code", "type": "choice", "default": 0, "choices": [
            {"value": 0, "label": "As in the sequences"},
            *({"value": n, "label": f"{n} · {table.names[0]}"}
              for n, table in sorted(CodonTable.unambiguous_dna_by_id.items()))]},
        {"key": "protein", "label": "Also save the protein alignment", "type": "bool", "default": True},
    ],
}
CITATIONS = [{"tool": "MACSE", "reference": "Ranwez et al. (2018), Molecular Biology and Evolution 35:2582",
              "url": "https://doi.org/10.1093/molbev/msy159"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(),
                             api.conda_command(ENGINE, ["bash", "-c", "macse -help 2>&1 | head -5"]), workdir)
    if f"MACSE V{VERSION}" not in text:
        raise RuntimeError(f"Expected MACSE {VERSION}; reinstall it in Settings › Tools")
    api.record_tool(job, "MACSE", VERSION, CITATIONS)


def run(job, workdir, operation, items, output, add_to=None, code=0, protein=True):
    if operation != "align.new" or add_to is not None or len(items) < 2:
        raise ValueError("MACSE needs at least two coding sequences for a new alignment")
    if code and code not in CodonTable.unambiguous_dna_by_id:
        raise ValueError(f"Unknown genetic code: {code}")
    if any(item.get("protein") for item in items):
        raise ValueError("MACSE aligns protein-coding DNA; choose DNA sequences, not proteins")
    api.steps(job, "Preparing sequences", "Aligning codons", "Saving alignment")
    records, rows = api.alignment_from_items(items)
    if not code:
        codes = [api.genetic_code(api.records(item)[0]) for item in items]
        code = max(set(codes), key=codes.count)
        if len(set(codes)) > 1:
            job["log"] += f"The sequences use different genetic codes; using {code}, the most common.\n"
    job["log"] += f"Genetic code {code}\n"
    _version(job, workdir)
    api.write_fasta(records, workdir / "input.fa")
    api.step(job, 1, detail="MACSE gives no progress; large sets can take a long time")
    args = ["macse", "-prog", "alignSequences", "-seq", "/data/input.fa", "-gc_def", str(code),
            "-out_NT", "/data/nt.fa", "-out_AA", "/data/aa.fa"]
    api.tool_watched(job, api.conda_command(ENGINE, args), workdir, lambda text, progress: None, stdout=True)
    api.step(job, 2, detail="Checking aligned sequences")
    for name in ("nt.fa", "aa.fa"):  # "!" = a frameshift: a gap in the DNA, X in the protein
        path = workdir / name
        path.write_text(path.read_text(encoding="utf-8").replace("!", "-" if name == "nt.fa" else "X"), encoding="utf-8")
    aligned = api.alignment_back(workdir / "nt.fa", rows, {r.id: str(r.seq) for r in records}, "MACSE")
    api.write_fasta(aligned, workdir / "result.fa")
    api.add_output(job, workdir / "result.fa", output, kind="alignment", extra={"sources": api.alignment_sources(rows)})
    if protein:
        api.write_fasta(api.alignment_back(workdir / "aa.fa", rows, program="MACSE"), workdir / "protein.fa")
        api.add_output(job, workdir / "protein.fa", f"{output} protein", kind="alignment")
