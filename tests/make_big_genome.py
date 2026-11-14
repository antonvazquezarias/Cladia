"""Makes a synthetic circular genome for performance testing.

    python tests/make_big_genome.py OUT.gb [length_kb] [n_genes]

Each gene gets gene + CDS (+ an intron in every 10th), plus scattered tRNAs,
so the default 500 kb / 600 genes gives ~1900 features.
"""

import random
import sys

from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord

out = sys.argv[1]
length = int(sys.argv[2]) * 1000 if len(sys.argv) > 2 else 500_000
n_genes = int(sys.argv[3]) if len(sys.argv) > 3 else 600

random.seed(1)
seq = "".join(random.choice("ACGT") for _ in range(length))
record = SeqRecord(Seq(seq), id="BIG_TEST", name="BIG_TEST", description="synthetic test genome",
                   annotations={"molecule_type": "DNA", "topology": "circular"})

step = length // n_genes
for i in range(n_genes):
    start = i * step + random.randint(0, step // 4)
    end = min(start + random.randint(300, step - 50), length)
    strand = random.choice([1, -1])
    name = f"gene{i:04d}"
    record.features.append(SeqFeature(SimpleLocation(start, end, strand), type="gene",
                                      qualifiers={"gene": [name]}))
    if i % 10 == 0 and end - start > 600:  # gene with an intron
        mid = (start + end) // 2
        parts = [SimpleLocation(start, mid - 100, strand), SimpleLocation(mid + 100, end, strand)]
        loc = CompoundLocation(parts if strand == 1 else parts[::-1])
    else:
        loc = SimpleLocation(start, end, strand)
    record.features.append(SeqFeature(loc, type="CDS", qualifiers={"gene": [name], "product": [f"protein {i}"]}))
    if i % 2 == 0:
        t = end + 10
        if t + 75 < length:
            record.features.append(SeqFeature(SimpleLocation(t, t + 75, strand), type="tRNA",
                                              qualifiers={"product": [f"tRNA-{i}"]}))

from Bio import SeqIO  # noqa: E402
SeqIO.write(record, out, "genbank")
print(f"{out}: {length} bp, {len(record.features)} features")
