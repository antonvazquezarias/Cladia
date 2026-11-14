"""Rows handed to an alignment or trimming program, and taken back checked.

Programs get the rows under opaque ids (seq000001, ...: some shorten, change or
reorder names). What comes back is checked before anything is saved: every row
there once, one length, put back in the input order under its own name, and for
an aligner the same residues as went in.
"""
import re
from pathlib import Path

from Bio.Seq import Seq

from . import job_outputs
from .genbank import read_records


def residues(text):
    """A row's letters without gaps, upper case."""
    return re.sub(r"[-.]", "", str(text)).upper()


def from_items(items):
    """Library sequences without gaps: (records under opaque ids, rows [(key, row name, item id)])."""
    used, records, rows = set(), [], []
    for index, item in enumerate(items):
        record = job_outputs.named_record(item, used)
        key = f"seq{index + 1:06d}"
        rows.append((key, record.id, item["id"]))
        record.seq = Seq(residues(record.seq))
        if not len(record.seq):
            raise ValueError(f"{item['label']}: empty sequence")
        record.id, record.name, record.description = key, key, ""
        records.append(record)
    return records, rows


def from_alignment(item):
    """An alignment item's rows under opaque ids: (records, rows [(key, row name, None)])."""
    records = read_records(Path(item["path"]), "fasta")
    rows = []
    for index, record in enumerate(records):
        key = f"seq{index + 1:06d}"
        rows.append((key, record.id, None))
        record.id, record.name, record.description = key, key, ""
    return records, rows


def back(path, rows, keep_residues=None, program="The program"):
    """A program's aligned FASTA as records under the rows' own names, in the input
    order. keep_residues: {key: residues} each row must still have (aligners);
    None for trimming, which removes columns."""
    aligned = read_records(Path(path), "fasta")
    by_id = {record.id: record for record in aligned}
    keys = [key for key, _, _ in rows]
    if len(by_id) != len(aligned) or set(by_id) != set(keys) or len({len(r.seq) for r in aligned}) != 1:
        raise RuntimeError(f"{program} returned incomplete or inconsistent alignment rows")
    out = []
    for key, name, _ in rows:
        record = by_id[key]
        if keep_residues is not None and residues(record.seq) != keep_residues[key]:
            raise RuntimeError(f"{program} changed the residues of {name}; no result was saved")
        record.seq = Seq(str(record.seq).upper().replace(".", "-"))
        record.id, record.name, record.description = name, name, ""
        out.append(record)
    return out


def tree_rows(item, pattern=r"[^A-Za-z0-9_.-]+"):
    """An alignment's records under names tree programs accept (other characters
    as _, made unique), and {row name: that name}, as IQ-TREE's jobs name them."""
    records = read_records(Path(item["path"]), "fasta")
    used, renamed = set(), {}
    for record in records:
        name = re.sub(pattern, "_", record.id) or "seq"
        while name in used:
            name += "_"
        used.add(name)
        renamed[record.id] = name
        record.id, record.name, record.description = name, name, ""
    return records, renamed


def sources(rows):
    """The alignment entry's `sources` (row name -> its library sequence) for the
    alignment view's features."""
    return {name: {"item": item_id} for _, name, item_id in rows if item_id}
