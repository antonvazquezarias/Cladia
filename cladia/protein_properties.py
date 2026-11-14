"""Properties of a protein sequence: the side panel's Properties tab (protein-properties.js).
Biopython's ProteinAnalysis on the 20 standard amino acids; other letters (X, B, Z, U...)
are counted but left out, gaps and stops are ignored."""

from Bio.SeqUtils import ProtParamData
from Bio.SeqUtils.ProtParam import ProteinAnalysis

STANDARD = "ACDEFGHIKLMNPQRSTVWY"
WINDOWS = (5, 7, 9, 11, 15, 21)  # Kyte-Doolittle windows offered


def properties(sequence, window=9):
    """{length, analysed, excluded, weight (Da), pI, charge (at pH 7), gravy, aromaticity,
    instability, extinction (280 nm, reduced and cystines), composition {aa: %},
    structure {helix, turn, sheet}, hydrophobicity {window, positions, values}}.
    Raises ValueError for fewer than two standard amino acids."""
    letters = [c for c in sequence.upper() if c.isalpha()]
    where = [i + 1 for i, c in enumerate(letters) if c in STANDARD]  # each kept residue's place in the sequence
    kept = "".join(c for c in letters if c in STANDARD)
    if len(kept) < 2:
        raise ValueError("A protein of at least two standard amino acids is needed")
    excluded = {}
    for c in letters:
        if c not in STANDARD:
            excluded[c] = excluded.get(c, 0) + 1
    analysis = ProteinAnalysis(kept)
    helix, turn, sheet = analysis.secondary_structure_fraction()
    reduced, oxidised = analysis.molar_extinction_coefficient()
    window = min(window, len(kept) if len(kept) % 2 else len(kept) - 1)  # odd, and not longer than the protein
    values = analysis.protein_scale(ProtParamData.kd, window, 1.0) if window >= 3 else []  # (the plot needs 3 residues)
    return {
        "length": len(letters), "analysed": len(kept), "excluded": excluded,
        "weight": analysis.molecular_weight(), "pI": analysis.isoelectric_point(),
        "charge": analysis.charge_at_pH(7.0), "gravy": analysis.gravy(), "aromaticity": analysis.aromaticity(),
        "instability": analysis.instability_index(), "extinction": {"reduced": reduced, "cystines": oxidised},
        "composition": {aa: round(share, 2) for aa, share in analysis.amino_acids_percent.items()},  # (already percent)
        "structure": {"helix": helix, "turn": turn, "sheet": sheet},
        "hydrophobicity": {"window": window, "positions": [where[i + window // 2] for i in range(len(values))],
                           "values": [round(v, 3) for v in values]},
    }
