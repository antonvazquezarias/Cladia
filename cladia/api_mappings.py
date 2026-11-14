"""Mapping, read pairing and sample preview routes."""
from .request_context import IdsRequest, _item, _sequences
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import library, mapping, reads, sanger

from fastapi import APIRouter

router = APIRouter()

class ReadsRequest(BaseModel):
    paths: list[str]
    read_type: str = "illumina"


def _read_groups(paths, read_type="illumina"):
    if read_type not in mapping.READ_TYPES:
        raise HTTPException(400, "Unknown read type")
    paths = [p.strip().strip('"') for p in paths if p.strip()]
    if not paths:
        raise HTTPException(400, "Pick the reads (from the library or files on disk)")
    try:
        return reads.read_groups(paths, read_type)  # files on disk, or reads items ("item:<id>")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/reads/pairs")
def read_pairs(req: ReadsRequest):
    """How the read files will be paired (shown in the dialog before mapping)."""
    groups = _read_groups(req.paths, req.read_type)
    for g in groups:
        g["size"] = sum(Path(f).stat().st_size for f in g["files"])
    return groups


@router.get("/api/mapping/{item_id}")
def get_mapping(item_id: str):
    item = _item(item_id)
    if item["kind"] != "mapping":
        raise HTTPException(400, f"{item['label']} is not a mapping")
    return library.mapping_data(item)


class MappingReadsModeRequest(BaseModel):
    mode: str


@router.put("/api/mapping/{item_id}/reads-mode")
def set_mapping_reads_mode(item_id: str, req: MappingReadsModeRequest):
    item = _item(item_id)
    if item["kind"] != "mapping":
        raise HTTPException(400, f"{item['label']} is not a mapping")
    try:
        mode = library.set_mapping_reads_mode(item_id, req.mode)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"reads_mode": mode}


@router.get("/api/mapping/{item_id}/reads")
def mapping_reads(item_id: str, start: int = 0, end: int = 0, limit: int = mapping.READS_LIMIT,
                  all_reads: bool = False):
    """The reads overlapping a window of the reference (0-based), for the reads lane."""
    item = _item(item_id)
    if item["kind"] != "mapping":
        raise HTTPException(400, f"{item['label']} is not a mapping")
    data = library.mapping_data(item)
    info = data.get("reads")
    if not info or not item.get("reads"):
        return {"reads": [], "in_view": 0, "more": False, "total": 0, "kept": 0,
                "stride": 1, "all": all_reads}
    start, end = max(0, int(start)), max(0, int(end))
    if end < start:
        start, end = end, start
    limit = mapping.READS_MAX if all_reads else max(1, min(5000, int(limit)))
    result = mapping.read_window(library.library_dir() / item["reads"], info, start, end,
                                 limit=limit, scan=(mapping.READS_MAX + 1 if all_reads else
                                                    max(mapping.READS_SCAN * 20, limit * 20)),
                                 ref_length=len((data.get("reference") or {}).get("sequence") or ""),
                                 all_reads=all_reads)
    result.update({k: info.get(k) for k in ("total", "kept", "stride")})
    return result


@router.get("/api/mapping/{item_id}/bam")
def download_bam(item_id: str):
    """Browser mode: the BAM as a download (the app window saves it through Export)."""
    item = _item(item_id)
    if not item.get("bam"):
        raise HTTPException(404, "This mapping didn't keep its BAM")
    return FileResponse(library.library_dir() / item["bam"], filename=item["bam"])


@router.post("/api/mapping/{item_id}/consensus")
def consensus_as_sequence(item_id: str):
    """The consensus as a new sequence, with the reference's features moved onto it."""
    item = _item(item_id)
    record, name = library.consensus_record(item)
    return {"id": library.same_folder(library.add_sequence(record, name), item)}


@router.post("/api/sanger/samples")
def sanger_samples(req: IdsRequest):
    """The selected reads grouped by sample, from their names (the Assemble dialog's preview)."""
    items = _sequences(req.ids)
    paired, alone = sanger.samples_by_name([it["name"] for it in items])
    return {"samples": [{"sample": s, "reads": names} for s, names in paired.items()], "alone": alone}
