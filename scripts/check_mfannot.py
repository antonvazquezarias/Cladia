"""Real check of the MFannot engine, in Cladia's own Linux.

    python scripts/check_mfannot.py [genome.gb]

Annotates one mitochondrial genome (default: NC_001638, the Chlamydomonas
reinhardtii mitochondrion, downloaded from GenBank) in a private temporary
library, the first time also unpacking the MFannot image (~510 MB), and
compares the genes found with the record's own. Exits non-zero on failure.
"""
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
scratch = Path(tempfile.mkdtemp(prefix="cladia-mfannot-check-"))
os.environ["CLADIA_DATA"] = str(scratch / "data")
sys.path.insert(0, str(ROOT))

from cladia import config, jobs, library  # noqa: E402
from cladia.genbank import read_records  # noqa: E402
from cladia.tool_registry import build_registry  # noqa: E402

config.DATA_DIR = scratch / "data"
config.APP_DIR = config.DATA_DIR / ".cladia"
library.ensure_folders()

if len(sys.argv) > 1:
    genome = Path(sys.argv[1])
else:
    genome = scratch / "NC_001638.gb"
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=NC_001638&rettype=gbwithparts&retmode=text"
    genome.write_bytes(urllib.request.urlopen(url, timeout=60).read())
(item_id,), errors = library.import_paths([genome])
assert not errors, errors
item = library.get_item(item_id)
own = {f.qualifiers["gene"][0].lower() for f in read_records(item["path"], "genbank")[0].features
       if f.type == "gene" and "gene" in f.qualifiers}

engine = build_registry().engines["mfannot"]
started = time.time()
job = jobs.submit(engine["jobKind"], "check mfannot", engine["run"], resources=(engine["lane"], engine["cores"]),
                  operation="annotate.denovo", items=[item], output="check", organelle="own", apply=False)
while jobs.JOBS[job["id"]]["status"] in ("queued", "running"):
    time.sleep(1)
job = jobs.JOBS[job["id"]]
print(job["log"][-2500:])
if job["status"] != "done" or not job["outputs"]:
    sys.exit(f"MFannot job {job['status']}: {job.get('error')}")
result = read_records(library.get_item(job["outputs"][0]["id"])["path"], "genbank")[0]
found = {f.qualifiers["gene"][0].lower() for f in result.features if f.type == "gene" and "gene" in f.qualifiers}
print(f"{time.time() - started:.0f} s; types: {sorted({f.type for f in result.features})}")
print(f"genes in the record {len(own)}, found by MFannot {len(found)}, in both {len(own & found)}")
print("record only:", sorted(own - found))
print("MFannot only:", sorted(found - own))
