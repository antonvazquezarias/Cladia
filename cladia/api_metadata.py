"""Metadata, saved versions and table import routes."""
from .request_context import MetadataRequest, _free, _item
import base64
import uuid
from fastapi import HTTPException
from pydantic import BaseModel
from . import library, metatable

from fastapi import APIRouter

router = APIRouter()

@router.get("/api/library/{item_id}/metadata")
def get_metadata(item_id: str):
    """A sequence record's editable header fields (the library's Edit metadata…)."""
    item = _item(item_id)
    if item["kind"] != "sequence":
        raise HTTPException(400, "Only a sequence record has metadata")
    return library.record_metadata(item)


@router.get("/api/library/{item_id}/history")
def get_history(item_id: str):
    return library.saved_versions(_item(item_id))


class HistoryRequest(BaseModel):
    index: int


@router.post("/api/library/{item_id}/history")
def restore_history(item_id: str, req: HistoryRequest):
    _free(item_id)
    try:
        return library.restore_version(_item(item_id), req.index)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc))


@router.post("/api/library/{item_id}/metadata")
def set_metadata(item_id: str, req: MetadataRequest):
    item = _item(item_id)
    if item["kind"] != "sequence":
        raise HTTPException(400, "Only a sequence record has metadata")
    _free(item_id)
    try:
        result = library.edit_metadata(item, req.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(507, f"Couldn't save metadata: {e}")
    return {"id": item["id"], "label": item["label"], **result}


METADATA_TABLES = {}  # token -> (file name, columns, rows): the last few tables read, kept while the dialog works


class MetadataTableRequest(BaseModel):
    filename: str
    ids: list[str] = []  # the selected items: the columns are scored by how many of their names they hold
    text: str | None = None
    data: str | None = None  # base64: an Excel file


@router.post("/api/metadata/table")
def read_metadata_table(req: MetadataTableRequest):
    """A table read: its columns, a few rows, which column holds the sequences' names, and the
    fields it can be imported as."""
    try:
        raw = base64.b64decode(req.data, validate=True) if req.data is not None else (req.text or "").encode("utf-8")
        columns, rows = metatable.read_table(req.filename, raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = uuid.uuid4().hex
    METADATA_TABLES[token] = (req.filename, columns, rows)
    for old in list(METADATA_TABLES)[:-4]:
        del METADATA_TABLES[old]
    return {"token": token, "columns": columns, "count": len(rows), "sample": rows[:3],
            "matches": library.table_matches(req.ids, columns, rows),
            "fields": [{"key": k, "label": label} for k, label in metatable.FIELDS]}


class MetadataImportRequest(BaseModel):
    ids: list[str]
    token: str
    name_column: int
    mapping: dict[str, int]  # field -> column
    clear_empty: bool = False
    apply: bool = False  # False: only say what would happen
    expected_hash: str | None = None


@router.post("/api/library/import-metadata")
def import_metadata(req: MetadataImportRequest):
    """Metadata from a table onto the sequences (or the lists' sequences) whose names it holds."""
    if req.token not in METADATA_TABLES:
        raise HTTPException(409, "That table is no longer loaded: choose the file again")
    _, columns, rows = METADATA_TABLES[req.token]
    if not 0 <= req.name_column < len(columns) or any(not 0 <= c < len(columns) for c in req.mapping.values()):
        raise HTTPException(400, "Unknown column")
    if any(f not in dict(metatable.FIELDS) for f in req.mapping):
        raise HTTPException(400, "Unknown field")
    items = [_item(i) for i in req.ids]
    if any(it["kind"] not in ("sequence", "list") for it in items):
        raise HTTPException(400, "Metadata goes onto sequences")
    try:
        with library._writing:
            if req.apply:
                _free(*req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
            return library.import_table_metadata([it["id"] for it in items], rows, req.name_column, req.mapping,
                                                 req.clear_empty, req.apply, req.expected_hash)
    except ValueError as e:
        raise HTTPException(409 if req.expected_hash else 400, str(e))
