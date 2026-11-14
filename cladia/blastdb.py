"""Local BLAST databases (Settings > BLAST databases), for BLAST on this PC.

A database is a set of files sharing a name (name.nsq, name.nhr, ... for
nucleotides; .psq, .phr ... for proteins; big ones in volumes, name.00.nsq).
Cladia's own (downloaded from GenBank, or made from a FASTA file) live in
"Blast databases" in the Cladia folder, one folder each; databases you already
have stay where they are (they can be hundreds of GB) and are only listed.
The list is the "blast_databases" setting: {name, path (the files' path without
the ending), type (nucl/prot), own, sequences, query, added}.
"""
from . import execution
from . import job_progress

import re
import shutil
import time
from pathlib import Path

from . import config, library, ncbi
from .genbank import decode, looks_protein, sniff

ENDINGS = {"nsq": "nucl", "nin": "nucl", "nhr": "nucl", "nal": "nucl", "ndb": "nucl",
           "psq": "prot", "pin": "prot", "phr": "prot", "pal": "prot", "pdb": "prot"}

# Estimates, measured on real downloads (621 mitochondria: 4.7 MB, 9 s; 20,925
# rbcL: 12.3 MB, 68 s): NCBI sends FASTA at ~5 MB/s, plus ~2 s a request and
# ~2.5 ms a record; a v5 nucleotide database with sequence ids is 2 bits a base
# plus each sequence's title and ~190 bytes of indexes
BATCH_BASES = 20e6     # bases fetched per request (records: 20 to 5000)
SPEED = 5e6            # bytes a second from NCBI
REQUEST = 2.0          # seconds a request costs besides its bytes
RECORD = 0.0025        # and each record
PER_SEQUENCE = 190     # database bytes a sequence costs besides its bases and title
FIXED = 80e3           # and the index files' own minimum (10 mitochondria: 114 kB)


def folder():
    return config.DATA_DIR / "Blast databases"


def _files(base):
    """A database's files: base.* (volumes too: base.00.nsq)."""
    base = Path(base)
    return [p for p in base.parent.glob(base.name + ".*") if p.is_file()] if base.parent.is_dir() else []


def databases():
    """The list, with each one's size on disk (missing: its files are gone)."""
    result = []
    for db in config.setting("blast_databases"):
        files = _files(db["path"])
        result.append({**db, "size": sum(p.stat().st_size for p in files), "missing": not files})
    return result


def _save(dbs):
    config.save_settings({"blast_databases": dbs})


def _check_name(name):
    name = name.strip()
    if not name:
        raise ValueError("A database needs a name")
    if name.lower() in (db["name"].lower() for db in config.setting("blast_databases")):
        raise ValueError(f"There is a database called {name} already")
    return name


def _register(name, path, dbtype, own, **extra):
    db = {"name": name, "path": str(path), "type": dbtype, "own": own, "added": time.strftime("%Y-%m-%d"), **extra}
    _save(config.setting("blast_databases") + [db])
    return db


def remove(name):
    """Off the list; Cladia's own are deleted, others' files are left alone."""
    dbs = config.setting("blast_databases")
    db = next((d for d in dbs if d["name"] == name), None)
    if db is None:
        raise ValueError(f"No database called {name}")
    if db["own"] and Path(db["path"]).parent.parent == folder():
        shutil.rmtree(Path(db["path"]).parent, ignore_errors=True)
    _save([d for d in dbs if d["name"] != name])


# ── Adding one you have ──────────────────────────────────────────────────

def kind_of(path):
    """("database", base path, type) for a file of a BLAST database,
    ("fasta", path, type) for a FASTA file (made into a database), else ValueError."""
    path = Path(path.strip().strip('"'))
    if not path.is_file():
        raise ValueError(f"Not found: {path}")
    parts = path.name.split(".")
    if len(parts) > 1 and parts[-1].lower() in ENDINGS:
        stem = parts[:-1]
        if len(stem) > 1 and re.fullmatch(r"\d+", stem[-1]):  # a volume (name.00.nsq): the whole database
            stem = stem[:-1]
        return "database", path.parent / ".".join(stem), ENDINGS[parts[-1].lower()]
    text = decode(path.open("rb").read(200000))
    if sniff(text) == "fasta":
        bases = "".join(line for line in text.splitlines()[:2000] if not line.startswith(">"))
        return "fasta", path, "prot" if looks_protein(bases) else "nucl"
    raise ValueError(f"{path.name} is neither a BLAST database file (.nsq, .nal, .psq, .pal...) nor FASTA")


def add_existing(path, name):
    """A database where it is: listed, not copied."""
    _, base, dbtype = kind_of(path)
    return _register(_check_name(name), base, dbtype, own=False)


# ── Making one: from FASTA, or downloaded from GenBank ───────────────────

def _make(job, workdir, fasta, name, dbtype, ids, **extra):
    """makeblastdb on workdir/fasta, the database moved into its own folder here.
    ids: the FASTA's names are sequence ids (NCBI's accessions), so hits can be
    looked up by them; a user's FASTA may have names makeblastdb refuses as ids."""
    execution.ensure_tools_image(job)
    safe = library.safe_name(name)
    target = library.unique_path(folder() / safe)
    (workdir / "db").mkdir()
    job["log"] += "Making the BLAST database…\n"
    job_progress.step(job, len(job["progress"]["steps"]) - 1)
    execution.tool(job, config.TOOLS_IMAGE, ["makeblastdb", "-in", f"/data/{fasta}", "-dbtype", dbtype,
                                         *(["-parse_seqids"] if ids else []), "-title", name,
                                         "-out", f"/data/db/{safe}", "-blastdb_version", "5"], workdir)
    target.mkdir(parents=True)
    for p in (workdir / "db").iterdir():
        shutil.move(str(p), target / p.name)
    db = _register(name, target / safe, dbtype, own=True, **extra)
    size = sum(p.stat().st_size for p in _files(db["path"]))
    job["log"] += f"Database {name}: {size / 1e6:.1f} MB in {target}\n"


def build_job(job, workdir, path, name):
    """A FASTA file you have, made into a database here."""
    job_progress.steps(job, "Making the database")
    _, path, dbtype = kind_of(path)
    shutil.copyfile(path, workdir / "seqs.fasta")
    count = sum(line.startswith(">") for line in (workdir / "seqs.fasta").open(encoding="utf-8", errors="replace"))
    job["log"] += f"{count} sequences from {path}\n"
    _make(job, workdir, "seqs.fasta", name, dbtype, ids=False, sequences=count, source=str(path))


def _plan(query):
    """A search on NCBI's history server, and what downloading it means."""
    count, webenv, key = ncbi.history(query)
    if not count:
        raise ValueError("NCBI finds nothing for that search")
    mean, title = ncbi.sample(webenv, key, count)
    batch = int(max(20, min(5000, BATCH_BASES / max(mean, 1))))
    return {"count": count, "webenv": webenv, "key": key, "mean": mean, "title": title, "batch": batch}


def estimate(query):
    """How big the database and how long the download would be (from a sample)."""
    plan = _plan(query)
    count, mean, title = plan["count"], plan["mean"], plan["title"]
    fasta = count * (mean * (1 + 1 / 70) + title + 3)  # FASTA lines of 70 bases
    requests = -(-count // plan["batch"])
    return {"count": count, "bases": round(count * mean), "mean": round(mean),
            "size": round(FIXED + count * (mean / 4 + title + PER_SEQUENCE)), "download": round(fasta),
            "seconds": round(requests * (REQUEST + ncbi.pause()) + count * RECORD + fasta / SPEED + fasta / 50e6 + 10)}


def download_job(job, workdir, query, name):
    """Every record of a GenBank (nucleotide) search, as FASTA, in batches, then
    made into a database here."""
    plan = _plan(query)
    batch = plan["batch"]
    count, webenv, key = ncbi.history(query)  # a fresh one: NCBI won't fetch from one esummary has read
    job["log"] += f"{count:,} records for “{query}”, {batch} per request\n"
    job_progress.steps(job, "Downloading", "Making the database")
    got = 0
    with open(workdir / "seqs.fasta", "w", encoding="utf-8", newline="\n") as out:
        for start in range(0, count, batch):
            if job.get("cancel"):
                raise RuntimeError("cancelled")
            text = ncbi.fetch_fasta(webenv, key, start, batch)
            out.write(text if text.endswith("\n") else text + "\n")
            got += text.count("\n>") + text.startswith(">")
            job["log"] += f"{got:,} of {count:,} records\n"
            job_progress.step(job, 0, got / count, f"{got:,} of {count:,} records")
    if not got:
        raise RuntimeError("NCBI sent no sequences")
    _make(job, workdir, "seqs.fasta", name, "nucl", ids=True, sequences=got, query=query)
