"""Read mapping engine using minimap2 and Cladia's existing mapping pipeline."""
import os
import shlex
import shutil
import time
from pathlib import Path
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "paths": [
            "/usr/local/bin/minimap2",
            "/usr/local/bin/samtools"
        ],
        "estimateBytes": 9000000,
        "note": "Includes samtools for BAM handling; shared Linux libraries are counted separately."
    },
    "id": "minimap2", "label": "minimap2", "blurb": "Maps short and long reads; the mapping includes coverage, consensus and differences.",
    "supports": ["map.reads"], "order": 10, "lane": "compute", "cores": None, "jobKind": "map",
    "options": [
        {"key": "read_type", "label": "Read type", "type": "choice", "default": "ont_hq", "choices": [
            {"value": "illumina", "label": "Illumina (short, paired)"},
            {"value": "ont", "label": "Nanopore (noisy / older reads)"},
            {"value": "ont_hq", "label": "Nanopore, high accuracy (Q20+)"},
            {"value": "hifi", "label": "PacBio HiFi"},
            {"value": "clr", "label": "PacBio CLR / older"},
        ]},
        {"key": "extend", "label": "Extend the ends from the reads (a linear contig)", "type": "bool", "default": False,
         "visibleWhen": {"key": "read_type", "values": ["illumina", "hifi"]}},
        {"key": "extend_rounds", "label": "Rounds, at most", "type": "int", "default": 5, "min": 1, "max": 50,
         "visibleWhen": {"key": "extend", "values": [True]}},
        {"key": "min_agree", "label": "Reads agreeing on a new base (%)", "type": "int", "default": 80, "min": 50, "max": 100,
         "visibleWhen": {"key": "extend", "values": [True]}},
        {"key": "min_depth", "label": "Minimum depth for the consensus", "type": "int", "default": 3, "min": 1, "advanced": True},
        {"key": "min_mapq", "label": "Minimum mapping quality", "type": "int", "default": 0, "min": 0, "max": 60, "advanced": True,
         "help": "Higher values can leave repeated regions uncovered."},
        {"key": "save_bam", "label": "Keep the mapped reads (BAM)", "type": "bool", "default": False},
    ],
}


UNPACKED_SIZE = 5

UNPACK_SPARE = 2 << 30

def run(job, workdir, operation, items, output, add_to=None, groups=None, read_type="ont_hq",
        extend=False, extend_rounds=5, min_agree=80, min_depth=3, min_mapq=0, save_bam=False):
    return run_mapping(job, workdir, reference=items[0], groups=groups, read_type=read_type, output=output,
                        min_depth=min_depth, min_mapq=min_mapq, save_bam=save_bam,
                        extend_rounds=extend_rounds if extend and read_type in {"illumina", "hifi"} else 0,
                        min_agree=min_agree / 100)


def run_mapping(job, workdir, reference, groups, output, min_depth=3, min_mapq=0, save_bam=False,
                extend_rounds=0, min_agree=0.8, read_type="illumina"):
    try:
        map_reads(job, workdir, reference, groups, output, min_depth, min_mapq, save_bam,
                 extend_rounds, min_agree, read_type)
    finally:  # the unpacked reads (_unpacked) are large: gone even if the job fails
        if job.get("volume"):
            api.volume_remove(job.pop("volume"))

def map_reads(job, workdir, reference, groups, output, min_depth=3, min_mapq=0, save_bam=False,
             extend_rounds=0, min_agree=0.8, read_type="illumina"):
    """Maps the read files (groups from mapping.pair_reads) to a reference sequence;
    the result is a mapping item (see mapping.py). extend_rounds: a linear
    reference is first extended at both ends from the reads hanging off them,
    up to that many rounds (min_agree: the share of reads that must agree on a
    new base)."""
    if read_type not in api.MAPPING_READ_TYPES:
        raise ValueError("Unknown read type")
    if read_type not in {"illumina", "hifi"} and extend_rounds:
        raise ValueError("Extension rounds are currently supported for Illumina and HiFi reads only")
    record = api.records(reference)[0]
    seq = str(record.seq)
    circular = record.annotations.get("topology") == "circular"
    folders, inside = api.mapping_mounts_for(groups)
    extending = bool(extend_rounds) and not circular
    # the stages map.sh announces in run.log (_watch_map), after the extension rounds
    api.steps(job, *(["Extending ends"] if extending else []), *(f"Mapping {g['name']}" for g in groups),
          "Depth and consensus", "Base counts and clipped reads", "Reading results")
    api.ensure_tools_image(job)
    job["progress"]["detail"] = ""
    # minimap2 / samtools progress, shown while it runs and saved with the log (_run)
    job["live_log"] = str(workdir / "run.log")
    (workdir / "count.awk").write_text(api.MAPPING_COUNT_AWK, newline="\n")
    (workdir / "clips.awk").write_text(api.MAPPING_CLIP_AWK, newline="\n")

    added = {"left": "", "right": "", "rounds": 0}
    if extend_rounds and circular:
        job["log"] += "The reference is circular: its ends aren't extended.\n"
    elif extend_rounds:
        inside = _unpacked(job, workdir, groups, inside, folders)  # every round reads them all
        (workdir / "ends.sh").write_text(api.mapping_ends_script(groups, inside, job.get("cores", 1), read_type=read_type), newline="\n")
        for round_ in range(1, extend_rounds + 1):
            api.step(job, 0, (round_ - 1) / extend_rounds, f"round {round_} of at most {extend_rounds}")
            pieces = api.mapping_ends_reference(added["left"] + seq + added["right"])
            (workdir / "ends.fasta").write_text("".join(f">{name}\n{bases}\n" for name, bases in pieces))
            _run_script(job, workdir, "ends.sh", folders)
            left, right = api.mapping_extend_ends(api.mapping_clipped_reads(workdir / "ends.sam"),
                                              {name: len(bases) for name, bases in pieces}, min_depth, min_agree)
            job["log"] += f"Extension round {round_}: {len(left)} bp at the start, {len(right)} bp at the end\n"
            if not left and not right:
                break
            added["left"], added["right"], added["rounds"] = left + added["left"], added["right"] + right, round_
    shift = len(added["left"])
    seq = added["left"] + seq + added["right"]
    length = len(seq)

    extend = (min(api.MAPPING_EXTEND, length) if read_type == "illumina" else length) if circular else 0
    (workdir / "ref.fasta").write_text(f">ref\n{seq + seq[:extend]}\n")
    (workdir / "map.sh").write_text(api.mapping_script(groups, inside, length, min_depth, min_mapq, extend,
                                                   job.get("cores", 1), read_type=read_type), newline="\n")
    log = workdir / "run.log"
    api.watch_log(job, log, _watch_map, len(log.read_text(encoding="utf-8", errors="replace")) if log.exists() else 0)
    api.step(job, 1 if extending else 0)
    _run_script(job, workdir, "map.sh", folders)
    job["watch"] = None
    api.step(job, len(job["progress"]["steps"]) - 1)

    depth, raw = api.mapping_read_depth(workdir / "depth.txt", length)
    per_position, insertions = api.mapping_read_consensus(workdir / "consensus_ref.fa", workdir / "consensus_ins.fa", length)
    if extend:
        ext = api.mapping_read_consensus(workdir / "consensus_ext_ref.fa", workdir / "consensus_ext_ins.fa", extend)
        per_position, insertions = api.mapping_merge_origin(per_position, insertions, *ext, raw, length)
    agreement, variable = api.mapping_read_counts(workdir / "counts.txt", length, min_depth)
    clips = api.mapping_read_clips(workdir / "clips.txt", length)
    stats = api.mapping_read_stats(workdir / "stats.txt")
    reads_info = None
    if (workdir / "reads_raw.tsv").exists():  # the reads lane's sidecar (a stand-in may skip it)
        total = api.mapping_count_reads(workdir / "reads_raw.tsv")  # thinned while read: not all in memory
        stride = max(1, -(-total // api.MAPPING_READS_MAX))
        reads = api.mapping_read_reads(workdir / "reads_raw.tsv", length, circular, stride)
        reads_info = api.mapping_write_reads(workdir / "reads.tsv", reads, length, total)
    data = api.record_to_editor(record)
    data["geneticCode"] = api.genetic_code(record)
    if shift or added["right"]:  # the extended reference: features moved along, the new ends marked
        data["sequence"] = seq
        for f in data["features"]:
            f["start"] += shift
            f["end"] += shift
            for part in f.get("locations") or []:
                part["start"] += shift
                part["end"] += shift
        note = {"note": [f"extended from the reads ({added['rounds']} rounds)"]}
        for start, end, side in ((0, shift - 1, "start"), (length - len(added["right"]), length - 1, "end")):
            if end >= start:
                data["features"].append({"name": f"extension ({side})", "type": "misc_feature", "strand": 1,
                                         "start": start, "end": end,
                                         "notes": {"label": [f"extension ({side})"], **note}})
    result = {
        "reference_item": reference["id"],
        "reference": data,
        "depth": depth,
        "consensus_ref": per_position,
        "insertions": {str(k): v for k, v in insertions.items()},
        "agreement": agreement,
        "variable": variable[:api.MAPPING_MAX_LISTED],
        "clips": clips,
        **({"reads": reads_info} if reads_info else {}),
        "extension": {"left": shift, "right": len(added["right"]), "rounds": added["rounds"]},
        "options": {"read_type": read_type, "preset": api.MAPPING_READ_TYPES[read_type],
                    "base_quality": 5 if read_type in {"ont", "clr"} else api.MAPPING_BASE_QUALITY,
                    "variable_min": 20 if read_type in {"ont", "clr"} else 5,
                    "min_depth": min_depth, "min_mapq": min_mapq, "extend_rounds": extend_rounds,
                    "min_agree": min_agree},
        "summary": api.mapping_summary(seq, depth, per_position, insertions, stats, min_depth, groups, variable),
    }
    new_id = api.add_mapping(result, output, workdir / "mapped.bam" if save_bam else None,
                                 workdir / "reads.tsv" if reads_info else None)
    item = api.get_item(new_id)
    job["outputs"].append({"id": item["id"], "label": item["label"], "kind": item["kind"]})

def _unpacked(job, workdir, groups, inside, folders):
    """Gzipped read files unpacked once, for jobs that read them several times
    (extension rounds, then the mapping), into a wslc volume of the job's: on the
    Linux side, kept between its containers. Reads come into a container from
    Windows folders slowly; from the volume, a pass of minimap2 over 2 x 1.5 M reads
    took 1.8 s instead of 4.7 s from the .gz (unpacking them once: 1.8 s). Unpacked
    into the job folder instead it was slower than the .gz: 4 x the bytes crossing.
    Only if the disk holding wslc's storage (it grows to fit, and keeps the space for
    later) has room: about 5 x the .gz and 2 GB to spare; else read as they are.
    `folders` gets the volume; returns the files' paths inside the container."""
    packed = [f for g in groups for f in g["files"] if f.lower().endswith(".gz")]
    if not packed:
        return inside
    need = sum(Path(f).stat().st_size for f in packed) * UNPACKED_SIZE
    storage = Path(os.environ.get("LOCALAPPDATA") or workdir)  # Cladia's Linux keeps its disk there
    free = shutil.disk_usage(storage if storage.exists() else workdir).free
    if free < need + UNPACK_SPARE:
        job["log"] += (f"Reads read from their .gz files (unpacking them would need about {need / 1e9:.1f} GB "
                       f"and {free / 1e9:.1f} GB are free)\n")
        return inside
    volume = f"cladia-{job['id']}"
    ok, message = api.volume_create(volume)
    if not ok:
        job["log"] += f"Reads read from their .gz files (no volume for them: {message})\n"
        return inside
    job["volume"] = volume  # removed when the job ends (run_mapping)
    folders[f"volume:{volume}"] = "/vol"
    api.step(job, 0, 0, "unpacking the reads")
    plain = {f: f"/vol/{i}.fq" for i, f in enumerate(packed)}
    lines = ["set -eo pipefail", "exec 2>>/data/run.log"]
    lines += [f"gzip -dc {shlex.quote(inside[f])} > {plain[f]} &" for f in packed]
    lines.append("wait")  # (set -e doesn't see background jobs: each file is checked)
    lines += [f"test -s {plain[f]}" for f in packed]
    (workdir / "unpack.sh").write_text("\n".join(lines) + "\n", newline="\n")
    started = time.time()
    _run_script(job, workdir, "unpack.sh", folders)
    job["log"] += f"Reads unpacked in {time.time() - started:.0f} s, into the job's volume (each round and the mapping read them)\n"
    return {**inside, **plain}

def _run_script(job, workdir, name, folders):
    """Runs one of the job's bash scripts in the tools image (the read folders
    mounted); a failure carries the end of run.log."""
    try:
        api.run_container(job, api.tools_image(), ["bash", f"/data/{name}"], workdir, mounts=folders)
    except RuntimeError as e:
        log = workdir / "run.log"
        tail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
        raise RuntimeError(f"{e}\n{tail}".strip()) from None

def _watch_map(text, progress):
    """map.sh's run.log: the stages it announces (Mapping <reads>, Depth and
    consensus, Base counts...)."""
    at = None
    for line in text.splitlines():
        for i, name in enumerate(progress["steps"]):
            if line.startswith(name):
                at = i
    return {"at": at, "fraction": None, "detail": ""} if at is not None else None
