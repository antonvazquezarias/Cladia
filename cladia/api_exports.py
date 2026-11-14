"""Submission validation and sequence export routes."""
from .sequence_export import _export_text, _original, _submission_records, _template
from .request_context import _item
from . import internal_jobs
from collections import Counter
import shutil
import subprocess
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel
from . import jobs, library, submission

from fastapi import APIRouter

router = APIRouter()

class ExportRequest(BaseModel):
    ids: list[str]
    format: str = "genbank"  # genbank, fasta, gff3, gff3fasta (with ##FASTA), protein (the CDSs' proteins as FASTA),
    #                          nexus (an alignment, or trees), phylip (an alignment), newick (trees), bam (a mapping)
    path: str | None = None  # app window: where to write; browser: None = send the text back
    codes: dict[str, int] = {}  # item id -> genetic code picked in the Code menu (for protein)


@router.post("/api/export")
def export(req: ExportRequest):
    """The only way files leave Cladia: writes the items to a new file."""
    if req.format not in ("genbank", "fasta", "gff3", "gff3fasta", "protein", "nexus", "phylip", "newick", "bam", "gfa", "vcf"):
        raise HTTPException(400, f"Unknown format: {req.format}")
    chosen = [_item(i) for i in req.ids]
    if req.format == "bam":  # a mapping's reads (app window: copied where chosen)
        if len(chosen) != 1 or not chosen[0].get("bam"):
            raise HTTPException(400, "Pick one mapping that kept its BAM")
        if not req.path:
            raise HTTPException(400, "BAM export needs a place to save it")
        bam = library.library_dir() / chosen[0]["bam"]
        shutil.copyfile(bam, req.path)
        shutil.copyfile(f"{bam}.bai", f"{req.path}.bai")
        return {"path": req.path}
    text = _export_text(chosen, req.format, req.codes)
    if req.path:
        Path(req.path).write_text(text, encoding="utf-8")
        return {"path": req.path}
    return {"text": text}


class SubmissionRequest(BaseModel):
    ids: list[str]
    path: str | None = None  # the .tbl to write (the FASTA goes beside it, same name .fsa); None: send the texts back
    locus_tag_prefix: str = ""
    drop_cladia_notes: bool = True
    submitter: str = ""  # a submitter preset's name: the template (.sbt) is made from it; empty = none
    overrides: dict = {}  # this submission's own authors / title / hold date, over the preset's


@router.post("/api/jobs/validate")
def validate_submission(req: SubmissionRequest):
    """NCBI's table2asn on the submission files, as a job (its report is the log)."""
    records, prefix = _submission_records(req)
    return jobs.submit("validate", f"Validate for GenBank: {records[0].name}" + (f" +{len(records) - 1}" if len(records) > 1 else ""),
                       internal_jobs.validate_job, cores=1, records=records, prefix=prefix, drop_cladia_notes=req.drop_cladia_notes,
                       template=_template(req))


@router.post("/api/export/submission")
def export_submission(req: SubmissionRequest):
    """A GenBank submission: FASTA + feature table of the sequences (table2asn reads the pair)."""
    records, prefix = _submission_records(req)
    dropped = Counter()  # qualifiers left out for not being GenBank's (a GFF3's ID, Dbxref...)
    fsa, tbl = submission.files(records, prefix, req.drop_cladia_notes, dropped)
    sbt = _template(req)
    if req.path:
        base = Path(req.path).with_suffix("")
        Path(f"{base}.tbl").write_text(tbl, encoding="utf-8")
        Path(f"{base}.fsa").write_text(fsa, encoding="utf-8")
        if sbt:
            Path(f"{base}.sbt").write_text(sbt, encoding="utf-8")
        return {"path": f"{base}.tbl", "count": len(records), "sbt": bool(sbt), "dropped": dict(dropped)}
    return {"fsa": fsa, "tbl": tbl, "sbt": sbt, "count": len(records), "dropped": dict(dropped)}


@router.post("/api/library/{item_id}/write-back")
def write_back(item_id: str):
    """Export to original location: the item over the file it was imported from,
    in that file's format."""
    item, original = _original(item_id)
    path = Path(original["path"])
    if item["parent"]:
        raise HTTPException(400, "A list's sequence can't replace the list's file: export the whole list")
    if not original["format"]:
        raise HTTPException(400, f"Cladia can't write {path.name}'s format: use Export")
    if not path.parent.is_dir():
        raise HTTPException(400, f"The original's folder is gone: {path.parent}")
    path.write_text(_export_text([item], original["format"]), encoding="utf-8")
    return {"path": str(path)}


@router.post("/api/library/{item_id}/reveal")
def reveal(item_id: str):
    """Show original: Explorer with the imported file selected."""
    _, original = _original(item_id)
    if not Path(original["path"]).exists():
        raise HTTPException(400, f"The original is no longer there: {original['path']}")
    subprocess.Popen(f'explorer /select,"{original["path"]}"')
    return {}


class WriteRequest(BaseModel):
    path: str  # chosen in the Save dialog
    text: str


@router.post("/api/write")
def write_file(req: WriteRequest):
    """App window: saves text made in the page (a tree drawing as SVG) where the user chose."""
    Path(req.path).write_text(req.text, encoding="utf-8")
    return {"path": req.path}
