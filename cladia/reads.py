"""Reads as library items: one sequencing sample (a FASTQ file, a pair, or
lanes) copied into the library with a report of its reads.

An item of kind "reads" is a small manifest, <sample>.reads.json, beside the
read files themselves (entry "read_files", kept gzipped as they came). Files
are grouped into samples as Map reads pairs them (mapping.pair_reads: R1 with
R2). The import is a job: copying a few hundred MB and reading every read
takes a while. The manifest holds what the reads viewer shows: counts per file,
the length distribution, quality and base composition along the reads, mean
read qualities, GC per read and the first reads themselves, with each file's
size and SHA-256 (provenance). Read files stay out of saved versions; project
ZIPs leave them out unless asked; the item can drop them and keep its report.

Per-position numbers come from the first SAMPLE reads of each file; counts and
lengths from all of them. Positions past POSITIONS are left out of the
position plots (long reads).
"""

import gzip
import hashlib
import shutil
import time
from pathlib import Path

import numpy as np

from . import library, mapping
from .job_progress import step, steps

SAMPLE = 200_000   # reads per file for the per-position numbers
POSITIONS = 1000   # positions along a read the plots cover
KEEP = 1000        # reads kept to show
KEEP_BASES = 400   # of each, at most this many bases shown
LONG = 1000        # a mean read length above this: long reads


def _open(path):
    with open(path, "rb") as f:
        packed = f.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rb") if packed else open(path, "rb")


def is_fastq(path):
    """FASTQ (gzipped or not): '@' on the first line, '+' on the third."""
    try:
        with _open(path) as f:
            lines = [f.readline() for _ in range(4)]
    except (OSError, EOFError):
        return False
    return lines[0].startswith(b"@") and lines[2].startswith(b"+") and len(lines[1].strip()) == len(lines[3].strip()) > 0


def samples(paths):
    """The FASTQ files grouped into samples (pairs by name, the rest one each)."""
    return mapping.pair_reads(paths)


def _records(f):
    while True:
        head = f.readline()
        if not head:
            return
        seq, _, qual = f.readline().rstrip(b"\r\n"), f.readline(), f.readline().rstrip(b"\r\n")
        yield head[1:].split(None, 1)[0].decode("utf-8", "replace"), seq, qual


def file_stats(path, check=None):
    """One FASTQ file's numbers (see the module's notes)."""
    lengths, n, bases = {}, 0, 0
    qsum = np.zeros(POSITIONS, np.float64)
    qcount = np.zeros(POSITIONS, np.int64)
    comp = np.zeros((5, POSITIONS), np.int64)  # A C G T N
    read_q = np.zeros(42, np.int64)  # mean quality of each read, 0..41+
    gc = np.zeros(101, np.int64)
    kept = []
    letters = [ord(c) for c in "ACGT"]
    with _open(path) as f:
        for name, seq, qual in _records(f):
            n += 1
            length = len(seq)
            bases += length
            lengths[length] = lengths.get(length, 0) + 1
            if n <= SAMPLE and length:
                upper = np.frombuffer(seq.upper(), np.uint8)
                q = np.frombuffer(qual, np.uint8).astype(np.int16) - 33
                m = min(length, POSITIONS)
                qsum[:m] += q[:m]
                qcount[:m] += 1
                for k, letter in enumerate(letters):
                    comp[k, :m] += upper[:m] == letter
                comp[4, :m] += ~np.isin(upper[:m], letters)
                read_q[min(41, max(0, int(round(q.mean()))))] += 1
                called = int(np.isin(upper, letters).sum())
                if called:
                    gc[int(round(100 * int(((upper == 67) | (upper == 71)).sum()) / called))] += 1
            if len(kept) < KEEP:
                kept.append([name, seq[:KEEP_BASES].decode("ascii", "replace"), qual[:KEEP_BASES].decode("ascii", "replace"), length])
            if check and n % 100_000 == 0:
                check(n)
    used = int(np.nonzero(qcount)[0].max()) + 1 if qcount.any() else 0
    return {"reads": n, "bases": bases, "lengths": sorted([int(k), v] for k, v in lengths.items()),
            "quality": [round(float(s) / c, 2) for s, c in zip(qsum[:used], qcount[:used])],
            "composition": {b: [int(x) for x in comp[i, :used]] for i, b in enumerate("ACGTN")},
            "read_quality": [int(x) for x in read_q], "gc": [int(x) for x in gc],
            "sampled": min(n, SAMPLE), "first": kept}


def _sha256_copy(src, dst, check=None):
    digest = hashlib.sha256()
    with open(src, "rb") as a, open(dst, "wb") as b:
        while chunk := a.read(4 << 20):
            digest.update(chunk)
            b.write(chunk)
            if check:
                check()
    return digest.hexdigest()


def import_job(job, workdir, sample, files, remove_after=False):
    """Copies a sample's files into the library and writes its report; the item
    appears when both are done."""
    try:
        steps(job, "Copying", "Reading the reads", "Saving")
        copies, about = [], []
        total = sum(Path(f).stat().st_size for f in files) or 1
        done = 0
        for f in files:
            target = library.unique_path(library.library_dir() / Path(f).name)
            staging = target.with_name(target.name + ".copying")
            sha = _sha256_copy(f, staging)
            staging.replace(target)
            copies.append(target)
            done += Path(f).stat().st_size
            step(job, 0, done / total)
            about.append({"name": Path(f).name, "size": target.stat().st_size, "sha256": sha})
        step(job, 1, 0)
        per_file = []
        for i, path in enumerate(copies):
            per_file.append(file_stats(path, lambda n: step(job, 1, i / len(copies), f"{Path(path).name}: {n:,} reads")))
        mean = sum(s["bases"] for s in per_file) / max(1, sum(s["reads"] for s in per_file))
        manifest = {"version": 1, "sample": sample, "files": about, "stats": per_file,
                    "paired": len(files) == 2, "long": mean > LONG,
                    "imported": time.strftime("%Y-%m-%d %H:%M")}
        step(job, 2)
        new_id = library.add_reads(manifest, [c.name for c in copies], sample)
        item = library.get_item(new_id)
        job["outputs"].append({"id": item["id"], "label": item["label"], "kind": "reads"})
        job["log"] += "".join(f"{a['name']}: {s['reads']:,} reads, {s['bases']:,} bases, SHA-256 {a['sha256']}\n"
                              for a, s in zip(about, per_file))
    except BaseException:
        for c in locals().get("copies", []):
            Path(c).unlink(missing_ok=True)
        raise
    finally:
        if remove_after:
            for f in files:
                Path(f).unlink(missing_ok=True)


ITEM = "item:"  # a tool's read input names a reads item this way, files on disk by their path


def read_groups(values, read_type="illumina"):
    """A tool's read input -> samples [{name, files}] as mapping.read_groups gives
    them: each reads item is one sample (its files; long reads: a sample per file),
    files on disk are paired by name. ValueError when something is missing."""
    groups, paths = [], []
    for value in values:
        if not value.startswith(ITEM):
            paths.append(value)
            continue
        item = library.get_item(value[len(ITEM):])
        if not item or item["kind"] != "reads":
            raise ValueError("A chosen reads item is no longer in the library")
        files = [str(library.library_dir() / n) for n in item.get("read_files") or []]
        if not files or any(not Path(f).is_file() for f in files):
            raise ValueError(f"The read files of {item['label']} were deleted (only its report is left)")
        groups += ([{"name": item["name"], "files": files}] if read_type == "illumina"
                   else [{"name": f"{item['name']} {i + 1}" if len(files) > 1 else item["name"], "files": [f]}
                         for i, f in enumerate(files)])
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        raise ValueError("Not found: " + ", ".join(missing))
    return groups + (mapping.read_groups(paths, read_type) if paths else [])


def submit(paths, folder=None, remove_after=False):
    """One import job per sample; returns [(job id, sample name, file count)]."""
    from . import jobs
    started = []
    for group in samples([str(p) for p in paths]):
        job = jobs.submit("readsimport", f"Import reads {group['name']}", import_job, folder=folder,
                          sample=group["name"], files=group["files"], remove_after=remove_after)
        started.append((job["id"], group["name"], len(group["files"])))
    return started
