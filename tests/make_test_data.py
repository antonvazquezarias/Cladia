"""Makes the synthetic files in tests/data (deterministic: run again, same files).

    python tests/make_test_data.py

- synthetic_mt.gb: a made-up 15,789 bp mitochondrion-like genome, "Mt": random
  AT-rich sequence with 8 genes whose CDSs are real open reading frames (ATG ...
  TAA, no stop codon of code 1 or 4 inside), their mRNAs and UTRs, 3 tRNAs and 14
  rRNA pieces, with the leftover GFF3 qualifiers (ID, Dbxref) a Geneious export has.
- read_qual.ab1, read_noqual.ab1: Sanger traces (ABIF files) of two stretches of
  it: an "ABI 3100" read with qualities, low at both ends, and an "ABI 310" one
  without (see trace).
graph_small.gfa and graph_spades.gfa (assembly graphs with random sequences) were
made once from the shapes of two real graphs and are not written by this script.
"""
import random
import struct
from pathlib import Path

from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from Bio import SeqIO

DATA = Path(__file__).parent / "data"
LENGTH = 15789

# (type, start, end (0-based, end exclusive), strand, name, product)
GENES = [("cob", 516, 544, 1621, 1677, -1, "cytochrome b"),
         ("nad4", 1691, 1712, 2936, 3018, -1, "NADH dehydrogenase subunit 4"),
         ("nad5", 3044, 3319, 4852, 4946, -1, "NADH dehydrogenase subunit 5"),
         ("cox1", 5041, 5134, 6559, 6691, 1, "cytochrome oxidase subunit 1"),
         ("nad2", 6705, 6826, 7855, 7952, 1, "NADH dehydrogenase subunit 2"),
         ("nad6", 7967, 8078, 8441, 8459, 1, "NADH dehydrogenase subunit 6"),
         ("nad1", 10368, 10406, 11237, 11251, 1, "NADH dehydrogenase subunit 1"),
         ("rtl", 11596, 11649, 12699, 12896, 1, "reverse transcriptase-like protein")]
TRNAS = [(8460, 8534, "trnW", "tRNA-Trp", "UGG"), (9240, 9312, "trnQ", "tRNA-Gln", "CAA"),
         (13865, 13937, "trnM", "tRNA-Met", "AUG")]
RRNAS = [(8728, 8886, "L6"), (8886, 9001, "S1"), (9001, 9141, "L5"), (9442, 10121, "L7"), (10121, 10339, "S2"),
         (11270, 11384, "L3a"), (11384, 11576, "L3b"), (12929, 13332, "L8"), (13332, 13742, "S3"),
         (13952, 14079, "L4"), (14079, 14536, "S4"), (14536, 14743, "L1"), (14743, 14832, "L2a"),
         (14832, 15052, "L2b")]
STOPS = {"TAA", "TAG", "TGA"}


def _bases(rng, n):
    return "".join(rng.choices("ACGT", weights=(32, 18, 18, 32), k=n))


def _orf(rng, n):
    """ATG, sense codons, TAA: n bases (a multiple of 3)."""
    codons = []
    while len(codons) < n // 3 - 2:
        codon = _bases(rng, 3)
        if codon not in STOPS:
            codons.append(codon)
    return "ATG" + "".join(codons) + "TAA"


def genome():
    rng = random.Random(15789)
    seq = list(_bases(rng, LENGTH))
    features = [SeqFeature(FeatureLocation(0, LENGTH, 1), type="source",
                           qualifiers={"mol_type": ["genomic DNA"]})]
    for i, (name, start, cds_start, cds_end, end, strand, product) in enumerate(GENES, 1):
        orf = _orf(rng, cds_end - cds_start)
        seq[cds_start:cds_end] = orf if strand > 0 else str(Seq(orf).reverse_complement())
        tag, gid = f"SynMt{i:02}", f"Syn.g{i:04}00"
        common = {"Dbxref": [f"GeneID:9000{i:02}"], "locus_tag": [tag], "label": [name]}
        utr5, utr3 = ((start, cds_start), (cds_end, end)) if strand > 0 else ((cds_end, end), (start, cds_start))
        features += [
            SeqFeature(FeatureLocation(start, end, strand), type="gene", qualifiers={"ID": [gid], **common}),
            SeqFeature(FeatureLocation(start, end, strand), type="mRNA", qualifiers={"ID": [gid + ".t1"], **common}),
            SeqFeature(FeatureLocation(*utr5, strand), type="5'UTR", qualifiers={"label": ["5'UTR"]}),
            SeqFeature(FeatureLocation(cds_start, cds_end, strand), type="CDS", qualifiers={
                "codon_start": ["1"], "ID": [gid + ".t1.CDS.1"], **common, "product": [product],
                "protein_id": [f"SYN_{i:06}.1"], "translation": [str(Seq(orf).translate(table=4, to_stop=True))]}),
            SeqFeature(FeatureLocation(*utr3, strand), type="3'UTR", qualifiers={"label": ["3'UTR"]})]
    for i, (start, end, name, product, codon) in enumerate(TRNAS, 1):
        features.append(SeqFeature(FeatureLocation(start, end, 1), type="tRNA", qualifiers={
            "ID": [f"Syn.t{i:04}.tRNA.1"], "codon_recognized": [codon], "product": [product], "label": [name]}))
    for i, (start, end, piece) in enumerate(RRNAS, 1):
        unit = "large" if piece.startswith("L") else "small"
        features.append(SeqFeature(FeatureLocation(start, end, 1), type="rRNA", qualifiers={
            "ID": [f"Syn.r{i:04}.rRNA"], "product": [f"{piece} ribosomal RNA"],
            "Note": [f"module encoding the {piece} portion of the fragmented {unit} subunit rRNA"],
            "label": [f"rrn{piece}"]}))
    features[1:] = sorted(features[1:], key=lambda f: (int(f.location.start), f.type != "gene"))
    record = SeqRecord(Seq("".join(seq)), id="Mt", name="Mt", description="", features=features,
                       annotations={"molecule_type": "DNA", "topology": "linear", "data_file_division": "UNA",
                                    "date": "14-NOV-2026", "organism": "."})
    return record


# ── Sanger traces ────────────────────────────────────────────────────────

def abif(tags):
    """An ABIF file from [(name, number, type code, element size, data bytes)]:
    the 128-byte header points to the directory of 28-byte entries; data of 4
    bytes or less sits in the entry itself."""
    body, entries = b"", []
    for name, number, code, size, data in tags:
        if len(data) <= 4:
            offset = struct.unpack(">I", data.ljust(4, bytes(1)))[0]
        else:
            offset = 128 + len(body)
            body += data
        entries.append(struct.pack(">4sI2H4I", name.encode(), number, code, size, len(data) // size, len(data), offset, 0))
    header = b"ABIF" + struct.pack(">H4sI2H3I", 101, b"tdir", 1, 1023, 28, len(entries), 28 * len(entries),
                                   128 + len(body))
    return header.ljust(128, bytes(1)) + body + b"".join(entries)


def trace(bases, quality, sample, machine, seed):
    """Peaks every 12 scans, each a Gaussian in its base's channel over a little noise."""
    rng = random.Random(seed)
    peaks = [20 + 12 * i + rng.randint(-1, 1) for i in range(len(bases))]
    scans = peaks[-1] + 30
    order = "GATC"
    channels = {b: [rng.randint(0, 40) for _ in range(scans)] for b in order}
    for base, peak in zip(bases, peaks):
        height = rng.randint(600, 1600)
        for x in range(peak - 8, peak + 9):
            channels[base][x] += int(height * 2.718 ** (-((x - peak) / 3) ** 2))
    shorts = lambda values: struct.pack(f">{len(values)}h", *values)
    tags = [("FWO_", 1, 2, 1, order.encode()), ("PBAS", 2, 2, 1, bases.encode()),
            ("PLOC", 2, 4, 2, shorts(peaks)), ("MODL", 1, 2, 1, machine.encode().ljust(4)),
            ("SMPL", 1, 18, 1, bytes([len(sample)]) + sample.encode()),
            ("RUND", 1, 10, 4, struct.pack(">h2B", 2026, 11, 14)), ("RUNT", 1, 11, 4, bytes([10, 30, 0, 0]))]
    tags += [("DATA", 9 + i, 4, 2, shorts(channels[b])) for i, b in enumerate(order)]
    if quality:
        tags.append(("PCON", 2, 2, 1, bytes(quality)))
    return abif(tags)


def traces(genome_seq):
    rng = random.Random(3100)
    bases = genome_seq[5200:5995]  # 795 bases of cox1
    quality = ([rng.randint(2, 9) for _ in range(18)] + [rng.randint(28, 60) for _ in range(679)]
               + [rng.randint(2, 9) for _ in range(98)])
    for i in (150, 420):  # a couple of weaker calls inside
        quality[i] = 14
    (DATA / "read_qual.ab1").write_bytes(trace(bases, quality, "SYN_S2_cox1F", "3100", 1))
    (DATA / "read_noqual.ab1").write_bytes(trace(genome_seq[9500:10368], [], "SYN_S7_rrnF", "310", 2))


if __name__ == "__main__":
    record = genome()
    SeqIO.write(record, DATA / "synthetic_mt.gb", "genbank")
    traces(str(record.seq))
    print("wrote", DATA / "synthetic_mt.gb", DATA / "read_qual.ab1", DATA / "read_noqual.ab1")
