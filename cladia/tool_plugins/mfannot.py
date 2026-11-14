"""MFannot: de novo annotation of mitochondrial and plastid genomes.

Runs in the MFannot authors' own Docker image (nbeck/mfannot, built from BFL-lab's
repositories), unpacked into an isolated root the first time (a ~510 MB download).
Each sequence goes through `mfannot -g <code> --tbl`, whose feature table (made
by mf2sqn) becomes gene, CDS, rRNA, tRNA, intron and exon features. The table
reader is the lab's own code.

MFannot reads a sequence as linear: a gene across a circular sequence's origin
isn't found (the log says so); move the origin between genes first.
"""
import re
import shlex

from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature

from cladia import engine_api as api

IMAGE = "nbeck/mfannot@sha256:b71c55d2cc7d008041447631f952bbe4767ad133ee8cedd6ca6f99d08e3a7020"  # latest, Feb 2021
CODES = (1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 33)
ORGANELLES = {"mitochondrion": 4, "plastid": 11}

ENGINE = {
    "id": "mfannot", "label": "MFannot", "order": 20,
    "blurb": "Annotates mitochondrial and plastid genes, rRNAs, tRNAs and introns (the MFannot authors' image).",
    "supports": ["annotate.denovo"], "lane": "compute", "cores": 1, "jobKind": "mfannot",
    "image": {"name": IMAGE, "check": "command -v mfannot && command -v mf2sqn && test -d /MFannot_data",
              "version": "1"},
    "storage": {"kind": "linux", "environment": "mfannot", "paths": ["/"], "estimateBytes": 1_800_000_000,
                "note": "Private environment, including its own dependencies."},
    "citations": [("MFannot", "Lang et al. (2023), Frontiers in Plant Science 14:1222186",
                   "https://doi.org/10.3389/fpls.2023.1222186")],
    "options": [
        {"key": "organelle", "label": "Genetic code", "type": "choice", "default": "mitochondrion", "choices": [
            {"value": "mitochondrion", "label": "Mitochondrion (genetic code 4)"},
            {"value": "plastid", "label": "Plastid (genetic code 11)"},
            {"value": "own", "label": "Each sequence's own (from its CDSs or /organelle)"},
            *({"value": str(n), "label": f"Genetic code {n}"} for n in CODES)]},
        {"key": "replace", "label": "Replace existing annotations", "type": "bool", "default": True,
         "help": "Otherwise the sequences' own annotations stay and only what they don't have is added."},
    ],
}


# ── Reading MFannot's feature table ──────────────────────────────────────

THREE_LETTER = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu", "G": "Gly",
                "H": "His", "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe", "P": "Pro", "S": "Ser",
                "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "U": "Sec", "O": "Pyl", "*": "TERM"}
# ORFs MFannot writes without a feature type, known by their protein_id ("lcl| G-ycf3")
UNTYPED_ORFS = (("odpa", "Pyruvate dehydrogenase E1 component subunit alpha"),
                ("odpb", "Pyruvate dehydrogenase E1 component subunit beta"),
                ("ycf", "hypothetical protein"), ("rnz", "Ribonuclease Z"))
TYPE_ORDER = {"gene": 0, "ncRNA": 1, "rRNA": 2, "tmRNA": 3, "CDS": 4, "tRNA": 5, "intron": 6, "exon": 7}


def _qualifiers(lines):
    out = {}
    for q in lines:
        if len(q) < 5:
            continue
        key, value = q[3], q[4]
        if key == "gene" and "orf" in value:
            continue  # an ORF's made-up name: kept as its label instead
        if key == "protein_id":
            value = value.replace("lcl| ", "")
        if key == "transl_except":  # "(pos:21144..21146, aa : Q)" -> INSDC's "(pos:21144..21146,aa:Gln)"
            m = re.match(r"\(pos:\s*(\S+?)\s*,\s*aa\s*:\s*(\S+?)\s*\)", value)
            if m:
                value = f"(pos:{m.group(1)},aa:{THREE_LETTER.get(m.group(2), m.group(2))})"
        out[key] = value
    return out


def read_table(text, code=None):
    """MFannot's .tbl -> [gene]: {name, parts [(start, end) 1-based], strand +1/-1,
    attrs, children [{type, parts, strand, attrs, children}]}. A feature in several
    parts is a typed line followed by lines without a type; its qualifiers come
    after the last part. Minus-strand coordinates are written end..start."""
    entries, current = [], None
    for line in text.splitlines():
        if not line.strip() or line.startswith(">"):
            current = None if line.startswith(">") else current
            continue
        fields = line.split("\t")
        if fields[0]:
            current = {"feature": fields, "qualifiers": []}
            entries.append(current)
        elif current is not None:
            current["qualifiers"].append(fields)

    genes, gene, feature, last, previous = [], None, None, None, ""
    for entry in entries:
        f, qual = entry["feature"], entry["qualifiers"]
        typed = len(f) > 2 and bool(f[2].strip())
        protein_id = next((q[4] for q in qual if len(q) >= 5 and q[3] == "protein_id"), "").lower()
        orf = next((product for key, product in UNTYPED_ORFS if key in protein_id), None)
        if typed:
            kind = f[2].strip()
        elif orf and qual:
            kind, qual[0][4] = "CDS", orf
        else:
            kind = previous
        kind = "ncRNA" if kind == "RNA" else kind
        continues = not typed and last is not None and kind == previous
        previous = kind
        a, b = int(f[0].lstrip("<>")), int(f[1].lstrip("<>"))
        span, strand = (min(a, b), max(a, b)), 1 if a <= b else -1
        attrs = _qualifiers(qual)
        if continues:
            last["parts"].append(span)
            last["attrs"].update(attrs)
            if last is gene and gene["name"] == "gene_unknown" and qual:
                gene["name"] = qual[0][4]
        elif kind == "gene":
            gene = last = {"name": qual[0][4] if qual else "gene_unknown", "parts": [span], "strand": strand,
                           "attrs": attrs, "children": []}
            genes.append(gene)
            feature = None
        elif gene is None:
            continue  # (MFannot writes the gene line first)
        elif kind in ("intron", "exon"):
            last = {"type": kind, "parts": [span], "strand": strand, "attrs": attrs, "children": []}
            (feature or gene)["children"].append(last)
        else:
            if kind == "CDS" and code:
                attrs = {"transl_table": str(code), **attrs}
            feature = last = {"type": kind, "parts": [span], "strand": strand, "attrs": attrs, "children": []}
            gene["children"].append(feature)
    return genes


def seq_features(genes):
    """Genes as Biopython features, each carrying its gene's /gene (an ORF: /label)."""
    def make(kind, parts, strand, attrs):
        ordered = sorted(parts, reverse=strand < 0)  # 5' to 3'
        locations = [FeatureLocation(s - 1, e, strand=strand) for s, e in ordered]
        location = locations[0] if len(locations) == 1 else CompoundLocation(locations)
        return SeqFeature(location, type=kind, qualifiers={k: [str(v)] for k, v in attrs.items()})

    out = []
    for gene in genes:
        named = {"gene": gene["attrs"]["gene"]} if "gene" in gene["attrs"] else {"label": gene["name"]}
        out.append(make("gene", gene["parts"], gene["strand"], {**named, **gene["attrs"]}))
        pending = list(gene["children"])
        while pending:
            child = pending.pop(0)
            out.append(make(child["type"], child["parts"], child["strand"], {**named, **child["attrs"]}))
            pending = child["children"] + pending
    return sorted(out, key=lambda f: (int(f.location.start), TYPE_ORDER.get(f.type, len(TYPE_ORDER))))


# ── Running it ───────────────────────────────────────────────────────────

def _code(organelle, record):
    if organelle in ORGANELLES:
        return ORGANELLES[organelle]
    if organelle == "own":
        return api.genetic_code(record)
    if organelle.isdigit() and int(organelle) in CODES:
        return int(organelle)
    raise ValueError(f"Unknown genetic code choice: {organelle}")


def _watch(text, progress):
    started = re.findall(r"^\[(\d+)/(\d+)\] ", text, re.M)
    if not started or started[-1][1] == "1":
        return None
    i, total = map(int, started[-1])
    return {"at": progress["at"], "fraction": (i - 1) / total, "detail": f"{i} of {total} sequences"}


def run(job, workdir, operation, items, output, into=None, apply=False, busy=None, add_to=None,
        organelle="mitochondrion", replace=True):
    if operation != "annotate.denovo" or not items:
        raise ValueError("Choose DNA sequences to annotate")
    records = [api.records(item)[0] for item in items]
    codes = [_code(organelle, record) for record in records]
    (workdir / "in").mkdir()
    lines = []
    for i, (item, record, code) in enumerate(zip(items, records, codes)):
        if item.get("protein"):
            raise ValueError(f"{item['label']}: choose a DNA sequence")
        bases = str(record.seq).upper()
        wrapped = "\n".join(bases[j:j + 60] for j in range(0, len(bases), 60))
        (workdir / "in" / f"s{i:05}.fasta").write_text(f">s{i:05}\n{wrapped}\n", encoding="ascii", errors="replace")
        lines.append(f"echo '[{i + 1}/{len(items)}] s{i:05}'; "
                     f"mfannot -g {code} --tbl s{i:05}.fasta > s{i:05}.log 2>&1 || echo 's{i:05}: exit code '$?")
    api.steps(job, "Annotating")
    api.watch_log(job, workdir / "run.log", _watch)
    script = "cd /data/in && { " + "; ".join(lines) + "; } > /data/run.log 2>&1"
    api.run_container(job, IMAGE, ["-c", script], workdir, entrypoint="bash")
    job["log"] += (workdir / "run.log").read_text(encoding="utf-8", errors="replace") if (workdir / "run.log").exists() else ""
    results = []
    for i, (item, record, code) in enumerate(zip(items, records, codes)):
        tables = sorted((workdir / "in").glob(f"s{i:05}.fasta.new*.tbl"))
        if not tables:
            log = workdir / "in" / f"s{i:05}.log"
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:] if log.exists() else []
            job["log"] += f"{item['label']}: MFannot produced no feature table\n" + "\n".join(tail) + "\n"
            continue
        found = seq_features(read_table(tables[-1].read_text(encoding="utf-8", errors="replace"), code))
        note = _merge(record, found, replace)
        circular = record.annotations.get("topology") == "circular"
        job["log"] += (f"{item['label']}: genetic code {code}; {note}"
                       + ("; read as linear: a gene across the origin isn't found" if circular else "") + "\n")
        path = workdir / f"result{i:05}.gb"
        api.write_record(record, path)
        results.append((item, path))
    if len(results) < len(items):
        job["log"] += f"\n{len(results)} of {len(items)} sequences annotated\n"
    api.annotated(job, workdir, results, "_mfannot", into, apply, busy)


def _merge(record, found, replace):
    """Puts MFannot's features on the record; Keep: its own stay, and of MFannot's
    only those it doesn't have already (noted "added by MFannot"). Returns a log line."""
    source = [f for f in record.features if f.type == "source"]
    own = [] if replace else [f for f in record.features if f.type != "source"]
    facts = [api.transfer_record_facts(f) for f in own]
    new = [f for f in found if not any(api.transfer_same_feature(api.transfer_record_facts(f), o) for o in facts)]
    if not replace:
        for f in new:
            f.qualifiers.setdefault("note", []).append("added by MFannot")
    record.features = source + sorted(own + new, key=lambda f: (int(f.location.start), -int(f.location.end)))
    genes = sum(f.type == "gene" for f in found)
    if replace:
        return f"{len(found)} features ({genes} genes)"
    return f"own annotations kept: {len(own)}; MFannot added {len(new)}, {len(found) - len(new)} were there already"
