"""Library, folder, trash and action-history routes."""
from .request_context import IdsRequest, ResultDestination, _action_stack, _clear_actions, _free, _item, _item_in, _record_action, _record_created, _result_folder
import base64
import os
import re
import shutil
import time
from pathlib import Path
from fastapi import HTTPException, Response
from pydantic import BaseModel
from . import action_delete, bam_import, config, library, reads
from .genbank import editor_to_record

from fastapi import APIRouter

router = APIRouter()

class PathsRequest(BaseModel):
    paths: list[str]
    folder: str | None = None  # where the new items go


class UploadRequest(BaseModel):
    filename: str
    text: str = ""
    data: str | None = None  # base64: a binary file (an .ab1 trace)
    folder: str | None = None
    paste: bool = False      # pasted text: one undo step (imports aren't undone)
    batch: str | None = None  # FASTQ files of one drop: the same batch...
    last: bool = False        # ...and the last of them starts the import jobs


@router.get("/api/library")
def list_items(response: Response):
    found = library.items()
    response.headers["X-Cladia-Version"] = library.version(found)  # the page's focus check
    response.headers["Cache-Control"] = "no-store"  # the version must be current
    return found


@router.get("/api/library/facts")
def list_row_facts():
    """A few numbers for each row that isn't a sequence (tips, hits, depth...)."""
    return library.row_facts()


@router.get("/api/library/check")
def check_library():
    return library.check_integrity()


@router.get("/api/library/version")
def library_version(response: Response):
    """The list's fingerprint: a window on focus fetches only this, and loads the
    whole list when it differs from the one it last saw."""
    response.headers["Cache-Control"] = "no-store"
    return {"version": library.version()}


@router.post("/api/library/add")
def add_files(req: PathsRequest):
    """Imports files: Cladia keeps copies, the originals are never changed. BAM
    and SAM files become mappings through a job (bam_import)."""
    paths, started, errors, fastq = [], [], [], []
    for path in req.paths:
        try:
            alignment = bam_import.kind_of(path)
        except OSError:
            alignment = None
        if not alignment:
            (fastq if reads.is_fastq(path) else paths).append(path)
            continue
        try:
            started.append(_bam_started(path, *bam_import.submit(path, req.folder)))
        except ValueError as e:
            errors.append(f"{Path(path).name}: {e}")
    for job_id, sample, n in reads.submit(fastq, req.folder) if fastq else []:  # reads: a job per sample
        started.append({"job": job_id, "message": f"Reads {sample}: copying {'a pair of files' if n == 2 else f'{n} file' + ('s' if n > 1 else '')} "
                                                  "and reading them (a job)"})
    notes = []
    ids, more = library.import_paths(paths, req.folder, notes=notes) if paths else ([], [])
    if ids:
        _clear_actions()
    return {"ids": ids, "errors": errors + more, "jobs": started, "notes": notes}


def _bam_started(path, job_id, labels):
    return {"job": job_id, "message": f"{Path(path).name}: reading its reads onto {', '.join(labels)} (a job)"}


class FolderRequest(BaseModel):
    name: str                  # a new folder's name, or an existing folder's path
    new: str | None = None     # rename: the new name
    parent: str | None = None  # new or move: the folder it goes in (None: the top)


@router.get("/api/folders")
def list_folders():
    return library.folders()


@router.post("/api/folders")
def add_folder(req: FolderRequest):
    try:
        with library._writing:
            if req.parent and req.parent not in library.folders():
                raise ValueError("The parent folder no longer exists. Choose a folder again.")
            return {"name": library.add_folder(req.name, req.parent or None)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/folders/rename")
def rename_folder(req: FolderRequest):
    try:
        name = library.rename_folder(req.name, req.new or "")
        _clear_actions()
        return {"name": name}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/folders/move")
def move_folder(req: FolderRequest):
    try:
        name = library.move_folder(req.name, req.parent or None)
        _clear_actions()
        return {"name": name}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/folders/delete")
def delete_folder(req: FolderRequest):
    """The folder only: what was in it goes up one level."""
    try:
        library.delete_folder(req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _clear_actions()
    return {"ok": True}


class MoveRequest(BaseModel):
    ids: list[str]
    folder: str | None = None  # None: no folder


@router.post("/api/library/move")
def move_items(req: MoveRequest):
    with library._writing:
        library.result_folder([], req.folder)  # reject stale destinations before moving anything
        _free(*req.ids)
        ids = {(_item(i).get("parent") or i) for i in req.ids}
        before = {e["id"]: e.get("folder") for e in library.load() if e["id"] in ids}
        library.move(req.ids, req.folder)
        after = {e["id"]: e.get("folder") for e in library.load() if e["id"] in ids}
        if before != after:
            _record_action({"kind": "move", "before": before, "after": after})
    return {"ok": True}


@router.post("/api/library/remove")
def remove_items(req: IdsRequest):
    """Deletes Cladia's copies (never files outside Cladia)."""
    # a list's sequences are renumbered when one goes: its list counts too
    _free(*req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
    with library._writing:
        if _action_stack() is not None:
            try:
                action = action_delete.perform(req.ids)
            except (OSError, ValueError, KeyError) as exc:
                _clear_actions()
                raise HTTPException(409, f"Deletion could not finish: {exc}. Check the library and Trash; the action may be partially applied.")
            if action["trash"]:
                _record_action(action)
        else:
            library.remove(req.ids, trash=True)
    return {"ok": True}


@router.get("/api/trash")
def trash():
    return library.trash_items()


class TrashRequest(BaseModel):
    ids: list[str] | None = None  # delete for good: None = everything in the trash


@router.post("/api/trash/restore")
def restore_from_trash(req: TrashRequest):
    try:
        ids = library.restore(req.ids or [])
        if ids:
            _clear_actions()
        return {"ids": ids}
    except (OSError, ValueError, KeyError) as e:
        raise HTTPException(400, f"Couldn't restore: {e}")


@router.post("/api/trash/delete")
def delete_from_trash(req: TrashRequest):
    return {"removed": library.empty_trash(req.ids)}


@router.post("/api/library/split")
def split_list(req: IdsRequest):
    item = _item(req.ids[0])
    _free(item["id"])
    if item["kind"] == "item_list":
        with library._writing:
            ids = [it["id"] for it in library.items() if it.get("group") == item["id"]]
            remove_items(IdsRequest(ids=[item["id"]]))
        return {"ids": ids}
    if item["kind"] != "list":
        raise HTTPException(400, f"{item['label']} is not a sequence list")
    with library._writing:
        if _action_stack() is None:
            ids = library.split(item["id"])
        else:
            ids = library.split(item["id"], remove_source=False)
            try:
                output = action_delete.capture(ids)
                source = action_delete.perform([item["id"]])
            except (OSError, ValueError, KeyError) as exc:
                _clear_actions()
                raise HTTPException(409, f"Split could not finish: {exc}. Check the library and Trash; the action may be partially applied.")
            _record_action({"kind": "split", "source": source, "output": output, "output_ids": ids})
    return {"ids": ids}


class GroupRequest(ResultDestination):
    ids: list[str]
    name: str


@router.post("/api/library/group")
def group_items(req: GroupRequest):
    """Move items into a new list."""
    _free(*req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
    try:
        with library._writing:
            all_items = library.item_map()
            chosen = [_item_in(all_items, i) for i in req.ids]
            ids = [it["id"] for it in chosen]
            folder = _result_folder(req, chosen)
            if library.needs_item_list(chosen):
                result, _ = library.group_items(chosen, req.name, folder)
                if any(it["parent"] or it.get("group") for it in chosen):
                    _clear_actions()
                else:
                    _record_created([result])
                return {"id": result}
            if _action_stack() is None:
                result = library.group(ids, req.name, folder=folder)
            else:
                result = library.group(ids, req.name, remove_source=False, folder=folder)
                try:
                    output = action_delete.capture([result])
                    source = action_delete.perform(ids)
                except (OSError, ValueError, KeyError) as exc:
                    _clear_actions()
                    raise HTTPException(409, f"Grouping could not finish: {exc}. Check the library and Trash; the action may be partially applied.")
                _record_action({"kind": "group", "source": source, "output": output, "output_ids": [result]})
        return {"id": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/library/remove-annotations")
def remove_annotations(req: IdsRequest):
    """Sequences (or lists) without their features."""
    items = [_item(i) for i in req.ids]
    if any(it["kind"] not in ("sequence", "list") for it in items):
        raise HTTPException(400, "Only sequences have annotations to remove")
    _free(*req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
    return {"removed": library.remove_annotations([it["id"] for it in items])}


class CircularizeRequest(BaseModel):
    ids: list[str]
    min_overlap: int = 30


@router.post("/api/library/circularize")
def circularize(req: CircularizeRequest):
    """Sequences whose end repeats their start: trimmed and marked circular."""
    items = [_item(i) for i in req.ids]
    if any(it["kind"] not in ("sequence", "list") for it in items):
        raise HTTPException(400, "Only sequences can be circularized")
    _free(*req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
    return library.circularize([it["id"] for it in items], max(10, req.min_overlap))


class TakeOutRequest(BaseModel):
    ids: list[str]             # sequences of lists
    folder: str | None = None  # where they go (None: the library's top)


@router.post("/api/library/take-out")
def take_out(req: TakeOutRequest):
    """Sequences taken out of their lists, each a sequence of its own."""
    _free(*req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
    ids = library.take_out([_item(i)["id"] for i in req.ids], req.folder if "folder" in req.model_fields_set else library.DEFAULT_FOLDER)
    if ids:
        _clear_actions()
    return {"ids": ids}


class AddToListRequest(BaseModel):
    id: str          # the list
    ids: list[str]   # sequences or lists to move into it


@router.post("/api/library/add-to-list")
def add_to_list(req: AddToListRequest):
    """Items moved into an existing list (dropped on it in the sidebar)."""
    _free(req.id, *req.ids, *{i.rsplit("-", 1)[0] for i in req.ids if "-" in i})
    try:
        with library._writing:
            all_items = library.item_map()
            target = _item_in(all_items, req.id)
            if target["kind"] not in ("list", "item_list"):
                raise ValueError("Drop items on a list")
            chosen = [_item_in(all_items, i) for i in req.ids]
            if target["kind"] == "item_list":
                list_id, added = library.group_items(chosen, target=target)
            elif library.needs_item_list(chosen):
                list_id, count = library.group_items([target, *chosen], target["name"], target["folder"])
                added = count - 1
            else:
                list_id = req.id
                added = library.add_to_group(req.id, [it["id"] for it in chosen])
            if added:
                _clear_actions()
            return {"added": added, **({"id": list_id} if list_id != req.id else {})}
    except ValueError as e:
        raise HTTPException(400, str(e))


class SortRequest(BaseModel):
    id: str
    key: str                       # library.SORT_KEYS
    descending: bool | None = None  # None: the key's usual way (longest first, A to Z)
    reference: int | None = None  # selected alignment row, before sorting


@router.post("/api/library/sort")
def sort_list(req: SortRequest):
    item = _item(req.id)
    _free(item["id"])
    if item["kind"] not in ("list", "alignment") or req.key not in library.SORT_KEYS:
        raise HTTPException(400, "Only a sequence list or an alignment can be sorted, by name, length, organism, description or aligned distance")
    try:
        library.sort_item(item["id"], req.key, req.descending, req.reference)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _clear_actions()
    return {"ok": True}


@router.post("/api/library/duplicate")
def duplicate_items(req: IdsRequest):
    with library._writing:
        try:
            ids = [library.duplicate(_item(i)) for i in req.ids]
        except ValueError as e:
            raise HTTPException(400, str(e))
        _record_created(ids)
    return {"ids": ids}


class NewSequence(BaseModel):
    name: str
    data: dict                   # editor JSON: sequence, features, geneticCode
    source: str | None = None    # the item it was cut from: its metadata (organism, organelle...) is kept


class NewSequencesRequest(BaseModel):
    sequences: list[NewSequence]
    list_name: str | None = None  # several: one new list of that name, else a sequence each
    folder: str | None = None     # where they go when the first has no source


@router.post("/api/library/new-sequences")
def new_sequences(req: NewSequencesRequest):
    """New sequences, e.g. regions copied in the viewer and pasted into the list
    (several rows of a list: one list, "<list>_extract"). One undo step."""
    records = []
    for s in req.sequences:
        original = library.records(_item(s.source))[0] if s.source else None
        record = editor_to_record({**s.data, "name": s.name}, original=original)
        record.name = re.sub(r"\s+", "_", s.name.strip()) or "sequence"
        records.append((record, s.name))
    sources = [_item(s.source) for s in req.sequences if s.source]
    if any(not s.source for s in req.sequences):
        _result_folder(req, [])  # validate before publishing any standalone records
    if req.list_name and len(records) > 1:
        _result_folder(req, sources)  # reject ambiguous destinations before publication
    with library._writing:
        if req.list_name and len(records) > 1:
            destination = _result_folder(req, sources)
            ids = [library.add_list([r for r, _ in records], req.list_name)]
            library.move(ids, destination)
        else:
            ids = []
            for sequence, (record, name) in zip(req.sequences, records):
                new_id = library.add_sequence(record, name)
                folder = library.folder_of(_item(sequence.source)) if sequence.source else req.folder
                library.move([new_id], folder)
                ids.append(new_id)
        _record_created(ids)
    return {"ids": ids}


class RenameRequest(BaseModel):
    id: str
    name: str


@router.post("/api/library/rename")
def rename_item(req: RenameRequest):
    _free(req.id)
    try:
        with library._writing:
            item = _item(req.id)
            before = {"name": item["name"], "mtime": item["mtime"]}
            library.rename(item, req.name)
            item = _item(req.id)
            after = {"name": item["name"], "mtime": item["mtime"]}
            if before != after:
                _record_action({"kind": "rename", "id": req.id, "before": before, "after": after})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": req.id}


class LibraryUndoRequest(BaseModel):
    redo: bool = False


@router.post("/api/library/action-history")
def library_action_history(req: LibraryUndoRequest):
    stack = _action_stack()
    if not stack:
        raise HTTPException(400, "No window session")
    source, target = ("redo", "undo") if req.redo else ("undo", "redo")
    with library._writing:
        if not stack[source]:
            raise HTTPException(409, "No supported library action to undo or redo")
        action = stack[source][-1]
        expected, desired = ("before", "after") if req.redo else ("after", "before")
        if action["kind"] == "rename":
            _free(action["id"])
            item = _item(action["id"])
            current = {"name": item["name"], "mtime": item["mtime"]}
            if current != action[expected]:
                raise HTTPException(409, "The item changed since this action; undo would overwrite newer work")
            library.rename(item, action[desired]["name"])
            updated = _item(action["id"])
            action[desired] = {"name": updated["name"], "mtime": updated["mtime"]}
            selected = [action["id"]]
        elif action["kind"] == "delete":
            ids = action["ids"]
            _free(*ids, *action["entries"])
            try:
                if req.redo:
                    action_delete.validate_redo(action)
                    replacement = action_delete.perform(ids)
                    action.clear()
                    action.update(replacement)
                    selected = []
                else:
                    selected = action_delete.undo(action)
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(409, f"{exc}. If an I/O error occurred, check the library and Trash; the action may be partially applied.")
        elif action["kind"] == "paste":
            _free(*action["output_ids"])
            try:
                if req.redo:
                    action_delete.validate_undo(action["output_delete"])
                    selected = action_delete.undo(action["output_delete"])
                else:
                    action_delete.validate_redo(action["output"])
                    action["output_delete"] = action_delete.perform(action["output_ids"])
                    selected = []
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(409, f"{exc}. Check the library and Trash; the action may be partially applied.")
        elif action["kind"] in ("group", "split"):
            source_action = action["source"]
            _free(*source_action["ids"], *source_action["entries"], *action["output_ids"])
            try:
                if req.redo:
                    action_delete.validate_redo(source_action)
                    action_delete.validate_undo(action["output_delete"])
                    new_source = action_delete.perform(source_action["ids"])
                    selected = action_delete.undo(action["output_delete"])
                    action["source"] = new_source
                else:
                    action_delete.validate_redo(action["output"])
                    action_delete.validate_undo(source_action)
                    output_delete = action_delete.perform(action["output_ids"])
                    selected = action_delete.undo(source_action)
                    action["output_delete"] = output_delete
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(409, f"{exc}. If an I/O error occurred, check the library and Trash; the action may be partially applied.")
        else:
            ids = list(action[expected])
            _free(*ids)
            entries = library.load()
            current = {e["id"]: e.get("folder") for e in entries if e["id"] in ids}
            if current != action[expected]:
                raise HTTPException(409, "The items changed since this action; undo would overwrite newer work")
            folders = set(library.folders())
            if any(folder and folder not in folders for folder in action[desired].values()):
                raise HTTPException(409, "A previous folder no longer exists")
            for entry in entries:
                if entry["id"] in action[desired]:
                    folder = action[desired][entry["id"]]
                    if folder:
                        entry["folder"] = folder
                    else:
                        entry.pop("folder", None)
            library._save(entries)
            selected = ids
        stack[source].pop()
        stack[target].append(action)
        return {"kind": action["kind"], "ids": selected}


@router.post("/api/library/upload")
def upload_file(req: UploadRequest):
    """Browser mode only (no real file paths): the file's text is uploaded."""
    try:
        data = base64.b64decode(req.data) if req.data is not None else None
        if data and re.search(r"\.(fastq|fq)(\.gz)?$", req.filename, re.I):  # reads: a job copies and reads them
            # the files of one drop share a batch (so R1 and R2 pair up); the last one starts the jobs
            batch = re.sub(r"[^A-Za-z0-9]", "", req.batch or "") or f"{os.getpid()}{time.time_ns()}"
            upload = config.APP_DIR / "transfers" / f"upload-{batch}" / Path(req.filename).name
            upload.parent.mkdir(parents=True, exist_ok=True)
            upload.write_bytes(data)
            if not reads.is_fastq(upload):
                upload.unlink(missing_ok=True)
                raise ValueError("not a FASTQ file")
            if req.batch and not req.last:
                return {"ids": [], "errors": [], "jobs": []}
            started = reads.submit(sorted(upload.parent.iterdir()), req.folder, remove_after=True)
            return {"ids": [], "errors": [], "jobs": [{"job": j, "message": f"Reads {sample}: reading them (a job)"} for j, sample, _ in started]}
        if data and req.filename.lower().endswith((".bam", ".sam", ".cram")):  # a mapping: a job reads it
            upload = config.APP_DIR / "transfers" / f"upload-{os.getpid()}-{time.time_ns()}{Path(req.filename).suffix}"
            upload.parent.mkdir(parents=True, exist_ok=True)
            upload.write_bytes(data)
            try:
                started = _bam_started(Path(req.filename), *bam_import.submit(upload, req.folder, remove_after=True))
            except ValueError:
                upload.unlink(missing_ok=True)
                raise
            return {"ids": [], "errors": [], "jobs": [started]}
        notes = []
        ids, errors = library.import_text(req.filename, req.text, req.folder, data, notes=notes)
    except Exception as e:  # unreadable file: tell the user why
        raise HTTPException(400, str(e))
    if req.paste:
        with library._writing:
            _record_created(ids)
    elif ids:
        _clear_actions()
    return {"ids": ids, "errors": errors, "notes": notes}


@router.get("/api/reads/{item_id}")
def get_reads(item_id: str):
    """A reads item's report (reads.py) and which of its files are still there."""
    item = _item(item_id)
    if item["kind"] != "reads":
        raise HTTPException(400, f"{item['label']} is not a reads item")
    return library.reads_data(item)


@router.post("/api/reads/{item_id}/remove-files")
def remove_read_files(item_id: str):
    """Deletes a reads item's FASTQ files (not to the trash: they are large); the report stays."""
    item = _item(item_id)
    if item["kind"] != "reads":
        raise HTTPException(400, f"{item['label']} is not a reads item")
    return {"removed": library.drop_read_files(item)}
