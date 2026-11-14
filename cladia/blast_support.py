"""Shared BLAST format and program compatibility."""

BLAST_PROGRAMS = {  # name in Cladia -> NCBI's PROGRAM and options
    "megablast": {"PROGRAM": "blastn", "MEGABLAST": "on"},
    "dc-megablast": {"PROGRAM": "blastn", "BLAST_PROGRAMS": "discoMegablast", "TEMPLATE_LENGTH": "18",
                     "TEMPLATE_TYPE": "0"},
    "blastn": {"PROGRAM": "blastn"},
    "blastx": {"PROGRAM": "blastx"},
    "blastp": {"PROGRAM": "blastp"},
    "tblastn": {"PROGRAM": "tblastn"},
}

LOCAL_DB_TYPE = {"megablast": "nucl", "dc-megablast": "nucl", "blastn": "nucl", "tblastn": "nucl",
                 "blastx": "prot", "blastp": "prot"}

def parse_blast(data):
    """NCBI's JSON2_S -> {query_length, hits: [{accession, title, organism, length,
    max_score, total_score, cover, evalue, identity}]} (the numbers of NCBI's own
    results table; the alignments themselves aren't kept)."""
    search = data["BlastOutput2"][0]["report"]["results"]["search"]
    qlen = search["query_len"]
    hits = []
    for h in search.get("hits", []):
        d = h["description"][0]
        hsps = [{"bits": s["bit_score"], "evalue": s["evalue"], "identity": s["identity"],
                 "align_len": s["align_len"], "q_from": s["query_from"], "q_to": s["query_to"]} for s in h["hsps"]]
        covered = set()
        for s in hsps:
            covered.update(range(min(s["q_from"], s["q_to"]), max(s["q_from"], s["q_to"]) + 1))
        best = max(hsps, key=lambda s: s["bits"])
        hits.append({
            "accession": d.get("accession", d.get("id", "")), "title": d.get("title", ""),
            "organism": d.get("sciname", ""), "taxid": d.get("taxid"), "length": h["len"],
            "max_score": round(best["bits"], 1), "total_score": round(sum(s["bits"] for s in hsps), 1),
            "cover": round(100 * len(covered) / qlen) if qlen else 0, "evalue": min(s["evalue"] for s in hsps),
            "identity": round(100 * best["identity"] / best["align_len"], 2) if best["align_len"] else 0,
        })
    return {"query_length": qlen, "hits": hits}
