"""ORF finder: open reading frames from a start codon to the next stop, in the
sequence's genetic code, on one or both strands. Circular sequences may have
ORFs that cross the origin.

Positions are 0-based and inclusive, as in the editor; the stop codon is part
of the ORF (as in a CDS). An ORF crossing the origin has start > end.
"""

from Bio.Data import CodonTable
from Bio.Seq import Seq


def find_orfs(sequence, code=1, min_codons=100, both_strands=True, any_start=False, circular=False):
    """ORFs as [{start, end, strand, codons}], sorted by position.

    min_codons counts the codons before the stop. any_start: every start codon
    of the genetic code (e.g. GTG, TTG), else only ATG.
    """
    table = CodonTable.unambiguous_dna_by_id[code]
    starts = set(table.start_codons) if any_start else {"ATG"}
    stops = set(table.stop_codons)
    seq = sequence.upper().replace("U", "T")
    n = len(seq)
    found = []
    for strand in ((1, -1) if both_strands else (1,)):
        s = seq if strand == 1 else str(Seq(seq).reverse_complement())
        scan = s + s if circular else s  # circular: read on past the origin
        for frame in range(3):
            open_at = None
            for i in range(frame, len(scan) - 2, 3):
                if open_at is not None and i - open_at >= n:
                    open_at = None  # once round the whole circle without a stop
                codon = scan[i:i + 3]
                if open_at is None and codon in starts and i < n:
                    open_at = i
                elif open_at is not None and codon in stops:
                    codons = (i - open_at) // 3
                    if codons >= min_codons:
                        found.append(_on_sequence(open_at, i + 2, strand, n, codons))
                    open_at = None
                if open_at is None and i >= n:
                    break  # nothing open past the origin: the rest is a repeat
    return sorted(found, key=lambda o: (min(o["start"], o["end"]), o["strand"]))


def _on_sequence(start, end, strand, n, codons):
    """Positions on the strand read (maybe past the origin) -> on the sequence."""
    if strand == -1:  # reverse complement positions back to the top strand
        start, end = n - 1 - end, n - 1 - start
    return {"start": start % n, "end": end % n, "strand": strand, "codons": codons}


def overlaps(orf, cds, n):
    """Whether an ORF and a CDS (same strand) share any position."""
    if orf["strand"] != cds["strand"]:
        return False
    def positions(start, end):
        return [(start, end)] if start <= end else [(start, n - 1), (0, end)]
    return any(a <= d and c <= b for a, b in positions(orf["start"], orf["end"])
               for c, d in positions(cds["start"], cds["end"]))
