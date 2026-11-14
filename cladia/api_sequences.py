"""Sequence, alignment and trace routes."""
from .request_context import IdsRequest, MetadataRequest, _free, _item, _item_in, _record_created
from pathlib import Path
from Bio.Data import CodonTable
from fastapi import HTTPException
from pydantic import BaseModel
from . import checks, gff, library, orfs, sanger, tracks
from .genbank import decode, editor_to_record, genetic_code, read_records, record_to_editor, turned

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/alignment/{item_id}/variants")
def get_alignment_variants(item_id: str):
    item = _item(item_id)
    if item["kind"] != "alignment":
        raise HTTPException(400, "Choose an alignment")
    return library.alignment_variants(item)

@router.get("/api/trace/{item_id}")
def get_trace(item_id: str):
    """A read's trace: base calls, qualities, peaks and dye channels of the whole
    read, and the part kept (start, end)."""
    try:
        return library.trace_data(_item(item_id))
    except (OSError, ValueError) as e:
        raise HTTPException(404, str(e))


class TrimRequest(BaseModel):
    limit: float = sanger.TRIM_LIMIT  # error probability; 0: the whole read


@router.post("/api/trace/{item_id}/trim")
def trim_read(item_id: str, req: TrimRequest):
    _free(item_id)
    try:
        return library.retrim(_item(item_id), max(0.0, min(req.limit, 0.5)))
    except ValueError as e:
        raise HTTPException(400, str(e))


class AnnotationsRequest(BaseModel):
    id: str | None = None     # the sequence to annotate; None: each GFF3 seqid onto the sequence it names
    path: str | None = None   # the GFF3 file (app window)...
    text: str | None = None   # ...or its text (browser mode)
    folder: str | None = None  # a GFF3 with sequences: where they go
    name: str | None = None    # the file's name (browser mode: names a data track)


@router.get("/api/tracks/{item_id}")
def get_tracks(item_id: str):
    """A sequence's data tracks (bedGraph / WIG): [{name, file, intervals, min, max}]."""
    return {"tracks": library.data_tracks(_item(item_id))}


class TrackRequest(BaseModel):
    name: str


@router.post("/api/tracks/{item_id}/remove")
def remove_track(item_id: str, req: TrackRequest):
    try:
        library.remove_track(_item(item_id), req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/annotations/import")
def import_annotations(req: AnnotationsRequest):
    """Adds a GFF3 file's features to sequences (the file isn't kept)."""
    target = _item(req.id) if req.id else None
    _free(req.id)
    if target and target["kind"] != "sequence":
        raise HTTPException(400, f"{target['label']} isn't a sequence")
    text = decode(Path(req.path).read_bytes()) if req.path else (req.text or "")
    if tracks.is_wig(text) or tracks.is_bedgraph(text):  # a data track, not features
        if not target:
            raise HTTPException(400, "Drop a bedGraph or WIG file on the sequence it belongs to")
        try:
            added = library.add_tracks(text, target, Path(req.path).name if req.path else (req.name or "track"))
        except ValueError as e:
            raise HTTPException(400, f"{target['label']}: {e}")
        return {"results": [], "errors": [], "tracks": added}
    if not target and gff.read_gff3(text)[1].strip():  # with its sequences (##FASTA): new sequences
        ids, errors = (library.import_paths([req.path], req.folder) if req.path
                       else library.import_text("annotations.gff3", text, req.folder))
        return {"ids": ids, "errors": errors}
    try:
        results, errors = library.import_annotations(text, target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"results": results, "errors": errors}


@router.get("/api/sequences/{item_id}")
def get_sequence(item_id: str):
    return record_to_editor(library.records(_item(item_id))[0])


@router.post("/api/sequences")
def get_sequences(req: IdsRequest):
    """Several sequences at once (the viewer's rows): the library is listed once,
    not once a row (that took ~4 ms a row in a library of 2,300 items)."""
    by_id = library.item_map()
    return [record_to_editor(library.records(_item_in(by_id, i))[0]) for i in req.ids]


class ChecksRequest(BaseModel):
    sequences: list[dict]  # editor JSON, as currently edited


@router.post("/api/checks")
def run_checks(req: ChecksRequest):
    """Submission checks of sequences as they are in the editor: one problem list each."""
    return [{"problems": (p := checks.check(d)), **checks.summary(p)} for d in req.sequences]


@router.get("/api/genetic-codes")
def genetic_codes():
    """NCBI genetic codes: number, name and codon -> amino acid ('*' = stop)."""
    codes = []
    for number, table in sorted(CodonTable.unambiguous_dna_by_id.items()):
        codons = {**table.forward_table, **{c: "*" for c in table.stop_codons}}
        codes.append({"id": number, "name": table.names[0], "codons": codons})
    return codes


@router.get("/api/info/{item_id}")
def item_info(item_id: str):
    """The list's hover card: a sequence's header facts, a list's or alignment's summary."""
    return library.info(_item(item_id))


@router.get("/api/alignment/{item_id}")
def get_alignment(item_id: str):
    """All sequences of an alignment file, with their gaps."""
    return library.alignment_rows(_item(item_id))  # name, sequence, description, organism


@router.get("/api/alignment/{item_id}/features")
def alignment_features(item_id: str):
    """Each row's features, in the row's own positions (0-based, inclusive), from
    the library sequence it came from (alignment "sources", else a sequence of
    the same name). A row whose sequence no longer matches its source is left
    out: its features would be drawn in the wrong place."""
    item = _item(item_id)
    every = library.items()
    by_id = {it["id"]: it for it in every}
    by_name = {it["name"]: it["id"] for it in every if it["kind"] == "sequence" and not it["missing"]}
    result = {}
    for row in read_records(Path(item["path"]), "fasta"):
        source_id, offset, reversed_, turn = library.row_source(item, row.id)
        source_id = source_id or by_name.get(row.id[3:] if reversed_ else row.id)
        source = by_id.get(source_id) if source_id else None
        if not source or source["missing"]:
            continue
        record = turned(library.records(source)[0], turn)  # (turn: as a lined-up comparison had it)
        n = len(record)
        ungapped = str(row.seq).replace("-", "").upper()
        full = str(record.seq.reverse_complement() if reversed_ else record.seq).upper()
        if full[offset: offset + len(ungapped)] != ungapped:
            continue
        lo, hi = offset, offset + len(ungapped) - 1  # the part of the source in this row
        features = []
        for index, f in enumerate(record_to_editor(record)["features"]):
            parts = []
            for p in f.get("locations") or [{"start": f["start"], "end": f["end"]}]:
                if p["start"] <= p["end"]:
                    parts.append((p["start"], p["end"]))
                else:  # crosses the origin
                    parts += [(p["start"], n - 1), (0, p["end"])]
            strand = f["strand"]
            if reversed_:  # MAFFT reverse-complemented this row
                parts = [(n - 1 - e, n - 1 - s) for s, e in parts]
                strand = -strand
            inside = [(max(s, lo), min(e, hi)) for s, e in parts if e >= lo and s <= hi]
            if not inside:
                continue
            # Bases cut off at the 5' end shift the reading frame of a CDS
            cut = sum(max(0, min(e, lo - 1) - s + 1) for s, e in parts) if strand == 1 \
                else sum(max(0, e - max(s, hi + 1) + 1) for s, e in parts)
            codon_start = int((f["notes"].get("codon_start") or ["1"])[0])
            places = [{"start": s - offset, "end": e - offset} for s, e in sorted(inside)]
            features.append({  # (index, start, end, notes: for the annotation table)
                "name": f["name"], "type": f["type"], "strand": strand, "index": index,
                "locations": places, "start": places[0]["start"], "end": places[-1]["end"],
                "notes": {k: v for k, v in f["notes"].items() if k != "translation"},
                "codonStart": (codon_start - 1 - cut) % 3 + 1,
            })
        result[row.id] = {"item": source["id"], "code": genetic_code(record), "features": features,
                          "trace": bool(source.get("trace")), "offset": offset}  # a Sanger read: its trace can be shown
    return {"rows": result}


class OrfRequest(BaseModel):
    sequence: str
    circular: bool = False
    code: int = 1
    min_codons: int = 100
    both_strands: bool = True
    any_start: bool = False
    skip_cds: bool = True
    cds: list[dict] = []  # the sequence's CDSs: {start, end, strand}


@router.post("/api/orfs")
def find_orfs(req: OrfRequest):
    """ORFs of the sequence open in the editor (it adds them as features)."""
    if req.code not in CodonTable.unambiguous_dna_by_id:
        raise HTTPException(400, f"Unknown genetic code: {req.code}")
    found = orfs.find_orfs(req.sequence, req.code, req.min_codons, req.both_strands, req.any_start, req.circular)
    if req.skip_cds:
        n = len(req.sequence)
        found = [o for o in found if not any(orfs.overlaps(o, c, n) for c in req.cds)]
    return {"orfs": found}


class ExtractRequest(BaseModel):
    id: str
    start: int                    # first and last column, 0-based
    end: int
    rows: list[int] | None = None  # first and last row; None = all


@router.post("/api/library/extract")
def extract_region(req: ExtractRequest):
    """A new alignment item from a block of an alignment."""
    item = _item(req.id)
    if item["kind"] != "alignment":
        raise HTTPException(400, f"{item['label']} is not an alignment")
    with library._writing:
        new_id = library.extract_alignment(item, req.start, req.end, req.rows)
        _record_created([new_id])
    return {"id": new_id}


@router.put("/api/sequences/{item_id}")
def save_sequence(item_id: str, data: dict):
    """Saves into Cladia's copy (always GenBank); other records of a list are kept."""
    item = _item(item_id)
    _free(item_id)
    if item["kind"] != "sequence":
        raise HTTPException(400, f"{item['label']} can't be edited here")
    original = library.records(item)[0]
    record = editor_to_record(data, original=original)
    if isinstance(data.get("metadata"), dict):
        allowed = set(MetadataRequest.model_fields)
        library.apply_record_metadata(record, {k: v for k, v in data["metadata"].items() if k in allowed and isinstance(v, str)})
    try:
        library.save_record(item, record)
    except OSError as e:
        raise HTTPException(507, f"Couldn't save sequence: {e}")
    # Enough for the page to update its cached item instead of reloading the whole
    # library after every save (mtime: the viewer's "file changed" check).
    return {"id": item_id, "mtime": Path(item["path"]).stat().st_mtime,
            "features": sum(1 for f in record.features if f.type != "source"), "length": len(record)}


class AlignmentRows(BaseModel):
    rows: list[dict]  # { name, sequence } in the file's order


class AlignmentCopyRows(AlignmentRows):
    as_sequences: bool = False


@router.post("/api/alignment/{item_id}/copy-rows")
def copy_alignment_rows(item_id: str, req: AlignmentCopyRows):
    item = _item(item_id)
    if item["kind"] != "alignment":
        raise HTTPException(400, "Select an alignment")
    try:
        with library._writing:
            ids = library.copy_alignment_rows(item, req.rows, req.as_sequences)
            _record_created(ids)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    return {"ids": ids}


@router.put("/api/alignment/{item_id}")
def save_alignment(item_id: str, req: AlignmentRows):
    """Saves edited gaps into Cladia's copy of an alignment."""
    item = _item(item_id)
    _free(item_id)
    if item["kind"] != "alignment":
        raise HTTPException(400, f"{item['label']} is not an alignment")
    try:
        library.save_alignment(item, req.rows)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(507, f"Couldn't save alignment: {e}")
    return {"id": item_id, "mtime": Path(item["path"]).stat().st_mtime}
