"""Files for a GenBank submission: a FASTA with the source modifiers in its
headers and NCBI's five-column feature table (.tbl), the pair table2asn reads
(same base name). Records are SeqRecords; nothing is checked here (see checks.py)."""
import re

from Bio.SeqFeature import AfterPosition, BeforePosition

from . import checks
from .genbank import genetic_code

# Qualifiers that are Cladia's own or that NCBI makes itself
SKIP = {"translation", "label", "sequence_name"}
RNAS = ("tRNA", "rRNA", "mRNA", "ncRNA", "misc_RNA")
LOCATIONS = {"mitochondri": "mitochondrion", "plastid": "plastid", "chloroplast": "chloroplast"}


def _source(record):
    return next((f for f in record.features if f.type == "source"), None)


def seq_id(record):
    return re.sub(r"[^\w.\-]", "_", record.name or record.id)


def fasta(record):
    """>id [organism=...] [moltype=genomic] [location=...] [topology=...] [gcode=...], 60 bases a line."""
    source = _source(record)
    quals = source.qualifiers if source else {}
    mods = []
    organism = record.annotations.get("organism") or (quals.get("organism") or [""])[0]
    if organism and organism != ".":
        mods.append(f"[organism={organism}]")
    mods.append("[moltype=genomic]")
    organelle = (quals.get("organelle") or [""])[0].lower()
    for word, name in LOCATIONS.items():
        if word in organelle:
            mods.append(f"[location={name}]")
            break
    mods.append(f"[topology={record.annotations.get('topology', 'linear')}]")
    mods.append(f"[gcode={genetic_code(record)}]")
    for key in ("strain", "isolate", "country", "collection_date", "host", "specimen_voucher"):
        if quals.get(key):
            mods.append(f"[{key}={quals[key][0]}]")
    seq = str(record.seq).upper()
    lines = [seq[i:i + 60] for i in range(0, len(seq), 60)]
    return f">{seq_id(record)} {' '.join(mods)}\n" + "\n".join(lines) + "\n"


def _intervals(feature):
    """(start, stop) per part, 1-based, in biological order; stop < start on the - strand.
    A partial end is written <start (5' end) or >stop (3' end), as the table wants."""
    out = []
    for part in feature.location.parts:
        a, b = int(part.start) + 1, int(part.end)
        before, after = isinstance(part.start, BeforePosition), isinstance(part.end, AfterPosition)
        if part.strand == -1:  # the 5' end is the right-hand one
            out.append((f"<{b}" if after else str(b), f">{a}" if before else str(a)))
        else:
            out.append((f"<{a}" if before else str(a), f">{b}" if after else str(b)))
    return out


def table(record, locus_tag_prefix="", drop_cladia_notes=True, dropped=None):
    """The feature table of one record (without the source: that is in the FASTA).
    dropped: a Counter that gets each qualifier left out for not being a GenBank one."""
    lines = [f">Feature {seq_id(record)}"]
    features = [f for f in record.features if f.type != "source"]
    tags = {}
    if locus_tag_prefix:  # one tag per gene, in the order along the sequence; its CDS and RNAs share it
        genes = sorted((f for f in features if f.type == "gene"), key=lambda f: int(f.location.start))
        for n, gene in enumerate(genes, 1):
            tags[id(gene)] = f"{locus_tag_prefix}_{n:03d}"
    for f in features:
        tag = tags.get(id(f))
        if locus_tag_prefix and not tag and f.type in ("CDS", *RNAS):
            over = next((g for g in features if g.type == "gene" and int(g.location.start) < int(f.location.end)
                         and int(f.location.start) < int(g.location.end)), None)
            tag = tags.get(id(over)) if over else None
        intervals = _intervals(f)
        lines.append(f"{intervals[0][0]}\t{intervals[0][1]}\t{f.type}")
        lines += [f"{a}\t{b}" for a, b in intervals[1:]]
        quals = {k: list(v) for k, v in f.qualifiers.items() if k not in SKIP}
        if f.type == "gene" and "gene" not in quals:
            name = (f.qualifiers.get("label") or f.qualifiers.get("locus_tag") or [""])[0]
            if name:
                quals["gene"] = [name]
        if tag:
            quals["locus_tag"] = [tag]
        for key, values in quals.items():
            if key not in checks.QUALIFIERS:  # a GFF3's ID, Dbxref, Parent...: NCBI would warn about each
                if dropped is not None:
                    dropped[key] += len(values)
                continue
            for value in values:
                if drop_cladia_notes and key == "note" and checks.CLADIA_NOTE.search(str(value)):
                    continue
                lines.append(f"\t\t\t{key}\t{str(value).replace(chr(10), ' ')}")
    return "\n".join(lines) + "\n"


def files(records, locus_tag_prefix="", drop_cladia_notes=True, dropped=None):
    """(fasta text, table text) for the records."""
    return ("".join(fasta(r) for r in records),
            "".join(table(r, locus_tag_prefix, drop_cladia_notes, dropped) for r in records))
