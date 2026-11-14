"""Alignment and tree formats besides FASTA, Clustal and Newick: NEXUS (alignments
and trees), PHYLIP, Stockholm, and Newick/NEXUS trees whose supports are in
comments (MrBayes, BEAST: [&prob=0.97], [&posterior=0.99]).

Biopython reads the alignments; NEXUS trees are read here, because Biopython's
NEXUS tree parser fails on MrBayes's comments."""

import re
from io import StringIO

from Bio import AlignIO, Phylo

PHYLIP_FORMATS = ("phylip-relaxed", "phylip", "phylip-sequential")  # names without/with spaces, wrapped sequences


# ── Alignments ───────────────────────────────────────────────────────────

def read_alignment(text, fmt):
    """Records of an alignment in "nexus", "phylip" or "stockholm" (all rows the
    same length; NEXUS missing data "?" becomes a gap)."""
    if fmt == "phylip":
        for variant in PHYLIP_FORMATS:  # each fails or misreads some layout; the first that fits wins
            try:
                recs = list(AlignIO.read(StringIO(text), variant))
            except Exception:
                continue
            if recs and len({len(r) for r in recs}) == 1:
                return recs
        raise ValueError("not a readable PHYLIP alignment")
    if fmt == "nexus":
        text = _without_trees(text)  # Biopython's NEXUS reader trips over MrBayes tree comments
    recs = list(AlignIO.read(StringIO(text), fmt))
    for r in recs:
        if "?" in r.seq:
            r.seq = r.seq.replace("?", "-")
        r.description = ""
    return recs


def nexus_has_alignment(text):
    return bool(re.search(r"begin\s+(data|characters)\s*;", text, re.I))


def write_alignment(records, fmt, protein):
    """An alignment as NEXUS or (relaxed) PHYLIP text."""
    out = []
    for r in records:
        r = r[:]
        r.id = re.sub(r"\s+", "_", r.id)
        r.description = ""
        r.annotations["molecule_type"] = "protein" if protein else "DNA"
        out.append(r)
    from Bio.Align import MultipleSeqAlignment
    buffer = StringIO()
    AlignIO.write(MultipleSeqAlignment(out), buffer, "nexus" if fmt == "nexus" else "phylip-relaxed")
    return buffer.getvalue()


# ── Trees ────────────────────────────────────────────────────────────────

def _statements(block):
    """A NEXUS block's statements, split at ";" outside comments and quotes."""
    out, start, depth, quote = [], 0, 0, False
    for i, c in enumerate(block):
        if c == "'" and not depth:
            quote = not quote
        elif c == "[" and not quote:
            depth += 1
        elif c == "]" and not quote:
            depth -= 1
        elif c == ";" and not depth and not quote:
            out.append(block[start:i].strip())
            start = i + 1
    return [s for s in out if s]


def _blocks(text, name):
    return re.findall(rf"begin\s+{name}\s*;(.*?)\bend(?:block)?\s*;", text, re.I | re.S)


def _without_trees(text):
    return re.sub(r"begin\s+trees\s*;.*?\bend(?:block)?\s*;", "", text, flags=re.I | re.S)


def _support(comment):
    """The support in a node comment such as "&prob=9.7e-01,prob(percent)="97",..."
    (MrBayes) or "&posterior=0.99,height=..." (BEAST), as text; None if none."""
    values, depth = {}, 0
    body = comment.lstrip("&")
    parts, start = [], 0
    for i, c in enumerate(body):  # split at commas outside {...} and quotes
        if c in "{(":
            depth += 1
        elif c in "})":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    for part in parts:
        key, _, value = part.partition("=")
        values[key.strip().lower()] = value.strip().strip('"')
    for key in ("prob", "posterior", "support", "label", "bootstrap"):
        if key in values:
            try:
                return f"{round(float(values[key]), 2):g}"
            except ValueError:
                return values[key] or None
    return None


def comments_to_labels(newick):
    """Newick with [&...] comments -> plain Newick: a support in a node's comment
    (right after its ")") becomes the node's label; all other comments go."""
    out, i = [], 0
    for m in re.finditer(r"\[([^\]]*)\]", newick):
        out.append(newick[i:m.start()])
        j = m.start() - 1
        while j >= 0 and newick[j].isspace():
            j -= 1
        if j >= 0 and newick[j] == ")" and m.group(1).startswith("&"):
            label = _support(m.group(1))
            if label:
                out.append(label)
        i = m.end()
    out.append(newick[i:])
    return "".join(out)


def read_trees(text, fmt):
    """The trees in a Newick or NEXUS file, as Bio.Phylo trees, supports from
    comments as confidences and NEXUS translate tables applied."""
    if fmt == "nexus":
        newicks, translate = [], {}
        for block in _blocks(text, "trees"):
            for statement in _statements(block):
                word = statement.split(None, 1)[0].lower()
                if word == "translate":
                    for pair in statement.split(None, 1)[1].split(","):
                        bits = pair.split(None, 1)
                        if len(bits) == 2:
                            translate[bits[0]] = bits[1].strip().strip("'")
                elif word in ("tree", "utree"):
                    newicks.append(statement.split("=", 1)[1].strip() + ";")
    else:
        newicks = [s + ";" for s in _statements(text.strip() + ";")]  # a missing final ";" is fine
    trees = []
    for newick in newicks:
        tree = Phylo.read(StringIO(comments_to_labels(newick)), "newick")
        for tip in tree.get_terminals():
            tip.name = translate.get(tip.name, tip.name) if fmt == "nexus" else tip.name
        trees.append(tree)
    return trees


def newick_text(tree):
    buffer = StringIO()
    Phylo.write(tree, buffer, "newick", format_branch_length="%g", format_confidence="%g")
    return buffer.getvalue()


def write_trees(texts, fmt):
    """Newick texts (the library's trees) as one Newick or NEXUS file."""
    if fmt == "newick":
        return "".join(t.strip() + "\n" for t in texts)
    trees = [Phylo.read(StringIO(t), "newick") for t in texts]
    buffer = StringIO()
    Phylo.write(trees, buffer, "nexus", format_confidence="%g", format_branch_length="%g")  # 95, not 95.00
    return buffer.getvalue()


# ── XMFA (progressiveMauve's genome alignments) ──────────────────────────
# Blocks separated by "=" lines; each entry "> genome:start-end strand file",
# 1-based genomes and positions, the bases of a - strand entry reverse
# complemented, gaps as "-". A block in one genome only is a stretch it shares
# with none of the others.

def read_xmfa(text):
    """[{"parts": [{"genome", "start", "end", "strand", "seq"}], "columns"}] in file order."""
    blocks = []
    for chunk in re.split(r"^=.*$", text, flags=re.M):
        parts = []
        for m in re.finditer(r"^> *(\d+):(\d+)-(\d+) ([+-])[^\n]*\n([^>]*)", chunk, re.M):
            start, end = int(m.group(2)), int(m.group(3))
            if not start and not end:  # a genome not in this block
                continue
            parts.append({"genome": int(m.group(1)) - 1, "start": start, "end": end,
                          "strand": 1 if m.group(4) == "+" else -1, "seq": re.sub(r"\s", "", m.group(5)).upper()})
        if parts:
            blocks.append({"parts": parts, "columns": max(len(p["seq"]) for p in parts)})
    return blocks
