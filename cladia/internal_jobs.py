"""Core graph comparison and submission-validation workers."""
import re
import time
from collections import Counter
from . import config, library, submission
from . import execution
from .job_progress import steps, step

def graph_blast_job(job, workdir, graph_item, queries, task="blastn"):
    steps(job, "Searching")
    data = library.graph_data(graph_item)
    names = [n for n, node in data["nodes"].items() if node["seq"]]
    if not names:
        raise RuntimeError("the graph's nodes have no sequences in the file")
    with open(workdir / "nodes.fasta", "w", encoding="utf-8") as out:
        for i, n in enumerate(names):
            out.write(f">n{i}\n{data['nodes'][n]['seq']}\n")
    records = [(item, r) for item in queries for r in library.records(item)]
    with open(workdir / "queries.fasta", "w", encoding="utf-8") as out:
        for i, (_, r) in enumerate(records):
            out.write(f">q{i}\n{r.seq}\n")
    job["log"] += f"{len(records)} quer{'y' if len(records) == 1 else 'ies'} against {len(names):,} nodes ({task})\n"
    execution.tool(job, config.TOOLS_IMAGE, ["makeblastdb", "-in", "/data/nodes.fasta", "-dbtype", "nucl", "-out", "/data/nodes"], workdir)
    execution.tool(job, config.TOOLS_IMAGE, ["blastn", *GRAPH_TASKS[task], "-query", "/data/queries.fasta", "-db", "/data/nodes",
                                    "-out", "/data/hits.tsv", "-evalue", "1e-5", "-max_target_seqs", "100000",
                                    "-num_threads", str(job["cores"]),
                                    "-outfmt", "6 qseqid sseqid pident length qstart qend sstart send evalue bitscore"], workdir)
    hits = []
    for line in (workdir / "hits.tsv").read_text(encoding="utf-8").splitlines():
        q, s, pident, length, qs, qe, ss, se, evalue, bits = line.split("\t")
        hits.append({"query": int(q[1:]), "node": names[int(s[1:])], "identity": float(pident), "length": int(length),
                     "qstart": int(qs), "qend": int(qe), "start": int(ss), "end": int(se),
                     "evalue": float(evalue), "bits": float(bits)})
    hits.sort(key=lambda h: -h["bits"])
    if len(hits) > GRAPH_HITS_MAX:
        job["log"] += f"{len(hits):,} hits: the best {GRAPH_HITS_MAX:,} kept\n"
        hits = hits[:GRAPH_HITS_MAX]
    result = {"date": time.strftime("%Y-%m-%d %H:%M"), "task": task,
              "queries": [{"name": r.name if len(records) > 1 else item["name"], "item": item["id"], "length": len(r)}
                          for item, r in records],
              "hits": hits}
    library.set_graph_hits(graph_item, result)
    found = len({h["node"] for h in hits})
    job["log"] += f"{len(hits):,} hits on {found:,} nodes\n"
    job["outputs"].append({"id": graph_item["id"], "label": graph_item["label"], "kind": "graph", "updated": True})

def validate_job(job, workdir, records, prefix="", drop_cladia_notes=True, template=None):
    """NCBI's table2asn on the submission files of these records (and the
    submission template, .sbt, when a submitter was chosen): its validation
    messages and discrepancy report go to the job log (the files are made again
    when you export)."""
    steps(job, "Writing the files", "Running table2asn")
    dropped = Counter()
    fsa, tbl = submission.files(records, prefix, drop_cladia_notes, dropped)
    if dropped:
        job["log"] += ("Left out of the files, not GenBank qualifiers: "
                       + ", ".join(f"/{k} x{n}" for k, n in sorted(dropped.items())) + "\n")
    (workdir / "sub.fsa").write_text(fsa, encoding="utf-8")
    (workdir / "sub.tbl").write_text(tbl, encoding="utf-8")
    execution.ensure_tools_image(job)
    step(job, 1, None, "table2asn")
    args = ["table2asn", "-i", "/data/sub.fsa", "-f", "/data/sub.tbl", "-o", "/data/sub.sqn", "-V", "vb", "-Z"]
    if template:
        (workdir / "sub.sbt").write_text(template, encoding="utf-8")
        args += ["-t", "/data/sub.sbt"]
    if prefix:
        args += ["-locus-tag-prefix", prefix]
    execution.ensure_image(job, config.TOOLS_IMAGE)
    code, out, err, cmd = execution.run_container(config.TOOLS_IMAGE, args, workdir, name=f"cladia-{job['id']}")
    job["log"] += "$ " + " ".join(cmd) + "\n" + out + err
    if "not found" in err and "table2asn" in err:
        raise RuntimeError("table2asn isn't in Cladia's Linux yet: it is added when the tools are updated")
    problems = len(re.findall(r"^\s*(?:Error|Warning):", out + err, re.M))  # its messages on screen
    for name in ("sub.val", "sub.dr"):
        path = workdir / name
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            problems += len(re.findall(r"ERROR|WARNING", text)) if name == "sub.val" else 0
            job["log"] += f"\n── {name} ──\n{text}"
    job["log"] += (f"\ntable2asn finished (exit {code}); {problems} error/warning line"
                   f"{'' if problems == 1 else 's'} in the validation report\n")

GRAPH_HITS_MAX = 20000

GRAPH_TASKS = {"blastn": ["-task", "blastn", "-dust", "no"], "megablast": ["-task", "megablast"]}
