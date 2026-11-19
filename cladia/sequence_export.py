"""Sequence export and submission preparation, independent of HTTP."""
from .item_inputs import _item
import re
from .item_inputs import InputError
from . import library, submitter
from .genbank import is_protein


def _template(req):
    """The submission template text for the request's submitter, or None."""
    if not req.submitter:
        return None
    try:
        return submitter.write_sbt(submitter.find(req.submitter, req.overrides))
    except ValueError as e:
        raise InputError(400, str(e))


def _submission_records(req):
    """The DNA records of a submission request and its checked locus tag prefix."""
    records, seen = [], set()
    for item in [_item(i) for i in req.ids]:
        for child in library.children(item):
            if child["kind"] == "sequence" and (child["path"], child["record"]) not in seen:
                seen.add((child["path"], child["record"]))
                records.append(library.records(child)[0])
    if not records:
        raise InputError(400, "Pick sequences (or a list of them)")
    if any(is_protein(r) for r in records):
        raise InputError(400, "A submission is of DNA sequences")
    prefix = req.locus_tag_prefix.strip()
    if prefix and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,11}", prefix):
        raise InputError(400, "A locus tag prefix is 3 to 12 letters and digits, starting with a letter")
    return records, prefix


def _export_text(chosen, fmt, codes=None):
    """The items as text in an export format (not BAM)."""
    if any(it["kind"] == "item_list" for it in chosen):
        raise InputError(400, "Select the items inside the list to export them")
    if any(it["kind"] in ("blast", "primers") for it in chosen):
        raise InputError(400, "A BLAST or primer result is a table: export it as CSV from its own view")
    if any(it["kind"] == "figure" for it in chosen):
        raise InputError(400, "Export a figure from its own view (SVG, PDF, PNG or TIFF)")
    variants = [it for it in chosen if it["kind"] == "variants"]
    if variants or fmt == "vcf":
        if len(variants) != 1 or len(chosen) != 1 or fmt != "vcf":
            raise InputError(400, "A VCF is exported on its own, as VCF")
        with open(variants[0]["path"], encoding="utf-8") as f:
            return f.read()
    if fmt == "protein" and any(it["kind"] == "mapping" for it in chosen):
        raise InputError(400, "For a mapping's proteins, make its consensus a sequence first")
    graphs = [it for it in chosen if it["kind"] == "graph"]
    if graphs and (len(chosen) != 1 or fmt not in ("gfa", "fasta")):
        raise InputError(400, "An assembly graph is exported on its own, as GFA or its nodes as FASTA")
    if fmt == "gfa" and not graphs:
        raise InputError(400, "Only assembly graphs can be exported as GFA")
    if graphs:
        return library.export_graph(graphs[0], fmt)
    trees = [it for it in chosen if it["kind"] == "tree"]
    alignments = [it for it in chosen if it["kind"] == "alignment"]
    if trees and (len(trees) < len(chosen) or fmt not in ("newick", "nexus")):
        raise InputError(400, "Trees are exported on their own, as Newick or NEXUS")
    if fmt == "newick" and not trees:
        raise InputError(400, "Only trees can be exported as Newick")
    if fmt in ("nexus", "phylip") and not trees and (len(chosen) != 1 or not alignments):
        raise InputError(400, "NEXUS and PHYLIP export one alignment (or trees)")
    if fmt not in ("fasta", "nexus", "phylip", "newick") and alignments:
        raise InputError(400, "Alignments are exported as FASTA, NEXUS or PHYLIP")
    if trees:
        return library.export_trees(chosen, fmt)
    if fmt in ("nexus", "phylip"):
        return library.export_alignment(alignments[0], fmt)
    return library.export_text(chosen, fmt, codes)


def _original(item_id):
    item = _item(item_id)
    if not item.get("original"):
        raise InputError(400, f"{item['label']} wasn't imported from a file (it was made in Cladia)")
    return item, item["original"]
