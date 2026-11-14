"""tRNAscan-SE 2: tRNA genes, as gene + tRNA features.

An optional tool: Settings › Tools › Install downloads tRNAscan-SE 2.0.13 (with
Infernal) from bioconda into its own environment (conda.py; lock:
locks/trnascan-2.0.13.lock). All sequences go in one search; a circular one has
its first bases repeated at its end, so a tRNA across the origin is found.
Names follow organelle usage: gene trnL-UAA, product tRNA-Leu, the anticodon and
score in a note. Introns make the tRNA a feature in two parts.
"""
import re

from cladia import engine_api as api

VERSION = "2.0.13"
WRAP = 300  # bases of a circular sequence repeated past its end
MODELS = {"organellar": ("-O", "Organelles (mitochondria and plastids)"),
          "bacterial": ("-B", "Bacterial (plastid tRNAs are bacterial-like)"),
          "general": ("-G", "General (all domains)"),
          "eukaryotic": ("-E", "Eukaryotic nuclear"),
          "archaeal": ("-A", "Archaeal"),
          "vert_mito": ("-M vert", "Vertebrate mitochondria")}
ENGINE = {
    "id": "trnascan", "label": "tRNAscan-SE", "order": 10,
    "blurb": "Find tRNA genes, with organellar, bacterial or general models.",
    "supports": ["annotate.trna"], "optional": True, "lane": "compute", "cores": 4, "jobKind": "trnascan",
    "conda": {"version": VERSION, "spec": "trnascan-se=2.0.13"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/trnascan-*"],
                "estimateBytes": 110_000_000},
    "options": [
        {"key": "model", "label": "Search model", "type": "choice", "default": "organellar",
         "choices": [{"value": key, "label": label} for key, (_, label) in MODELS.items()]},
        {"key": "pseudo", "label": "Keep predicted pseudogenes", "type": "bool", "default": False,
         "help": "Models other than organellar mark likely pseudogenes; they are left out unless ticked."},
        {"key": "replace", "label": "Replace the sequences' existing tRNAs", "type": "bool", "default": False,
         "help": "Otherwise tRNAs already annotated stay and only new ones are added."},
    ],
}
CITATIONS = [{"tool": "tRNAscan-SE", "reference": "Chan et al. (2021), Nucleic Acids Research 49:9077",
              "url": "https://doi.org/10.1093/nar/gkab688"},
             {"tool": "Infernal (used by tRNAscan-SE)", "reference": "Nawrocki & Eddy (2013), Bioinformatics 29:2933",
              "url": "https://doi.org/10.1093/bioinformatics/btt509"}]
AMINO = {"Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E", "Gly": "G",
         "His": "H", "Ile": "I", "Ile2": "I", "Leu": "L", "Lys": "K", "Met": "M", "iMet": "M", "fMet": "fM",
         "Phe": "F", "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "SeC": "U",
         "Pyl": "O", "Sup": "X", "Undet": "X"}
PRODUCT = {"Ile2": "Ile", "iMet": "Met", "fMet": "fMet", "SeC": "Sec", "Sup": "OTHER", "Undet": "Xxx"}


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(),
                             api.conda_command(ENGINE, ["bash", "-c", "LC_ALL=C tRNAscan-SE -h 2>&1 | head -5"]), workdir)
    found = re.search(r"tRNAscan-SE (\d+\.\d+\.\d+)", text)
    if not found or found.group(1) != VERSION:
        raise RuntimeError(f"Expected tRNAscan-SE {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "tRNAscan-SE", VERSION, CITATIONS)


def read_table(text):
    """tRNAscan-SE's -o table: {sequence: [hit]}; a hit has 1-based begin/end
    (begin > end on the minus strand), intron bounds (0 = none), type, anticodon,
    score and note."""
    hits = {}
    for line in text.splitlines():
        fields = [f.strip() for f in line.split("\t")]
        if len(fields) < 9 or not fields[1].isdigit():
            continue  # the three header lines
        begin, end, intron_a, intron_b = (int(fields[k]) for k in (2, 3, 6, 7))
        hits.setdefault(fields[0], []).append({
            "begin": begin, "end": end, "intron": (intron_a, intron_b) if intron_a and intron_b else None,
            "type": fields[4], "anticodon": fields[5].upper(), "score": float(fields[8]),
            "note": fields[9] if len(fields) > 9 else ""})
    return hits


def _parts(hit, length):
    """The tRNA's exons as 0-based inclusive (start, end) in the sequence, ascending;
    past the end of a circular sequence (the repeated bases) they wrap round."""
    lo, hi = sorted((hit["begin"], hit["end"]))
    pieces = [(lo, hi)]
    if hit["intron"]:
        a, b = sorted(hit["intron"])
        pieces = [(lo, a - 1), (b + 1, hi)]
    out = []
    for s, e in pieces:  # (1-based, possibly past the end)
        if e <= length:
            out.append((s - 1, e - 1))
        elif s > length:
            out.append((s - 1 - length, e - 1 - length))
        else:
            out += [(s - 1, length - 1), (0, e - 1 - length)]
    return out


def features(hit, length):
    """A tRNA hit as [gene, tRNA] editor features."""
    kind = hit["type"]
    anticodon = hit["anticodon"].replace("T", "U")
    gene = f"trn{AMINO.get(kind, 'X')}" + (f"-{anticodon}" if re.fullmatch(r"[ACGU]{3}", anticodon) else "")
    product = f"tRNA-{PRODUCT.get(kind, kind)}"
    strand = 1 if hit["begin"] <= hit["end"] else -1
    parts = _parts(hit, length)
    note = f"anticodon {anticodon}; tRNAscan-SE score {hit['score']:g}" + (f"; {hit['note']}" if hit["note"] else "")
    inference = [f"COORDINATES:profile:tRNAscan-SE:{VERSION}"]

    def feature(type_, locations, notes):
        out = {"name": gene, "type": type_, "strand": strand, "notes": notes,
               "start": locations[0][0], "end": locations[-1][1]}
        if len(locations) > 1:
            out["locations"] = [{"start": s, "end": e} for s, e in locations]
        return out
    span = [parts[0][0], parts[-1][1]]
    gene_parts = [tuple(span)] if span[0] <= span[1] else [(span[0], length - 1), (0, span[1])]
    return [feature("gene", gene_parts, {"gene": [gene]}),
            feature("tRNA", parts, {"gene": [gene], "product": [product], "note": [note], "inference": inference})]


def _facts(feature, length):
    locations = feature.get("locations") or [{"start": feature["start"], "end": feature["end"]}]
    return feature["type"], feature["strand"], api.transfer_spans(locations, length)


def run(job, workdir, operation, items, output, into=None, apply=False, busy=None, add_to=None,
        model="organellar", pseudo=False, replace=False):
    if operation != "annotate.trna" or not items:
        raise ValueError("Choose DNA sequences to search for tRNAs")
    if model not in MODELS or not all(isinstance(v, bool) for v in (pseudo, replace)):
        raise ValueError("Invalid tRNAscan-SE options")
    api.steps(job, "Searching for tRNAs", "Saving annotations")
    records, fasta = [], []
    for index, item in enumerate(items):
        record = api.records(item)[0]
        sequence = str(record.seq).upper()
        if item.get("protein") or not sequence:
            raise ValueError(f"{item['label']}: choose a DNA sequence")
        circular = record.annotations.get("topology") == "circular"
        fasta.append(f">s{index}\n{sequence + (sequence[:WRAP] if circular and len(sequence) > WRAP else '')}\n")
        records.append(record)
    (workdir / "input.fa").write_text("".join(fasta), encoding="ascii", errors="replace")
    _version(job, workdir)
    flag = MODELS[model][0].split()
    args = ["tRNAscan-SE", *flag, "-q", "--thread", str(max(1, int(job.get("cores") or 1))),
            "-o", "/data/trna.txt", "/data/input.fa"]
    api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["env", "LC_ALL=C", *args]), workdir)
    found = read_table((workdir / "trna.txt").read_text(encoding="utf-8", errors="replace"))
    api.step(job, 1)
    results = []
    for index, (item, record) in enumerate(zip(items, records)):
        n = len(record)
        hits = [h for h in found.get(f"s{index}", []) if min(h["begin"], h["end"]) <= n]  # (copies past the end: again)
        if not pseudo:
            hits = [h for h in hits if "pseudo" not in h["note"].lower()]
        data = api.record_to_editor(record)
        if replace:
            gone = [_facts(f, n) for f in data["features"] if f["type"] == "tRNA"]
            data["features"] = [f for f in data["features"] if f["type"] != "tRNA" and not (
                f["type"] == "gene" and any(api.transfer_same_feature(("tRNA", *_facts(f, n)[1:]), g) for g in gone))]
        existing = [_facts(f, n) for f in data["features"] if f["type"] == "tRNA"]
        added = 0
        for hit in hits:
            gene, trna = features(hit, n)
            if any(api.transfer_same_feature(_facts(trna, n), other) for other in existing):
                continue
            data["features"] += [gene, trna]
            added += 1
        path = workdir / f"result{index}.gb"
        api.write_record(api.editor_to_record(data, original=record), path)
        results.append((item, path))
        job["log"] += f"{item['label']}: {len(hits)} tRNA{'s' if len(hits) != 1 else ''} found, {added} added\n"
        api.step(job, 1, (index + 1) / len(items))
    api.annotated(job, workdir, results, "_trnas", into, apply, busy)
