"""GFF3: export (the features of GenBank records, optionally with the sequences
in a ##FASTA section at the end, as Prokka and most annotation tools write it)
and import (annotations added to sequences, see `read_gff3`)."""

import re
from urllib.parse import quote, unquote

from .genbank import feature_name

SKIP = {"translation"}  # long and derivable; GFF3 readers don't need it


def _escape(text):
    """GFF3 escapes these characters in column 9 (and tabs/newlines anywhere)."""
    return quote(str(text), safe=" !\"#$'()*+-./:<>?@[\\]^_`{|}~")


def _seqid(record):
    """The record's name (LOCUS, the name Cladia shows and renames); its id can be a
    program's internal accession (Geneious writes urn.local...)."""
    name = record.name if record.name not in ("", "<unknown name>") else record.id
    return re.sub(r"\s+", "_", name)


def records_to_gff3(records, with_fasta=False):
    lines = ["##gff-version 3"]
    for record in records:
        lines.append(f"##sequence-region {_seqid(record)} 1 {len(record)}")
    for record in records:
        lines += _feature_lines(record)
    if with_fasta:
        lines.append("##FASTA")
        for record in records:
            seq = str(record.seq)
            lines.append(f">{_seqid(record)}")
            lines += [seq[i:i + 70] for i in range(0, len(seq), 70)]
    return "\n".join(lines) + "\n"


def _feature_lines(record):
    seqid = _seqid(record)
    circular = record.annotations.get("topology") == "circular"
    lines, used = [], {}
    for f in record.features:
        name = feature_name(f.qualifiers, f.type)
        attributes = []
        if f.type == "source":
            ftype = "region"
            if circular:
                attributes.append("Is_circular=true")
        else:
            ftype = f.type
            base = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
            used[base] = used.get(base, 0) + 1
            fid = base if used[base] == 1 else f"{base}_{used[base]}"
            attributes += [f"ID={_escape(fid)}", f"Name={_escape(name)}"]
        for key, values in f.qualifiers.items():
            if key in SKIP or key == "codon_start":
                continue
            attributes.append(f"{_escape(key)}={','.join(_escape(v) for v in values)}")
        strand = {1: "+", -1: "-"}.get(f.location.strand, ".")
        # The parts in biological order: a CDS's phase follows the bases before each part
        codon_start = int(f.qualifiers.get("codon_start", ["1"])[0])
        before = 0
        for part in f.location.parts:
            phase = (codon_start - 1 - before) % 3 if f.type == "CDS" else "."
            before += len(part)
            lines.append("\t".join([seqid, "Cladia", ftype, str(int(part.start) + 1), str(int(part.end)),
                                    ".", strand, str(phase), ";".join(attributes)]))
    return lines


# ── Reading ──────────────────────────────────────────────────────────────

# GFF3 (Sequence Ontology) types that GenBank names differently
TYPES = {"five_prime_UTR": "5'UTR", "three_prime_UTR": "3'UTR", "pseudogene": "gene"}
# Types whose Name is a gene name (GenBank /gene); others get it as /label
GENE_TYPES = {"gene", "pseudogene", "CDS", "mRNA", "tRNA", "rRNA", "exon", "intron", "ncRNA", "tmRNA",
              "misc_RNA", "five_prime_UTR", "three_prime_UTR"}
# GFF3 attributes -> GenBank qualifiers (others keep their own name; these are dropped)
QUALIFIERS = {"Note": "note", "Dbxref": "db_xref", "Alias": "synonym"}
DROPPED = {"ID", "Parent", "Name", "Is_circular", "Target", "Gap", "Derives_from"}
SEQUENCE_TYPES = {"region", "source", "chromosome", "contig", "supercontig", "scaffold"}  # the sequence itself


# ── GTF (Ensembl, GENCODE, StringTie...): read by converting it to GFF3 ──

GTF_TYPES = {"transcript": "mRNA", "five_prime_utr": "five_prime_UTR", "three_prime_utr": "three_prime_UTR",
             "5utr": "five_prime_UTR", "3utr": "three_prime_UTR"}
GTF_SKIPPED = {"start_codon", "stop_codon", "selenocysteine", "intron", "inter", "inter_cns", "intron_cns"}
GTF_KEPT = {"product", "note", "gene_biotype", "transcript_biotype"}


def is_gtf(text):
    """The first feature line has attributes as `key "value";` (GFF3 has key=value)."""
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            attrs = line.split("\t")[-1]
            return "=" not in attrs.split(";")[0] and re.search(r'\w+ "?[^"]*"?\s*;?', attrs) is not None \
                and len(line.split("\t")) >= 9
    return False


def gtf_to_gff3(text):
    """GTF text as GFF3 text: gene, transcript (-> mRNA), exon and CDS lines of a
    transcript share an ID so they read as one feature in parts, named after
    gene_name (else gene_id); start/stop codon lines are left out."""
    out = ["##gff-version 3"]
    names = dict(re.findall(r'gene_id\s+"([^"]*)"[^\n]*?gene_name\s+"([^"]*)"', text))  # a gene's name, for its other lines
    for k, line in enumerate(text.splitlines()):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.rstrip("\r").split("\t")
        if len(cols) < 9:
            raise ValueError(f"line {k + 1}: a GTF line has 9 columns separated by tabs")
        ftype = cols[2]
        if ftype.lower() in GTF_SKIPPED:
            continue
        attrs = {}
        for key, quoted, bare in re.findall(r'(\w+)\s+(?:"([^"]*)"|([^;\s]+))\s*;?', cols[8]):
            attrs.setdefault(key, []).append(quoted or bare)
        first = lambda key: attrs.get(key, [""])[0]
        gene, transcript = first("gene_id"), first("transcript_id")
        name = first("gene_name") or names.get(gene) or gene or transcript
        kind = GTF_TYPES.get(ftype.lower(), ftype)
        if kind == "gene":
            ident, parent = f"gene-{gene}", ""
        elif kind == "mRNA":
            ident, parent = f"mrna-{transcript}", f"gene-{gene}" if gene else ""
        else:  # parts of a transcript: one feature per type and transcript
            owner = transcript or gene
            ident, parent = f"{kind}-{owner}", f"mrna-{transcript}" if transcript else ""
        pairs = [("ID", ident), ("Parent", parent), ("Name", name)] + [(k, v) for k in sorted(GTF_KEPT) for v in attrs.get(k, [])]
        text_attrs = ";".join(f"{k}={quote(v, safe=' /:+()')}" for k, v in pairs if v)
        out.append("\t".join(cols[:2] + [kind] + cols[3:8] + [text_attrs]))
    return "\n".join(out) + "\n"


def bed_to_gff3(text):
    """BED text as GFF3 text: a line is a feature "misc_feature" named by its 4th column
    (start 0-based, end exclusive, as BED; GFF3 1-based inclusive); the blocks of a
    BED12 line (exons) are its parts, lines sharing an ID."""
    out = ["##gff-version 3"]
    for k, line in enumerate(text.splitlines()):
        if not line.strip() or line.startswith(("#", "track", "browser")):
            continue
        cols = line.rstrip("\r").split("\t")
        if len(cols) < 3 or not cols[1].isdigit() or not cols[2].isdigit():
            raise ValueError(f"line {k + 1}: a BED line starts with chromosome, start and end, separated by tabs")
        start, end = int(cols[1]), int(cols[2])
        name = cols[3] if len(cols) > 3 and cols[3] not in (".", "") else ""
        strand = cols[5] if len(cols) > 5 and cols[5] in "+-" else "."
        attrs = f"ID=bed{k}" + (f";Name={name.replace(";", "%3B")}" if name else "")
        blocks = [(start, end)]
        if len(cols) >= 12 and cols[9].isdigit():
            try:  # BED12: sizes and starts of the blocks, relative to the start
                sizes, starts = [int(x) for x in cols[10].strip(",").split(",")], [int(x) for x in cols[11].strip(",").split(",")]
                blocks = [(start + b, start + b + s) for b, s in zip(starts, sizes)]
            except ValueError:
                pass
        for b0, b1 in blocks:
            out.append("\t".join([cols[0], "bed", "misc_feature", str(b0 + 1), str(max(b1, b0 + 1)), ".", strand, ".", attrs]))
    return "\n".join(out) + "\n"


def is_bed(text):
    """The first data line is chromosome, start, end (two integers after a name)."""
    for line in text.splitlines():
        if line.strip() and not line.startswith(("#", "track", "browser")):
            cols = line.split("\t")
            return len(cols) >= 3 and cols[1].isdigit() and cols[2].isdigit()
    return False


def read_gff3(text):
    """GFF3 text (or GTF, converted first) -> ({seqid: [feature]}, FASTA text after ##FASTA or "").

    A feature: {type, strand, parts: [(start, end)] 0-based inclusive, ascending,
    qualifiers, codon_start}. Lines sharing an ID are one feature in parts (a CDS
    over exons); a feature without a name takes its Parent's (a CDS -> its gene)."""
    by_seq, by_id, fasta = {}, {}, ""
    if is_bed(text):
        text = bed_to_gff3(text)
    elif is_gtf(text):
        text = gtf_to_gff3(text)
    lines = text.splitlines()
    for k, line in enumerate(lines):
        if line.startswith("##FASTA"):
            fasta = "\n".join(lines[k + 1:])
            break
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.rstrip("\r").split("\t")
        if len(cols) < 9:
            raise ValueError(f"line {k + 1}: a GFF3 line has 9 columns separated by tabs")
        seqid, _, ftype, start, end, _, strand, phase, attrs = cols[:9]
        seqid = unquote(seqid)
        if ftype in SEQUENCE_TYPES:
            continue
        attributes = {}
        for pair in filter(None, (a.strip() for a in attrs.split(";"))):
            key, _, value = pair.partition("=")
            attributes[unquote(key)] = [unquote(v) for v in value.split(",")]
        try:
            part = (int(start) - 1, int(end) - 1)
        except ValueError:
            raise ValueError(f"line {k + 1}: start and end must be numbers") from None
        fid = attributes.get("ID", [None])[0]
        if fid and (seqid, fid) in by_id:  # another part of the same feature
            f = by_id[(seqid, fid)]
            f["parts"].append(part)
            f["phases"].append(phase)
            continue
        f = {"type": ftype, "strand": -1 if strand == "-" else 1, "parts": [part], "phases": [phase],
             "attributes": attributes}
        if fid:
            by_id[(seqid, fid)] = f
        by_seq.setdefault(seqid, []).append(f)

    def own_name(f, keys):
        return next((f["attributes"][k][0] for k in keys if f["attributes"].get(k)), None)

    def name_of(f, depth=0):  # its Name or gene, else its parent's (a CDS -> its gene), else locus_tag or product
        name = own_name(f, ("Name", "gene"))
        for parent in f["attributes"].get("Parent", []) if not name and depth < 5 else []:
            p = by_id.get((f["seqid"], parent))
            name = name or (p and name_of(p, depth + 1))
        return name or own_name(f, ("locus_tag", "product"))

    for seqid, feats in by_seq.items():
        for f in feats:
            f["seqid"] = seqid
    result = {}
    for seqid, feats in by_seq.items():
        for f in feats:
            name = name_of(f)
            qualifiers = {}
            if name:
                qualifiers["gene" if f["type"] in GENE_TYPES else "label"] = [name]
            for key, values in f["attributes"].items():
                if key in DROPPED or (key == "gene" and "gene" in qualifiers):
                    continue
                qualifiers.setdefault(QUALIFIERS.get(key, key), []).extend(values)
            # A CDS's phase: bases to skip at its 5' end (its first part in reading order)
            order = sorted(zip(f["parts"], f["phases"]), key=lambda pp: pp[0][0], reverse=f["strand"] == -1)
            codon_start = int(order[0][1]) + 1 if f["type"] == "CDS" and order[0][1] in "012" else 1
            if codon_start > 1:
                qualifiers["codon_start"] = [str(codon_start)]
            result.setdefault(seqid, []).append({
                "type": TYPES.get(f["type"], f["type"]), "strand": f["strand"],
                "parts": sorted(f["parts"]), "qualifiers": qualifiers, "name": name or f["type"]})
    return result, fasta


def editor_features(features, length, circular):
    """Features from read_gff3 as editor JSON for a sequence of this length. A part
    past the end of a circular sequence crosses its origin (GFF3 allows end >
    length there). Raises ValueError for parts outside the sequence."""
    out = []
    for f in features:
        parts = []
        for s, e in f["parts"]:
            if s < 0 or e < s or s >= length or (e >= length and not circular) or e >= 2 * length:
                raise ValueError(f"{f['type']} {f['name']} at {s + 1}..{e + 1} is outside the sequence ({length} bp)")
            parts.append({"start": s, "end": e - length if e >= length else e})
        feature = {"name": f["name"], "type": f["type"], "strand": f["strand"], "notes": f["qualifiers"],
                   "start": parts[0]["start"], "end": parts[-1]["end"]}
        if len(parts) > 1:
            feature["locations"] = parts
        out.append(feature)
    return out
