"""Reading and writing sequence files, and conversion between Biopython
SeqRecords and the editor's JSON format.

Files are read in whatever encoding they come in (decode) and always written as
UTF-8. The editor's JSON uses 0-based, end-inclusive coordinates and
keeps GenBank qualifiers in a "notes" dict. Biopython uses 0-based,
end-exclusive coordinates. Everything about the file that the editor does not
touch (LOCUS metadata, source feature, comments...) is taken from the original
file when saving, so a round trip only changes what was edited.
"""

import codecs
import re
import time
from io import StringIO
from pathlib import Path

from Bio import SeqIO
from Bio.Data.CodonTable import TranslationError
from Bio.Seq import Seq
from Bio.SeqFeature import AfterPosition, BeforePosition, CompoundLocation, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord

from . import tracks

GENBANK_EXT = {".gb", ".gbk", ".genbank", ".gbff", ".gpff"}  # (library files are .gb or .fasta)

# Qualifiers used as the feature's display name, in order of preference
NAME_KEYS = ["label", "gene", "product", "locus_tag", "note"]


# ── Text ─────────────────────────────────────────────────────────────────

def decode(data):
    """File bytes -> text: UTF-8 (with or without a BOM), UTF-16 with its BOM
    ("Unicode" in old Notepad), else Windows-1252 (older Windows programs)."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8", "replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def read_text(path):
    return decode(Path(path).read_bytes())


# columns 3-12 of BLAST+ -outfmt 6: % identity, length, mismatches, gap opens, query and subject ranges, E value, bit score
_NUM = r"\d+(?:\.\d+)?"
BLASTTAB = re.compile(rf"{_NUM}\t\d+\t\d+\t\d+\t\d+\t\d+\t\d+\t\d+\t[0-9.eE+-]+\t{_NUM}")


def sniff(text):
    """A file's format from its first line: genbank, gff3, embl, nexus, stockholm,
    phylip, fasta, clustal, newick, gfa, phyloxml, blasttab (BLAST outfmt 6), bed, bedgraph, wig, vcf or None.
    (By content, so any file ending works: .faa, .aln, .txt...)"""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--"):  # an ASN.1 comment
            continue
        if re.match(r"(Submit-block|Seqdesc)\s*::=", line):  # NCBI's submission template (.sbt)
            return "sbt"
        if line.startswith("##fileformat=VCF"):
            return "vcf"
        if line.startswith("LOCUS"):
            return "genbank"
        if line.startswith("##gff-version"):
            return "gff3"
        if line.startswith("ID   "):
            return "embl"
        if line.upper().startswith("#NEXUS"):
            return "nexus"
        if line.upper().startswith("# STOCKHOLM"):
            return "stockholm"
        if re.fullmatch(r"\s*\d+\s+\d+\s*", line):  # "ntax nchar"
            return "phylip"
        if line.startswith(">"):
            # SPAdes' FASTG: no spaces, "EDGE_1_length_5_cov_2:EDGE_2',EDGE_3;" ending with ;
            return "fastg" if re.fullmatch(r">[^\s:;]+(:[^\s;]*)?;", line) else "fasta"
        if line.upper().startswith(("CLUSTAL", "MUSCLE", "PROBCONS")):
            return "clustal"
        if line.startswith("("):
            return "newick"
        if line.startswith(("<?xml", "<phyloxml")):
            return "phyloxml" if "<phyloxml" in text[:4096] else None
        if line.startswith(("track ", "browser ")):  # a BED, bedGraph or WIG file's header lines
            if re.search(r"type=(bedGraph|wiggle_0)", line, re.I):
                return "wig" if "wiggle" in line.lower() else "bedgraph"
            continue
        if line.startswith(("variableStep", "fixedStep")):  # WIG
            return "wig"
        fields = line.split("\t")
        if len(fields) >= 12 and BLASTTAB.fullmatch("\t".join(fields[2:12])):  # BLAST+ -outfmt 6 (its 12 default columns)
            return "blasttab"
        if len(fields) >= 3 and fields[1].isdigit() and fields[2].isdigit():  # chrom, start, end: BED (bedGraph: a number fourth)
            return "bedgraph" if tracks.is_bedgraph(text) else "bed"
        if re.match(r"[HSLP]\t", line):  # an assembly graph (GFA 1)
            return "gfa"
        return None
    return None


def looks_protein(seq):
    """Mostly letters that aren't nucleotide codes = amino acids."""
    letters = str(seq).upper().replace("-", "").replace(".", "").replace("*", "").replace("?", "").replace("X", "")
    if not letters:
        return False
    nucleotide = sum(letters.count(c) for c in "ACGTUN")
    return nucleotide < 0.9 * len(letters)


def is_protein(record):
    return record.annotations.get("molecule_type", "").lower() == "protein"


# ── Reading ──────────────────────────────────────────────────────────────

def file_format(path):
    return "genbank" if Path(path).suffix.lower() in GENBANK_EXT else "fasta"


# The usual feature keys as GenBank spells them: some files have "cds", "Gene",
# "TRNA"... (then shown grey, not translated); read with these spellings, so
# everything (colours, translations, exports, tools) knows them, and saved so
FEATURE_KEYS = {k.lower(): k for k in (
    "CDS", "gene", "mRNA", "tRNA", "rRNA", "ncRNA", "tmRNA", "misc_RNA", "exon", "intron", "5'UTR", "3'UTR",
    "misc_feature", "repeat_region", "rep_origin", "regulatory", "promoter", "terminator", "primer_bind",
    "protein_bind", "misc_binding", "stem_loop", "D-loop", "source", "variation", "mobile_element", "gap",
    "polyA_signal", "sig_peptide", "mat_peptide", "transit_peptide", "operon", "precursor_RNA", "prim_transcript")}


def read_records(source, fmt):
    """All records from a path (in any encoding, see decode) or a text stream.
    Records without a molecule type get DNA, or protein if they look like one;
    feature keys get their usual spelling (FEATURE_KEYS)."""
    if isinstance(source, (str, Path)):
        source = StringIO(read_text(source))
    records = list(SeqIO.parse(source, fmt))
    for record in records:
        record.annotations.setdefault("molecule_type", "protein" if looks_protein(record.seq) else "DNA")
        for f in record.features:
            f.type = FEATURE_KEYS.get(f.type.lower(), f.type)
    return records


def read_record(path):
    """Reads the first record of a GenBank or FASTA file."""
    records = read_records(path, file_format(path))
    if not records:
        raise ValueError(f"No sequences found in {Path(path).name}")
    return records[0]


def feature_name(qualifiers, fallback):
    for key in NAME_KEYS:
        if qualifiers.get(key):
            return qualifiers[key][0]
    return fallback


def record_to_editor(record):
    """SeqRecord -> editor sequenceData dict."""
    circular = record.annotations.get("topology") == "circular"
    features = []
    for i, f in enumerate(record.features):
        if f.type == "source":
            continue  # restored from the original file on save
        notes = {k: [str(v) for v in vals] for k, vals in f.qualifiers.items()}
        # Minus-strand compound locations are stored in biological order
        parts = sorted(f.location.parts, key=lambda p: p.start)
        feat = {
            "id": f"f{i}",
            "name": feature_name(notes, f.type),
            "type": f.type,
            "strand": -1 if f.location.strand == -1 else 1,
            "notes": notes,
        }
        if len(parts) > 1:
            locs = [{"start": int(p.start), "end": int(p.end) - 1} for p in parts]
            if circular and _wraps_origin(parts, len(record)):
                # join(x..end, 1..y): the editor expects the part after the origin last
                locs = locs[1:] + locs[:1]
            feat["locations"] = locs
            feat["start"], feat["end"] = locs[0]["start"], locs[-1]["end"]
        else:
            feat["start"], feat["end"] = int(f.location.start), int(f.location.end) - 1
        # a partial end, <1..>500: left = the lowest coordinate is "<", right = the highest is ">"
        left, right = isinstance(parts[0].start, BeforePosition), isinstance(parts[-1].end, AfterPosition)
        if left or right:
            feat["partial"] = {"left": left, "right": right}
        features.append(feat)

    return {
        "name": record.name,
        "description": own_description(record.description, record.name, record.id),
        "circular": circular,
        "sequence": str(record.seq),
        "features": features,
        "organism": "" if record.annotations.get("organism", ".") in (".", "unknown") else record.annotations["organism"],
        "geneticCode": genetic_code(record),  # Cladia's own field; the editor ignores it
        "protein": is_protein(record),
    }


def own_description(text, *names):
    """A description without what isn't one: Biopython's FASTA description starts
    with the id ("seq1" when there is nothing else), GenBank writes "." for none."""
    text = (text or "").strip()
    words = text.split(None, 1)
    if words and words[0].rstrip(".") in names:
        text = words[1] if len(words) > 1 else ""
    text = text.rstrip(".").strip()
    return "" if text == "<unknown description>" else text


def genetic_code(record):
    """NCBI genetic code for translations: a CDS /transl_table, else from /organelle.

    Mitochondria get 4 and plastids 11; anything else gets 1.
    """
    for f in record.features:
        table = f.qualifiers.get("transl_table", [""])[0] if f.type == "CDS" else ""
        if str(table).strip().isdigit():
            return int(table)
    source = next((f for f in record.features if f.type == "source"), None)
    organelle = source.qualifiers.get("organelle", [""])[0].lower() if source else ""
    if "mitochondri" in organelle:
        return 4
    if "plastid" in organelle or "chloroplast" in organelle:
        return 11
    return 1


def cds_protein(feature, seq, code):
    """A CDS's protein, as NCBI writes /translation: no final stop, M for an
    alternative start codon. Uses the CDS's /transl_table and /codon_start;
    `code` is the genetic code when there's no /transl_table.
    A CDS that fails the CDS checks (partial, internal stop, being edited) is
    translated codon by codon instead."""
    try:
        protein = feature.translate(seq, table=code, cds=True)
    except TranslationError:
        protein = feature.translate(seq, table=code, cds=False)
    return str(protein).removesuffix("*")


def _wraps_origin(sorted_parts, length):
    return sorted_parts[0].start == 0 and sorted_parts[-1].end == length and len(sorted_parts) == 2


# ── Writing ──────────────────────────────────────────────────────────────

def editor_to_record(data, original=None):
    """Editor sequenceData dict -> SeqRecord, keeping the original's metadata."""
    seq = Seq(data["sequence"])
    length = len(seq)

    if original is not None:
        record = original[:0]  # empty copy that keeps id, name, annotations...
        record.annotations = dict(original.annotations)
        record.dbxrefs = list(original.dbxrefs)
        source = next((f for f in original.features if f.type == "source"), None)
    else:
        from Bio.SeqRecord import SeqRecord
        record = SeqRecord(seq, id=data.get("name") or "unnamed")
        source = None

    record.seq = seq
    record.name = (data.get("name") or record.name or "unnamed").replace(" ", "_")
    record.annotations["molecule_type"] = record.annotations.get(
        "molecule_type", "protein" if data.get("protein") or looks_protein(seq) else "DNA")
    record.annotations["topology"] = "circular" if data.get("circular") else "linear"

    features = []
    if source is not None:
        features.append(SeqFeature(SimpleLocation(0, length, strand=1), type="source",
                                   qualifiers=source.qualifiers))

    # The editor sends features either as a list or as a dict keyed by id
    editor_features = data.get("features") or []
    if isinstance(editor_features, dict):
        editor_features = list(editor_features.values())
    # Sort by position; features at the same position keep their original file order
    editor_features = sorted(editor_features, key=lambda f: (_first_start(f), _original_index(f)))

    # A CDS whose DNA changed gets its /translation recomputed; untouched CDSs keep
    # theirs exactly (it may use a code or exception Cladia doesn't know about).
    # The editor sends the genetic code it shows, else it comes from the file.
    old = {f"f{i}": f for i, f in enumerate(original.features)} if original is not None else {}
    code = int(data.get("geneticCode") or (genetic_code(original) if original is not None else 1))
    for feat in editor_features:
        f = _editor_feature_to_seqfeature(feat, length)
        before = old.get(str(feat.get("id")))
        table = lambda feature: feature.qualifiers.get("transl_table")  # a new genetic code: translated again
        changed = (before is None or str(f.extract(seq)) != str(before.extract(original.seq))
                   or table(f) != table(before))
        if f.type == "CDS" and "translation" in f.qualifiers and changed:
            f.qualifiers["translation"] = [cds_protein(f, seq, code)]
        features.append(f)
    record.features = features
    return record


def _original_index(feat):
    """Features read from a file get ids f0, f1...; new ones go after them."""
    fid = str(feat.get("id", ""))
    return int(fid[1:]) if fid[:1] == "f" and fid[1:].isdigit() else 10**9


def _first_start(feat):
    return min(loc["start"] for loc in feat["locations"]) if feat.get("locations") else feat["start"]


def _editor_feature_to_seqfeature(feat, length):
    strand = -1 if feat.get("strand") == -1 else 1
    ranges = feat.get("locations") or [{"start": feat["start"], "end": feat["end"]}]

    parts = []
    for r in ranges:
        start, end = int(r["start"]), int(r["end"])
        if start <= end:
            parts.append(SimpleLocation(start, end + 1, strand=strand))
        else:  # crosses the origin of a circular sequence
            parts.append(SimpleLocation(start, length, strand=strand))
            parts.append(SimpleLocation(0, end + 1, strand=strand))

    partial = feat.get("partial") or {}
    if partial.get("left") or partial.get("right"):  # <1..>500: the lowest start / highest end
        first = min(range(len(parts)), key=lambda k: int(parts[k].start))
        last = max(range(len(parts)), key=lambda k: int(parts[k].end))
        if partial.get("left"):
            p = parts[first]
            parts[first] = SimpleLocation(BeforePosition(int(p.start)), p.end, strand=strand)
        if partial.get("right"):
            p = parts[last]
            parts[last] = SimpleLocation(p.start, AfterPosition(int(p.end)), strand=strand)
    if strand == -1:
        parts = parts[::-1]  # biological order for complement(join(...))
    location = parts[0] if len(parts) == 1 else CompoundLocation(parts)

    qualifiers = {k: (v if isinstance(v, list) else [v]) for k, v in (feat.get("notes") or {}).items()}
    _sync_name(qualifiers, feat.get("name"))
    return SeqFeature(location, type=feat.get("type") or "misc_feature", qualifiers=qualifiers)


def _sync_name(qualifiers, name):
    """Writes a renamed feature back into the qualifier its name came from."""
    if not name:
        return
    key = next((k for k in NAME_KEYS if qualifiers.get(k)), "label")
    if not qualifiers.get(key) or qualifiers[key][0] != name:
        qualifiers[key] = [name] + qualifiers.get(key, [])[1:]


def end_overlap(seq, min_overlap=30, max_overlap=1000):
    """The longest stretch (min_overlap..max_overlap, under half the sequence) that
    ends the sequence exactly as it starts it: an assembled circle's duplicated end.
    0 when there is none."""
    seq = str(seq).upper()
    for size in range(min(max_overlap, len(seq) // 2), min_overlap - 1, -1):
        if seq[-size:] == seq[:size]:
            return size
    return 0


def circularized(record, size):
    """A copy of the record without its last `size` bases (the copy of its start),
    marked circular, with a note. Features lying across the cut are dropped
    (returned count); the source follows the new length."""
    from Bio.SeqRecord import SeqRecord
    n = len(record) - size
    new = SeqRecord(record.seq[:n], id=record.id, name=record.name, description=record.description)
    new.annotations = dict(record.annotations)
    new.dbxrefs = list(record.dbxrefs)
    new.annotations["topology"] = "circular"
    note = f"Circularized: the last {size} bp repeated the start and were trimmed."
    new.annotations["comment"] = (str(new.annotations["comment"]) + "\n" if new.annotations.get("comment") else "") + note
    dropped = 0
    for f in record.features:
        if f.type == "source":
            new.features.append(SeqFeature(SimpleLocation(0, n, strand=1), type="source", qualifiers=f.qualifiers))
        elif int(f.location.end) <= n:
            new.features.append(f)
        elif int(f.location.start) < n:
            dropped += 1  # across the cut
    return new, dropped


def rotated_feature(feature, shift, n):
    """A feature with its positions moved by `shift` on a circle of n bases (a part
    that comes to cross the origin is cut in two)."""
    if not shift:
        return feature
    parts = []
    for p in feature.location.parts:
        start, end = (int(p.start) + shift) % n, (int(p.start) + shift) % n + len(p)
        pieces = [(start, end)] if end <= n else [(start, n), (0, end - n)]
        if p.strand == -1:
            pieces.reverse()  # biological order on the minus strand
        parts += [SimpleLocation(a, b, strand=p.strand) for a, b in pieces]
    location = parts[0] if len(parts) == 1 else CompoundLocation(parts)
    return SeqFeature(location, type=feature.type, qualifiers=dict(feature.qualifiers))


def turned(record, start=0, flip=False):
    """The record turned to begin at `start` (0-based; a circular sequence), then
    reverse complemented when `flip`; its features follow (a part that comes to
    cross the new origin is cut in two)."""
    n = len(record)
    if start % n:
        start %= n
        new = SeqRecord(record.seq[start:] + record.seq[:start], id=record.id, name=record.name,
                        description=record.description, annotations=dict(record.annotations))
        new.features = [f if f.type == "source" else rotated_feature(f, n - start, n) for f in record.features]
        record = new
    if flip:
        record = record.reverse_complement(id=True, name=True, description=True, features=True, annotations=True,
                                           letter_annotations=False)
    return record


def write_record(record, path):
    """Writes one record or a list of records.

    Atomic: a crash mid-save never leaves a half-written file.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(records_text(record, file_format(path)), encoding="utf-8")
    replace_file(tmp, path)


def when_free(action):
    """Runs a file action, retrying for ~2 s while Windows says the file is in use
    (another request still reading it, e.g. a list being opened while deleted)."""
    for attempt in range(40):
        try:
            return action()
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def replace_file(new, target):
    when_free(lambda: Path(new).replace(target))


def records_text(records, fmt):
    """Records (or one) as GenBank or FASTA text."""
    buffer = StringIO()
    SeqIO.write(records, buffer, fmt)
    return buffer.getvalue()


def write_fasta(records, path):
    """FASTA, as UTF-8 (job inputs, alignments)."""
    Path(path).write_text(records_text(records, "fasta"), encoding="utf-8")
