"""Annotate from a reference: the reference's features are looked for in the
target with BLAST and copied where they are found.

Each part of each feature is a BLAST query (its bases as on the reference's top
strand); the target is the subject, twice in a row when circular so features
across its origin are found too. A part's place is a chain of colinear hits
(the best one plus others on the same diagonal, e.g. around an indel), its ends
extended over any unaligned query ends. A feature's dissimilarity is
1 - identical bases / its length, so unaligned ends count as differences;
features above the limit aren't copied. Every place that passes is used, so a
gene of an inverted repeat lands in both copies.

A copied CDS then gets its ends moved to the nearest start and stop codons in its
frame (`snap`), as the gene in the target may be a few codons longer or shorter.
"""

from collections import defaultdict

from Bio.Data import CodonTable
from Bio.Seq import reverse_complement

from .genbank import feature_name

FIELDS = "qseqid sseqid pident length qstart qend sstart send bitscore"
SKIP_QUALIFIERS = {"translation"}  # recomputed for the target


def queries(record):
    """FASTA text of every part of every feature (not the source), id f<i>p<j>,
    and their lengths."""
    lines, lengths = [], {}
    for i, f in enumerate(record.features):
        if f.type == "source":
            continue
        for j, part in enumerate(f.location.parts):
            qid = f"f{i}p{j}"
            seq = str(record.seq[int(part.start):int(part.end)])
            lines += [f">{qid}", seq]
            lengths[qid] = len(seq)
    return "\n".join(lines) + "\n", lengths


def read_hits(text):
    """BLAST tabular output (FIELDS) -> {query id: [hit, ...]}."""
    hits = defaultdict(list)
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 9:
            continue
        qid, _, pident, length, qs, qe, ss, se, bits = cols[:9]
        ss, se = int(ss), int(se)
        hits[qid].append({
            "qs": int(qs), "qe": int(qe),
            "lo": min(ss, se) - 1, "hi": max(ss, se) - 1,  # 0-based, inclusive
            "strand": 1 if ss <= se else -1,
            "matches": round(float(pident) * int(length) / 100), "bits": float(bits),
        })
    return hits


def _diagonal(h):
    return h["lo"] - h["qs"] if h["strand"] == 1 else h["hi"] + h["qs"]


def places(hits, qlen):
    """Where one query lies in the target: chains of colinear hits, best first.
    Each: {start, end (0-based inclusive, ends extended), strand, matches}."""
    slack = max(30, qlen // 5)  # indels allowed along a chain
    left = sorted(hits, key=lambda h: -h["bits"])
    result = []
    while left:
        anchor = left.pop(0)
        chain = [anchor]
        for h in list(left):
            if (h["strand"] == anchor["strand"] and abs(_diagonal(h) - _diagonal(anchor)) <= slack
                    and all(h["qe"] < c["qs"] or h["qs"] > c["qe"] for c in chain)):
                chain.append(h)
                left.remove(h)
        qs, qe = min(c["qs"] for c in chain), max(c["qe"] for c in chain)
        lo, hi = min(c["lo"] for c in chain), max(c["hi"] for c in chain)
        if anchor["strand"] == 1:
            start, end = lo - (qs - 1), hi + (qlen - qe)
        else:
            start, end = lo - (qlen - qe), hi + (qs - 1)
        result.append({"start": start, "end": end, "strand": anchor["strand"],
                       "matches": min(qlen, sum(c["matches"] for c in chain))})
    return result


def transfer(reference, target, circular, hits, lengths, limit, existing=(), code=1):
    """The reference's features placed on the target sequence (a string).

    Returns (editor features, report lines). limit: the highest dissimilarity
    allowed (0-1). existing: (type, strand, spans) of the features the target
    already has: a copy that is the same feature (same_feature) isn't added.
    code: genetic code for a CDS without /transl_table."""
    target_length = len(target)
    features, report, seen = [], [], set()
    found = missed = snapped = there = 0
    for i, f in enumerate(reference.features):
        if f.type == "source":
            continue
        name = feature_name(f.qualifiers, f.type)
        parts = list(f.location.parts)
        qids = [f"f{i}p{j}" for j in range(len(parts))]
        total = sum(lengths[q] for q in qids)
        part_places = [places(hits.get(q, []), lengths[q]) for q in qids]
        anchor_j = max(range(len(parts)), key=lambda j: lengths[qids[j]])
        span = int(f.location.end) - int(f.location.start)
        best_dissimilarity, placed, taken, there_before = None, 0, [], there
        for anchor in sorted(part_places[anchor_j], key=lambda p: -p["matches"]):
            chosen = []
            for j, options in enumerate(part_places):
                near = [p for p in options if p["strand"] == anchor["strand"]
                        and abs(p["start"] - anchor["start"]) <= span * 1.5 + 2000]
                if not near:
                    break
                chosen.append(max(near, key=lambda p: p["matches"]))
            else:
                dissimilarity = 1 - sum(p["matches"] for p in chosen) / total
                if best_dissimilarity is None or dissimilarity < best_dissimilarity:
                    best_dissimilarity = dissimilarity
                if dissimilarity > limit:
                    continue
                strand = (-1 if f.location.strand == -1 else 1) * anchor["strand"]
                moved = ""
                if f.type == "CDS" and _complete(f):
                    table = int(f.qualifiers.get("transl_table", [code])[0])
                    first_codon = str(f.extract(reference.seq)[:3]).upper()
                    parts, moved = snap(sorted((p["start"], p["end"]) for p in chosen), strand, target, circular,
                                        table, prefer=first_codon)
                    chosen = [{"start": s, "end": e} for s, e in parts]
                    snapped += bool(moved)
                locations = _locations(chosen, target_length, circular)
                if locations is None:
                    continue
                key = (f.type, tuple((l["start"], l["end"]) for l in locations))
                # the same place again (the circle's second copy, a cut-off hit at its end)
                spans = _spans(locations, target_length)
                if key in seen or any(_overlap(spans, other) for other in taken):
                    continue
                if any(same_feature((f.type, strand, spans), e) for e in existing):  # the target has it already
                    there += 1
                    taken.append(spans)
                    continue
                seen.add(key)
                taken.append(spans)
                notes = {k: [str(v) for v in vals] for k, vals in f.qualifiers.items() if k not in SKIP_QUALIFIERS}
                if "translation" in f.qualifiers:
                    notes["translation"] = [""]  # recomputed on save (editor_to_record)
                notes.setdefault("note", []).append(
                    f"copied from {reference.name}, {100 * (1 - dissimilarity):.1f} % identical{moved}")
                feature = {"name": name, "type": f.type, "strand": strand, "notes": notes,
                           "start": locations[0]["start"], "end": locations[-1]["end"],
                           "_ref": i, "_flip": anchor["strand"] == -1}  # for follow_parts
                if len(locations) > 1:
                    feature["locations"] = locations
                features.append(feature)
                placed += 1
        if placed or there_before != there:
            found += 1
        else:
            missed += 1
            why = "not found" if best_dissimilarity is None else f"{100 * best_dissimilarity:.0f} % different"
            report.append(f"  not copied: {f.type} {name} ({why})")
    fitted = follow_parts(features, reference, target_length)
    for feature in features:
        del feature["_ref"], feature["_flip"]
    report.insert(0, f"{found} of {found + missed} features found, {len(features)} placed "
                     f"(limit {100 * limit:g} % different)" + (f", {there} not copied: the sequence has them already"
                                                              if there else "") +
                     f"; {snapped} CDSs had an end moved to a start or stop codon; "
                     f"{fitted} genes fitted to their CDS or RNA.")
    return features, report


# ── A gene fitted to its part ────────────────────────────────────────────
# Each feature is placed on its own, and a CDS's ends are then moved to start
# and stop codons: a gene copied with it could end up shorter or longer than it.
# So a copied gene takes its ends from its copied part (CDS first, else an RNA
# of the same name), with the same distances as in the reference: equal ends
# stay equal, UTRs stay as long.

PART_TYPES = ("CDS", "tRNA", "rRNA", "tmRNA", "ncRNA", "misc_RNA")


def follow_parts(features, reference, length):
    """Moves each copied gene's ends after its part's; returns how many changed."""
    ref = reference.features
    fitted = 0
    for gene in features:
        g = ref[gene["_ref"]]
        if gene["type"] != "gene" or "locations" in gene or len(g.location.parts) > 1:
            continue
        # its part in the reference: same name, overlapping, CDS before RNAs
        parts = [k for k, f in enumerate(ref) if f.type in PART_TYPES
                 and feature_name(f.qualifiers, f.type) == gene["name"]
                 and int(f.location.start) < int(g.location.end) and int(g.location.start) < int(f.location.end)]
        parts.sort(key=lambda k: PART_TYPES.index(ref[k].type))
        # its copy here: placed from that part, overlapping this gene, the same way round
        copy = next((p for k in parts for p in features if p["_ref"] == k and p["_flip"] == gene["_flip"]
                     and p["start"] <= p["end"] and p["start"] <= gene["end"] and gene["start"] <= p["end"]), None)
        if not copy or gene["start"] > gene["end"]:
            continue  # no part copied, or across the origin: left as placed
        r = ref[copy["_ref"]]
        before = int(r.location.start) - int(g.location.start)  # the gene's extra bases, reference coordinates
        after = int(g.location.end) - int(r.location.end)
        if gene["_flip"]:  # placed reversed: what was before the part is after it
            before, after = after, before
        start, end = max(0, copy["start"] - before), min(length - 1, copy["end"] + after)
        if (start, end) != (gene["start"], gene["end"]):
            gene["start"], gene["end"] = start, end
            gene["notes"]["note"][-1] += f"; ends fitted to its {copy['type']}"
            fitted += 1
    return fitted


# ── CDS ends on start and stop codons ────────────────────────────────────

def _complete(f):
    """A CDS that should start with a start codon and end with a stop: not partial
    (< or > in its location) and read from its first base."""
    location = str(f.location)
    return "<" not in location and ">" not in location and f.qualifiers.get("codon_start", ["1"])[0] == "1"


def snap(parts, strand, target, circular, table, prefer="ATG"):
    """A copied CDS's ends moved to the nearest start and stop codons in its frame.

    parts: (start, end) inclusive, sorted, in the target's coordinates (beyond its
    ends when circular: positions wrap). First each end's frame is checked: an end
    placed a base off (an indel near it in the alignment) is moved by one base if
    its frame has 2 or more stop codons near that end and that frame none. Then the 5' end goes to the
    nearest start codon (`prefer`, else ATG, else any of the table) within `window` codons, not
    past a stop when moving upstream; the 3' end to the first stop after the start,
    if that is within `window` codons of the old end (a stop far inside is left for
    the viewer to show). Only the outer ends move. prefer: the reference's own
    start codon, looked for first (genes often keep a non-ATG start). Returns
    (parts, note: "" if unchanged)."""
    n = len(target)
    codons = CodonTable.unambiguous_dna_by_id[table]
    starts, stops = set(codons.start_codons), set(codons.stop_codons)

    def read(idx):  # bases at these positions, in reading order, on the CDS's strand
        if len(idx) < 3 or not circular and any(i < 0 or i >= n for i in idx):
            return None
        text = "".join(target[i % n] for i in idx).upper()
        return reverse_complement(text)[::-1] if strand == -1 else text

    def stop_count(idx):  # None: off a linear target's ends
        found = [read(idx[k:k + 3]) for k in range(0, len(idx) - 2, 3)]
        return None if None in found else sum(c in stops for c in found)

    def frame(options):  # 0, or a shift whose frame has no stops where this one has 2 or more
        now = stop_count(options[0])
        better = [s for s in (1, -1) if stop_count(options[s]) == 0]
        return better[0] if now is not None and now >= 2 and better else 0

    # The CDS's positions in reading order
    pos = [i for s, e in parts for i in range(s, e + 1)]
    if strand == -1:
        pos.reverse()
    five0, three0 = pos[0], pos[-1]
    stretch = min(len(pos) // 2, 600)  # bases near each end where the frame is judged

    # Each end's frame: the same, one base more or one base less
    before, after = pos[0] - strand, pos[-1] + strand
    heads = {0: pos[:stretch], 1: [before] + pos[:stretch - 1], -1: pos[1:stretch + 1]}
    head = frame(heads)
    tail_end = {0: pos[-stretch:], 1: pos[-stretch + 1:] + [after], -1: pos[-stretch - 1:-1]}
    # the last codon may be the stop itself: it isn't counted
    tail = frame({s: x[len(x) % 3: -3] for s, x in tail_end.items()})
    pos = ([before] if head == 1 else []) + pos[(1 if head == -1 else 0): len(pos) - (1 if tail == -1 else 0)] \
        + ([after] if tail == 1 else [])

    # `window` codons more on each side
    window = max(15, min(100, len(pos) // 15))
    w = 3 * window
    full = [pos[0] - strand * k for k in range(w, 0, -1)] + pos + [pos[-1] + strand * k for k in range(1, w + 1)]
    codon = lambda c: read(full[3 * c: 3 * c + 3]) if c >= 0 else None
    first = window                       # the first codon
    last = window + len(pos) // 3 - 1    # the last whole codon (in the 5' end's frame)

    def nearest(wanted):  # the closest start codon, upstream first on a tie
        for d in range(1, window + 1):
            for c in (first - d, first + d):
                if codon(c) in wanted and (c > first or all(codon(k) not in stops for k in range(c, first))):
                    return c
        return None

    start = first
    if codon(first) not in starts:
        for wanted in ({prefer} & starts, {"ATG"} & starts, starts):
            found = nearest(wanted) if wanted else None
            if found is not None:
                start = found
                break
    five = full[3 * start]
    three = pos[-1]
    for c in range(start, last + window + 1):  # the stop, in the start's frame
        if codon(c) is None:
            break
        if codon(c) in stops:
            if abs(c - last) <= window:
                three = full[3 * c + 2]
            break
    if (five, three) == (five0, three0):
        return parts, ""

    # Only the outer ends move: the 5' end within the first part (in reading
    # order), the 3' end within the last
    lo, hi = (five, three) if strand == 1 else (three, five)
    if lo > parts[0][1] or hi < parts[-1][0]:
        return parts, ""
    parts = [(lo, parts[0][1])] + parts[1:]
    parts[-1] = (parts[-1][0], hi)
    moves = []
    for what, new, old in (("start", five, five0), ("end", three, three0)):
        d = (new - old) * strand  # > 0: downstream
        if d:
            moves.append(f"{what} moved {abs(d)} bp {'down' if d > 0 else 'up'}stream")
    return parts, f"; {', '.join(moves)} (start/stop codons)"


# ── The same feature? ────────────────────────────────────────────────────
# Keeping a sequence's own annotations, a new one is left out when the sequence
# already has it: the same type, on the same strand, and overlapping by at least
# SAME_OVERLAP of the shorter one. Not by name: the same gene is often named
# differently (nad4 / ND4, trnM(cau) / trnM).

SAME_OVERLAP = 0.8


def same_feature(a, b):
    """a, b: (type, strand, spans [(start, end) inclusive])."""
    if a[0] != b[0] or a[1] != b[1]:
        return False
    shared = sum(max(0, min(e, e2) - max(s, s2) + 1) for s, e in a[2] for s2, e2 in b[2])
    shorter = min(sum(e - s + 1 for s, e in spans) for spans in (a[2], b[2]))
    return shorter > 0 and shared >= SAME_OVERLAP * shorter


def record_facts(feature):
    """A Biopython feature as (type, strand, spans) for same_feature."""
    parts = feature.location.parts
    return feature.type, -1 if parts[0].strand == -1 else 1, [(int(p.start), int(p.end) - 1) for p in parts]


def _spans(locations, length):
    """Editor locations as plain (start, end) ranges; parts across the origin split."""
    spans = []
    for l in locations:
        spans += [(l["start"], l["end"])] if l["start"] <= l["end"] else [(l["start"], length - 1), (0, l["end"])]
    return spans


def _overlap(a, b):
    return any(s <= e2 and s2 <= e for s, e in a for s2, e2 in b)


def _locations(chosen, length, circular):
    """Part places (on the target, doubled when circular) -> editor locations, in
    the target's coordinates; a part across the origin has start > end. None when
    a part runs off the end of a linear target."""
    parts = sorted((p["start"], p["end"]) for p in chosen)
    if circular:
        if parts[0][0] >= length:  # found in the second copy
            parts = [(s - length, e - length) for s, e in parts]
        if parts[0][0] < 0:
            parts = [(s + length, e + length) for s, e in parts]
        if parts[-1][1] - parts[0][0] >= length:
            return None
        return [{"start": s % length, "end": e % length} for s, e in parts]
    if parts[0][0] < 0 or parts[-1][1] >= length:
        return None
    return [{"start": s, "end": e} for s, e in parts]
