"""Genome comparison engine."""
import re
import shlex
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "packages": [
            "progressivemauve"
        ],
        "estimateBytes": 6000000
    },
    "id": "mauve", "label": "progressiveMauve", "blurb": "Compares genomes with rearrangements and inversions.",
    "supports": ["align.compare"], "order": 10, "lane": "compute", "cores": 1, "jobKind": "mauve",
    "options": [
        {"key": "seed_weight", "label": "Seed size", "type": "int", "default": None, "min": 3, "max": 21,
         "help": "Leave empty to choose from genome size."},
        {"key": "line_up", "label": "Start the genomes where the first one starts", "type": "bool", "default": True,
         "help": "Circular genomes may be turned; reverse-complemented genomes are flipped."},
    ],
}


LINE_UP_WINDOW = 500

MAUVE_STAGES = ["Reading the genomes", "Finding seeds", "Comparing pairs of genomes", "Aligning"]

def run(job, workdir, operation, items, output, add_to=None, **options):
    return run_comparison(job, workdir, items, output, **options)


def _line_up(job, workdir, records):
    """How to turn each genome so that it starts where the first one does, on the
    same strand: [(start, flip)], start 0-based in the genome (circular ones only;
    a linear one can only be flipped). progressiveMauve reads every genome from
    base 1: circular genomes starting at different places split the blocks that
    cross their origins. Found with BLAST: windows of the first genome against
    each other one (doubled when circular), the best hit kept."""
    ref = str(records[0].seq)
    windows = sorted({max(0, min(len(ref) - LINE_UP_WINDOW, k * len(ref) // 8)) for k in range(8)})
    (workdir / "windows.fasta").write_text("".join(f">{w}\n{ref[w:w + LINE_UP_WINDOW]}\n" for w in windows))
    commands = []
    for i, record in enumerate(records[1:], 1):
        doubled = record.annotations.get("topology") == "circular"
        (workdir / f"line{i}.fasta").write_text(f">g\n{record.seq}{record.seq if doubled else ''}\n")
        commands.append(shlex.join(["blastn", "-task", "blastn", "-query", "/data/windows.fasta", "-subject",
                                    f"/data/line{i}.fasta", "-dust", "no", "-evalue", "1e-10", "-max_hsps", "1",
                                    "-outfmt", "6 qseqid qstart sstart send bitscore", "-out", f"/data/line{i}.tsv"]))
    api.run_container(job, api.tools_image(), ["bash", "-c", " && ".join(commands)], workdir)
    turns = [(0, False)]
    for i, record in enumerate(records[1:], 1):
        hits = [line.split("\t") for line in (workdir / f"line{i}.tsv").read_text().splitlines() if line.strip()]
        if not hits:
            job["log"] += f"{record.name}: not lined up (no match with the first genome)\n"
            turns.append((0, False))
            continue
        window, qstart, sstart, send, _ = max(hits, key=lambda h: float(h[4]))
        n = len(record)
        w, x = int(window) + int(qstart) - 1, (int(sstart) - 1) % n  # the first genome's base w is this one's x
        flip = int(send) < int(sstart)
        # where the first genome's base 0 is: w bases before x (after it, on the other strand); once
        # reverse complemented, the genome starts with the base before its start
        start = (x - w) % n if not flip else (x + w + 1) % n
        if record.annotations.get("topology") != "circular":
            start = 0
        turns.append((start, flip))
        if start or flip:
            job["log"] += (f"{record.name}: " + ", ".join(
                (["starts at its position " + f"{start + 1:,}"] if start else [])
                + (["reverse complemented"] if flip else [])) + " (lined up with the first genome)\n")
    return turns

def run_comparison(job, workdir, items, output, seed_weight=None, line_up=True):
    """line_up: circular genomes turned to start where the first one does, and any
    genome on the other strand reverse complemented (_line_up); each genome's
    `start` and `flipped` are kept, so blocks can be found in the library sequence."""
    api.steps(job, *MAUVE_STAGES)  # followed in its messages (_watch_mauve)
    job["progress"]["genomes"] = len(items)
    api.ensure_tools_image(job)
    records = [api.records(item)[0] for item in items]
    turns = [(0, False)] * len(records)
    if line_up:
        job["progress"]["detail"] = "lining up the genomes"
        turns = _line_up(job, workdir, records)
    job["progress"]["detail"] = ""
    used, genomes = set(), []
    for i, (item, record, (start, flip)) in enumerate(zip(items, records, turns)):
        name = item["name"]
        while name in used:
            name += "_2"
        used.add(name)
        record = api.turned(record, start, flip)
        data = api.record_to_editor(record)
        data["geneticCode"] = api.genetic_code(record)
        genomes.append({"item": item["id"], "name": name, "data": data, **({"start": start} if start else {}),
                        **({"flipped": True} if flip else {})})
        (workdir / f"g{i}.fasta").write_text(f">g{i}\n{record.seq}\n", encoding="utf-8")
    args = ["progressiveMauve", "--output=/data/out.xmfa", "--backbone-output=/data/out.backbone",
            *([f"--seed-weight={seed_weight}"] if seed_weight else []), *(f"/data/g{i}.fasta" for i in range(len(items)))]
    api.tool_watched(job, args, workdir, _watch_mauve, stdout=True)
    blocks = []
    for index, block in enumerate(api.formats_read_xmfa((workdir / "out.xmfa").read_text(encoding="utf-8"))):
        if len(block["parts"]) < 2:  # a genome's own stretch: not a shared block
            continue
        blocks.append({"xmfa": index, "columns": block["columns"],
                       "parts": [{k: p[k] for k in ("genome", "start", "end", "strand")} for p in block["parts"]]})
    # in the order of the first genome they're in, then by where they start there
    blocks.sort(key=lambda b: (b["parts"][0]["genome"], b["parts"][0]["start"]))
    job["log"] += f"{len(blocks)} block{'' if len(blocks) == 1 else 's'} shared by two genomes or more\n"
    result = {"genomes": genomes, "blocks": blocks, "options": {"seed_weight": seed_weight, "line_up": line_up}}
    new_id = api.add_comparison(result, output, workdir / "out.xmfa")
    item = api.get_item(new_id)
    job["outputs"].append({"id": item["id"], "label": item["label"], "kind": item["kind"]})

def _watch_mauve(text, progress):
    n = progress["genomes"]
    if "Aligning..." in text:
        merges = len(re.findall(r"^Aligning node", text, re.M))  # the line starts a merge
        return {"at": 3, "fraction": min(1, max(0, merges - 1) / max(1, n - 1)),
                "detail": f"merge {max(1, merges)} of {n - 1}" if n > 2 else ""}
    if "Calculating pairwise breakpoint distances" in text:
        pairs, total = len(re.findall(r"^Pair \d+, \d+ has", text, re.M)), n * (n - 1) // 2
        return {"at": 2, "fraction": min(1, pairs / max(1, total)), "detail": f"{pairs} of {total} pairs"}
    seeds = text.find("mers for initial seeds")
    if seeds >= 0:
        done = re.findall(r"(\d+)%", text[seeds:])
        return {"at": 1, "fraction": int(done[-1]) / 100 if done else 0, "detail": ""}
    read = text.count("Sequence loaded successfully")
    return {"at": 0, "fraction": read / n, "detail": f"{read} of {n}"}
