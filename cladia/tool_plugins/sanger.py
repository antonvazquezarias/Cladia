"""Sanger assembly engine; handles one sample or name-grouped batches."""
from Bio.Seq import Seq
from Bio.SeqFeature import SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "builtin"
    },
    "id": "sanger", "label": "Sanger assembly", "blurb": "Assemble short Sanger reads and call a quality-aware consensus.",
    "supports": ["assemble.one", "assemble.batch"], "order": 10, "lane": "compute", "cores": 1,
    "jobKind": "assemble", "options": [],
}


def run(job, workdir, operation, items, output, add_to=None, samples=None):
    if operation == "assemble.batch":
        return run_batch(job, workdir, samples, output)
    return run_one(job, workdir, items, output)


def run_one(job, workdir, items, output):
    """Sanger reads assembled (api.sanger_assemble): an alignment of the reads (each
    with its source, so the view shows their traces and features) and their
    consensus as a sequence, with the places where the reads disagree marked."""
    api.steps(job, "Assembling")
    consensus = _assemble_sample(job, workdir, items, output)
    api.write_record(consensus, workdir / "consensus.gb")
    api.add_output(job, workdir / "consensus.gb", output, kind="sequence")

def run_batch(job, workdir, samples, output):
    """Many samples' reads, each sample assembled as above (samples: [(name,
    items)], paired by name: sanger.samples_by_name): an alignment per sample and
    one list of their consensus sequences, all in a new folder `output`. A sample
    that can't be assembled is noted in the log; the others go on."""
    api.steps(job, "Assembling samples")
    consensuses = []
    for n, (sample, items) in enumerate(samples):
        api.step(job, 0, n / len(samples), sample)
        job["log"] += f"\n-- {sample}\n"
        try:
            consensuses.append(_assemble_sample(job, workdir, items, sample, folder=f"s{n}"))
        except RuntimeError as e:
            job["log"] += f"{sample}: not assembled: {e}\n"
    if not consensuses:
        raise RuntimeError("No sample could be assembled (see the log)")
    api.write_record(consensuses, workdir / "consensus.gb")
    api.add_output(job, workdir / "consensus.gb", f"{output} consensus", kind="list" if len(consensuses) > 1 else "sequence",
                extra={"name": f"{output} consensus"})
    api.create_result_folder(job, output)
    job["log"] += f"\n{len(consensuses)} of {len(samples)} samples assembled, in the folder {job['folder']}\n"

def _assemble_sample(job, workdir, items, output, folder=""):
    """One sample's reads: their alignment added to the library ("<output> reads");
    returns the consensus record (the reads' disagreements as features)."""
    reads = []
    for item in items:
        record = api.records(item)[0]
        seq, quality = str(record.seq).upper(), None
        if item.get("trace"):  # the qualities of the part kept, while the read still has its length
            trace = api.trace_data(item)
            if trace["quality"] and trace["end"] - trace["start"] == len(seq):
                quality = trace["quality"][trace["start"]:trace["end"]]
        reads.append({"name": item["name"], "seq": seq, "quality": quality, "item": item["id"]})
        if quality is None:
            job["log"] += f"{item['label']}: no qualities (each base counts {api.SANGER_NO_QUALITY})\n"
    result = api.sanger_assemble(reads)
    for name, why in result["left_out"]:
        job["log"] += f"{name}: left out, {why}\n"
    if len(result["rows"]) < 2:
        raise RuntimeError("No read overlaps the longest one well enough to assemble (see the log)")
    by_name = {r["name"]: r for r in reads}
    rows, sources = [], {}
    for row in result["rows"]:
        name = row["name"].replace(" ", "_")
        rows.append(SeqRecord(Seq(row["gapped"]), id=("_R_" if row["reversed"] else "") + name, description=""))
        sources[name] = {"item": by_name[row["name"]]["item"], "offset": 0}
        job["log"] += f"{row['name']}: {'reverse complemented' if row['reversed'] else 'as it is'}\n"
    (workdir / folder).mkdir(exist_ok=True)
    api.write_fasta(rows, workdir / folder / "reads.fasta")
    api.add_output(job, workdir / folder / "reads.fasta", f"{output} reads", kind="alignment",
                extra={"sources": sources, "name": f"{output} reads"})

    consensus = SeqRecord(Seq(result["consensus"]), id=output.replace(" ", "_"), name=output.replace(" ", "_"),
                          description=f"Consensus of {len(rows)} Sanger reads ({', '.join(r['name'] for r in result['rows'])})")
    consensus.annotations.update(molecule_type="DNA", topology="linear")
    for d in result["disagreements"][:500]:
        at = min(d["at"], len(result["consensus"]) - 1)
        letters = ", ".join(f"{letter} q{q} ({name})" for letter, q, name in d["letters"])
        consensus.features.append(SeqFeature(SimpleLocation(at, at + 1, strand=1), type="misc_feature",
                                             qualifiers={"label": ["reads disagree"], "note": [f"{letters}; called {d['called']}"]}))
    job["log"] += (f"Consensus: {len(result['consensus']):,} bases from {len(rows)} reads; "
                   f"{len(result['disagreements'])} place{'' if len(result['disagreements']) == 1 else 's'} where they disagree "
                   "(marked as features)\n")
    return consensus
