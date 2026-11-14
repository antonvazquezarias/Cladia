"""Distance trees from an alignment: neighbour-joining (Saitou & Nei 1987) or UPGMA.

Pairwise distances use only the sites both rows have (gaps, N and other
ambiguity codes are left out pair by pair). Columns are first reduced to their
distinct patterns with counts, so a pair's comparison counts are matrix products
of one-hot codes weighted by the counts; a bootstrap replicate only redraws the
weights. Supports are the percentage of replicates with each branch's split.
"""
import numpy as np

DNA, PROTEIN = "ACGT", "ACDEFGHIKLMNPQRSTVWY"
DISTANCES = {"auto", "p", "jc", "k2p", "poisson"}
DISTANCE_NAMES = {"p": "p-distance", "jc": "Jukes-Cantor", "k2p": "Kimura 2-parameter", "poisson": "Poisson"}
DNA_ONLY, PROTEIN_ONLY = {"jc", "k2p"}, {"poisson"}
SATURATED = 10.0  # the distance given to pairs too different for the correction


# ── Distances ──────────────────────────────────────────────────────────────

def _patterns(sequences, letters):
    """(one-hot codes [letter, row, pattern], counts per pattern) of the columns."""
    table = np.full(256, -1, dtype=np.int8)
    for code, letter in enumerate(letters):
        table[ord(letter)] = table[ord(letter.lower())] = code
    if letters == DNA:
        table[ord("U")] = table[ord("u")] = 3
    rows = np.stack([table[np.frombuffer(s.encode("ascii", "replace"), dtype=np.uint8)] for s in sequences])
    columns, counts = np.unique(rows, axis=1, return_counts=True)
    return np.stack([(columns == k).astype(np.float64) for k in range(len(letters))]), counts.astype(np.float64)


def distances(onehot, weights, kind, names):
    """The distance matrix for column weights (counts, or a bootstrap's redraw)."""
    weighted = onehot * weights
    present = onehot.sum(axis=0)
    valid = (present * weights) @ present.T
    same = sum(weighted[k] @ onehot[k].T for k in range(len(onehot)))
    if np.any(valid == 0):
        i, j = np.argwhere(valid == 0)[0]
        raise ValueError(f"{names[i]} and {names[j]} share no compared sites")
    p = (valid - same) / valid
    with np.errstate(divide="ignore", invalid="ignore"):
        if kind == "p":
            d = p
        elif kind == "jc":
            d = -0.75 * np.log(1 - 4 / 3 * p)
        elif kind == "poisson":
            d = -np.log(1 - p)
        else:  # k2p: transitions A<->G, C<->T
            ts = sum(weighted[a] @ onehot[b].T + weighted[b] @ onehot[a].T for a, b in ((0, 2), (1, 3))) / valid
            tv = p - ts
            d = -0.5 * np.log(1 - 2 * ts - tv) - 0.25 * np.log(1 - 2 * tv)
    d = np.where(np.isfinite(d), d, SATURATED)
    np.fill_diagonal(d, 0)
    return np.maximum(d, 0)


# ── Trees ──────────────────────────────────────────────────────────────────

class Node:
    def __init__(self, name=None, children=(), length=0.0):
        self.name, self.children, self.length, self.support = name, list(children), length, None


def nj(d, names):
    """Neighbour-joining: an unrooted tree, its last three nodes joined at the root."""
    nodes = [Node(name) for name in names]
    d = d.copy()
    while len(nodes) > 3:
        m = len(nodes)
        r = d.sum(axis=1)
        q = (m - 2) * d - r[:, None] - r[None, :]
        np.fill_diagonal(q, np.inf)
        i, j = np.unravel_index(np.argmin(q), q.shape)
        li = d[i, j] / 2 + (r[i] - r[j]) / (2 * (m - 2))
        li = min(max(li, 0.0), d[i, j])
        nodes[i].length, nodes[j].length = li, d[i, j] - li
        joined = Node(children=[nodes[i], nodes[j]])
        new = (d[i] + d[j] - d[i, j]) / 2
        keep = [k for k in range(m) if k not in (i, j)]
        d = np.vstack([np.append(d[np.ix_(keep, keep)], new[keep][:, None], axis=1),
                       np.append(new[keep], 0.0)[None, :]])
        nodes = [nodes[k] for k in keep] + [joined]
    if len(nodes) == 2:
        nodes[0].length = nodes[1].length = d[0, 1] / 2
        return Node(children=nodes)
    a, b, c = nodes
    a.length = max(0.0, (d[0, 1] + d[0, 2] - d[1, 2]) / 2)
    b.length = max(0.0, (d[0, 1] + d[1, 2] - d[0, 2]) / 2)
    c.length = max(0.0, (d[0, 2] + d[1, 2] - d[0, 1]) / 2)
    return Node(children=nodes)


def upgma(d, names):
    """UPGMA: a rooted tree, every tip at the same height."""
    nodes = [Node(name) for name in names]
    sizes, heights = [1] * len(names), [0.0] * len(names)
    d = d.astype(np.float64).copy()
    np.fill_diagonal(d, np.inf)
    while len(nodes) > 1:
        i, j = np.unravel_index(np.argmin(d), d.shape)
        i, j = min(i, j), max(i, j)
        height = d[i, j] / 2
        nodes[i].length, nodes[j].length = max(0.0, height - heights[i]), max(0.0, height - heights[j])
        merged = (sizes[i] * d[i] + sizes[j] * d[j]) / (sizes[i] + sizes[j])
        nodes[i], sizes[i], heights[i] = Node(children=[nodes[i], nodes[j]]), sizes[i] + sizes[j], height
        d[i, :], d[:, i] = merged, merged
        d[i, i] = np.inf
        d = np.delete(np.delete(d, j, axis=0), j, axis=1)
        del nodes[j], sizes[j], heights[j]
    return nodes[0]


def _walk(root):
    """Every node, parents before children (no recursion: deep trees)."""
    order, stack = [], [root]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(node.children)
    return order


def root_on(root, tip_name):
    """The tree rooted halfway along the branch to one tip (an outgroup)."""
    order = _walk(root)
    neighbours = {id(n): [] for n in order}
    for node in order:
        for child in node.children:
            neighbours[id(node)].append((child, child.length))
            neighbours[id(child)].append((node, child.length))
    tip = next((n for n in order if n.name == tip_name and not n.children), None)
    if tip is None:
        raise ValueError(f"No row named {tip_name}")
    (inner, length), = neighbours[id(tip)]
    half = length / 2
    stack = [(inner, tip, half)]  # hang the rest of the tree from the tip's neighbour
    while stack:
        node, came_from, above = stack.pop()
        node.length = above
        node.children = [n for n, _ in neighbours[id(node)] if n is not came_from]
        stack.extend((n, node, l) for n, l in neighbours[id(node)] if n is not came_from)
    tip.length, tip.children = half, []
    new = Node(children=[tip, inner])
    if len(inner.children) == 1:  # the old root had two branches: drop the node between them
        (only,) = inner.children
        only.length += inner.length
        new.children[1] = only
    return new


def splits(root, index):
    """{node id: the tips on its side of the branch above it, as a bit mask, normalised
    to the side without tip 0} for the inner branches."""
    masks, full = {}, (1 << len(index)) - 1
    for node in reversed(_walk(root)):
        masks[id(node)] = (1 << index[node.name]) if not node.children else \
            sum(masks[id(c)] for c in node.children)
    out = {}
    for node in _walk(root):
        if node is root or not node.children:
            continue
        mask = masks[id(node)]
        mask = full ^ mask if mask & 1 else mask
        # a real split has two tips or more on each side; the root's two branches
        # are one branch, so its split is kept once
        if bin(mask).count("1") >= 2 and bin(full ^ mask).count("1") >= 2 and mask not in out.values():
            out[id(node)] = mask
    return out


def _tail(node, root):
    """What follows a node's children in Newick: ")", its label, its branch length."""
    if node.children:
        text = ")" + ("" if node.support is None else str(node.support))
    else:
        name = node.name or ""
        text = name if name and not any(c in name for c in " ()[]:;,'\t") else "'" + name.replace("'", "''") + "'"
    return text + ("" if node is root else f":{node.length:.6g}")


def newick(root):
    """Newick text, names quoted when needed, supports as inner node labels."""
    out, stack = [], [root]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif item.children:
            out.append("(")
            stack.append(_tail(item, root))
            for k, child in enumerate(reversed(item.children)):
                if k:
                    stack.append(",")
                stack.append(child)
        else:
            out.append(_tail(item, root))
    return "".join(out) + ";"


# ── The job ────────────────────────────────────────────────────────────────

def build(names, sequences, method="nj", distance="auto", bootstrap=0, outgroup=None,
          protein=False, seed=1, cancelled=lambda: False, progress=lambda fraction: None):
    """(Newick, notes) for aligned sequences under unique names."""
    if method not in ("nj", "upgma"):
        raise ValueError("Choose neighbour-joining or UPGMA")
    if distance not in DISTANCES or (protein and distance in DNA_ONLY) or (not protein and distance in PROTEIN_ONLY):
        raise ValueError(f"The {distance} distance is for {'DNA' if distance in DNA_ONLY else 'proteins'}")
    if len(names) < 3:
        raise ValueError("A tree needs at least three sequences")
    if not isinstance(bootstrap, int) or isinstance(bootstrap, bool) or not 0 <= bootstrap <= 10000:
        raise ValueError("Bootstrap replicates must be between 0 and 10,000")
    kind = ("poisson" if protein else "k2p") if distance == "auto" else distance
    onehot, counts = _patterns(sequences, PROTEIN if protein else DNA)
    make = nj if method == "nj" else upgma

    def tree(weights):
        root = make(distances(onehot, weights, kind, names), names)
        return root_on(root, outgroup) if outgroup and method == "nj" else root

    root = tree(counts)
    notes = [f"{'Neighbour-joining' if method == 'nj' else 'UPGMA'}, {DISTANCE_NAMES[kind]} distances, "
             f"{int(counts.sum()):,} columns ({len(counts):,} distinct)"]
    if outgroup and method == "upgma":
        notes.append("UPGMA trees are rooted by the method itself; the outgroup was not used")
    if bootstrap:
        index = {name: k for k, name in enumerate(names)}
        mine = splits(root, index)
        found = dict.fromkeys(mine.values(), 0)
        rng = np.random.default_rng(seed)
        total = int(counts.sum())
        for b in range(bootstrap):
            if cancelled():
                raise RuntimeError("cancelled")
            weights = rng.multinomial(total, counts / total).astype(np.float64)
            for mask in splits(tree(weights), index).values():
                if mask in found:
                    found[mask] += 1
            progress((b + 1) / bootstrap)
        by_id = {id(n): n for n in _walk(root)}
        for node_id, mask in mine.items():
            by_id[node_id].support = round(100 * found[mask] / bootstrap)
        notes.append(f"{bootstrap} bootstrap replicates (seed {seed})")
    return newick(root), notes
