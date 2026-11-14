"""Mapping Illumina reads to a reference: the parts that aren't the job itself.

The job (tool_plugins.minimap2.run_mapping) runs minimap2 and samtools in the tools image on any
number of FASTQ files, paired by their names. The read files stay where they
are: their folders are mounted read-only, nothing is copied. The result is a
"mapping" item: a JSON file with the reference, the depth at every position,
the consensus, a summary and the index of the reads sidecar, plus the BAM next
to it if it was kept and the reads themselves (one line a read) for the lane.

Circular references: reads across the origin can't map to a linear sequence,
so the reference is mapped with its first EXTEND bases appended. The depth
there is folded back onto the start; reads in that stretch land on either copy,
so the consensus of each of those positions comes from the copy with more reads.

Linear references can first be extended (`extend_ends`): reads that hang off an
end are soft-clipped by minimap2; the consensus of those overhangs is added to
the end, and the ends are mapped again, round after round, until nothing grows
(as Geneious's "iterate up to N times"). Each round maps all the reads but only
against the two ends (`ENDS_WINDOW` bases each), which is quick.

Besides the depth and the consensus, every position gets its base counts
(samtools mpileup, counted by COUNT_AWK): the agreement (the share of reads with
the commonest base) and the variable positions; and the clipped reads (reads
that stop matching there and go on as overhang, CLIP_AWK), which mark the ends
worth extending and misassemblies.
"""

import heapq
import re
from pathlib import Path

READ_EXT = (".fastq.gz", ".fq.gz", ".fasta.gz", ".fna.gz", ".fa.gz", ".fastq", ".fq", ".fasta", ".fna", ".fa")
EXTEND = 1000  # bases appended to a circular reference
MAX_LISTED = 2000  # differences / gaps / variable positions kept in the summary
ENDS_WINDOW = 2000  # bases at each end mapped in an extension round
VARIABLE = 0.05  # a position is variable when its second base is in at least this share of reads...
VARIABLE_READS = 3  # ...and in at least this many (one error where few reads cover isn't variation)
BASE_QUALITY = 20  # bases below this quality aren't counted (most sequencing errors)

# One line per position of samtools mpileup -> "position A C G T deleted" (counts):
# read starts (^ and its quality) and ends ($) out, insertions/deletions after a
# base (+3ACG, -2AT) out, "." and "," are the reference base
COUNT_AWK = r"""BEGIN { FS = "\t" }
{
  s = $5; ref = toupper($3)
  gsub(/\^./, "", s); gsub(/\$/, "", s)
  while (match(s, /[-+][0-9]+/)) {
    n = substr(s, RSTART + 1, RLENGTH - 1) + 0
    s = substr(s, 1, RSTART - 1) substr(s, RSTART + RLENGTH + n)
  }
  m = gsub(/[.,]/, "", s); a = gsub(/[Aa]/, "", s); c = gsub(/[Cc]/, "", s)
  g = gsub(/[Gg]/, "", s); t = gsub(/[Tt]/, "", s); d = gsub(/[*#]/, "", s)
  if (ref == "A") a += m; else if (ref == "C") c += m; else if (ref == "G") g += m; else if (ref == "T") t += m
  print $2, a, c, g, t, d
}
"""

# SAM lines -> "L position count" / "R position count": reads soft-clipped on their
# left at their first aligned position, on their right at their last
CLIP_AWK = r"""BEGIN { FS = "\t" }
{
  cig = $6; pos = $4 + 0
  if (cig ~ /^[0-9]+S/) left[pos]++
  if (cig ~ /[0-9]+S$/) {
    span = 0; rest = cig
    while (match(rest, /[0-9]+[MDN=X]/)) { span += substr(rest, RSTART, RLENGTH - 1) + 0; rest = substr(rest, RSTART + RLENGTH) }
    right[pos + span - 1]++
  }
}
END { for (p in left) print "L", p, left[p]; for (p in right) print "R", p, right[p] }
"""

# <sample>_R1_001, <sample>_1, <sample>.R2 ...: the mate number, with an optional _001 after it
_MATE = re.compile(r"^(.*?)([._-])(R?)([12])((?:[._-]\d{3})?)$", re.I)


# ── Pairing the read files ───────────────────────────────────────────────

def read_stem(path):
    """The file name without its FASTQ extension."""
    name = Path(path).name
    for ext in READ_EXT:
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return Path(path).stem


READ_TYPES = {"illumina": "sr", "ont": "map-ont", "ont_hq": "lr:hq", "hifi": "map-hifi", "clr": "map-pb"}


def read_groups(paths, read_type="illumina"):
    if read_type not in READ_TYPES:
        raise ValueError("Unknown read type")
    return pair_reads(paths) if read_type == "illumina" else [
        {"name": read_stem(p), "files": [str(p)]} for p in paths]


def pair_reads(paths):
    """Groups FASTQ files into pairs by name (R1 with R2); a file without its
    mate is single-end. Returns [{"name", "files": [r1, r2] or [file]}]."""
    groups, order = {}, []
    for path in paths:
        m = _MATE.match(read_stem(path))
        key = (m.group(1), m.group(2), m.group(3).upper(), m.group(5)) if m else (read_stem(path),)
        mate = int(m.group(4)) if m else 0
        if key not in groups:
            groups[key] = {}
            order.append(key)
        groups[key].setdefault(mate, []).append(str(path))
    result = []
    for key in order:
        mates = groups[key]
        if set(mates) == {1, 2} and len(mates[1]) == len(mates[2]) == 1:
            result.append({"name": key[0], "files": [mates[1][0], mates[2][0]]})
        else:
            result += [{"name": read_stem(f), "files": [f]} for files in mates.values() for f in files]
    return result


# ── The container script ─────────────────────────────────────────────────

def mounts_for(groups):
    """Each folder holding read files -> where it is mounted; and each file's path inside."""
    folders, inside = {}, {}
    for group in groups:
        for f in group["files"]:
            folder = str(Path(f).resolve().parent)
            folders.setdefault(folder, f"/reads/{len(folders)}")
            inside[f] = f"{folders[folder]}/{Path(f).name}"
    return folders, inside


# samtools sort keeps 768 MB per thread by default: with every core (-@ $T) that
# is ~9 GB on a 12-core PC, more than WSL gets on a PC with little RAM ("couldn't
# allocate memory for bam_mem"). So: at most 4 sort threads, sharing 40 % of the
# memory free in the container, 100-768 MB each (more goes to temporary files).
SORT_MEMORY = [
    "ST=$(( T < 4 ? T : 4 ))",
    "SM=$(awk -v t=$ST '/MemAvailable/ {m = int($2 / 1024 * 0.4 / t); if (m < 100) m = 100; "
    "if (m > 768) m = 768; print m \"M\"}' /proc/meminfo)",
]


def threads(cores):
    """The script's thread count T: the job's cores (Settings > Cores), no more than
    the Linux side has (WSL may be given fewer, .wslconfig processors=)."""
    return f"T=$(( {int(cores)} < $(nproc) ? {int(cores)} : $(nproc) ))"


def script(groups, inside, length, min_depth, min_mapq, extend=0, cores=1, read_type="illumina"):
    """The bash script the job runs in /data (ref.fasta is there, `extend` bases
    longer than the reference when it is circular)."""
    q = lambda p: "'" + p.replace("'", "'\\''") + "'"
    # Noisy raw reads often have qualities below Q20; keep Q5+ bases for their
    # agreement/count tracks. HiFi and Illumina retain the existing Q20 filter.
    lines = ["set -eo pipefail", "exec 2>>/data/run.log", threads(cores), *SORT_MEMORY]
    for i, group in enumerate(groups):
        files = " ".join(q(inside[f]) for f in group["files"])
        lines.append(f"echo 'Mapping {group['name']}' >&2")
        lines.append(f"minimap2 -t $T -ax {READ_TYPES[read_type]} /data/ref.fasta {files} | samtools sort -@ $ST -m $SM -o /data/part{i}.bam -")
    if len(groups) > 1:
        lines.append("samtools merge -@ $T -f /data/mapped.bam /data/part*.bam && rm /data/part*.bam")
    else:
        lines.append("mv /data/part0.bam /data/mapped.bam")
    lines += [
        "samtools index /data/mapped.bam",
        "echo 'Depth and consensus' >&2",
        f"samtools depth -aa -Q {int(min_mapq)} /data/mapped.bam > /data/depth.txt",
        "samtools stats /data/mapped.bam | grep '^SN' > /data/stats.txt",
        "echo 'Base counts and clipped reads' >&2",
        "samtools faidx /data/ref.fasta",
        f"samtools mpileup -aa -B -Q {5 if read_type in {'ont', 'clr'} else BASE_QUALITY} -q {int(min_mapq)} -d 0 -f /data/ref.fasta /data/mapped.bam"
        " | awk -f /data/count.awk > /data/counts.txt",
        f"samtools view -F 0x904 -q {int(min_mapq)} /data/mapped.bam | awk -f /data/clips.awk > /data/clips.txt",
        f"samtools view -F 0xF04 -q {int(min_mapq)} /data/mapped.bam"
        " | awk -v OFS='\t' '{print $2, $4, $5, $6, $10}' > /data/reads_raw.tsv",
    ]
    # simple majority consensus, N below the minimum depth; once in reference
    # coordinates (deletions as *, no insertions), once with the insertions
    # (samtools 1.16 can't mark them; read_consensus compares the two). Circular:
    # also for the appended copy of the start (_ext).
    regions = [("", 1, length)] + ([("_ext", length + 1, length + extend)] if extend else [])
    for suffix, start, end in regions:
        for show_ins, kind in (("no", "ref"), ("yes", "ins")):
            lines.append(f"samtools consensus -@ $((T - 1)) -a -m simple -c 0.5 -d {int(min_depth)} --min-MQ {int(min_mapq)} "
                         f"--show-del yes --show-ins {show_ins} -r ref:{start}-{end} -f fasta "
                         f"-o /data/consensus{suffix}_{kind}.fa /data/mapped.bam")
    return "\n".join(lines) + "\n"


# ── Extending the ends of a linear reference ─────────────────────────────

def ends_reference(seq):
    """The bits of the reference an extension round maps to: its two ends (or all
    of it when short), as [(name, bases)]."""
    if len(seq) <= 2 * ENDS_WINDOW:
        return [("whole", seq)]
    return [("left", seq[:ENDS_WINDOW]), ("right", seq[-ENDS_WINDOW:])]


def ends_script(groups, inside, cores=1, read_type="illumina"):
    """The bash script of an extension round: every read mapped to /data/ends.fasta,
    the mapped ones (primary) kept as SAM in /data/ends.sam."""
    q = lambda p: "'" + p.replace("'", "'\\''") + "'"
    lines = ["set -eo pipefail", "exec 2>>/data/run.log", threads(cores), ": > /data/ends.sam"]
    for group in groups:
        files = " ".join(q(inside[f]) for f in group["files"])
        lines.append(f"minimap2 -t $T -ax {READ_TYPES[read_type]} /data/ends.fasta {files} | samtools view -F 0x904 - >> /data/ends.sam")
    return "\n".join(lines) + "\n"


_CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")


def clipped_reads(sam_path):
    """Reads in a SAM file -> [(contig, first aligned position, last aligned
    position (0-based), bases clipped on the left, bases clipped on the right)]."""
    reads = []
    with open(sam_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            cols = line.split("\t")
            if len(cols) < 10 or cols[5] == "*":
                continue
            ops = [(int(n), op) for n, op in _CIGAR.findall(cols[5])]
            span = sum(n for n, op in ops if op in "MDN=X")
            left = ops[0][0] if ops[0][1] == "S" else 0
            right = ops[-1][0] if ops[-1][1] == "S" else 0
            seq, start = cols[9], int(cols[3]) - 1
            reads.append((cols[2], start, start + span - 1, seq[:left], seq[len(seq) - right:] if right else ""))
    return reads


def extend_ends(reads, lengths, min_depth, min_agree):
    """New bases beyond each end, from the overhangs of the reads: (left, right).
    lengths: contig name -> length; the "left" contig (or "whole") is the start
    of the reference, "right" (or "whole") its end. A base is added while at
    least min_depth reads reach it and min_agree of them agree; the first place
    that fails stops that side."""
    outward = {"left": {}, "right": {}}  # distance from the end (1 = the next base) -> base counts
    for contig, start, end, left, right in reads:
        if contig not in lengths:
            continue
        if left and contig in ("left", "whole"):
            for k, base in enumerate(left):  # left clip base k sits at start - len(left) + k
                d = len(left) - k - start  # bases beyond the start
                if d >= 1:
                    counts = outward["left"].setdefault(d, {})
                    counts[base] = counts.get(base, 0) + 1
        if right and contig in ("right", "whole"):
            for k, base in enumerate(right):
                d = end + 1 + k - lengths[contig] + 1
                if d >= 1:
                    counts = outward["right"].setdefault(d, {})
                    counts[base] = counts.get(base, 0) + 1
    grown = {}
    for side, columns in outward.items():
        bases = []
        for d in range(1, len(columns) + 2):
            counts = columns.get(d, {})
            depth = sum(counts.values())
            if depth < min_depth:
                break
            base, top = max(counts.items(), key=lambda kv: kv[1])
            if top / depth < min_agree or base.upper() not in "ACGT":
                break
            bases.append(base.upper())
        grown[side] = "".join(bases)
    return grown["left"][::-1], grown["right"]


# ── Reading the results ──────────────────────────────────────────────────

def read_depth(path, length):
    """(Depth per position (0-based) with the extension folded back onto the start,
    the depth as mapped: the extension separate)."""
    raw = []
    with open(path) as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 3:
                raw.append(int(parts[2]))
    depth = [0] * length
    for pos, d in enumerate(raw):
        depth[pos % length] += d
    return depth, raw


def merge_origin(per_position, insertions, ext_positions, ext_insertions, raw, length):
    """Circular: each of the first positions from whichever copy (the start, or the
    appended one) has more reads there."""
    chars = list(per_position)
    for i, ch in enumerate(ext_positions):
        if length + i < len(raw) and raw[length + i] > raw[i]:
            chars[i] = ch
            insertions.pop(i, None)
            if i in ext_insertions:
                insertions[i] = ext_insertions[i]
    return "".join(chars), insertions


def _fasta_text(path):
    return "".join(line.strip() for line in open(path) if not line.startswith(">"))


def read_consensus(ref_path, ins_path, length):
    """The two consensus files -> (one character per reference position, "*" for a
    deletion; {position: bases inserted after it}). The file with insertions is the
    other one with bases added: walking both, a base that doesn't match is inserted.
    (In a run of the same base, which one counts as inserted doesn't change the
    sequence.)"""
    per_position, with_ins = _fasta_text(ref_path), _fasta_text(ins_path)
    if len(per_position) != length:
        raise RuntimeError(f"The consensus has {len(per_position)} positions, the reference {length}")
    insertions = {}
    i = j = 0
    while j < len(with_ins):
        if i < length and with_ins[j] == per_position[i]:
            i += 1
        else:  # inserted after position i - 1
            insertions[i - 1] = insertions.get(i - 1, "") + with_ins[j]
        j += 1
    if i != length:
        raise RuntimeError("The two consensus files don't match")
    return per_position, insertions


def read_counts(path, length, min_depth):
    """counts.txt -> (agreement per position in % (the commonest base's share; 100
    where no reads), variable positions [{pos, depth, counts}]). Positions past
    the end (a circular reference's appended start) fold back onto the start."""
    counts = [[0] * 5 for _ in range(length)]  # A C G T deleted
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 6:
                row = counts[(int(parts[0]) - 1) % length]
                for k in range(5):
                    row[k] += int(parts[k + 1])
    agreement, variable = [], []
    for pos, row in enumerate(counts):
        depth = sum(row)
        if not depth:
            agreement.append(100)
            continue
        ranked = sorted(row, reverse=True)
        agreement.append(int(100 * ranked[0] / depth))
        if depth >= min_depth and ranked[1] >= VARIABLE_READS and ranked[1] / depth >= VARIABLE:
            variable.append({"pos": pos, "depth": depth, "counts": dict(zip(("A", "C", "G", "T", "-"), row))})
    return agreement, variable


def read_clips(path, length):
    """clips.txt -> [[position (0-based), reads clipped there]] (folded as above)."""
    clips = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3:
                pos = (int(parts[1]) - 1) % length
                clips[pos] = clips.get(pos, 0) + int(parts[2])
    return sorted([p, n] for p, n in clips.items())


def read_stats(path):
    """The SN lines of samtools stats, as {name: number}."""
    stats = {}
    for line in open(path):
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 3:
            try:
                stats[parts[1].rstrip(":")] = float(parts[2])
            except ValueError:
                pass
    return stats


def runs(flags):
    """[start, end] (inclusive) of each run of True."""
    result, start = [], None
    for i, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            result.append([start, i - 1])
            start = None
    return result


def summary(reference_seq, depth, per_position, insertions, stats, min_depth, groups, variable=None):
    """What the mapping view shows besides the depth: numbers, gaps, differences."""
    length = len(depth)
    ref = reference_seq.upper()
    differences = []
    for start, end in runs([per_position[i] == "*" for i in range(length)]):
        differences.append({"start": start, "end": end, "kind": "deletion", "ref": ref[start:end + 1], "alt": ""})
    for i, ch in enumerate(per_position):
        if ch not in "*N" and ch.upper() != ref[i]:
            differences.append({"start": i, "end": i, "kind": "substitution", "ref": ref[i], "alt": ch})
    for pos, bases in insertions.items():
        differences.append({"start": pos, "end": pos, "kind": "insertion", "ref": "", "alt": bases})
    differences.sort(key=lambda d: d["start"])
    gaps = runs([d < min_depth and per_position[i] != "*" for i, d in enumerate(depth)])  # a deletion isn't a gap
    total = stats.get("raw total sequences", 0)
    return {
        "reads": int(total),
        "mapped": int(stats.get("reads mapped", 0)),
        "mapped_percent": round(100 * stats.get("reads mapped", 0) / total, 2) if total else 0,
        "properly_paired_percent": round(100 * stats.get("reads properly paired", 0) / total, 2) if total else 0,
        "read_length": round(stats.get("average length", 0), 1),
        "insert_size": round(stats.get("insert size average", 0), 1),
        "insert_size_sd": round(stats.get("insert size standard deviation", 0), 1),
        "mean_depth": round(sum(depth) / length, 1) if length else 0,
        "min_depth": min(depth) if depth else 0,
        "max_depth": max(depth) if depth else 0,
        "covered_percent": round(100 * sum(d >= min_depth for d in depth) / length, 2) if length else 0,
        "gaps": gaps[:MAX_LISTED],
        "gap_count": len(gaps),
        "differences": differences[:MAX_LISTED],
        "difference_count": len(differences),
        "variable_count": len(variable or []),
        "pairs": [{"name": g["name"], "files": [Path(f).name for f in g["files"]]} for g in groups],
    }


# ── The reads themselves (the reads lane in the mapping view) ────────────
# The job writes one line per read (the reads the coverage uses: primary, not
# duplicate, at the minimum MAPQ), in reference coordinates and sorted by start.
# The mapping JSON keeps a byte offset every READS_BIN bases, so a view reads
# just its window. Above READS_MAX reads, every READS stride-th read is kept (a
# deterministic sample, still spread over the whole mapping). Each kept read
# also stores its stable, non-overlapping display lane and ordinal; the latter
# keeps viewport sampling identical across overlapping requests.

READS_MAX = 200_000   # reads kept in the sidecar
READS_BIN = 1000      # bases per index entry
READS_LIMIT = 500     # reads a window request returns
READS_SCAN = 5000     # reads a window request looks at, then says there are more

# Older sidecars don't store a lane per read. Cache lanes assigned across the
# entire file so horizontal window requests can still draw stable rows.
_READ_ROW_CACHE = {}


def _read_rows(reads):
    """Assign stable, non-overlapping lanes to reads sorted by start position."""
    active, available = [], []
    result = [0] * len(reads)
    next_row = 0
    for index in sorted(range(len(reads)), key=lambda i: (reads[i][0], i)):
        start, end = reads[index][:2]
        while active and active[0][0] < start:
            _, row = heapq.heappop(active)
            heapq.heappush(available, row)
        row = heapq.heappop(available) if available else next_row
        if row == next_row:
            next_row += 1
        result[index] = row
        heapq.heappush(active, (end, row))
    return result, next_row


def _legacy_read_rows(path):
    """Return (byte offset -> (row, index), row count) for an older sidecar."""
    path = Path(path)
    stat = path.stat()
    key = str(path.resolve())
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _READ_ROW_CACHE.get(key)
    if cached and cached[0] == signature:
        return cached[1], cached[2]

    reads, offsets = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        while True:
            offset = f.tell()
            line = f.readline()
            if not line:
                break
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            reads.append((int(parts[0]), int(parts[1])))
            offsets.append(offset)
    assigned, count = _read_rows(reads)
    rows = {offset: (row, index) for index, (offset, row) in
            enumerate(zip(offsets, assigned))}
    _READ_ROW_CACHE[key] = (signature, rows, count)
    while len(_READ_ROW_CACHE) > 8:
        _READ_ROW_CACHE.pop(next(iter(_READ_ROW_CACHE)))
    return rows, count


def _span(cigar):
    """Reference bases an alignment covers (M, D, N, =, X)."""
    return sum(int(n) for n, op in _CIGAR.findall(cigar) if op in "MDN=X")


def _split_read(start, cigar, seq, length):
    """A read crossing a circular reference's origin -> [(start, cigar, seq)] pieces."""
    # Split CIGAR runs directly: expanding a 50 kb read into one operation per
    # base made circular long-read sidecars unnecessarily slow and memory-heavy.
    pieces, ref, read, piece, split_at = [[], []], start, 0, 0, None
    for raw_n, op in _CIGAR.findall(cigar):
        remaining = int(raw_n)
        while remaining:
            if ref >= length and piece == 0:
                piece, split_at = 1, read
            take = min(remaining, length - ref) if piece == 0 and op in "MDN=X" else remaining
            runs = pieces[piece]
            if runs and runs[-1][1] == op:
                runs[-1][0] += take
            else:
                runs.append([take, op])
            if op in "MDN=X": ref += take
            if op in "MIS=X": read += take
            remaining -= take
    if piece == 0:
        return [(start, cigar, seq)]
    packed = lambda runs: "".join(f"{n}{op}" for n, op in runs)
    return [(start, packed(pieces[0]), seq[:split_at]), (0, packed(pieces[1]), seq[split_at:])]


def count_reads(path):
    """How many lines reads_raw.tsv has (counted, not parsed)."""
    with open(path, "rb") as f:
        return sum(block.count(b"\n") for block in iter(lambda: f.read(1 << 20), b""))


def read_reads(path, length, circular, stride=1):
    """reads_raw.tsv (samtools view: flag, POS, MAPQ, CIGAR, SEQ) ->
    [(start, end, strand, mapq, cigar, seq)] (0-based, sorted): circular
    positions folded back, reads across the origin split in two. stride: only
    every stride-th line is read (the job keeps at most READS_MAX: a deep mapping
    has millions of reads, and holding them all took the server's memory)."""
    reads = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for number, line in enumerate(f):
            if number % stride:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 5 or parts[3] == "*" or not parts[4]:
                continue
            flag, pos, mapq, cigar, seq = parts
            start = int(pos) - 1
            span = _span(cigar)
            if circular:
                if start >= length:
                    start %= length
                if start + span > length:  # across the origin
                    for s, c, q in _split_read(start, cigar, seq, length):
                        reads.append((s, s + _span(c) - 1, "-" if int(flag) & 16 else "+",
                                      int(mapq), c, q))
                    continue
            reads.append((start, start + span - 1, "-" if int(flag) & 16 else "+",
                          int(mapq), cigar, seq))
    reads.sort(key=lambda r: r[0])
    return reads


def write_reads(path, reads, length, total=None):
    """The sidecar and its index: one line a read, an offset every READS_BIN
    bases. Returns what the mapping JSON keeps under "reads". total: how many
    there were, when `reads` were already thinned (read_reads' stride)."""
    own = max(1, -(-len(reads) // READS_MAX))
    kept = reads[::own]
    total = total or len(reads)
    stride = own if total == len(reads) else max(1, -(-total // max(1, len(kept))))
    row_numbers, row_count = _read_rows(kept)
    max_span = max((r[1] - r[0] + 1 for r in kept), default=1)
    offsets, size = [], 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for index, (r, row) in enumerate(zip(kept, row_numbers)):
            line = f"{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}\t{r[5]}\t{row}\t{index}\n"
            offsets.append((r[0], size))
            size += len(line.encode("utf-8"))
            f.write(line)
    bins, j = {}, 0
    for b in range(0, max(1, length), READS_BIN):
        while j < len(offsets) and offsets[j][0] < b - max_span:
            j += 1
        bins[str(b)] = offsets[j][1] if j < len(offsets) else size
    return {"bin": READS_BIN, "max_span": max_span, "bins": bins, "total": total,
            "kept": len(kept), "stride": stride, "rows": row_count, "indexed": True}


def read_window(path, info, start, end, limit=READS_LIMIT, scan=READS_SCAN * 20, ref_length=None,
                all_reads=False):
    """The reads overlapping [start, end] (0-based), spread over it: a stride from
    the mapping's density, so a wide window shows reads from all along it, not
    just its first ones. Sampling is based on each read's mapping-wide ordinal,
    not its position within this window, so overlapping requests return the same
    reads. Returns the reads, how many are in view, and whether there are more."""
    bins = info.get("bins") or {}
    bin_size = info.get("bin") or READS_BIN
    b = max(0, (start // bin_size) * bin_size)
    while b > 0 and str(b) not in bins:
        b -= bin_size
    width = max(1, end - start + 1)
    density = (info.get("kept") or 0) / max(1, ref_length or width)
    stride = 1 if all_reads else max(1, int(density * width / max(1, limit) + 0.999))
    legacy_rows, legacy_count = _legacy_read_rows(path) if not info.get("indexed") else ({}, 0)
    found, seen, more = [], 0, False
    with open(path, encoding="utf-8", errors="replace") as f:
        f.seek(bins.get(str(b), 0))
        while True:
            offset = f.tell()
            line = f.readline()
            if not line:
                break
            parts = line.rstrip("\n").split("\t")
            if len(parts) not in (6, 7, 8):
                continue
            if int(parts[0]) > end:
                break
            if int(parts[1]) >= start:  # it starts before the window and ends after it: in view
                seen += 1
                if seen > scan:
                    more = True
                    break
                if len(parts) == 8:
                    row, index = int(parts[6]), int(parts[7])
                else:
                    row, index = legacy_rows.get(offset, (0, seen - 1))
                if index % stride == 0 and len(found) < limit:
                    found.append([int(parts[0]), int(parts[1]), parts[2], int(parts[3]),
                                  parts[4], parts[5], row, index])
    return {"reads": found, "in_view": seen, "rows": info.get("rows") or legacy_count,
            "more": more or seen > len(found) or (not all_reads and len(found) >= limit),
            "all": all_reads}


# ── The consensus as a sequence ──────────────────────────────────────────

def consensus_sequence(per_position, insertions):
    """The consensus itself (deletions out, insertions in), and for each reference
    position where it is in the consensus (a deleted one: where the next base is)."""
    bases, where = [], []
    for i, ch in enumerate(per_position):
        where.append(len(bases))
        if ch != "*":
            bases.append(ch)
        bases += list(insertions.get(i, ""))
    return "".join(bases), where


def lift_features(features, per_position, where, length):
    """Reference features (editor JSON) moved onto the consensus; a part that is
    all deleted is dropped."""
    def start_of(p):
        return where[p]

    def end_of(p):  # the last consensus base at or before reference position p
        while p >= 0 and per_position[p] == "*":
            p -= 1
        return where[p] if p >= 0 else -1

    lifted = []
    for f in features:
        parts = f.get("locations") or [{"start": f["start"], "end": f["end"]}]
        moved = []
        for part in parts:
            if not (0 <= part["start"] < length and 0 <= part["end"] < length) or part["start"] > part["end"]:
                moved = None  # across the origin: left out
                break
            s, e = start_of(part["start"]), end_of(part["end"])
            if e >= s:
                moved.append({"start": s, "end": e})
        if not moved:
            continue
        out = {k: v for k, v in f.items() if k not in ("id", "locations")}
        out.update(start=moved[0]["start"], end=moved[-1]["end"])
        if len(moved) > 1:
            out["locations"] = moved
        lifted.append(out)
    return lifted
