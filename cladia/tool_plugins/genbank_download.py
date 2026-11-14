"""GenBank nucleotide download through NCBI accession lookup."""
from io import StringIO
from Bio import SeqIO
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "builtin"
    },
    "id": "genbank_download", "label": "NCBI GenBank", "blurb": "Fetch nucleotide records and annotations from NCBI.",
    "supports": ["download.accessions", "download.search"], "order": 10,
    "lane": "download", "cores": 0, "jobKind": "download",
    "options": [{"key": "as_list", "label": "Several records", "type": "choice", "default": True,
                 "choices": [{"value": True, "label": "One named sequence list"},
                             {"value": False, "label": "Separate sequences"}]}],
}


EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

BATCH = 100

def run(job, workdir, operation, items, output, add_to=None, accessions=None, as_list=True):
    return run_download(job, workdir, accessions=accessions, output=output, as_list=as_list)


def run_download(job, workdir, accessions, output, as_list):
    """GenBank records (with their sequences: rettype gbwithparts) fetched from
    NCBI nuccore by accession, in batches; one sequence list, or a sequence each.
    Accessions NCBI doesn't return are listed in the log."""
    records = []
    for i in range(0, len(accessions), BATCH):
        if job.get("cancel"):
            raise RuntimeError("cancelled")
        batch = accessions[i:i + BATCH]
        if not i:
            api.steps(job, "Downloading")
        api.step(job, 0, i / len(accessions), f"{i:,} of {len(accessions):,} records")
        job["log"] += f"Fetching {', '.join(batch[:5])}{'…' if len(batch) > 5 else ''} ({len(batch)})\n"
        try:  # spaced to NCBI's limit; brief server errors are retried
            text = api.ncbi_post(EFETCH, {"db": "nuccore", "rettype": "gbwithparts", "retmode": "text",
                                          **api.ncbi_identity(), "id": ",".join(batch)}, timeout=180)
        except ValueError as e:  # a bad accession makes NCBI refuse the whole batch
            raise RuntimeError(str(e)) from None
        records += list(SeqIO.parse(StringIO(text), "genbank"))
    got = {a.lower() for r in records for a in [r.id, r.id.split(".")[0], *r.annotations.get("accessions", [])]}
    missing = [a for a in accessions if a.lower() not in got and a.lower().split(".")[0] not in got]
    job["log"] += f"{len(records)} record{'s' if len(records) != 1 else ''} downloaded.\n"
    if missing:
        job["log"] += f"Not found in GenBank (nucleotide): {', '.join(missing)}\n"
    if not records:
        raise RuntimeError("Nothing downloaded" + (f": not found: {', '.join(missing)}" if missing else ""))
    if as_list and len(records) > 1:
        api.write_record(records, workdir / "download.gb")
        api.add_output(job, workdir / "download.gb", output, kind="list", extra={"name": output})
    else:
        for i, record in enumerate(records):
            api.write_record(record, workdir / f"{i}.gb")
            api.add_output(job, workdir / f"{i}.gb", record.name, kind="sequence")
