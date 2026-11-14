"""Checks before a GenBank submission: problems in a sequence and its annotations,
found from the editor's JSON (so unsaved edits are checked as they are).

A problem is {feature, severity, code, message}: `feature` is the index into the
data's features (None for the sequence), severity "error" (NCBI will reject it)
or "warning" (worth a look). Nothing is changed here."""
import re

from Bio.Data import CodonTable
from Bio.Seq import Seq

IUPAC = set("ACGTURYKMSWBDHVN-")
RNA_TYPES = ("tRNA", "rRNA", "mRNA")
CLADIA_NOTE = re.compile(r"copied from|^added by [A-Za-z][\w-]*$|annotation extension|assembly graph node", re.I)
TRNA_PRODUCT = re.compile(r"^tRNA-\w+$")
# The qualifiers of the INSDC feature table (GenBank, ENA, DDBJ): others (a GFF3's ID, Parent,
# Dbxref, Cladia's own label...) aren't accepted in a submission and are left out of the export.
# label is Cladia's name for a feature and sequence_name its CDS names: also left out, but not a problem.
QUALIFIERS = set("""allele altitude anticodon artificial_location bio_material bound_moiety cell_line cell_type chromosome
citation clone clone_lib codon_start collected_by collection_date compare country cultivar culture_collection db_xref
dev_stage direction EC_number ecotype environmental_sample estimated_length exception experiment focus frequency
function gap_type gene gene_synonym germline haplogroup host identified_by inference isolate isolation_source lab_host
lat_lon linkage_evidence locus_tag macronuclear map mating_type metagenome_source mobile_element_type mod_base mol_type
ncRNA_class note number old_locus_tag operon organelle organism partial PCR_conditions PCR_primers phenotype plasmid
pop_variant product protein_id proviral pseudo pseudogene rearranged recombination_class regulatory_class replace
ribosomal_slippage rpt_family rpt_type rpt_unit_range rpt_unit_seq satellite segment serotype serovar sex standard_name
strain sub_clone sub_species sub_strain tag_peptide tissue_lib tissue_type transgenic translation transl_except
transl_table trans_splicing type_material variety virion""".split())
OWN_QUALIFIERS = {"label", "sequence_name"}  # Cladia's: never exported, not worth a warning


def _problem(index, severity, code, message):
    return {"feature": index, "severity": severity, "code": code, "message": message}


def _parts(feat):
    return feat.get("locations") or [{"start": feat["start"], "end": feat["end"]}]


def feature_bases(data, feat):
    """A feature's bases as transcribed (parts joined, reverse complemented on the - strand)."""
    seq = data["sequence"]
    joined = "".join(seq[p["start"]:p["end"] + 1] for p in _parts(feat))
    return str(Seq(joined).reverse_complement()) if feat.get("strand") == -1 else joined


def _first(notes, key):
    values = [str(v).strip() for v in notes.get(key, []) if str(v).strip()]
    return values[0] if values else ""


def _overlap(a, b):
    return a["start"] <= b["end"] and b["start"] <= a["end"]


def check(data):
    """All the problems of one sequence (editor JSON), in the order of the features."""
    problems = []
    seq = data.get("sequence", "").upper()
    features = data.get("features") or []
    if isinstance(features, dict):
        features = list(features.values())
    length = len(seq)
    code = int(data.get("geneticCode") or 1)

    # ── The sequence ──
    odd = sorted(set(seq) - IUPAC)
    if odd:
        problems.append(_problem(None, "error", "bases", f"Characters that are not bases: {', '.join(odd)}"))
    if not data.get("organism"):
        problems.append(_problem(None, "warning", "organism", "No organism (the submission needs /organism)"))
    if not length:
        problems.append(_problem(None, "error", "empty", "The sequence is empty"))

    genes = [f for f in features if f.get("type") == "gene"]
    seen = set()
    for i, f in enumerate(features):
        kind, name = f.get("type"), f.get("name") or f.get("type")
        notes = f.get("notes") or {}

        # ── Every feature ──
        if any(p["start"] < 0 or p["end"] >= length for p in _parts(f)):
            problems.append(_problem(i, "error", "outside", f"{name} reaches outside the sequence"))
            continue
        for key, values in notes.items():
            if key != "translation" and any(CLADIA_NOTE.search(str(v)) for v in values):
                problems.append(_problem(i, "warning", "cladia-note",
                                         "A note written by Cladia (where the feature came from): remove it before submitting"))
                break
        unknown = sorted(k for k in notes if k not in QUALIFIERS and k not in OWN_QUALIFIERS)
        if unknown:
            problems.append(_problem(i, "warning", "qualifier", f"{name}: {', '.join('/' + k for k in unknown)} "
                                     f"{'is' if len(unknown) == 1 else 'are'} not GenBank qualifier{'' if len(unknown) == 1 else 's'} "
                                     "(left out of the export)"))
        if (kind, f.get("strand"), tuple((p["start"], p["end"]) for p in _parts(f))) in seen:
            problems.append(_problem(i, "warning", "duplicate", f"{name}: another {kind} feature has exactly the same place"))
        seen.add((kind, f.get("strand"), tuple((p["start"], p["end"]) for p in _parts(f))))

        if kind == "CDS":
            problems += _check_cds(i, f, name, notes, data, code)
            if not any(_overlap(f, g) for g in genes):
                problems.append(_problem(i, "warning", "no-gene", f"{name}: no gene feature over it"))
        elif kind in RNA_TYPES:
            product = _first(notes, "product")
            if not product:
                problems.append(_problem(i, "error", "product", f"{name}: no /product"))
            elif kind == "tRNA" and not TRNA_PRODUCT.match(product):
                problems.append(_problem(i, "warning", "trna-name", f"{name}: a tRNA product is written tRNA-Xxx (e.g. tRNA-Met)"))
        elif kind == "gene":
            if not (_first(notes, "gene") or _first(notes, "locus_tag")):
                problems.append(_problem(i, "error", "gene-name", f"{name}: a gene needs /gene or /locus_tag"))
            if not any(g.get("type") in ("CDS", *RNA_TYPES) and _overlap(f, g) for g in features):
                problems.append(_problem(i, "warning", "gene-alone", f"{name}: no CDS or RNA under this gene"))
    return problems


def _check_cds(i, f, name, notes, data, code):
    """Frame, start, stop, internal stops and product of one CDS."""
    out = []
    table = int(_first(notes, "transl_table")) if _first(notes, "transl_table").isdigit() else code
    tbl = CodonTable.unambiguous_dna_by_id.get(table, CodonTable.unambiguous_dna_by_id[1])
    shift = int(_first(notes, "codon_start")) - 1 if _first(notes, "codon_start").isdigit() else 0
    body = feature_bases(data, f).upper()[shift:]
    left, right = (f.get("partial") or {}).get("left"), (f.get("partial") or {}).get("right")
    start_partial, end_partial = (right, left) if f.get("strand") == -1 else (left, right)  # the 5' and 3' ends
    if len(body) % 3 and not (start_partial or end_partial):
        out.append(_problem(i, "error", "cds-frame", f"{name}: length {len(body)} is not a multiple of 3 (a CDS cut short is marked partial: <1..>500)"))
    codons = [body[k:k + 3] for k in range(0, len(body) - len(body) % 3, 3)]
    if codons:
        if not shift and not start_partial and codons[0] not in tbl.start_codons:
            out.append(_problem(i, "warning", "cds-start", f"{name}: starts with {codons[0]}, not a start codon of table {table} (or mark its start partial)"))
        if not end_partial and codons[-1] not in tbl.stop_codons:
            out.append(_problem(i, "warning", "cds-stop", f"{name}: no stop codon at the end (or mark its end partial)"))
        internal = [k + 1 for k, c in enumerate(codons[:-1]) if c in tbl.stop_codons]
        if internal:
            more = f" (and {len(internal) - 1} more)" if len(internal) > 1 else ""
            out.append(_problem(i, "error", "cds-internal-stop", f"{name}: stop codon inside, at codon {internal[0]}{more}"))
    if not _first(notes, "product"):
        out.append(_problem(i, "error", "product", f"{name}: no /product"))
    return out


def summary(problems):
    return {"errors": sum(p["severity"] == "error" for p in problems),
            "warnings": sum(p["severity"] == "warning" for p in problems)}
