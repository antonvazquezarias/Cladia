"""Local BLAST engine using databases installed in Cladia settings."""
import json
import time
from pathlib import Path
from cladia import engine_api as api

PROGRAMS = [
    {"value": "megablast", "label": "megablast: highly similar sequences (fast)", "queryType": "dna"},
    {"value": "dc-megablast", "label": "discontiguous megablast: more distant sequences", "queryType": "dna"},
    {"value": "blastn", "label": "blastn: somewhat similar, short queries", "queryType": "dna"},
    {"value": "blastx", "label": "blastx: translated, against proteins", "queryType": "dna"},
    {"value": "blastp", "label": "blastp: against proteins", "queryType": "protein"},
    {"value": "tblastn", "label": "tblastn: against translated nucleotides", "queryType": "protein"},
]
DNA_PROGRAMS = ["megablast", "dc-megablast", "blastn", "tblastn"]
PROTEIN_PROGRAMS = ["blastx", "blastp"]
DATABASES = [{"value": f"local:{db['name']}", "label": db["name"],
              "when": {"key": "program", "values": PROTEIN_PROGRAMS if db["type"] == "prot" else DNA_PROGRAMS}}
             for db in api.local_databases() if not db["missing"]]
if not DATABASES:
    DATABASES = [{"value": "", "label": "No local databases; add one in Settings → BLAST databases"}]
ENGINE = {
    "catalogLabel": 'BLAST+ (local)',
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "paths": [
            "/usr/local/bin/*blast*",
            "/usr/local/bin/makembindex",
            "/usr/local/bin/makeprofiledb",
            "/usr/local/bin/convert2blastmask",
            "/usr/local/bin/dustmasker",
            "/usr/local/bin/segmasker",
            "/usr/local/bin/windowmasker"
        ],
        "estimateBytes": 800000000,
        "note": "BLAST+ files; local databases are counted separately in BLAST databases."
    },
    "id": "local_blast", "label": "Local · this PC", "tabLabel": "Local", "tabOrder": 10,
    "blurb": "Runs on this PC using your local BLAST databases. Sequences stay on this computer.",
    "supports": ["blast.search"], "order": 20, "lane": "compute", "cores": None, "jobKind": "localblast",
    "options": [
        {"key": "program", "label": "Program", "type": "choice", "default": "megablast", "choices": PROGRAMS},
        {"key": "database", "label": "Database", "type": "choice", "default": DATABASES[0]["value"],
         "choices": DATABASES, "required": True},
        {"key": "max_hits", "label": "Hits at most", "type": "choice", "default": 50, "choices": [
            {"value": n, "label": str(n)} for n in (20, 50, 100, 250)]},
    ],
}


LOCAL_PROGRAMS = {  # name in Cladia -> BLAST+ program and options
    "megablast": ["blastn", "-task", "megablast"], "dc-megablast": ["blastn", "-task", "dc-megablast"],
    "blastn": ["blastn", "-task", "blastn"], "blastx": ["blastx"], "blastp": ["blastp"], "tblastn": ["tblastn"],
}

def run(job, workdir, operation, items, output, add_to=None, **kwargs):
    return run_search(job, workdir, **kwargs)


def run_search(job, workdir, query, program, db, max_hits=50, code=1):
    """db: an entry of the local databases' list ({name, path, type})."""
    api.steps(job, "Searching")
    api.ensure_tools_image(job)
    job["progress"]["detail"] = ""
    (workdir / "query.fasta").write_text(f">query\n{query['sequence']}\n", encoding="utf-8")
    base = Path(db["path"])
    job["log"] += f"BLAST of {query['name']} ({len(query['sequence']):,} letters) on this PC: {program}, {db['name']}\n"
    api.run_container(job, api.tools_image(), [*LOCAL_PROGRAMS[program], "-query", "/data/query.fasta", "-db", f"/db/{base.name}",
                                    "-out", "/data/out.json", "-outfmt", "15", "-evalue", "0.05",
                                    "-max_target_seqs", str(max_hits), "-num_threads", str(job["cores"]),
                                    *(["-query_gencode", str(code)] if program == "blastx" else [])],
          workdir, mounts={str(base.parent): "/db"})
    result = api.parse_blast(json.loads((workdir / "out.json").read_text(encoding="utf-8")))
    result.update(query={k: query.get(k) for k in ("name", "item", "start", "end")}, program=program,
                  database=f"{db['name']} (this PC)", entrez="", local=True, date=time.strftime("%Y-%m-%d %H:%M"))
    job["log"] += f"{len(result['hits'])} hits.\n"
    new_id = api.add_blast(result, f"{query['name']} BLAST")
    job["outputs"].append({"id": new_id, "label": api.get_item(new_id)["label"], "kind": "blast"})
