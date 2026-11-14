"""Open reading frame annotation engine."""
from Bio.Data import CodonTable

from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "builtin"
    },
    "id": "orfs", "label": "ORF finder", "blurb": "Find open reading frames in the selected DNA sequences.",
    "supports": ["annotate.orfs"], "order": 10, "lane": "compute", "cores": 1, "jobKind": "orfs",
    "options": [
        {"key": "min_codons", "label": "Minimum length (codons)", "type": "int", "default": 100, "min": 1},
        {"key": "code", "label": "Genetic code", "type": "choice", "default": 0, "choices": [
            {"value": 0, "label": "As in each sequence"},
            *({"value": n, "label": f"{n} · {table.names[0]}"}
              for n, table in sorted(CodonTable.unambiguous_dna_by_id.items())),
        ]},
        {"key": "both_strands", "label": "Both strands", "type": "bool", "default": True},
        {"key": "any_start", "label": "Any start codon", "type": "bool", "default": False},
        {"key": "skip_cds", "label": "Skip ORFs that overlap an existing CDS", "type": "bool", "default": True},
        {"key": "replace", "label": "Replace existing annotations", "type": "bool", "default": False},
    ],
}


def run(job, workdir, operation, items, output, add_to=None, into=None, apply=False, busy=None, **options):
    return run_annotation(job, workdir, items=items, into=into, apply=apply, busy=busy, **options)


def run_annotation(job, workdir, items, code=0, min_codons=100, both_strands=True, any_start=False,
             skip_cds=True, replace=False, into=None, apply=False, busy=None):
    """Open reading frames as ORF features on new sequences (`_orfs`), one per
    sequence, or one list for a whole list (into). code 0: each sequence's own
    genetic code; skip_cds: leave out ORFs that overlap an existing CDS;
    replace: drop the sequence's own features (the source is always kept)."""
    api.steps(job, "Finding ORFs")
    results = []
    for i, item in enumerate(items):
        if job.get("cancel"):
            raise RuntimeError("cancelled")
        record = api.records(item)[0]
        n = len(record)
        use_code = code or api.genetic_code(record)
        found = api.orfs_find_orfs(str(record.seq), use_code, min_codons, both_strands, any_start,
                               record.annotations.get("topology") == "circular")
        data = api.record_to_editor(record)
        if skip_cds:
            cds = [{"start": loc["start"], "end": loc["end"], "strand": f["strand"]}
                   for f in data["features"] if f["type"] == "CDS"
                   for loc in (f.get("locations") or [{"start": f["start"], "end": f["end"]}])]
            found = [o for o in found if not any(api.orfs_overlaps(o, c, n) for c in cds)]
        if replace:
            data["features"] = []
        for k, o in enumerate(found, 1):
            name = f"ORF {k} ({o['codons']} aa)"
            data["features"].append({
                "name": name, "type": "ORF", "start": o["start"], "end": o["end"], "strand": o["strand"],
                "notes": {"label": [name],
                          "note": [f"open reading frame, {o['codons']} codons, genetic code {use_code}"]},
            })
        data["geneticCode"] = use_code
        api.write_record(api.editor_to_record(data, original=record), workdir / f"result{i}.gb")
        job["log"] += f"{item['label']}: {len(found)} ORF{'s' if len(found) != 1 else ''} (genetic code {use_code})\n"
        results.append((item, workdir / f"result{i}.gb"))
        api.step(job, 0, (i + 1) / len(items), f"{i + 1} of {len(items)} sequences")
    api.annotated(job, workdir, results, "_orfs", into, apply, busy)
