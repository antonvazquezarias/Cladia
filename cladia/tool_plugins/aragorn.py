"""ARAGORN: tRNA and tmRNA genes, as gene + tRNA (or tmRNA) features.

An optional tool: Settings › Tools › Install downloads ARAGORN 1.2.41 from
bioconda into its own environment (conda.py; lock: locks/aragorn-1.2.41.lock).
Circular and linear sequences go in one search each (ARAGORN's -c / -l), so a
tRNA across a circular sequence's origin is found. Names as tRNAscan-SE's engine
gives them: gene trnL-CAG, product tRNA-Leu; introns make the tRNA two parts.
The batch-output reader is the lab's own code.
"""
import re

from cladia import engine_api as api

VERSION = "1.2.41"
MODELS = {"standard": (["-t"], "tRNAs (bacterial, plastid, nuclear)"),
          "metazoan_mito": (["-mt"], "Metazoan mitochondrial tRNAs")}
ENGINE = {
    "id": "aragorn", "label": "ARAGORN", "order": 20,
    "blurb": "Find tRNA and tmRNA genes, including tRNAs with introns.",
    "supports": ["annotate.trna"], "optional": True, "lane": "compute", "cores": 1, "jobKind": "aragorn",
    "conda": {"version": VERSION, "spec": "aragorn=1.2.41"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/aragorn-*"],
                "estimateBytes": 6_000_000},
    "citations": [("ARAGORN", "Laslett and Canback (2004), Nucleic Acids Research 32:11",
                   "https://doi.org/10.1093/nar/gkh152")],
    "options": [
        {"key": "model", "label": "Search for", "type": "choice", "default": "standard",
         "choices": [{"value": key, "label": label} for key, (_, label) in MODELS.items()]},
        {"key": "tmrna", "label": "Also find tmRNA genes (ssrA)", "type": "bool", "default": True,
         "help": "Bacteria and many plastids (e.g. red algae) have one."},
        {"key": "introns", "label": "Find tRNAs with introns", "type": "bool", "default": True},
        {"key": "replace", "label": "Replace the sequences' existing tRNAs", "type": "bool", "default": False,
         "help": "Otherwise tRNAs already annotated stay and only new ones are added."},
    ],
}
AMINO = {"Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E", "Gly": "G",
         "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P", "Ser": "S",
         "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "Sec": "U", "Pyl": "O"}


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["bash", "-c", "aragorn -h 2>&1 | head -5"]), workdir)
    found = re.search(r"ARAGORN v(\d+\.\d+\.\d+)", text)
    if not found or found.group(1) != VERSION:
        raise RuntimeError(f"Expected ARAGORN {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "ARAGORN", VERSION, [{"tool": t, "reference": r, "url": u} for t, r, u in ENGINE["citations"]])


# ── Reading ARAGORN's batch output (-w) ──────────────────────────────────

def read_batch(text):
    """{sequence: [hit]}. The output has a section per sequence: ">name", "N genes
    found", then rows "1 tRNA-Leu [a,b] pos (cag)" (c[a,b] on the minus strand;
    a > b across a circular origin; "i(distance,length)" after the codon for an
    intron) or "1 tmRNA [a,b] from,to TAGPEPTIDE*". Ends with ">end"."""
    hits, name = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            name = None if line.startswith(">end") else line[1:].split()[0]
            continue
        fields = line.split()
        coords = re.fullmatch(r"(c?)\[(\d+),(\d+)\]", fields[2]) if len(fields) > 2 else None
        if name is None or not coords:
            continue
        kind, _, amino = fields[1].partition("-")
        tail = " ".join(fields[4:])
        intron = re.search(r"i\((\d+),(\d+)\)", tail)
        hits.setdefault(name, []).append({
            "kind": kind, "amino": amino.split("|")[0], "strand": -1 if coords.group(1) else 1,
            "start": int(coords.group(2)), "end": int(coords.group(3)),
            "position": fields[3] if len(fields) > 3 else "",
            "codon": re.sub(r"i\(\d+,\d+\)|[()]", "", tail).strip().upper(),
            "intron": (int(intron.group(1)), int(intron.group(2))) if intron else None})
    return hits


def _wrap(pieces, length):
    """1-based (start, end) pieces, possibly past a circular sequence's end ->
    0-based inclusive pieces in the sequence, in order (across the origin: the
    piece at the end first)."""
    out = []
    for s, e in pieces:
        if e <= length:
            out.append((s - 1, e - 1))
        elif s > length:
            out.append((s - 1 - length, e - 1 - length))
        else:
            out += [(s - 1, length - 1), (0, e - 1 - length)]
    return out


def features(hit, length):
    """A hit as [gene, tRNA or tmRNA] editor features."""
    start, end = hit["start"], hit["end"]
    if start > end:  # across the origin: coordinates continued past the end
        end += length
    pieces = [(start, end)]
    if hit["intron"]:
        distance, size = hit["intron"]
        if hit["strand"] > 0:
            a = start + distance - 1
        else:
            a = end - distance - size + 2
        pieces = [(start, a - 1), (a + size, end)]
    parts = _wrap(pieces, length)
    whole = _wrap([(start, end)], length)
    if hit["kind"] == "tmRNA":
        gene, type_, notes = "ssrA", "tmRNA", {"product": ["transfer-messenger RNA"],
                                                "note": [f"tag peptide {hit['codon']}; ARAGORN"]}
    else:
        anticodon = hit["codon"].replace("T", "U")
        gene = f"trn{AMINO.get(hit['amino'], 'X')}" + (f"-{anticodon}" if re.fullmatch(r"[ACGU]{3}", anticodon) else "")
        type_ = "tRNA"
        notes = {"product": [f"tRNA-{hit['amino'] if hit['amino'] in AMINO else 'OTHER'}"],
                 "note": [f"anticodon {anticodon}; ARAGORN"]}
    notes["inference"] = [f"COORDINATES:profile:ARAGORN:{VERSION}"]

    def feature(kind, locations, qualifiers):
        out = {"name": gene, "type": kind, "strand": hit["strand"], "notes": qualifiers,
               "start": locations[0][0], "end": locations[-1][1]}
        if len(locations) > 1:
            out["locations"] = [{"start": s, "end": e} for s, e in locations]
        return out
    return [feature("gene", whole, {"gene": [gene]}), feature(type_, parts, {"gene": [gene], **notes})]


# ── Running it ───────────────────────────────────────────────────────────

def _facts(feature, length):
    locations = feature.get("locations") or [{"start": feature["start"], "end": feature["end"]}]
    return feature["type"], feature["strand"], api.transfer_spans(locations, length)


def run(job, workdir, operation, items, output, into=None, apply=False, busy=None, add_to=None,
        model="standard", tmrna=True, introns=True, replace=False):
    if operation != "annotate.trna" or not items:
        raise ValueError("Choose DNA sequences to search for tRNAs")
    if model not in MODELS or not all(isinstance(v, bool) for v in (tmrna, introns, replace)):
        raise ValueError("Invalid ARAGORN options")
    api.steps(job, "Searching for tRNAs", "Saving annotations")
    records, groups = [], {"c": [], "l": []}
    for index, item in enumerate(items):
        record = api.records(item)[0]
        if item.get("protein") or not len(record):
            raise ValueError(f"{item['label']}: choose a DNA sequence")
        circular = record.annotations.get("topology") == "circular"
        groups["c" if circular else "l"].append(f">s{index}\n{str(record.seq).upper()}\n")
        records.append(record)
    _version(job, workdir)
    flags = [*MODELS[model][0], *(["-m"] if tmrna else []), *(["-i"] if introns else [])]
    found = {}
    for topology, fasta in groups.items():
        if not fasta:
            continue
        (workdir / f"input-{topology}.fa").write_text("".join(fasta), encoding="ascii", errors="replace")
        args = ["aragorn", *flags, f"-{topology}", "-w", "-o", f"/data/out-{topology}.txt", f"/data/input-{topology}.fa"]
        api.run_container(job, api.tools_image(), api.conda_command(ENGINE, args), workdir)
        found.update(read_batch((workdir / f"out-{topology}.txt").read_text(encoding="utf-8", errors="replace")))
    api.step(job, 1)
    kinds = ("tRNA", "tmRNA") if tmrna else ("tRNA",)
    results = []
    for index, (item, record) in enumerate(zip(items, records)):
        n = len(record)
        hits = found.get(f"s{index}", [])
        data = api.record_to_editor(record)
        if replace:
            gone = [_facts(f, n) for f in data["features"] if f["type"] in kinds]
            data["features"] = [f for f in data["features"] if f["type"] not in kinds and not (
                f["type"] == "gene" and any(api.transfer_same_feature((g[0], *_facts(f, n)[1:]), g) for g in gone))]
        existing = [_facts(f, n) for f in data["features"] if f["type"] in kinds]
        added = 0
        for hit in hits:
            gene, rna = features(hit, n)
            if any(api.transfer_same_feature(_facts(rna, n), other) for other in existing):
                continue
            data["features"] += [gene, rna]
            added += 1
        path = workdir / f"result{index}.gb"
        api.write_record(api.editor_to_record(data, original=record), path)
        results.append((item, path))
        job["log"] += f"{item['label']}: {len(hits)} gene{'s' if len(hits) != 1 else ''} found, {added} added\n"
        api.step(job, 1, (index + 1) / len(items))
    api.annotated(job, workdir, results, "_trnas", into, apply, busy)
