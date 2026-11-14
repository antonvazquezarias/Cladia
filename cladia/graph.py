"""Assembly graphs (GFA 1, as SPAdes, GetOrganelle, Unicycler, Flye and MEGAHIT
write them): nodes (S lines, contigs with their sequence), links between node
ends (L lines, with the overlap the two share) and paths (P lines).

An item of kind "graph" is the .gfa file as imported. The graph view draws
each group of linked nodes as parallel lines (layout); a track (a walk through
a group, tracks) opens on its own in the sequence viewer, to be saved.
"""

import math
import re

from Bio.Seq import Seq

COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVNacgtryswkmbdhvn", "TGCAYRSWMKVHDBNtgcayrswmkvhdbn")


def revcomp(seq):
    return seq.translate(COMPLEMENT)[::-1]


def flip(o):
    return "-" if o == "+" else "+"


# ── Reading ──────────────────────────────────────────────────────────────

def _tags(fields):
    tags = {}
    for field in fields:
        parts = field.split(":", 2)
        if len(parts) == 3:
            name, kind, value = parts
            tags[name] = float(value) if kind in "if" and re.fullmatch(r"-?[\d.eE+-]+", value) else value
    return tags


def _overlap(cigar):
    """Bases two linked nodes share, from the link's CIGAR (55M, 0M or *)."""
    return sum(int(n) for n, op in re.findall(r"(\d+)([MDN=X])", cigar or ""))


def read_gfa(text):
    """{nodes: {id: {seq, length, depth}}, links: [(a, ao, b, bo, overlap)], paths: [{name, steps}]}.
    Depth: the node's DP tag, else its k-mer (KC) or read (RC) count per base."""
    nodes, links, paths = {}, [], []
    for line in text.splitlines():
        fields = line.rstrip("\r").split("\t")
        if fields[0] == "S" and len(fields) >= 3:
            seq = "" if fields[2] == "*" else fields[2]
            tags = _tags(fields[3:])
            length = int(tags.get("LN") or len(seq))
            depth = tags.get("DP", tags.get("dp"))
            if depth is None:
                count = tags.get("KC", tags.get("RC", tags.get("FC")))
                depth = count / length if isinstance(count, float) and length else None
            nodes[fields[1]] = {"seq": seq, "length": length, "depth": depth if isinstance(depth, float) else None}
        elif fields[0] == "L" and len(fields) >= 6:
            links.append((fields[1], fields[2], fields[3], fields[4], _overlap(fields[5])))
        elif fields[0] == "P" and len(fields) >= 3:
            steps = [(s[:-1], s[-1]) for s in fields[2].split(",") if s and s[-1] in "+-"]
            paths.append({"name": fields[1], "steps": steps})
    if not nodes:
        raise ValueError("no nodes (S lines) in the GFA file")
    known = set(nodes)
    links = [l for l in links if l[0] in known and l[2] in known]
    return {"nodes": nodes, "links": links, "paths": paths}


def summary(graph):
    """What the list's hover card says."""
    lengths = sorted((n["length"] for n in graph["nodes"].values()), reverse=True)
    total, run, n50 = sum(lengths), 0, 0
    for length in lengths:
        run += length
        if run >= total / 2:
            n50 = length
            break
    return {"nodes": len(lengths), "links": len(graph["links"]), "total": total, "longest": lengths[0], "n50": n50,
            "sequences": all(n["seq"] for n in graph["nodes"].values())}


def view(graph):
    """The viewer's data: nodes without sequences, with their GC."""
    nodes = []
    for name, n in graph["nodes"].items():
        seq = n["seq"].upper()
        gc = round(100 * (seq.count("G") + seq.count("C")) / len(seq), 1) if seq else None
        nodes.append({"id": name, "length": n["length"], "depth": n["depth"], "gc": gc})
    return {"nodes": nodes, "links": [list(l) for l in graph["links"]],
            "paths": [{"name": p["name"], "steps": [f"{a}{o}" for a, o in p["steps"]]} for p in graph["paths"]]}


# ── Paths ────────────────────────────────────────────────────────────────
# A link a+ -> b- also reads b+ -> a- (the other strand), so both are looked up.

def _link_table(graph):
    table = {}
    for a, ao, b, bo, overlap in graph["links"]:
        table[(a, ao, b, bo)] = overlap
        table[(b, flip(bo), a, flip(ao))] = overlap
    return table


def oriented(graph, name, o):
    seq = graph["nodes"][name]["seq"]
    if not seq:
        raise ValueError(f"node {name} has no sequence in the file")
    return seq if o == "+" else revcomp(seq)


def path_sequence(graph, steps):
    """steps [(node, orientation)] -> (sequence, circular). Consecutive nodes must
    be linked; the overlap each link shares is kept once. Circular when the last
    node links back to the first (that overlap then comes off the end)."""
    table = _link_table(graph)
    for (a, ao), (b, bo) in zip(steps, steps[1:]):
        if (a, ao, b, bo) not in table:
            raise ValueError(f"no link from {a}{ao} to {b}{bo}")
    seq = oriented(graph, *steps[0])
    for (a, ao), (b, bo) in zip(steps, steps[1:]):
        seq += oriented(graph, b, bo)[table[(a, ao, b, bo)]:]
    closing = table.get((*steps[-1], *steps[0]))
    if closing is not None:
        seq = seq[: len(seq) - closing] if closing else seq
    return seq, closing is not None


# ── Tracks ───────────────────────────────────────────────────────────────
# The graph shown as sequences: each connected group of nodes becomes a walk
# through them, its main track (circular when the walk closes on itself), and
# the nodes the walk leaves out become branch tracks, placed under where they
# join it. A node is used as many times as its depth says it is in the genome
# (an inverted repeat, at twice the depth of the rest, comes twice).

MAX_COPIES = 4          # a node is used at most this many times in a walk
CLOSED_SHARE = 0.9      # a closed walk this long (of the longest walk's bases) is preferred: a circular track
SEARCH_BUDGET = 200000  # walks tried in a group before settling for the longest found
SEARCH_NODES = 80       # bigger groups: a greedy walk (no search)
MAX_BASES = 30_000_000  # tracks sent to the viewer, at most (as the sequence viewer's budget)
MAX_TRACKS = 500
MAX_BLOCKS = 3000       # reference hits drawn


def _groups(graph, keep):
    """Connected groups of the kept nodes, the longest node's group first."""
    near = {n: set() for n in keep}
    for a, _, b, _, _ in graph["links"]:
        if a in near and b in near:
            near[a].add(b)
            near[b].add(a)
    seen, groups = set(), []
    for start in sorted(keep, key=lambda n: -graph["nodes"][n]["length"]):
        if start in seen:
            continue
        seen.add(start)
        group, stack = [], [start]
        while stack:
            n = stack.pop()
            group.append(n)
            for m in near[n] - seen:
                seen.add(m)
                stack.append(m)
        groups.append(group)
    return sorted(groups, key=lambda g: -sum(graph["nodes"][n]["length"] for n in g))


def copies(graph, group):
    """How many times each node is likely in the genome: its depth over the depth of
    most of the group's bases (a repeat collapsed into one node has twice the depth)."""
    nodes = graph["nodes"]
    known = sorted((nodes[n]["depth"], nodes[n]["length"]) for n in group if nodes[n]["depth"])
    if not known:
        return {n: 1 for n in group}
    half, run, base = sum(length for _, length in known) / 2, 0, known[-1][0]
    for depth, length in known:
        run += length
        if run >= half:
            base = depth
            break
    return {n: max(1, min(MAX_COPIES, round(nodes[n]["depth"] / base))) if nodes[n]["depth"] else 1 for n in group}


def _nexts(table):
    nexts = {}
    for a, ao, b, bo in table:
        nexts.setdefault((a, ao), []).append((b, bo))
    return nexts


def _extend(graph, steps, used, limit, members, nexts, table, need=None):
    """The walk `steps` made longer at its end, each node used up to limit[n] times.
    need: the steps of a walk using every copy; one that closes on itself is
    returned at once. Small groups are searched (with a budget), big ones greedy.
    Returns (steps, closed)."""
    nodes = graph["nodes"]
    best = [list(steps), sum(nodes[n]["length"] for n, _ in steps)]
    budget = [SEARCH_BUDGET if len(members) <= SEARCH_NODES else 0]
    closed = []  # failing that, the first closed walk through every node (depths can mislead)
    loop = [None, 0]  # and the longest closed walk of all (the rest become branches)

    def options(last):
        found = [(b, bo) for b, bo in nexts.get(last, []) if b in members and used[b] < limit[b]]
        return sorted(found, key=lambda s: (used[s[0]] > 0, -nodes[s[0]]["length"]))  # new nodes, long ones first

    def search(total):
        if need and (*steps[-1], *steps[0]) in table:
            if len(steps) == need:
                return True
            if not closed and all(used[n] for n in members):
                closed.append(list(steps))
            if total > loop[1]:
                loop[0], loop[1] = list(steps), total
        if total > best[1]:
            best[0], best[1] = list(steps), total
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        for b, bo in options(steps[-1]):
            steps.append((b, bo))
            used[b] += 1
            if search(total + nodes[b]["length"]):
                return True
            steps.pop()
            used[b] -= 1
        return False

    if budget[0] and search(best[1]):
        return list(steps), True
    if closed:
        return closed[0], True
    if loop[0] and loop[1] >= CLOSED_SHARE * best[1]:  # a circle nearly as long as the longest walk: a circular genome
        return loop[0], True                         # (e.g. a contig closing through one of two short joins)
    steps[:] = best[0]
    for n in used:
        used[n] = 0
    for n, _ in steps:
        used[n] += 1
    while True:  # greedy from there (the whole walk, for big groups)
        choices = options(steps[-1])
        if not choices:
            return list(steps), False
        steps.append(choices[0])
        used[choices[0][0]] += 1


def _walk(graph, members, limit, nexts, table, need=None):
    """A walk through `members` from their longest node: extended forward, then
    backward (from its other strand). Returns (steps, circular)."""
    first = min(members, key=lambda n: (-graph["nodes"][n]["length"], n))  # (ties: by name, so the same each time)
    used = {n: 0 for n in members}
    used[first] = 1
    steps, closed = _extend(graph, [(first, "+")], used, limit, members, nexts, table, need)
    if closed:
        return steps, True
    back = [(n, flip(o)) for n, o in reversed(steps)]
    steps, _ = _extend(graph, back, used, limit, members, nexts, table)
    steps = [(n, flip(o)) for n, o in reversed(steps)]
    if steps[0][1] == "-" and all(o == "-" for _, o in steps):  # all reversed: the + strand reads better
        steps = [(n, flip(o)) for n, o in reversed(steps)]
    return steps, len(steps) > 0 and (*steps[-1], *steps[0]) in table


def _track(graph, steps, circular, table):
    """A walk as a track: its sequence (overlaps once) and where each node is in it."""
    placed, seq = [], ""
    for i, (n, o) in enumerate(steps):
        part = oriented(graph, n, o)
        overlap = table[(*steps[i - 1], n, o)] if i else 0
        start = len(seq) - overlap
        seq += part[overlap:]
        placed.append({"id": n, "o": o, "start": start, "end": start + len(part) - 1})
    if circular:
        closing = table[(*steps[-1], *steps[0])]
        seq = seq[: len(seq) - closing] if closing else seq
    return {"sequence": seq, "circular": circular, "nodes": placed, "walk": walk_text(steps), "chosen": False}


# ── Routes the user changes ──────────────────────────────────────────────
# A walk travels between the viewer and here as text: "LSC+,IR+,SSC+,IR-".

def walk_text(steps):
    return ",".join(f"{n}{o}" for n, o in steps)


def parse_walk(text):
    return [(s[:-1], s[-1]) for s in text.split(",") if len(s) > 1 and s[-1] in "+-"]


def linked(table, steps):
    return all((*a, *b) in table for a, b in zip(steps, steps[1:]))


def turn_round(graph, steps, k):
    """The walk with its k-th node read on the other strand (an inversion, as the
    two orientations of a plastid's small single-copy region): only when the
    links before and after it allow it. Raises ValueError."""
    table = _link_table(graph)
    n, o = steps[k]
    new = list(steps)
    new[k] = (n, flip(o))
    circular = (*steps[-1], *steps[0]) in table
    if not linked(table, new) or (circular and (*new[-1], *new[0]) not in table):
        raise ValueError(f"node {n} can't be turned round there: the nodes beside it aren't linked to its other strand")
    return new


def take_branch(graph, steps, branch):
    """The walk with a branch put in: the stretch of the walk between the node the
    branch leaves from and the node it leads back into is replaced by it (a bubble's
    other side; across the origin of a circular walk too). A branch joined at one
    end only replaces the walk from there to that end. Raises ValueError."""
    table = _link_table(graph)
    first, last = branch[0], branch[-1]
    n = len(steps)
    circular = n > 1 and (*steps[-1], *steps[0]) in table
    outs = [i for i, s in enumerate(steps) if (*s, *first) in table]   # the branch can follow these
    ins = [j for j, s in enumerate(steps) if (*last, *s) in table]     # and lead into these
    best = None
    for i in outs:
        for j in ins:
            if j > i or circular:  # replaced: steps i+1 .. j-1 (round the origin when circular)
                gap = (j - i - 1) % n if circular else j - i - 1
                if best is None or gap < best[2]:
                    best = (i, j, gap)
    if best:
        i, j, _ = best
        if j > i:
            return steps[: i + 1] + list(branch) + steps[j:]
        return steps[j: i + 1] + list(branch)  # round the origin: the walk read from j
    if outs:  # the branch only leaves the walk: it ends the walk
        return steps[: outs[0] + 1] + list(branch)
    if ins:  # it only leads in: it starts the walk
        return list(branch) + steps[ins[-1]:]
    raise ValueError("this branch isn't linked to the track")


def tracks(graph, min_length=0, min_depth=0, only=None, walks=()):
    """The graph as tracks: [{name, description, sequence, circular, nodes:
    [{id, o, start, end}], shift (leading gaps: a branch under where it joins),
    branch_of, walk}], and how many nodes were left out (hidden, or past the budget).
    min_length: groups shorter in all are hidden; min_depth: see kept.
    only: node names; groups without any of them are left out.
    walks: routes the user chose ([(node, o)...]), each used as its group's main
    track instead of Cladia's own (when all its nodes are shown and it is linked)."""
    nodes = graph["nodes"]
    keep = [n for n in kept(graph, min_depth) if nodes[n]["seq"]]
    table = _link_table(graph)
    nexts = _nexts(table)
    groups, hidden = shown_groups(graph, keep, min_length)
    result, bases, left = [], 0, len(nodes) - len(keep) + hidden
    for group in groups:
        if only is not None and not only.intersection(group):
            left += len(group)
            continue
        size = sum(nodes[n]["length"] for n in group)
        if result and (bases + size > MAX_BASES or len(result) >= MAX_TRACKS):
            left += len(group)
            continue
        bases += size
        members = set(group)
        chosen = next((w for w in walks if w and all(n in members for n, _ in w) and linked(table, w)), None)
        if chosen:
            steps, circular = list(chosen), (*chosen[-1], *chosen[0]) in table
        else:
            limit = copies(graph, group)
            steps, circular = _walk(graph, members, limit, nexts, table, need=sum(limit.values()))
        main = _track(graph, steps, circular, table)
        main["chosen"] = bool(chosen)
        k = len([t for t in result if t["name"].startswith("Track") and t["branch_of"] is None]) + 1
        main.update(name=(f"node {group[0]}" if len(group) == 1 else f"Track {k}") + (" (circular)" if circular else ""),
                    shift=0, branch_of=None)
        index = len(result)
        result.append(main)
        # the nodes left out: branches, each a walk of its own through them
        left_out = {n for n in group if n not in {s[0] for s in steps}}
        placed = list(steps)  # the track's steps and the branches' so far
        while left_out:
            branch, _ = _walk(graph, left_out, {n: 1 for n in left_out}, nexts, table)
            branch = _along(table, placed, branch)
            placed += branch
            left_out -= {n for n, _ in branch}
            track = _track(graph, branch, False, table)
            track.update(name=f"Track {k} branch {len(result) - index}", branch_of=index,  # (k: the main track's number)
                         shift=_attach(main, track, branch, table))
            result.append(track)
    for t in result:
        steps = len(t["nodes"])
        distinct = len({p["id"] for p in t["nodes"]})
        t["description"] = ", ".join(filter(None, [
            "circular" if t["circular"] else "linear",
            ("1 node" if steps == 1 else f"{steps} nodes") if steps == distinct else f"{steps} steps through {distinct} nodes",
            f"{len(t['sequence']):,} bp"]))
    return result, left


def _along(table, placed, branch):
    """A branch read the way it joins what is placed already: in from something's
    end at its start, out at its end into something's start. Turned round (the
    other strand, the other order) when more of its links run the other way: a
    branch is walked from its longest node's + strand, whichever way it joins."""
    turned = [(n, flip(o)) for n, o in reversed(branch)]
    def joins(b):
        return sum((*s, *b[0]) in table for s in placed) + sum((*b[-1], *s) in table for s in placed)
    return turned if joins(turned) > joins(branch) else branch


def _attach(main, branch, steps, table):
    """Where a branch goes under its main track: after the node it leaves from, or
    ending where the node it leads into starts. Leading gaps; 0 when unlinked."""
    first, last = steps[0], steps[-1]
    for p in main["nodes"]:
        overlap = table.get((p["id"], p["o"], *first))
        if overlap is not None:
            return max(0, p["end"] + 1 - overlap)
        overlap = table.get((*last, p["id"], p["o"]))
        if overlap is not None:
            return max(0, p["start"] + overlap - len(branch["sequence"]))
    return 0


def joined(graph, steps):
    """Steps through the graph as one sequence with its nodes placed: {sequence,
    circular, nodes: [{id, o, start, end}]}. Raises ValueError when consecutive
    nodes aren't linked (or one has no sequence)."""
    _, circular = path_sequence(graph, steps)
    return _track(graph, steps, circular, _link_table(graph))


# Viridis sampled at eighths; interpolate the same stops for nodes and legend.
DEPTH_PALETTE = ["#fde725", "#addc30", "#5ec962", "#28ae80", "#21918c",
                 "#2c728e", "#3b528b", "#472d7b", "#440154"]


def contigs_to_gfa(contigs, min_overlap=30, max_overlap=1000):
    """GFA text from contigs [(name, sequence, depth or None)]: a link wherever the
    end of one contig (either strand) is the start of another (or its own start: a
    circle), the longest such overlap of min_overlap..max_overlap bases. MEGAHIT's
    final contigs overlap by exactly k (the number in names like k141_7), SPAdes'
    by k as well. Returns (text, {nodes, links, loops})."""
    flip = {"+": "-", "-": "+"}
    oriented = {}
    for name, seq, _ in contigs:
        seq = seq.upper()
        oriented[name, "+"] = seq
        oriented[name, "-"] = str(Seq(seq).reverse_complement())
    starts = {}
    for key, seq in oriented.items():
        if len(seq) > min_overlap:
            starts.setdefault(seq[:min_overlap], []).append(key)
    found = {}
    for (name, o), seq in oriented.items():
        tail = seq[-max_overlap:]
        for i in range(len(tail) - min_overlap + 1):  # earliest position = longest overlap
            for other in starts.get(tail[i:i + min_overlap], []):
                size = len(tail) - i
                target = oriented[other]
                if size < len(target) and size < len(seq) and target.startswith(tail[i:]):
                    link = (name, o, other[0], other[1])
                    key = min(link, (other[0], flip[other[1]], name, flip[o]))
                    found.setdefault(key, size)
    lines = ["H\tVN:Z:1.0"]
    for name, seq, depth in contigs:
        lines.append(f"S\t{name}\t{seq.upper()}" + (f"\tDP:f:{depth}" if depth else ""))
    for (a, ao, b, bo), size in sorted(found.items()):
        lines.append(f"L\t{a}\t{ao}\t{b}\t{bo}\t{size}M")
    loops = sum(1 for a, _, b, _ in found if a == b)
    return "\n".join(lines) + "\n", {"nodes": len(contigs), "links": len(found), "loops": loops}


def fastg_to_gfa(text):
    """A FASTG file (SPAdes' assembly_graph.fastg) as GFA text. Every edge is
    written twice (EDGE_1 and EDGE_1'); the reverse ones are left out and their
    links kept once. FASTG edges overlap by k bases, which is found from the
    sequences (the most common overlap of the first links)."""
    records, header, parts = [], None, []
    for line in text.splitlines() + [">"]:
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(parts).upper()))
            header, parts = line[1:].strip(), []
        elif header is not None:
            parts.append(line.strip())
    if any("[" in seq for _, seq in records):
        raise ValueError("this FASTG file has alternative sequences ([a|b]), which Cladia doesn't read")

    def edge(name):  # "EDGE_1_length_500_cov_20.5'" -> ("EDGE_1", "-", 20.5)
        reverse = name.endswith("'")
        name = name.rstrip("'")
        found = re.match(r"(.*?)_length_\d+_cov_([\d.]+)$", name)
        return (found.group(1) if found else name), ("-" if reverse else "+"), (float(found.group(2)) if found else None)

    seqs, depth, links = {}, {}, []
    for head, seq in records:
        names = head.rstrip(";").split(":")
        node, orient, cov = edge(names[0])
        if orient == "+" or node not in seqs:
            seqs[node] = seq if orient == "+" else str(Seq(seq).reverse_complement())
        depth[node] = cov
        for succ in filter(None, (names[1].split(",") if len(names) > 1 else [])):
            other, other_orient, _ = edge(succ)
            links.append((node, orient, other, other_orient))
    flip = {"+": "-", "-": "+"}
    seen, unique = set(), []
    for a, ao, b, bo in links:  # the same link seen from the other strand
        key = min((a, ao, b, bo), (b, flip[bo], a, flip[ao]))
        if key not in seen and a in seqs and b in seqs:
            seen.add(key)
            unique.append((a, ao, b, bo))

    def oriented(node, orient):
        return seqs[node] if orient == "+" else str(Seq(seqs[node]).reverse_complement())

    def overlap(a, ao, b, bo):
        sa, sb = oriented(a, ao), oriented(b, bo)
        for o in range(min(len(sa), len(sb), 300), 0, -1):
            if sa.endswith(sb[:o]):
                return o
        return 0

    sample = [overlap(*link) for link in unique[:500]]
    k = max(set(sample), key=sample.count) if sample else 0
    k = k if k >= 10 else 0
    out = ["H\tVN:Z:1.0"]
    for node, seq in seqs.items():
        out.append(f"S\t{node}\t{seq}" + (f"\tDP:f:{depth[node]}" if depth.get(node) is not None else ""))
    for a, ao, b, bo in unique:
        out.append(f"L\t{a}\t{ao}\t{b}\t{bo}\t{k}M")
    return "\n".join(out) + "\n"


def depth_color(t):
    """Depth on a perceptually ordered yellow-to-purple Viridis ramp."""
    position = max(0, min(1, t)) * (len(DEPTH_PALETTE) - 1)
    i = min(int(position), len(DEPTH_PALETTE) - 2)
    fraction = position - i
    a, b = DEPTH_PALETTE[i], DEPTH_PALETTE[i + 1]
    channels = [round(int(a[k:k+2], 16) * (1-fraction) + int(b[k:k+2], 16) * fraction) for k in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(channels)


def depth_scale(graph):
    """t (0..1) of a depth: log scale from the 5th to the 95th percentile of the nodes."""
    import math
    depths = sorted(n["depth"] for n in graph["nodes"].values() if n["depth"])
    if not depths:
        return lambda d: None, None
    lo, hi = depths[int(len(depths) * 0.05)], depths[min(len(depths) - 1, int(len(depths) * 0.95))]
    span = math.log(hi) - math.log(lo) if hi > lo else 0
    return (lambda d: None if not d else 0.5 if not span else min(1, max(0, (math.log(d) - math.log(lo)) / span))), (lo, hi)




# ── The viewer's rows ────────────────────────────────────────────────────
# Each main track a row; its branches packed into lanes under it (branches that
# don't overlap share one, as reads are packed), and connectors drawn for every
# link the rows don't already show (two nodes one after the other on a row), so
# the graph's structure reads as in Bandage: where a branch leaves the track,
# where it comes back, a dead end, a loop.

LANE_GAP = 0.01        # between branches sharing a lane: this share of the track's length (50 bases at least)
MAX_CONNECTORS = 3000


def _lanes(main, branches):
    """Branches [(index, track)] packed into lanes by their place under the main track."""
    gap = max(50, int(len(main["sequence"]) * LANE_GAP))
    lanes = []  # [{"branches": [...], "end": column}]
    for bi, b in sorted(branches, key=lambda x: x[1]["shift"]):
        lane = next((l for l in lanes if l["end"] + gap <= b["shift"]), None)
        if lane is None:
            lane = {"branches": [], "end": 0}
            lanes.append(lane)
        lane["branches"].append((bi, b))
        lane["end"] = b["shift"] + len(b["sequence"])
    return [lane["branches"] for lane in lanes]


def track_view(graph, track_list, hits=None, reference=None):
    """The viewer's data, in a genome comparison's shape: genomes (rows: the
    reference first, then each main track and its branch lanes) with their nodes
    as features coloured by depth; blocks (the reference's hits on the nodes,
    wherever those nodes are); connectors ({from: [row, feature, side], to: [...]},
    side "start" or "end": a link leaves a node's end and enters a node's start)."""
    nodes = graph["nodes"]
    scale, depth_range = depth_scale(graph)
    counts = {}
    for t in track_list:
        for p in t["nodes"]:
            counts[p["id"]] = counts.get(p["id"], 0) + 1

    def node_feature(p, base, limit):
        node = nodes[p["id"]]
        depth = f" {node['depth']:.1f}×" if node["depth"] else ""
        twice = f" (×{counts[p['id']]})" if counts[p["id"]] > 1 else ""
        return {"type": "node", "name": f"{p['id']}{p['o']}{depth}{twice}",
                "start": base + p["start"], "end": base + min(p["end"], limit - 1),
                "strand": 1 if p["o"] == "+" else -1,
                "color": depth_color(scale(node["depth"]) or 0) if node["depth"] else "#9aa1a9",
                "notes": {"node": [p["id"]], "length": [str(node["length"])],
                          "depth": [f"{node['depth']:.1f}" if node["depth"] else ""]}}

    genomes = []
    if reference:
        genomes.append({"item": reference.get("item"), "name": reference["name"], "reference": True, "shift": 0,
                        "data": {**reference, "circular": reference.get("circular", False)}})
    placed = {}      # track index -> (row, its first base among the row's bases)
    feature_of = {}  # (track index, step) -> the feature's index in its row
    for ti, t in enumerate(track_list):
        if t["branch_of"] is not None:
            continue
        row = len(genomes)
        placed[ti] = (row, 0)
        features = [node_feature(p, 0, len(t["sequence"])) for p in t["nodes"]]
        feature_of.update({(ti, k): k for k in range(len(t["nodes"]))})
        genomes.append({"item": None, "name": t["name"], "shift": 0, "branch_of": None,
                        "walk": t["walk"], "chosen": t["chosen"],
                        "data": {"name": t["name"], "description": t["description"], "sequence": t["sequence"],
                                 "circular": t["circular"], "features": features, "organism": "", "geneticCode": 1}})
        branches = [(bi, b) for bi, b in enumerate(track_list) if b["branch_of"] == ti]
        for n, lane in enumerate(_lanes(t, branches)):
            lane_row = len(genomes)
            gapped, bases, features, info = "", 0, [], []
            for bi, b in lane:
                gapped += "-" * (b["shift"] - len(gapped))
                placed[bi] = (lane_row, bases)
                for k, p in enumerate(b["nodes"]):
                    feature_of[(bi, k)] = len(features)
                    features.append(node_feature(p, bases, len(b["sequence"])))
                info.append({"name": b["name"], "walk": b["walk"], "col0": len(gapped),
                             "col1": len(gapped) + len(b["sequence"]) - 1, "base0": bases,
                             "description": b["description"]})
                gapped += b["sequence"]
                bases += len(b["sequence"])
            name = f"{t['name'].replace(' (circular)', '')} branches{f' {n + 1}' if n else ''}"
            genomes.append({"item": None, "name": name, "shift": 0, "branch_of": row, "lane": info,
                            "data": {"name": name, "description": f"{len(lane)} branch{'es' if len(lane) > 1 else ''}",
                                     "sequence": gapped, "circular": False, "features": features,
                                     "organism": "", "geneticCode": 1}})

    # connectors: each link between two shown node places, unless they follow each other on a row
    where = {}  # node -> [(track index, step, orientation)]
    for ti, t in enumerate(track_list):
        for k, p in enumerate(t["nodes"]):
            where.setdefault(p["id"], []).append((ti, k, p["o"]))
    # links a row shows already: two nodes one after the other (or a circular track's closing link)
    shown = set()
    for t in track_list:
        steps = [(p["id"], p["o"]) for p in t["nodes"]]
        pairs = list(zip(steps, steps[1:])) + ([(steps[-1], steps[0])] if t["circular"] else [])
        for x, y in pairs:
            shown.update({(*x, *y), (y[0], flip(y[1]), x[0], flip(x[1]))})
    connectors, seen = [], set()
    for a, ao, b, bo, _ in graph["links"]:
        if (a, ao, b, bo) in shown:
            continue  # (also where a repeat's other copy could draw it again)
        for ta, ka, oa in where.get(a, []):
            for tb, kb, ob in where.get(b, []):
                # the link read the way the two places run: leaving a's end (or start, if a is drawn reversed)
                out = "end" if oa == ao else "start"
                into = "start" if ob == bo else "end"
                ends = ((ta, ka, out), (tb, kb, into))
                if ends in seen or ends[::-1] in seen:
                    continue
                seen.add(ends)
                connectors.append({"from": [placed[ta][0], feature_of[(ta, ka)], out],
                                   "to": [placed[tb][0], feature_of[(tb, kb)], into]})
                if len(connectors) >= MAX_CONNECTORS:
                    break

    blocks = []
    if hits and reference:
        for h in sorted(hits["hits"], key=lambda h: -h.get("bits", 0)):
            if h["query"] != 0:
                continue
            for ti, t in enumerate(track_list):
                row, base = placed[ti]
                for p in t["nodes"]:
                    if p["id"] != h["node"]:
                        continue
                    length = nodes[p["id"]]["length"]
                    lo, hi = min(h["start"], h["end"]), max(h["start"], h["end"])
                    forward = (h["end"] >= h["start"]) == (p["o"] == "+")
                    a, b = (p["start"] + lo, p["start"] + hi) if p["o"] == "+" else \
                           (p["start"] + length - hi + 1, p["start"] + length - lo + 1)
                    b = min(b, len(t["sequence"]))
                    if a > b:
                        continue
                    blocks.append({"parts": [{"genome": 0, "start": min(h["qstart"], h["qend"]),
                                              "end": max(h["qstart"], h["qend"]), "strand": 1},
                                             {"genome": row, "start": base + a, "end": base + b,
                                              "strand": 1 if forward else -1}],
                                   "identity": h["identity"]})
            if len(blocks) >= MAX_BLOCKS:
                break
    return {"genomes": genomes, "blocks": blocks, "connectors": connectors, "depth_range": depth_range}


# ── The graph view's layout ──────────────────────────────────────────────
# Each connected group drawn as parallel lines (as the user sketched it): the
# group's nodes read one way (from its longest node, following the links), each
# starting just after the latest node leading into it, so alternatives leaving
# one place start together and paths meet again where they rejoin. The longest
# path (in bases) is the main line, in the middle; the other nodes stack above
# and below it, a fork's branches on alternate sides, a branch's own branches
# further out on its side. Links that loop back (a circle's closing link) don't
# place anything; a group whose main line closes on itself is drawn as a ring.

NODE_PX = (6, 0.9, 14)  # a node's length in the view: 6 + 0.9 sqrt(bp), 14 at least (its own px)
NODE_GAP = 6            # between a node and the next along a line
MAX_LAYOUT_NODES = 6000 # nodes laid out, at most (the rest counted as left out)
COMPACT_MAX = 400       # a ring of more nodes keeps its first placement (see _compact_ring)
SCALE_PX, SCALE_LEAST = 1600, 6  # lengths to scale: the longest node's px, the shortest's least


def node_px(bp):
    base, factor, least = NODE_PX
    return max(least, base + factor * math.sqrt(bp))


def kept(graph, min_depth=0):
    """The nodes shown: every node deep enough, including sequence-free segments. (Nodes aren't
    hidden by length: that cut paths, the topology the view is for; as Bandage,
    everything is shown and short groups can be hidden whole, see shown_groups.)"""
    return [n for n, node in graph["nodes"].items() if not min_depth or (node["depth"] or 0) >= min_depth]


def shown_groups(graph, keep, min_length=0):
    """The connected groups of the kept nodes, but those shorter in all than
    min_length bases (and how many nodes those had)."""
    nodes, groups, hidden = graph["nodes"], [], 0
    for group in _groups(graph, keep):
        if sum(nodes[n]["length"] for n in group) < min_length:
            hidden += len(group)
        else:
            groups.append(group)
    return groups, hidden


def _orient(group, members, nexts, nodes):
    """Each node's strand as drawn: from the longest node's +, following the links."""
    first = max(group, key=lambda n: (nodes[n]["length"], n))
    ori, queue = {first: "+"}, [first]
    while queue:
        n = queue.pop(0)
        for side in "+-":
            for b, bo in nexts.get((n, side), []):
                if b in members and b not in ori:
                    ori[b] = bo if side == ori[n] else flip(bo)  # leaving n's end: b read as linked; its start: the other way
                    queue.append(b)
    return first, ori


def layout_group(graph, group, table, nexts, px=node_px):
    """One group's drawing: {nodes: {id: {o, x0, x1, lane}}, links: [[a, side, b,
    side, kind]], circular, span, main}; sides "start"/"end" as drawn (left/right);
    kind "forward" (left to right), "back" (loops back), "twisted" (joins two ends
    or two starts: one of the two nodes read the other way there)."""
    nodes, members = graph["nodes"], set(group)
    first, ori = _orient(group, members, nexts, nodes)
    edges, twisted = set(), []
    for a, ao, b, bo in table:
        if a in members and b in members and (a, ao, b, bo) <= (b, flip(bo), a, flip(ao)):  # each link once
            if ao == ori[a] and bo == ori[b]:
                edges.add((a, b))
            elif ao != ori[a] and bo != ori[b]:
                edges.add((b, a))
            else:  # end to end, or start to start: one of the two is read the other way there
                twisted.append((a, "end" if ao == ori[a] else "start", b, "start" if bo == ori[b] else "end"))
    succ = {n: [] for n in group}
    for a, b in edges:
        succ[a].append(b)
    # depth-first from the longest node: links back to a node still open close loops
    state, back, order = {}, set(), []
    for root in sorted(group, key=lambda n: (n != first, -nodes[n]["length"])):
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(sorted(succ[root], key=lambda m: -nodes[m]["length"])))]
        while stack:
            n, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[n] = 2
                order.append(n)
                stack.pop()
            elif state.get(nxt) == 1:
                back.add((n, nxt))
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(sorted(succ[nxt], key=lambda m: -nodes[m]["length"]))))
    order.reverse()  # every forward link runs left to right
    preds = {n: [] for n in group}
    for a, b in edges - back:
        preds[b].append(a)
    x0, x1 = {}, {}
    for n in order:
        x0[n] = max((x1[p] + NODE_GAP for p in preds[n]), default=0)
        x1[n] = x0[n] + px(nodes[n]["length"])
    # nodes not downstream of the longest node (leading into the group: nothing
    # places them, so they all fell to the left edge) go as late as they can:
    # just before the nodes they lead into
    succs = {n: [] for n in group}
    for a, b in edges - back:
        succs[a].append(b)
    downstream, stack = {first}, [first]
    while stack:
        for b in succs[stack.pop()]:
            if b not in downstream:
                downstream.add(b)
                stack.append(b)
    for n in reversed(order):
        if n not in downstream and succs[n]:
            right = min(x0[b] for b in succs[n]) - NODE_GAP
            x0[n], x1[n] = right - (x1[n] - x0[n]), right
    least = min(x0.values())
    for n in group:  # (moved left of the start: everything shifted right)
        x0[n] -= least
        x1[n] -= least
    # the main line: the longest path in bases
    best, came = {}, {}
    for n in order:
        p = max(preds[n], key=lambda q: best[q], default=None)
        best[n] = nodes[n]["length"] + (best[p] if p else 0)
        came[n] = p
    end = max(order, key=lambda n: best[n])
    main = []
    while end:
        main.append(end)
        end = came[end]
    main.reverse()
    closes = len(main) > 0 and (main[-1], main[0]) in back
    lane = {n: 0 for n in main}
    taken = {0: [(x0[n], x1[n]) for n in main]}
    def free(l, a, b):
        return all(b + NODE_GAP <= s or a >= e + NODE_GAP for s, e in taken.get(l, []))
    turn = [1]  # off the main line, ties alternate sides: -1 above, 1 below
    for n in sorted(order, key=lambda m: (x0[m], -nodes[m]["length"])):
        if n in lane:
            continue
        placed = [p for p in preds[n] if p in lane]
        p = max(placed, key=lambda q: nodes[q]["length"], default=None)
        base = lane[p] if p else 0
        def nearest(side, start):  # the first free line from start outwards on that side
            return next(l for l in range(start, side * (len(group) + 2), side) if l != 0 and free(l, x0[n], x1[n]))
        if not closes:
            # Reuse any vacant lane, including the main lane's empty stretches.
            # Minimise the group's height before the distance to placed neighbours.
            low, high = min(taken), max(taken)
            neighbours = [m for m in preds[n] + succs[n] if m in lane]
            candidates = [l for l in range(low - 1, high + 2) if free(l, x0[n], x1[n])]
            chosen = min(candidates, key=lambda l: (
                max(high, l) - min(low, l),
                sum(abs(l - lane[m]) for m in neighbours),
                abs(l), l))
        elif base == 0:  # off the main line: the side with a free line nearer to it
            up, down = nearest(-1, -1), nearest(1, 1)
            if -up == down:
                turn[0] = -turn[0]
                chosen = up if turn[0] < 0 else down
            else:
                chosen = up if -up < down else down
        else:          # off a branch: its own line if free, else further out on its side
            side = 1 if base > 0 else -1
            chosen = nearest(side, base)
        lane[n] = chosen
        taken.setdefault(chosen, []).append((x0[n], x1[n]))
    links = [[a, "end", b, "start", "forward"] for a, b in sorted(edges - back)] + \
            [[a, "end", b, "start", "back"] for a, b in sorted(back)] + [[*t, "twisted"] for t in twisted]
    if closes and len(group) <= COMPACT_MAX:  # (the search below is quadratic: 600 nodes took 2.5 s, 1,100 took 11 s)
        _compact_ring(group, main, x0, x1, lane, ori, links)
    # Keep a visible link gap, including for a single self-linked segment.
    gap = NODE_GAP if closes else 0
    return {"nodes": {n: {"o": ori[n], "x0": round(x0[n], 1), "x1": round(x1[n], 1), "lane": lane[n]} for n in group},
            "links": links, "circular": closes, "span": round(max(x1.values()) + gap, 1), "main": main}


def _compact_ring(group, main, x0, x1, lane, ori, links):
    """Bounded coordinate descent: keep the main ring, shorten branch links.

    Candidate centres come from neighbouring endpoints and the midpoints of
    both circular intervals between them. Try both strands and nearby vacant
    lanes. All incident endpoints move with a strand change; topology is fixed.
    """
    span = max(x1[n] for n in main) + NODE_GAP
    on_main = set(main)
    incident = {n: [] for n in group}
    for i, link in enumerate(links):
        incident[link[0]].append(i)
        if link[2] != link[0]:
            incident[link[2]].append(i)
    radius = max(30, span / (2 * math.pi)) - min(lane.values()) * 11

    def point(n, side, start=None, level=None, reverse=False):
        if reverse:
            side = "start" if side == "end" else "end"
        start = x0[n] if start is None else start
        at = start + (x1[n] - x0[n] if side == "end" else 0)
        angle = 2 * math.pi * at / span
        r = radius + (lane[n] if level is None else level) * 11
        return r * math.cos(angle), r * math.sin(angle)

    branches = sorted((n for n in group if n not in on_main), key=lambda n: (-(x1[n] - x0[n]), n))
    for _ in range(4):
        changed = False
        for n in branches:
            width = x1[n] - x0[n]
            if width >= span:
                continue
            neighbours = []
            for i in incident[n]:
                a, sa, b, sb, _kind = links[i]
                other, side = (b, sb) if a == n else (a, sa)
                if other != n:
                    neighbours.append(x1[other] if side == "end" else x0[other])
            # Cap quadratic candidate generation for unusually high-degree nodes.
            anchors = sorted(set(neighbours))[:16]
            centres = [x0[n] + width / 2]
            for x in anchors:
                centres.extend((x - width / 2 - NODE_GAP, x + width / 2 + NODE_GAP))
            for i, a in enumerate(anchors):
                for b in anchors[i:]:
                    centres.extend(((a + b) / 2, (a + b + span) / 2))
            starts = sorted({round(max(0, min(span - width, c % span - width / 2)), 6) for c in centres})
            levels = sorted({lane[n], -2, -1, 1, 2, lane[n] - 1, lane[n] + 1})

            def cost(start, level, reverse):
                if radius + level * 11 <= 5:
                    return math.inf
                for m in group:
                    if m != n and lane[m] == level and start < x1[m] + NODE_GAP and start + width + NODE_GAP > x0[m]:
                        return math.inf
                total = 0
                for i in incident[n]:
                    a, sa, b, sb, _kind = links[i]
                    pa = point(a, sa, start, level, reverse) if a == n else point(a, sa)
                    pb = point(b, sb, start, level, reverse) if b == n else point(b, sb)
                    total += math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                return total + abs(level) * 0.25 + (6 * abs(level) if level < 0 else 0)

            best = (cost(x0[n], lane[n], False), x0[n], lane[n], False)
            for start in starts:
                for level in levels:
                    for reverse in (False, True):
                        score = cost(start, level, reverse)
                        if score < best[0] - 1e-6:
                            best = score, start, level, reverse
            _, start, level, reverse = best
            if abs(start - x0[n]) > 1e-5 or level != lane[n] or reverse:
                changed = True
                x0[n], x1[n], lane[n] = start, start + width, level
                if reverse:
                    ori[n] = flip(ori[n])
                    for i in incident[n]:
                        link = links[i]
                        for node_index, side_index in ((0, 1), (2, 3)):
                            if link[node_index] == n:
                                link[side_index] = "start" if link[side_index] == "end" else "end"
                        link[4] = "twisted" if link[1] == link[3] else "forward"
        if not changed:
            break


GROUP_ORDERS = ("track", "total", "nodes")  # the longest track (main line) / all the bases / the most nodes first


def layout(graph, min_length=0, min_depth=0, only=None, hits=None, to_scale=False, order="track"):
    """The graph view's data: groups (the one with the longest track first, or by total
    bases or number of nodes: `order`), each laid out (layout_group) with its nodes'
    facts, and how many nodes were left out.
    to_scale: nodes drawn in proportion to their bases (the longest shown
    `SCALE_PX` px, none under `SCALE_LEAST`), else by the square root (node_px)."""
    nodes = graph["nodes"]
    table = _link_table(graph)
    nexts = _nexts(table)
    show = kept(graph, min_depth)
    groups, hidden = shown_groups(graph, show, min_length)
    show = [n for g in groups for n in g]
    px = node_px
    if to_scale and show:
        per_bp = SCALE_PX / max(nodes[n]["length"] for n in show)
        px = lambda bp: max(SCALE_LEAST, bp * per_bp)
    scale, depth_range = depth_scale(graph)
    marks = {}
    for h in (hits or {}).get("hits", []):
        if h["query"] == 0:
            # [reference from, to, identity, reversed on the node, node from, to (0-based, in the file's sequence)]
            marks.setdefault(h["node"], []).append([min(h["qstart"], h["qend"]), max(h["qstart"], h["qend"]),
                                                    round(h["identity"], 1), h["end"] < h["start"],
                                                    min(h["start"], h["end"]) - 1, max(h["start"], h["end"])])
    result, count, left = [], 0, len(nodes) - len(show)
    for group in groups:
        if only is not None and not only.intersection(group):
            left += len(group)
            continue
        if count + len(group) > MAX_LAYOUT_NODES and result:
            left += len(group)
            continue
        count += len(group)
        g = layout_group(graph, group, table, nexts, px)
        for n, place in g["nodes"].items():
            node = nodes[n]
            place.update(length=node["length"], depth=round(node["depth"], 1) if node["depth"] else None,
                         color=depth_color(scale(node["depth"]) or 0) if node["depth"] else "#9aa1a9",
                         hits=marks.get(n, []))
        bases = sum(nodes[n]["length"] for n in group)
        g["name"] = f"node {group[0]}" if len(group) == 1 else ""
        g["track"] = sum(nodes[n]["length"] for n in g["main"])  # the main line's bases
        g["description"] = ", ".join(filter(None, ["circular" if g["circular"] else "",
                                                   f"{len(group)} node{'s' if len(group) > 1 else ''}",
                                                   f"{g['track']:,} bp track" if len(group) > 1 else "", f"{bases:,} bp"]))
        g["bases"] = bases
        result.append(g)
    key = {"track": lambda g: (-g["track"], -g["bases"]), "total": lambda g: -g["bases"],
           "nodes": lambda g: (-len(g["nodes"]), -g["bases"])}[order if order in GROUP_ORDERS else "track"]
    result.sort(key=key)  # (stable: equal ones stay in the order of their total bases)
    for number, g in enumerate((g for g in result if len(g["nodes"]) > 1), 1):
        g["name"] = f"Group {number}"
    return {"groups": result, "left": left, "depth_range": depth_range, "depth_palette": DEPTH_PALETTE}
