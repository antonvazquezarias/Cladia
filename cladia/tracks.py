"""Data tracks: a number along a sequence, from bedGraph or WIG files.

A track is a list of intervals [start, end, value] (0-based, end exclusive, as
bedGraph has them), drawn under the ruler like the GC track (seqview-gc.js).
Tracks are kept beside their sequence (entry "tracks", <name>.tracks.json:
{"tracks": [{name, file, intervals, min, max}]}), not as items of their own:
nothing but the viewer reads them. Coverage, methylation, expression or any
score from another program fits.

bedGraph:  chrom  start  end  value        (optional "track"/"browser" lines)
WIG:       variableStep chrom=X [span=N]   then "position value" lines (1-based)
           fixedStep chrom=X start=S step=T [span=N]   then one value per line
"""

import re

MAX_INTERVALS = 200_000  # per track; more are averaged into this many bins


def is_bedgraph(text):
    """bedGraph: a "track type=bedGraph" line, or data lines of exactly
    chrom, start, end and a number (a BED file's fourth column is a name)."""
    seen = 0
    for line in text.splitlines()[:200]:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("browser "):
            continue
        if line.startswith("track "):
            if re.search(r"type=bedGraph", line, re.I):
                return True
            continue
        fields = line.split()
        if len(fields) != 4 or not (fields[1].isdigit() and fields[2].isdigit()) or not _number(fields[3]):
            return False
        seen += 1
        if seen >= 5:
            break
    return seen > 0


def is_wig(text):
    for line in text.splitlines()[:50]:
        line = line.strip()
        if not line or line.startswith(("#", "track ", "browser ")):
            continue
        return line.startswith(("variableStep", "fixedStep"))
    return False


def _number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def _name(text, fallback):
    for line in text.splitlines()[:20]:
        if line.startswith("track "):
            m = re.search(r'name=("([^"]*)"|(\S+))', line)
            if m:
                return m.group(2) or m.group(3)
    return fallback


def read_tracks(text, fallback_name="track"):
    """{chrom: intervals} from bedGraph or WIG text, and the track's name."""
    by_chrom = {}
    if is_wig(text):
        chrom = None
        mode = start = step = span = position = None
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(("#", "track ", "browser ")):
                continue
            if line.startswith(("variableStep", "fixedStep")):
                opts = dict(p.split("=", 1) for p in line.split()[1:] if "=" in p)
                mode, chrom = line.split()[0], opts.get("chrom")
                if not chrom:
                    raise ValueError(f"line {n}: no chrom= on the {mode} line")
                span = int(opts.get("span", 1))
                if mode == "fixedStep":
                    start, step = int(opts.get("start", 1)), int(opts.get("step", 1))
                    position = start
                continue
            if mode is None:
                raise ValueError(f"line {n}: data before a variableStep or fixedStep line")
            parts = line.split()
            try:
                if mode == "variableStep":
                    pos, value = int(parts[0]), float(parts[1])
                else:
                    pos, value = position, float(parts[0])
                    position += step
            except (ValueError, IndexError):
                raise ValueError(f"line {n}: not a WIG data line")
            by_chrom.setdefault(chrom, []).append([pos - 1, pos - 1 + span, value])
    elif is_bedgraph(text):
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(("#", "track ", "browser ")):
                continue
            fields = line.split()
            try:
                by_chrom.setdefault(fields[0], []).append([int(fields[1]), int(fields[2]), float(fields[3])])
            except (ValueError, IndexError):
                raise ValueError(f"line {n}: not a bedGraph line (chrom, start, end, value)")
    else:
        raise ValueError("not a bedGraph or WIG file")
    if not by_chrom:
        raise ValueError("no values in this file")
    return by_chrom, _name(text, fallback_name)


def fit(intervals, length):
    """Intervals sorted and clipped to a sequence of this length, averaged into
    MAX_INTERVALS bins when there are more. Raises ValueError when none fit."""
    kept = sorted([max(0, s), min(e, length), v] for s, e, v in intervals if s < length and e > s and e > 0)
    if not kept:
        raise ValueError("none of its values fall inside the sequence")
    if len(kept) > MAX_INTERVALS:
        size = -(-length // MAX_INTERVALS)
        bins = {}
        for s, e, v in kept:  # each bin: the mean over the bases it has values for
            for b in range(s // size, (e - 1) // size + 1):
                covered = min(e, (b + 1) * size) - max(s, b * size)
                total, bases = bins.get(b, (0.0, 0))
                bins[b] = (total + v * covered, bases + covered)
        kept = [[b * size, min(length, (b + 1) * size), total / bases] for b, (total, bases) in sorted(bins.items())]
    values = [v for _, _, v in kept]
    return {"intervals": kept, "min": min(values), "max": max(values)}
