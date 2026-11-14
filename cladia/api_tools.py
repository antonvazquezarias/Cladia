"""Registry submission, engine settings and job/log routes."""
from .tool_validation import validate_tool_request
from .request_context import IdsRequest, LOCKS, ResultDestination, _result_folder
from . import job_progress
import time
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel
from . import jobs, library

from fastapi import APIRouter

router = APIRouter()

def _annotation_busy(ids):
    now = time.time()
    return any(lock["until"] >= now and any(key == i or key.startswith(f"{i}-") or i.startswith(f"{key}-") for i in ids)
               for key, lock in LOCKS.items())


@router.get("/api/tool-registry")
def tool_registry():
    """The category/operation/engine contract used by generated tool dialogs."""
    from .tool_registry import build_registry
    return build_registry().public()


@router.get("/api/tool-engines")
def tool_engines():
    """Fast tool list; disk measurement is requested only when details are opened."""
    from .tool_registry import build_registry
    from .tool_storage import catalog
    registry = build_registry()
    sizes = catalog(registry.engines, measure=False)
    return [{**row, "storage": sizes[row["id"]]} for row in registry.installable(include_builtin=True)]


@router.get("/api/tool-engines/storage")
def tool_engine_storage():
    """Measured footprints for the detail dialog, fetched on demand."""
    from .tool_registry import build_registry
    from .tool_storage import catalog
    return catalog(build_registry().engines)


@router.get("/api/tool-engines/{engine_id}/settings")
def get_tool_engine_settings(engine_id: str):
    from .tool_registry import build_registry, tool_settings
    registry = build_registry()
    engine = registry.engines.get(engine_id)
    if not engine or not engine.get("optional") or not registry.available(engine):
        raise HTTPException(404, "Tool is not installed")
    values = tool_settings(engine_id)
    return {field["key"]: (bool(values.get(field["key"])) if field["type"] == "secret"
            else values.get(field["key"], field.get("default", False if field["type"] == "bool" else "")))
            for field in engine.get("settings", [])}


@router.put("/api/tool-engines/{engine_id}/settings")
def put_tool_engine_settings(engine_id: str, changes: dict):
    from .tool_registry import build_registry, save_tool_settings
    registry = build_registry()
    engine = registry.engines.get(engine_id)
    if not engine or not engine.get("optional") or not registry.available(engine):
        raise HTTPException(404, "Tool is not installed")
    fields = {field["key"]: field for field in engine.get("settings", [])}
    if set(changes) - fields.keys():
        raise HTTPException(400, "Unknown tool setting")
    for key, value in changes.items():
        kind = fields[key]["type"]
        if kind == "bool" and not isinstance(value, bool) or kind != "bool" and value is not None and (
                not isinstance(value, str) or len(value) > 4096):
            raise HTTPException(400, f"Invalid value for {key}")
        if kind == "secret" and value is True:
            raise HTTPException(400, f"Invalid value for {key}")
    save_tool_settings(engine_id, changes)
    return get_tool_engine_settings(engine_id)


class InstallEngineRequest(BaseModel):
    engines: list[dict]


@router.post("/api/tool-engines/install")
def install_tool_engines(req: InstallEngineRequest):
    """Install (or update) optional tools: a job that downloads them."""
    from .tool_install import install_engines
    return install_engines(req.engines)


@router.post("/api/tool-engines/{engine_id}/uninstall")
def uninstall_tool_engine(engine_id: str):
    from .tool_install import uninstall_engine
    return uninstall_engine(engine_id)


class RegisteredToolRequest(ResultDestination):
    operation: str
    engine: str
    inputs: dict[str, list]
    options: dict = {}
    output: str = "alignment"
    save_copy: bool = False
    context: dict = {}


@router.post("/api/tool-registry/run")
def run_registered_tool(req: RegisteredToolRequest):
    """Validate shared operation inputs, then dispatch to the selected engine."""
    operation, engine, resolved, items, options = validate_tool_request(req)
    if operation.get("prepare"):
        return operation["prepare"](req, resolved, items, engine, options, _annotation_busy)
    output = library.safe_name(req.output) or operation.get("fallbackName", "alignment")
    title = f"{engine['label']}: {operation['label'].lower()}"
    folder_items = [] if operation.get("destination") == "current" else items
    return jobs.submit(engine.get("jobKind", engine["id"]), title, engine["run"], folder=_result_folder(req, folder_items),
                       resources=(engine["lane"], engine["cores"]),
                       operation=req.operation, items=items, add_to=resolved.get("add_to"), output=output,
                       **options)


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if job_id not in jobs.JOBS:
        raise HTTPException(404, f"No such job: {job_id}")
    jobs.cancel(job_id)
    return {"ok": True}


def _facts(job):
    """A job without its log text or its program's live log (the list is polled while jobs run)."""
    facts = {k: v for k, v in job.items() if not k.startswith("_") and k not in ("log", "live_log", "cancel", "watch", "watch_from")}
    if job.get("status") == "running":
        facts["progress"] = job_progress.current_progress(job)
    facts["has_log"] = bool(job.get("log") or job.get("error") or job.get("live_log") or job.get("has_log"))
    return facts


@router.get("/api/jobs")
def list_jobs():
    """This session's jobs, newest first (earlier ones are in the archive, /api/logs)."""
    return sorted((_facts(j) for j in jobs.JOBS.values()), key=lambda j: j.get("created") or 0, reverse=True)


@router.get("/api/logs")
def list_logs():
    """The archive: every saved job log's facts, newest first."""
    return [_facts(j) for j in jobs.saved_logs()]


@router.post("/api/logs/delete")
def delete_logs(req: IdsRequest):
    return {"removed": jobs.remove_logs(req.ids)}


@router.get("/api/logs/{job_id}")
def get_log(job_id: str):
    """One job's log: a job of this run (with its program's log while it runs), or a saved one."""
    job = jobs.JOBS.get(job_id)
    if job:
        job = {k: v for k, v in job.items() if not k.startswith("_")}
        live = Path(job.pop("live_log", "") or "")
        if job["status"] == "running" and live.is_file():  # e.g. IQ-TREE's log while it runs
            job["log"] += live.read_text(encoding="utf-8", errors="replace")[-20000:]
        return job
    try:
        return jobs.saved_log(job_id)
    except (OSError, ValueError):
        raise HTTPException(404, f"No log for job {job_id}")
