"""Online NCBI BLAST engine."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from cladia import engine_api as api

PROGRAMS = [
    {"value": "megablast", "label": "megablast: highly similar sequences (fast)", "queryType": "dna"},
    {"value": "dc-megablast", "label": "discontiguous megablast: more distant sequences", "queryType": "dna"},
    {"value": "blastn", "label": "blastn: somewhat similar, short queries", "queryType": "dna"},
    {"value": "blastx", "label": "blastx: translated, against proteins", "queryType": "dna"},
    {"value": "blastp", "label": "blastp: against proteins", "queryType": "protein"},
    {"value": "tblastn", "label": "tblastn: against translated nucleotides", "queryType": "protein"},
]
DATABASES = [
    {"value": "core_nt", "label": "Core nucleotide (core_nt)", "when": {"key": "program", "values": ["megablast", "dc-megablast", "blastn", "tblastn"]}},
    {"value": "nt", "label": "Nucleotide collection (nt)", "when": {"key": "program", "values": ["megablast", "dc-megablast", "blastn", "tblastn"]}},
    {"value": "refseq_rna", "label": "RefSeq RNA", "when": {"key": "program", "values": ["megablast", "dc-megablast", "blastn", "tblastn"]}},
    {"value": "nr", "label": "Non-redundant proteins (nr)", "when": {"key": "program", "values": ["blastx", "blastp"]}},
    {"value": "refseq_protein", "label": "RefSeq proteins", "when": {"key": "program", "values": ["blastx", "blastp"]}},
    {"value": "swissprot", "label": "UniProtKB/Swiss-Prot", "when": {"key": "program", "values": ["blastx", "blastp"]}},
]
ENGINE = {
    "catalogLabel": 'BLAST (NCBI)',
    "storage": {
        "kind": "remote"
    },
    "id": "ncbi_blast", "label": "NCBI online", "tabLabel": "Online", "tabOrder": 20,
    "blurb": "Sends query sequences to NCBI over the internet. Searches use NCBI databases and may wait in its queue.",
    "supports": ["blast.search"], "order": 10, "lane": "blast", "cores": 0, "jobKind": "blast",
    "options": [
        {"key": "program", "label": "Program", "type": "choice", "default": "megablast", "choices": PROGRAMS},
        {"key": "database", "label": "Database", "type": "choice", "default": "core_nt", "choices": DATABASES},
        {"key": "entrez", "label": "Limit to", "type": "text", "default": "",
         "help": "Optional NCBI query, e.g. Rhodophyta[organism]."},
        {"key": "max_hits", "label": "Hits at most", "type": "choice", "default": 50, "choices": [
            {"value": n, "label": str(n)} for n in (20, 50, 100, 250)]},
    ],
}


BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

BLAST_POLL = 60

def run(job, workdir, operation, items, output, add_to=None, **kwargs):
    return run_search(job, workdir, **kwargs)


def _blast_request(params=None, data=None, timeout=120):
    url = BLAST_URL + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        with urllib.request.urlopen(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                    timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"NCBI BLAST said {e.code}: {e.reason}") from None
    except OSError as e:
        raise RuntimeError(f"Couldn't reach NCBI ({e}). Is this PC online?") from None

def _wait(job, seconds):
    """Sleeps, but a cancel ends it within a second."""
    for _ in range(int(seconds)):
        if job.get("cancel"):
            raise RuntimeError("cancelled")
        time.sleep(1)

def run_search(job, workdir, query, program, database, entrez="", max_hits=50, code=1):
    """query: {name, sequence, item, start, end} (start/end 1-based, of the item).
    The parsed results become a "blast" item named "<query> BLAST"."""
    put = {"CMD": "Put", "QUERY": query["sequence"], "DATABASE": database, "HITLIST_SIZE": max_hits,
           "TOOL": "Cladia", **api.BLAST_PROGRAMS[program]}
    if api.setting("ncbi_email"):
        put["EMAIL"] = api.setting("ncbi_email")  # NCBI asks for it
    if entrez:
        put["ENTREZ_QUERY"] = entrez
    if program == "blastx":
        put["QUERY_GENETIC_CODE"] = code
    job["log"] += f"Sending {query['name']} ({len(query['sequence']):,} letters) to NCBI: {program}, {database}" + \
                  (f", limited to {entrez}" if entrez else "") + "\n"
    text = _blast_request(data=put)
    rid = re.search(r"RID = (\S+)", text)
    if not rid:
        message = re.search(r'<p class="error">(.*?)</p>', text, re.S)
        raise RuntimeError("NCBI refused the search" + (f": {message.group(1).strip()}" if message else ""))
    rid = rid.group(1)
    rtoe = int((re.search(r"RTOE = (\d+)", text) or [0, 20])[1])
    job["log"] += f"Request id {rid}; NCBI expects it to take about {rtoe} s. Checked once a minute.\n"
    api.steps(job, "Waiting at NCBI")
    api.step(job, 0, detail=f"NCBI expects about {rtoe} s")
    _wait(job, max(10, rtoe))
    while True:
        status = re.search(r"Status=(\w+)", _blast_request({"CMD": "Get", "FORMAT_OBJECT": "SearchInfo", "RID": rid}))
        status = status.group(1) if status else "UNKNOWN"
        if status == "READY":
            break
        if status != "WAITING":
            raise RuntimeError(f"NCBI BLAST: the search ended with status {status} (request id {rid})")
        job["log"] += f"{time.strftime('%H:%M:%S')} still running at NCBI\n"
        waited = round(time.time() - job.get("started", time.time()))  # (the stage already says "Waiting at NCBI")
        api.step(job, 0, detail=f"{waited} s so far" if waited < 90 else f"{round(waited / 60)} min so far")
        _wait(job, BLAST_POLL)
    result = api.parse_blast(json.loads(_blast_request({"CMD": "Get", "FORMAT_TYPE": "JSON2_S", "RID": rid}, timeout=300)))
    result.update(query={k: query.get(k) for k in ("name", "item", "start", "end")}, program=program,
                  database=database, entrez=entrez, rid=rid, date=time.strftime("%Y-%m-%d %H:%M"))
    job["log"] += f"{len(result['hits'])} hits.\n"
    new_id = api.add_blast(result, f"{query['name']} BLAST")
    job["outputs"].append({"id": new_id, "label": api.get_item(new_id)["label"], "kind": "blast"})
