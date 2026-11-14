"""Importing BAM / SAM files as mapping items.

A BAM made elsewhere (the HPC's BWA, Bowtie2, minimap2…) becomes a mapping item
like Map reads makes: coverage, consensus, agreement, clipped reads and the
reads lane, computed once on import. A BAM holds only its references' names and
lengths, not their bases, so each reference is matched to a library sequence
of the same name (its list name, LOCUS name or accession) and the same length;
one mapping item per matched reference, the others listed in the job's log.
The BAM is copied into the job, sorted and indexed if it needs it, cut to each
reference, and kept beside the mapping (Export: BAM). Needs samtools (the tools'
Linux), so it is a job. CRAM isn't read (it needs its reference FASTA).
"""

import gzip
import shlex
import shutil
from pathlib import Path

from . import config, execution, jobs, library, mapping
from .job_progress import step, steps


# ── Reading the header (no samtools: Python reads the start of the file) ──

def kind_of(path):
    """"bam", "sam", "cram" or None, from the file's first bytes."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head == b"CRAM":
        return "cram"
    if head[:2] == b"\x1f\x8b":
        try:
            with gzip.open(path, "rb") as f:
                return "bam" if f.read(4) == b"BAM\x01" else None
        except OSError:
            return None
    if head[:1] == b"@" and head[1:3] in (b"HD", b"SQ", b"RG", b"PG", b"CO"):
        return "sam"
    return None


def references(path, kind):
    """([(name, length)], sorted by coordinate?) from a BAM or SAM header."""
    if kind == "bam":
        with gzip.open(path, "rb") as f:
            f.read(4)
            text = f.read(int.from_bytes(f.read(4), "little")).decode("utf-8", "replace")
            refs = []
            for _ in range(int.from_bytes(f.read(4), "little")):
                name = f.read(int.from_bytes(f.read(4), "little")).rstrip(b"\0").decode("utf-8", "replace")
                refs.append((name, int.from_bytes(f.read(4), "little")))
    else:
        text, refs = "", []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.startswith("@"):
                    break
                text += line
                if line.startswith("@SQ"):
                    tags = dict(t.split(":", 1) for t in line.rstrip("\n").split("\t")[1:] if ":" in t)
                    if "SN" in tags and "LN" in tags:
                        refs.append((tags["SN"], int(tags["LN"])))
    sorted_ = any(line.startswith("@HD") and "SO:coordinate" in line for line in text.splitlines())
    return refs, sorted_


def match(refs):
    """Each reference to the one DNA library sequence of its name and length:
    ([(item, name)], [why each other one isn't])."""
    candidates = [it for it in library.items() if it["kind"] == "sequence" and not it["missing"]
                  and not library.item_is_protein(it)]
    named = [(it, library._names(it)) for it in candidates]
    pairs, unmatched = [], []
    for name, length in refs:
        key = name.lower()
        hits = [it for it, names in named if key in names or key.split(".")[0] in names]
        fit = [it for it in hits if it.get("length") == length]
        if len(fit) == 1:
            pairs.append((fit[0], name))
        elif len(fit) > 1:
            unmatched.append(f"{name}: {len(fit)} sequences of that name and length")
        elif hits:
            unmatched.append(f"{name}: {hits[0]['label']} is {hits[0].get('length') or 0:,} bp, the BAM's {length:,}")
        else:
            unmatched.append(f"{name}: no sequence in the library has this name")
    return pairs, unmatched


def submit(path, folder=None, remove_after=False):
    """A job importing this BAM / SAM; returns (job id, what the job will make).
    ValueError when the file can't be one or nothing in it matches the library."""
    kind = kind_of(path)
    if kind == "cram":
        raise ValueError("CRAM files need their reference FASTA to be read: convert to BAM first (samtools view -b)")
    if kind is None:
        raise ValueError("not a BAM or SAM file")
    refs, sorted_ = references(path, kind)
    if not refs:
        raise ValueError("the file names no reference sequences (@SQ lines)")
    pairs, unmatched = match(refs)
    if not pairs:
        raise ValueError("none of its references is in the library by name and length. Import the reference "
                         "first. " + "; ".join(unmatched[:5]))
    name = Path(path).name
    job = jobs.submit("bamimport", f"Import {name}", bam_job, folder=folder, path=str(path), file_kind=kind,
                         sorted_=sorted_, pairs=[(it["id"], ref) for it, ref in pairs], unmatched=unmatched,
                         remove_after=remove_after)
    return job["id"], [it["label"] for it, _ in pairs]


# ── The job ──────────────────────────────────────────────────────────────

MIN_DEPTH = 3


def ref_script(ref, length, cores, min_depth=MIN_DEPTH, min_mapq=0):
    """mapping.script's second half for one reference of an existing BAM
    (/data/input.bam, sorted and indexed): cut to it, then depth, base counts,
    clipped reads, the reads lane and the consensus, as Map reads computes them."""
    r = shlex.quote(ref)
    region = shlex.quote(f"{ref}:1-{length}")
    return "\n".join([
        "set -eo pipefail", "exec 2>>/data/run.log", mapping.threads(cores),
        f"samtools view -b -o /data/mapped.bam /data/input.bam {r}",
        "samtools index /data/mapped.bam",
        f"samtools depth -aa -r {r} -Q {int(min_mapq)} /data/mapped.bam > /data/depth.txt",
        "samtools stats /data/mapped.bam | grep '^SN' > /data/stats.txt",
        "samtools faidx /data/ref.fasta",
        f"samtools mpileup -aa -r {r} -B -Q {mapping.BASE_QUALITY} -q {int(min_mapq)} -d 0 -f /data/ref.fasta /data/mapped.bam"
        " | awk -f /data/count.awk > /data/counts.txt",
        f"samtools view -F 0x904 -q {int(min_mapq)} /data/mapped.bam | awk -f /data/clips.awk > /data/clips.txt",
        f"samtools view -F 0xF04 -q {int(min_mapq)} /data/mapped.bam | awk -v OFS='\\t' '{{print $2, $4, $5, $6, $10}}' > /data/reads_raw.tsv",
        *(f"samtools consensus -@ $((T - 1)) -a -m simple -c 0.5 -d {int(min_depth)} --min-MQ {int(min_mapq)} "
          f"--show-del yes --show-ins {show} -r {region} -f fasta -o /data/consensus_{kind}.fa /data/mapped.bam"
          for show, kind in (("no", "ref"), ("yes", "ins"))),
    ]) + "\n"


def bam_job(job, workdir, path, file_kind, sorted_, pairs, unmatched, remove_after=False):
    try:
        _bam_job(job, workdir, Path(path), file_kind, sorted_, pairs, unmatched)
    finally:
        if remove_after:  # an upload's temporary copy
            Path(path).unlink(missing_ok=True)


def _bam_job(job, workdir, path, kind, sorted_, pairs, unmatched):
    steps(job, "Copying and indexing", *(f"Reading {ref}" for _, ref in pairs), "Saving")
    execution.ensure_tools_image(job)
    job["live_log"] = str(workdir / "run.log")
    for line in unmatched:
        job["log"] += f"Not imported: {line}\n"
    shutil.copyfile(path, workdir / f"input.{kind}")
    prepare = ["set -eo pipefail", "exec 2>>/data/run.log", mapping.threads(job.get("cores", 1)), *mapping.SORT_MEMORY]
    if kind == "sam" or not sorted_:
        prepare.append(f"samtools sort -@ $ST -m $SM -o /data/sorted.bam /data/input.{kind} && mv /data/sorted.bam /data/input.bam")
    prepare.append("samtools index /data/input.bam")
    (workdir / "prepare.sh").write_text("\n".join(prepare) + "\n", newline="\n")
    (workdir / "count.awk").write_text(mapping.COUNT_AWK, newline="\n")
    (workdir / "clips.awk").write_text(mapping.CLIP_AWK, newline="\n")
    _run(job, workdir, "prepare.sh")
    source = path.name
    for i, (item_id, ref) in enumerate(pairs, 1):
        step(job, i)
        reference = library.get_item(item_id)
        record = library.records(reference)[0]
        seq, length = str(record.seq), len(record.seq)
        (workdir / "ref.fasta").write_text(f">{ref}\n{seq}\n")
        for stale in ("ref.fasta.fai", "reads.tsv"):
            (workdir / stale).unlink(missing_ok=True)
        (workdir / "one.sh").write_text(ref_script(ref, length, job.get("cores", 1)), newline="\n")
        _run(job, workdir, "one.sh")
        depth, _ = mapping.read_depth(workdir / "depth.txt", length)
        per_position, insertions = mapping.read_consensus(workdir / "consensus_ref.fa", workdir / "consensus_ins.fa", length)
        agreement, variable = mapping.read_counts(workdir / "counts.txt", length, MIN_DEPTH)
        clips = mapping.read_clips(workdir / "clips.txt", length)
        stats = mapping.read_stats(workdir / "stats.txt")
        total = mapping.count_reads(workdir / "reads_raw.tsv")
        stride = max(1, -(-total // mapping.READS_MAX))
        reads = mapping.read_reads(workdir / "reads_raw.tsv", length, False, stride)
        reads_info = mapping.write_reads(workdir / "reads.tsv", reads, length, total)
        data = library.record_to_editor(record)
        data["geneticCode"] = library.genetic_code(record)
        groups = [{"name": source, "files": [source]}]
        result = {
            "reference_item": item_id, "reference": data, "depth": depth, "consensus_ref": per_position,
            "insertions": {str(k): v for k, v in insertions.items()}, "agreement": agreement,
            "variable": variable[:mapping.MAX_LISTED], "clips": clips, "reads": reads_info,
            "extension": {"left": 0, "right": 0, "rounds": 0},
            "options": {"read_type": "imported", "preset": f"imported from {source}", "base_quality": mapping.BASE_QUALITY,
                        "variable_min": 5, "min_depth": MIN_DEPTH, "min_mapq": 0, "extend_rounds": 0, "min_agree": 0.8},
            "summary": mapping.summary(seq, depth, per_position, insertions, stats, MIN_DEPTH, groups, variable),
            "imported": source,
        }
        name = f"{Path(source).stem}" if len(pairs) == 1 else f"{Path(source).stem} {reference['name']}"
        new_id = library.add_mapping(result, name, workdir / "mapped.bam", workdir / "reads.tsv")
        new = library.get_item(new_id)
        job["outputs"].append({"id": new["id"], "label": new["label"], "kind": new["kind"]})
        job["log"] += f"{ref}: {total:,} reads on {reference['label']}, mean depth {result['summary']['mean_depth']}×\n"
    step(job, len(pairs) + 1)


def _run(job, workdir, name):
    try:
        execution.tool(job, config.TOOLS_IMAGE, ["bash", f"/data/{name}"], workdir)
    except RuntimeError as e:
        log = workdir / "run.log"
        tail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
        raise RuntimeError(f"{e}\n{tail}".strip()) from None
