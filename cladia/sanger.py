"""Sanger reads: ABI trace files (.ab1), their quality trimming, and assembling
forward and reverse reads into a consensus.

A read in the library is a sequence item (GenBank, so every tool can use it)
with its trace beside it in <name>.trace.json (entry "trace"): the base calls,
qualities and peak positions of the whole read, the four dye channels, and the
part kept after trimming (start, end). The sequence is that part.
"""

import re

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

TRIM_LIMIT = 0.05  # error probability: bases worse than this at the ends are trimmed (as Geneious)


# ── Reading .ab1 files ───────────────────────────────────────────────────

def is_abi(raw):
    return raw[:4] == b"ABIF"


def read_trace(path):
    """An .ab1 file -> {bases, quality, peaks, channels {A, C, G, T}, sample, machine, date}.
    peaks: the scan (trace position) of each base's peak; channels: the dye
    signal per scan, by base (the file's FWO_ order says which channel is which)."""
    record = SeqIO.read(str(path), "abi")
    raw = record.annotations.get("abif_raw", {})
    bases = str(record.seq).upper()
    order = (raw.get("FWO_1") or b"GATC").decode("ascii", "replace")
    channels = {base: list(raw.get(f"DATA{9 + i}") or ()) for i, base in enumerate(order)}
    peaks = list(raw.get("PLOC2") or raw.get("PLOC1") or ())
    quality = list(record.letter_annotations.get("phred_quality") or [])
    if len(quality) != len(bases) or not any(quality):  # some machines (the 310) store none
        quality = []
    if len(peaks) != len(bases):
        raise ValueError("the trace's peak positions don't match its bases")
    text = lambda key: (raw.get(key) or b"").decode("ascii", "replace").strip() if isinstance(raw.get(key), bytes) else str(raw.get(key) or "")
    return {"bases": bases, "quality": quality, "peaks": peaks, "channels": channels,
            "sample": record.id if record.id and record.id != "<unknown id>" else "",
            "machine": text("MODL1"), "date": str(record.annotations.get("run_start") or "")}


# ── Trimming ─────────────────────────────────────────────────────────────

def trim(trace, limit=TRIM_LIMIT):
    """The part of a read to keep: (start, end), end exclusive. The modified Mott
    algorithm (phred, Geneious's "error probability limit"): each base scores
    limit - its error probability, and the best-scoring stretch is kept. A read
    without qualities (or limit 0) is kept whole; one with no stretch better than
    the limit, empty."""
    quality = trace["quality"]
    if not quality or not limit:
        return 0, len(trace["bases"])
    best = start = run = 0
    kept = (0, 0)
    for i, q in enumerate(quality):
        run += limit - 10 ** (-q / 10)
        if run <= 0:
            run, start = 0, i + 1
        elif run > best:
            best, kept = run, (start, i + 1)
    return kept


# ── The read as a sequence ───────────────────────────────────────────────

def read_record(trace, name, start, end):
    """The kept part of a read as a GenBank record (linear DNA), named `name`."""
    bits = [f"Sanger read{f' {trace['sample']}' if trace['sample'] and trace['sample'] != name else ''}",
            f"kept bases {start + 1}-{end} of {len(trace['bases'])}" if (start, end) != (0, len(trace["bases"])) else "",
            f"ABI {trace['machine']}" if trace["machine"] else ""]
    record = SeqRecord(Seq(trace["bases"][start:end]), id=name.replace(" ", "_"), name=name.replace(" ", "_"),
                       description=", ".join(b for b in bits if b))
    record.annotations.update(molecule_type="DNA", topology="linear")
    return record


def stored(trace, start, end):
    """The sidecar's content: the whole read and the part kept."""
    return {**trace, "start": start, "end": end}


# ── Assembling reads ─────────────────────────────────────────────────────
# Forward and reverse reads of one sample (and internal ones) put together:
# each read is aligned to the longest one, on the strand where it fits better
# (ends free: reads overlap only in part), the alignments merged on that read's
# positions, and a consensus called from the qualities: in each column, the
# letter with the highest summed quality among the reads covering it (a gap
# counts with its neighbours' quality; equal sums give an IUPAC code).

MIN_OVERLAP = 30      # aligned bases with the longest read, at least
MIN_IDENTITY = 0.75   # and that share of them the same
NO_QUALITY = 20       # what a base counts without qualities (a 310 read, an edited one)
IUPAC = {frozenset(k): v for k, v in {"AG": "R", "CT": "Y", "GC": "S", "AT": "W", "GT": "K", "AC": "M", "CGT": "B",
                                      "AGT": "D", "ACT": "H", "ACG": "V", "ACGT": "N"}.items()}
COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN-", "TGCAYRSWMKVHDBN-")


def revcomp(seq):
    return seq.translate(COMPLEMENT)[::-1]


def _aligner():
    from Bio.Align import PairwiseAligner
    aligner = PairwiseAligner(mode="global", match_score=2, mismatch_score=-3, open_gap_score=-5, extend_gap_score=-2)
    aligner.end_gap_score = 0  # a read may start and end anywhere along the other
    aligner.wildcard = "N"
    return aligner


def _placed(aligner, anchor, seq):
    """seq aligned to anchor: (score, gapped anchor, gapped seq, aligned, identical)."""
    alignment = aligner.align(anchor, seq)[0]
    a, s = alignment[0], alignment[1]
    pairs = [(x, y) for x, y in zip(a, s) if x != "-" and y != "-"]
    return alignment.score, a, s, len(pairs), sum(x == y for x, y in pairs)


def assemble(reads):
    """reads: [{name, seq, quality (one per base, or None)}]. Returns {rows: [{name,
    gapped, reversed}], consensus, quality-free disagreements [{at (0-based in
    the consensus), letters: [(letter, quality, read name)]}], left_out: [(name,
    why)]}. Rows are the alignment's (the longest read first, as it is)."""
    aligner = _aligner()
    order = sorted(range(len(reads)), key=lambda i: -len(reads[i]["seq"]))
    anchor = reads[order[0]]["seq"].upper()
    placed, left_out = [], []  # (read index, reversed, gapped anchor, gapped read)
    for i in order[1:]:
        seq = reads[i]["seq"].upper()
        best = max(((False, *_placed(aligner, anchor, seq)), (True, *_placed(aligner, anchor, revcomp(seq)))),
                   key=lambda p: p[1])
        reversed_, _, a, s, aligned, same = best
        if aligned < MIN_OVERLAP or same < MIN_IDENTITY * aligned:
            left_out.append((reads[i]["name"], f"overlaps the longest read by {aligned} bases, {round(100 * same / max(1, aligned))} % identical"))
            continue
        placed.append((i, reversed_, a, s))

    # merged on the anchor's positions: before each anchor base (and after the last), as many
    # columns as the longest insertion any read has there
    n = len(anchor)
    pieces = []  # per placed read: (before[0..n] inserted text, at[0..n-1] its letter at each anchor base)
    longest = [0] * (n + 1)
    for _, _, a, s in placed:
        before, at, k, extra = [""] * (n + 1), [None] * n, 0, ""
        for x, y in zip(a, s):
            if x == "-":
                extra += y
            else:
                before[k], at[k], extra = extra, y, ""
                k += 1
        before[n] = extra
        for k in range(n + 1):
            longest[k] = max(longest[k], len(before[k]))
        pieces.append((before, at))

    def row(before, at, first=False):
        out = []
        for k in range(n + 1):
            # before the first base, inserted letters sit against it (gaps on the left); elsewhere after the base before
            out.append(before[k].rjust(longest[k], "-") if k == 0 else before[k].ljust(longest[k], "-"))
            if k < n:
                out.append(at[k] if at[k] is not None else "-")
        return "".join(out)

    rows = [{"read": order[0], "reversed": False, "gapped": row([""] * (n + 1), list(anchor))}]
    for (i, reversed_, _, _), (before, at) in zip(placed, pieces):
        rows.append({"read": i, "reversed": reversed_, "gapped": row(before, at)})
    if any(r["read"] == 0 and r["reversed"] for r in rows):  # the first read given as it is (a forward read, usually)
        for r in rows:
            r["gapped"], r["reversed"] = revcomp(r["gapped"]), not r["reversed"]

    # qualities along each row (gaps: the lower of the bases on either side)
    width = len(rows[0]["gapped"])
    for r in rows:
        read = reads[r["read"]]
        quality = list(read["quality"]) if read.get("quality") and len(read["quality"]) == len(read["seq"]) \
            else [NO_QUALITY] * len(read["seq"])
        if r["reversed"]:
            quality.reverse()
        gapped, q, k = r["gapped"], [], 0
        for ch in gapped:
            if ch != "-":
                q.append(quality[k])
                k += 1
            else:
                q.append(None)
        letters = [i for i, ch in enumerate(gapped) if ch != "-"]
        r["first"], r["last"] = (letters[0], letters[-1]) if letters else (width, -1)
        for j in range(width):  # a gap inside the read: its neighbours' quality
            if q[j] is None and r["first"] < j < r["last"]:
                left = next(q[m] for m in range(j - 1, -1, -1) if q[m] is not None)
                right = next(q[m] for m in range(j + 1, width) if q[m] is not None)
                q[j] = min(left, right)
        r["quality"] = q

    consensus, disagreements = [], []
    for j in range(width):
        votes = [(r["gapped"][j], r["quality"][j], reads[r["read"]]["name"]) for r in rows if r["first"] <= j <= r["last"]]
        if not votes:
            continue
        sums = {}
        for letter, q, _ in votes:
            if letter != "N":
                sums[letter] = sums.get(letter, 0) + q
        if not sums:
            consensus.append("N")
            continue
        top = max(sums.values())
        best = sorted(letter for letter, total in sums.items() if total == top)
        letter = best[0] if len(best) == 1 else ("-" if best == ["-"] else IUPAC.get(frozenset(b for b in best if b != "-"), "N"))
        if letter != "-":
            consensus.append(letter)
        if len({v[0] for v in votes if v[0] != "N"}) > 1:
            disagreements.append({"at": max(0, len(consensus) - (letter != "-")), "letters": votes, "called": letter})
    return {"rows": [{"name": reads[r["read"]]["name"], "gapped": r["gapped"], "reversed": r["reversed"]} for r in rows],
            "consensus": "".join(consensus), "disagreements": disagreements, "left_out": left_out}


def sample_name(names):
    """The sample's name from its reads' (R6574_F, R6574_R -> R6574): their common
    start without a trailing separator."""
    import os
    common = os.path.commonprefix(list(names)).rstrip("_-. ")
    if len(names) > 1 and len(common) >= 2:
        return common
    return f"{names[0]} consensus"


# ── Many samples at once: reads paired by name ───────────────────────────
# A read's name says its sample and its primer: AG123_rbcLF, AG123-F,
# AG123_rbcLR_B01_8833. The primer is the last word that names a direction
# (ending in F or R, F1/R2, fwd/rev, forward/reverse); the sample is what comes
# before it. What follows it (a plate well, a run number) differs between a
# sample's reads, so it is left out. Words are split at _ - . and spaces.

DIRECTION = re.compile(r"^(?:.*[FR]|[FR]\d+|fwd|rev|forward|reverse)$", re.I)


def sample_of(name):
    """(sample, primer) from a read's name; primer None when no word names a direction."""
    words = [w for w in re.split(r"[_\-.\s]+", name) if w]
    for i in range(len(words) - 1, 0, -1):  # (never the first word: that is the sample)
        if DIRECTION.match(words[i]):
            return "_".join(words[:i]), words[i]
    return "_".join(words) or name, None


def samples_by_name(names):
    """{sample: [names]} in the order first seen, and the names left alone (a
    sample with one read, or no direction in the name)."""
    groups = {}
    for name in names:
        sample, primer = sample_of(name)
        groups.setdefault(sample if primer else f"\0{name}", []).append(name)
    paired = {sample: reads for sample, reads in groups.items() if len(reads) > 1 and not sample.startswith("\0")}
    alone = [name for sample, reads in groups.items() if sample not in paired for name in reads]
    return paired, alone
