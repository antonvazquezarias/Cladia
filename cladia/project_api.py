"""Local project-transfer jobs, previews and downloadable ZIPs."""
from . import job_progress
import atexit
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from . import config, jobs, library, projects

router = APIRouter(prefix="/api/project")
_pending, _downloads = {}, {}
_owned = set()
_lock = threading.RLock()
TTL = 3600


@router.get("/recovery")
def recovery_report():
    _expire()
    path = config.APP_DIR / "transfer-recovery.json"
    if not path.exists():
        return {"removed": 0, "issues": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"removed": 0, "issues": [f"Couldn't read transfer recovery report: {exc}"]}


def _temporary():
    folder = projects.transfer_root() / ("preview-" + uuid.uuid4().hex)
    folder.mkdir()
    _owned.add(folder.resolve())
    return folder


def _discard(folder):
    if not folder.exists() and folder.resolve() not in _owned:
        return
    if folder.resolve() not in _owned or folder.is_symlink():
        raise ValueError("Unsafe transfer cleanup path")
    if (folder / "journal.json").exists():
        return  # cleanup needs startup recovery, including an external export temp file
    shutil.rmtree(folder, ignore_errors=True)
    _owned.discard(folder.resolve())


def _expire():
    with _lock:
        for entries in (_pending, _downloads):
            for token, (folder, created) in list(entries.items()):
                if time.time() - created > TTL:
                    _discard(folder)
                    del entries[token]


@atexit.register
def _close_transfers():
    for folder, created in list(_pending.values()) + list(_downloads.values()):
        _discard(folder)


def _check(job):
    if job and job.get("cancel"):
        raise RuntimeError("cancelled")


def _progress(job):
    return (lambda at, detail, fraction: job_progress.step(job, at, fraction, detail)) if job else None


def _inspect(folder, job=None):
    manifest = projects.validate_zip(folder / "project.zip", folder / "checked", lambda: _check(job), _progress(job))
    shutil.rmtree(folder / "checked")
    _check(job)
    token = uuid.uuid4().hex
    _expire()
    with _lock:
        if len(_pending) >= 4:
            old = next(iter(_pending))
            _discard(_pending.pop(old)[0])
        _pending[token] = (folder, time.time())
    return {"token": token, "items": len(manifest["entries"]), "folders": len(manifest["folders"]),
            "trash": len(manifest["trash"]), "history": any("history/" in n for n in manifest["files"]),
            "bytes": sum(f["size"] for f in manifest["files"].values()), "excluded": manifest["excluded"]}


def _submit(kind, title, folder, runner):
    def run(job, workdir):
        job["log"] += title + "\n"
        job["result"] = runner(job)
        job["log"] += "Completed.\n"
    job = jobs.submit(kind, title, run,
                      cleanup=lambda job: _discard(folder) if job["status"] != "done" else None)
    return {"job": job["id"]}


class ExportRequest(BaseModel):
    history: bool = False
    trash: bool = False
    reads: bool = False  # reads items' FASTQ files (large: left out unless asked)
    path: str | None = None
    background: bool = True


@router.post("/export")
def export_project(req: ExportRequest):
    folder = _temporary()
    if req.path:
        target = Path(req.path).resolve()
        if any(target.is_relative_to(p.resolve()) for p in (config.APP_DIR, config.DATA_DIR / "library")):
            _discard(folder)
            raise HTTPException(400, "Save the project ZIP outside the library's internal folders")
    def run(job=None):
        if job:
            job_progress.steps(job, "Snapshot", "Compress", "Save ZIP")
        result = projects.export_zip(folder / "project.zip", req.history, req.trash,
                                     include_reads=req.reads, check=lambda: _check(job), progress=_progress(job))
        _check(job)
        if req.path:
            if job:
                job_progress.step(job, 2, detail="Writing the selected ZIP file")
            staging = target.with_name(".cladia-export-" + uuid.uuid4().hex + ".tmp")
            if staging.exists():
                raise ValueError("Export staging file already exists; retry")
            projects._journal(folder, {"format": "Cladia export transaction", "staging": str(staging),
                                       "destination": str(target)})
            try:
                projects._copy(folder / "project.zip", staging, lambda: _check(job))
                _check(job)
                staging.replace(target)
            finally:
                staging.unlink(missing_ok=True)
                try:
                    (folder / "journal.json").unlink(missing_ok=True)
                except OSError:
                    pass  # no external temp remains; startup can finish journal cleanup
            _discard(folder)
            return {**result, "saved": True}
        token = uuid.uuid4().hex
        _expire()
        if req.background:
            with _lock:
                if len(_downloads) >= 4:
                    old = next(iter(_downloads))
                    _discard(_downloads.pop(old)[0])
                _downloads[token] = (folder, time.time())
        return {**result, "download": token}
    if req.background:
        return _submit("project-export", "Export project ZIP", folder, run)
    try:
        result = run()
        if req.path:
            return result
        return FileResponse(folder / "project.zip", media_type="application/zip", filename="Cladia-project.zip",
                            background=BackgroundTask(_discard, folder))
    except (ValueError, OSError) as exc:
        _discard(folder)
        raise HTTPException(400, str(exc))


@router.get("/download/{token}")
def download_project(token: str):
    _expire()
    with _lock:
        ready = _downloads.get(token)
    if not ready:
        raise HTTPException(410, "This ZIP download expired; export the project again")
    return FileResponse(ready[0] / "project.zip", media_type="application/zip", filename="Cladia-project.zip")


class InspectRequest(BaseModel):
    path: str
    background: bool = True


@router.post("/inspect")
def inspect_project(req: InspectRequest):
    folder = _temporary()
    def run(job=None):
        if job:
            job_progress.steps(job, "Copy and check ZIP")
        source = Path(req.path)
        if source.stat().st_size > projects.MAX_BYTES:
            raise ValueError("Project ZIP exceeds the size limit")
        projects._copy(source, folder / "project.zip", lambda: _check(job))
        return _inspect(folder, job)
    if req.background:
        return _submit("project-inspect", "Check project ZIP", folder, run)
    try:
        return run()
    except (ValueError, OSError) as exc:
        _discard(folder)
        raise HTTPException(400, str(exc))


@router.post("/upload")
async def upload_project(request: Request, background: bool = True):
    folder = _temporary()
    try:
        size = 0
        with (folder / "project.zip").open("wb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > projects.MAX_BYTES:
                    raise ValueError("Project ZIP exceeds the size limit")
                stream.write(chunk)
        if background:
            def run(job):
                job_progress.steps(job, "Check ZIP")
                return _inspect(folder, job)
            return _submit("project-inspect", "Check project ZIP", folder, run)
        return await run_in_threadpool(_inspect, folder)
    except (ValueError, OSError) as exc:
        _discard(folder)
        raise HTTPException(400, str(exc))
    except BaseException:
        _discard(folder)
        raise


class ImportRequest(BaseModel):
    token: str
    name: str = "Imported project"
    background: bool = True


@router.delete("/preview/{token}")
def discard_preview(token: str):
    with _lock:
        pending = _pending.pop(token, None)
    if pending:
        _discard(pending[0])
    return {"discarded": pending is not None}


@router.post("/import")
def import_project(req: ImportRequest):
    with _lock:
        pending = _pending.pop(req.token, None)
    if pending is None:
        raise HTTPException(409, "Choose the project ZIP again; its preview is no longer available")
    folder, created = pending
    def run(job=None):
        try:
            if time.time() - created > TTL:
                raise ValueError("Project preview expired; choose the ZIP again")
            if job:
                job_progress.steps(job, "Validate", "Prepare", "Publish")
                job["_preserve_outputs"] = True
            result = projects.import_zip(folder / "project.zip", req.name, lambda: _check(job), _progress(job))
            if job:
                entries = {e["id"]: e for e in library.load()}
                job["outputs"] = [{"id": i, "label": entries.get(i, {}).get("name", "Imported item"),
                                   "kind": entries.get(i, {}).get("kind")} for i in result["ids"]]
            return result
        finally:
            _discard(folder)
    if req.background:
        return _submit("project-import", "Import project ZIP", folder, run)
    try:
        return run()
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
