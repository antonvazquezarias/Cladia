"""Primer design with Primer3, and a small in-silico PCR for testing primers.

Primer3 works on a linear template; circular sequences are handled here by
rotating a target to the middle, or by doubling the sequence so a product may
cross the origin. Positions in the public results are 1-based.
"""

from __future__ import annotations

IUPAC = {
    "A": set("A"), "C": set("C"), "G": set("G"), "T": set("T"), "U": set("T"),
    "R": set("AG"), "Y": set("CT"), "S": set("CG"), "W": set("AT"),
    "K": set("GT"), "M": set("AC"), "B": set("CGT"), "D": set("AGT"),
    "H": set("ACT"), "V": set("ACG"), "N": set("ACGT"),
}
COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVNacgtryswkmbdhvn", "TGCAYRSWMKVHDBNtgcayrswmkvhdbn")


def normalize(primer):
    """A primer as Cladia holds it: uppercase, U as T, no spaces."""
    return "".join(str(primer or "").split()).upper().replace("U", "T")


def reverse_complement(primer):
    return normalize(primer).translate(COMPLEMENT)[::-1]


def validate(primer, what="primer"):
    primer = normalize(primer)
    if not 8 <= len(primer) <= 40:
        raise ValueError(f"The {what} must be 8 to 40 bases long")
    bad = sorted({c for c in primer if c not in IUPAC})
    if bad:
        raise ValueError(f"The {what} has characters that are not DNA/IUPAC: {''.join(bad)}")
    return primer


def _matches(base, code):
    return base in IUPAC.get(code, ())


def find_sites(sequence, primer, mismatches=0, circular=False, limit=2000, three_prime_first=False):
    """Binding sites of one primer on the plus strand: 0-based start/end.

    A site may cross the origin when circular. Up to `mismatches` bases may
    differ; those in the five bases at the primer's 3' end are counted separately
    ("three"): the last five, or the first five when the sequence searched is a
    reverse primer's reverse complement (three_prime_first).
    """
    seq = str(sequence).upper()
    primer = normalize(primer)
    n = len(seq)
    if not n or not primer:
        return []
    scan = seq + seq[:len(primer) - 1] if circular else seq
    last = len(primer) - 1
    found = []
    for start in range(n if circular else n - len(primer) + 1):
        errors = three = 0
        for j, code in enumerate(primer):
            if not _matches(scan[start + j], code):
                errors += 1
                if (j <= 4) if three_prime_first else (j >= last - 4):
                    three += 1
                if errors > mismatches:
                    break
        else:
            found.append({"start": start, "end": (start + last) % n if circular else start + last,
                          "mismatches": errors, "three": three,
                          "sequence": scan[start:start + len(primer)]})
            if len(found) >= limit:
                break
    return found


def _products(sequence, forward, reverse, mismatches, product_min, product_max, circular):
    """Products for one orientation: forward on the plus strand, reverse on minus."""
    seq = str(sequence).upper()
    n = len(seq)
    fwd = find_sites(seq, forward, mismatches, circular)
    # the reverse primer binds the plus strand as its reverse complement: its 3' end comes first
    rev = find_sites(seq, reverse_complement(reverse), mismatches, circular, three_prime_first=True)
    hits = []
    for f in fwd:
        for r in rev:
            size = (r["start"] - f["start"]) % n + len(reverse) if circular else r["start"] - f["start"] + len(reverse)
            if not product_min <= size <= product_max or size > n:
                continue
            end = (f["start"] + size - 1) % n if circular else f["start"] + size - 1
            hits.append({
                "forward": {"start": f["start"] + 1, "end": f["end"] + 1,
                            "mismatches": f["mismatches"], "three": f["three"]},
                "reverse": {"start": r["start"] + 1, "end": r["end"] + 1,
                            "mismatches": r["mismatches"], "three": r["three"]},
                "product_size": size, "product_start": f["start"] + 1, "product_end": end + 1,
                "wraps": circular and end < f["start"],
            })
            if len(hits) >= 200:
                return hits
    return hits


def test_pair(sequence, forward, reverse, mismatches=0, product_min=50, product_max=2000,
              circular=False, both_orientations=True, three_prime=None):
    """Products of a primer pair: the given orientation, and optionally its swap.

    The second orientation tests a template whose plus strand is the reverse
    complement of the given one; its strand is -1. three_prime: the most
    mismatches either primer may have in its last five bases (the 3' end, which
    decides whether it's extended), None = any within `mismatches`.
    """
    forward, reverse = validate(forward, "forward primer"), validate(reverse, "reverse primer")
    hits = _products(sequence, forward, reverse, mismatches, product_min, product_max, circular)
    for hit in hits:
        hit["strand"] = 1
    if both_orientations:
        other = _products(sequence, reverse, forward, mismatches, product_min, product_max, circular)
        for hit in other:
            hit["strand"] = -1
        hits += other
    if three_prime is not None:
        hits = [h for h in hits if h["forward"]["three"] <= three_prime and h["reverse"]["three"] <= three_prime]
    # The same product found twice (e.g. a symmetric site) is shown once.
    seen, unique = set(), []
    for hit in hits:
        key = (hit["strand"], hit["forward"]["start"], hit["reverse"]["start"], hit["product_size"])
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return sorted(unique, key=lambda h: (h["strand"], h["forward"]["start"]))


def design_input(sequence, target_start=None, target_end=None, product_min=200, product_max=1000,
                 tm_min=57.0, tm_opt=60.0, tm_max=63.0, gc_min=30.0, gc_max=70.0, num_return=5):
    """Primer3 input for a linear template. target_start/end are 0-based inclusive."""
    lines = [
        "PRIMER_TASK=generic",
        "PRIMER_PICK_LEFT_PRIMER=1",
        "PRIMER_PICK_RIGHT_PRIMER=1",
        "PRIMER_PICK_INTERNAL_OLIGO=0",
        "PRIMER_NUM_RETURN=" + str(max(1, min(int(num_return), 20))),
        "PRIMER_THERMODYNAMIC_PARAMETERS_PATH=/etc/primer3_config",
        f"PRIMER_PRODUCT_SIZE_RANGE={int(product_min)}-{int(product_max)}",
        f"PRIMER_MIN_TM={float(tm_min):g}",
        f"PRIMER_OPT_TM={float(tm_opt):g}",
        f"PRIMER_MAX_TM={float(tm_max):g}",
        f"PRIMER_MIN_GC={float(gc_min):g}",
        f"PRIMER_MAX_GC={float(gc_max):g}",
        "PRIMER_MAX_POLY_X=5",
        "PRIMER_EXPLAIN_FLAG=1",
        "SEQUENCE_ID=template",
        "SEQUENCE_TEMPLATE=" + str(sequence),
    ]
    if target_start is not None and target_end is not None:
        start, length = int(target_start), int(target_end) - int(target_start) + 1
        if length > 0:
            lines.append(f"SEQUENCE_TARGET={start},{length}")
    lines.append("=")
    return "\n".join(lines) + "\n"


def parse_design(text):
    """Primer3 key=value output -> [(pair dict)] and warnings."""
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if values.get("PRIMER_ERROR"):
        raise ValueError(values["PRIMER_ERROR"])
    warnings = [v for k, v in values.items() if k.startswith("PRIMER_WARNING")]
    pairs = []
    for i in range(50):
        left = values.get(f"PRIMER_LEFT_{i}_SEQUENCE")
        right = values.get(f"PRIMER_RIGHT_{i}_SEQUENCE")
        if not left or not right:
            continue
        def position(side):
            # Primer3 writes PRIMER_LEFT_i=start,length and PRIMER_RIGHT_i=5'end,length;
            # older output calls the same values ..._POSITION.
            raw = values.get(f"PRIMER_{side}_{i}_POSITION") or values.get(f"PRIMER_{side}_{i}", "")
            try:
                start, length = (int(x) for x in raw.split(","))
            except ValueError:
                return None
            if side == "RIGHT":  # the 5' end -> the leftmost base on the template
                start -= length - 1
            return start, length
        lp, rp = position("LEFT"), position("RIGHT")
        if not lp or not rp:
            continue
        pairs.append({
            "left": {"sequence": left, "start": lp[0], "length": lp[1],
                     "tm": float(values.get(f"PRIMER_LEFT_{i}_TM", 0) or 0),
                     "gc": float(values.get(f"PRIMER_LEFT_{i}_GC_PERCENT", 0) or 0),
                     "penalty": float(values.get(f"PRIMER_LEFT_{i}_PENALTY", 0) or 0)},
            "right": {"sequence": right, "start": rp[0], "length": rp[1],
                      "tm": float(values.get(f"PRIMER_RIGHT_{i}_TM", 0) or 0),
                      "gc": float(values.get(f"PRIMER_RIGHT_{i}_GC_PERCENT", 0) or 0),
                      "penalty": float(values.get(f"PRIMER_RIGHT_{i}_PENALTY", 0) or 0)},
            "product_size": int(values.get(f"PRIMER_PAIR_{i}_PRODUCT_SIZE", 0) or 0),
            "penalty": float(values.get(f"PRIMER_PAIR_{i}_PENALTY", 0) or 0),
        })
    return pairs, warnings


def map_design(pairs, rotation, length, circular=False):
    """Primer3 positions on a rotated/doubled template -> 1-based original positions."""
    mapped = []
    seen = set()
    for pair in pairs:
        left = pair["left"]
        right = pair["right"]
        left_start = (left["start"] + rotation) % length
        right_start = (right["start"] + rotation) % length
        product = pair["product_size"]
        if not product:
            product = right_start - left_start + right["length"]
        if product > length:
            continue
        if circular:
            left_end = (left_start + left["length"] - 1) % length
            right_end = (right_start + right["length"] - 1) % length
            product_end = (left_start + product - 1) % length
            wraps = product_end < left_start
        else:
            left_end = left_start + left["length"] - 1
            right_end = right_start + right["length"] - 1
            product_end = left_start + product - 1
            if left_end >= length or right_end >= length or product_end >= length:
                continue
            wraps = False
        key = (left_start, right_start, product)
        if key in seen:
            continue
        seen.add(key)
        mapped.append({
            "left": {**left, "start": left_start + 1, "end": left_end + 1},
            "right": {**right, "start": right_start + 1, "end": right_end + 1},
            "product_size": product,
            "product_start": left_start + 1,
            "product_end": product_end + 1,
            "wraps": wraps,
            "penalty": pair.get("penalty"),  # the pair's penalty (left/right carry their own)
        })
    return mapped


def circular_design_template(sequence, target_start=None, target_end=None):
    """A template for Primer3 on a circular sequence.

    With a target, the sequence is rotated so the target sits in the middle
    (one linear copy). Without one, it is doubled so products may cross the
    origin. Returns (template, rotation, target_start_on_template, target_length).
    """
    seq = str(sequence).upper()
    n = len(seq)
    if target_start is None or target_end is None:
        return seq + seq, 0, None, None
    target_start, target_end = int(target_start), int(target_end)
    target_len = (target_end - target_start) % n + 1 if target_end < target_start else target_end - target_start + 1
    if target_len >= n:
        return seq + seq, 0, None, None
    middle = (n - target_len) // 2
    rotation = (target_start - middle) % n
    template = seq[rotation:] + seq[:rotation]
    return template, rotation, middle, target_len
