"""Transfer annotations from a close reference sequence."""
import shlex
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "shared",
        "uses": "local_blast",
        "sharedLabel": "BLAST+",
        "note": "Uses the same BLAST+ installation as local BLAST; no second copy."
    },
    "id": "reference_annotation", "label": "Reference transfer",
    "blurb": "Copy features from a related annotated sequence using sequence matches.",
    "supports": ["annotate.reference"], "order": 10, "lane": "compute", "cores": 1, "jobKind": "reference",
    "options": [
        {"key": "limit", "label": "Maximum difference (%)", "type": "int", "default": 30, "min": 0, "max": 100},
        {"key": "replace", "label": "Replace existing annotations", "type": "bool", "default": True},
    ],
}


def run(job, workdir, operation, items, output, add_to=None, reference=None, into=None,
        apply=False, busy=None, limit=0.3, replace=True):
    return run_annotation(job, workdir, items=items, reference=reference, into=into, apply=apply,
                             busy=busy, limit=limit, replace=replace)


def run_annotation(job, workdir, items, reference, limit, into=None, replace=False, apply=False, busy=None):
    """Copies the reference's features onto each sequence where BLAST finds them
    (see transfer.py); the results are new sequences, as with the other methods (into: a whole
    list annotated, whose results make one list of that name). One BLAST per
    sequence, several at once in one container: one search against all of them
    would give other E values (they grow with the total length searched), and
    short features near the cut-off would be lost."""
    ref = api.records(reference)[0]
    records = [api.records(item)[0] for item in items]
    text, lengths = api.transfer_queries(ref)
    (workdir / "features.fasta").write_text(text, encoding="utf-8")
    commands = []
    for i, record in enumerate(records):
        circular = record.annotations.get("topology") == "circular"  # doubled, for features across the origin
        (workdir / f"t{i}.fasta").write_text(f">target\n{record.seq}{record.seq if circular else ''}\n",
                                             encoding="utf-8")
        # -task blastn: short words, for features as short as tRNAs; no dust filter
        # (AT-rich organelle DNA would be masked)
        commands.append(shlex.join(["blastn", "-task", "blastn", "-query", "/data/features.fasta",
                                    "-subject", f"/data/t{i}.fasta", "-dust", "no", "-evalue", "1e-5",
                                    "-outfmt", f"6 {api.TRANSFER_FIELDS}", "-out", f"/data/hits{i}.tsv"]))
    api.steps(job, "Searching with BLAST", "Placing features")
    api.ensure_tools_image(job)
    job["progress"]["detail"] = ""
    api.in_parallel(job, api.tools_image(), workdir, commands, [item["label"] for item in items])

    results = []
    for i, (item, record) in enumerate(zip(items, records)):
        if job.get("cancel"):
            raise RuntimeError("cancelled")
        api.step(job, 1, i / len(items), f"{i + 1} of {len(items)} sequences" if len(items) > 1 else "")
        hits = workdir / f"hits{i}.tsv"
        if not hits.exists():
            job["log"] += f"\n{item['label']}: BLAST gave no results\n"
            continue
        n = len(record)
        data = api.record_to_editor(record)
        if replace:  # the sequence's own annotations left out: only the copied ones
            data["features"] = []
        existing = [(f["type"], -1 if f["strand"] == -1 else 1, api.transfer_spans(f.get("locations") or [f], n))
                    for f in data["features"]]
        features, report = api.transfer_transfer(ref, str(record.seq), record.annotations.get("topology") == "circular",
                                             api.transfer_read_hits(hits.read_text()), lengths, limit, existing,
                                             code=api.genetic_code(ref))
        job["log"] += (f"\n── {item['label']} ──\n" if len(items) > 1 else "") + "\n".join(report) + "\n"
        data["features"] += features
        data["geneticCode"] = api.genetic_code(ref) if any(f["type"] == "CDS" for f in features) else data["geneticCode"]
        api.write_record(api.editor_to_record(data, original=record), workdir / f"result{i}.gb")
        results.append((item, workdir / f"result{i}.gb"))
    api.annotated(job, workdir, results, "_annotated", into, apply, busy)
