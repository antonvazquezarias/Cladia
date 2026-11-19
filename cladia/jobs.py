"""Background job scheduling, lifecycle, logs and cancellation."""
import json
import os
import re
import shlex
import shutil
import threading
import time
import uuid
from pathlib import Path
from . import config, library, linux, run_details
from .execution import container_command, _try
from .job_progress import current_progress

JOBS = {}  # job id -> job dict (kept in memory; workspaces stay on disk)

CORES = max(1, (os.cpu_count() or 2) - 1)  # for computing jobs: all but one, so the window stays quick


def budget():
    """Cores for computing jobs: the Settings dialog's number, else CORES."""
    chosen = config.setting("cores")
    return max(1, min(int(chosen), os.cpu_count() or 1)) if chosen else CORES
ALL = None  # a job that takes every core of the budget (and runs alone in its lane)
JOB_KINDS = {  # jobs without an engine declaration
    "project-export": ("transfer", 0), "project-inspect": ("transfer", 0), "project-import": ("transfer", 0),
    "graphblast": ("compute", None), "bamimport": ("compute", None), "readsimport": ("transfer", 0), "blastdb": ("download", 0), "validate": ("compute", 1),
    "tool-install": ("download", 0), "tool-uninstall": ("download", 0),
}
_queue = []  # waiting (job, func, params), in the order they came
_queue_lock = threading.Lock()

# ── Job bookkeeping ──────────────────────────────────────────────────────

def relocate_destinations(old, new):
    """Called under the library lock after a folder rename/move/delete."""
    for job in list(JOBS.values()):
        if job.get("status") not in {"queued", "running"} and not job.get("_destination_pending"):
            continue
        job["folder"] = library._moved(job["folder"], old, new) if job.get("folder") else None
        for output in job.get("outputs", []):
            if "folder" in output:
                output["folder"] = library._moved(output["folder"], old, new) if output["folder"] else None


def _place_outputs(job, outputs):
    with library._writing:
        grouped = {}
        for output in outputs:
            folder = output.get("folder", job.get("folder"))
            if output.get("folder_source"):
                try:
                    folder = library.folder_of(library.get_item(output["folder_source"]))
                except KeyError:
                    pass  # source removed: use the tracked fallback destination
            grouped.setdefault(folder, []).append(output["id"])
        for folder, ids in grouped.items():
            library.move(ids, folder)


def submit(kind, title, func, folder=None, cores=None, cleanup=None, resources=None, **params):
    """folder: where the results go (the folder of what they are made from).
    cores: this job's own count instead of its registered or non-tool default.
    resources: an engine's (lane, cores) declaration, if present.
    func: the runner supplied by an engine or a compatibility endpoint."""
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    job = {
        "id": job_id, "kind": kind, "title": title, "status": "queued",
        "log": "", "outputs": [], "error": None, "created": time.time(), "folder": folder,
    }
    if resources is None:
        from .tool_registry import build_registry
        engine = next((e for e in build_registry().engines.values()
                       if e.get("jobKind", e["id"]) == kind), None)
        resources = (engine["lane"], engine["cores"]) if engine else JOB_KINDS.get(kind, ("compute", 1))
    job["lane"], needed = resources
    if not callable(func):
        raise ValueError(f"No runner for {kind}")
    job["_run_details"] = run_details.capture(kind, func, params)
    job["cores"] = needed if cores is None else cores  # ALL: the budget when it starts
    with library._writing:
        if folder and folder not in library.folders():
            raise library.DestinationError("The result folder no longer exists. Choose a destination again.")
        JOBS[job_id] = job
    if kind.startswith("project-") or kind == "tool-install":
        job["_finished"] = threading.Event()
    if cleanup:
        job["_cleanup"] = cleanup
    with _queue_lock:
        _queue.append((job, func, params))
        _schedule()
    return job


def _schedule():
    """Starts the waiting jobs that fit (called with _queue_lock held)."""
    running = [j for j in JOBS.values() if j["status"] == "running"]
    for entry in list(_queue):
        job = entry[0]
        if job["status"] == "cancelled":  # cancelled while waiting
            _queue.remove(entry)
            _cleanup_job(job)
            run_details.append_log(job)
            save_log(job)
            if job.get("_finished"):
                job["_finished"].set()
            continue
        lane = [j for j in running if j["lane"] == job["lane"]]
        cores = budget() if job["cores"] is ALL else min(job["cores"], budget())
        fits = sum(j["cores"] for j in lane) + cores <= budget() if job["lane"] == "compute" else not lane
        ahead = any(e[0]["lane"] == job["lane"] and e[0]["status"] == "queued" for e in _queue[:_queue.index(entry)])
        if not fits or ahead:  # first come, first served in each lane
            continue
        _queue.remove(entry)
        job["status"] = "running"
        job["cores"] = cores
        running.append(job)
        threading.Thread(target=_run_then_next, args=entry, daemon=True).start()


def _run_then_next(job, func, params):
    try:
        _run(job, func, params)
    finally:
        try:
            _cleanup_job(job)
            with _queue_lock:
                _schedule()
        finally:
            if job.get("_finished"):
                job["_finished"].set()


def _run(job, func, params):
    job["_destination_pending"] = True
    job["started"] = time.time()
    workdir = config.APP_DIR / "jobs" / job["id"]
    try:
        workdir.mkdir(parents=True)
        func(job, workdir, **params)
        outcome = "done"
    except Exception as e:  # any failure is reported on the job, not raised
        outcome = "cancelled" if job.get("cancel") else "failed"
        job["error"] = None if job.get("cancel") else str(e)
    # a program's own log (IQ-TREE's run.log, minimap2/samtools) goes into the saved log
    live = Path(job.pop("live_log", "") or "")
    if live.is_file():
        text = re.sub(r"[^\n]*\r(?!\n)", "", live.read_text(encoding="utf-8", errors="replace"))  # counters: their last value
        job["log"] += f"\n── {live.name} ──\n" + text[-50000:]
    run_details.observed_versions(job)
    job["finished"] = time.time()
    outputs = [*job["outputs"], *job.pop("_grouped_outputs", [])]
    created_outputs = [] if job.get("_preserve_outputs") else [o for o in outputs if not o.get("updated")]
    try:
        if outputs and not job.get("_preserve_outputs"):
            inputs = []
            for value in params.values():
                candidates = value if isinstance(value, list) else [value]
                for item in candidates:
                    if isinstance(item, dict) and item.get("id") and item.get("label"):
                        inputs.append({"id": item["id"], "label": item["label"]})
            provenance = {"job_id": job["id"], "kind": job["kind"], "title": job["title"],
                          "finished": job["finished"], "status": outcome, "inputs": inputs, **job.get("_run_details", {})}
            library.set_provenance([o["id"] for o in outputs], provenance)
        if created_outputs:
            _place_outputs(job, created_outputs)
    except (OSError, ValueError, KeyError) as exc:
        outcome = "failed"
        job["error"] = f"Could not finish placing results: {exc}"
    finally:
        with library._writing:
            job["status"] = outcome
            job.pop("_destination_pending", None)
    run_details.append_log(job)
    save_log(job)
    if job["status"] == "done":
        # the results were copied into the library; the job's files go (a mapping's are large)
        shutil.rmtree(workdir, ignore_errors=True)


def _cleanup_job(job):
    cleanup = job.pop("_cleanup", None)
    if cleanup:
        try:
            cleanup(job)
        except (OSError, ValueError) as exc:
            job["log"] += f"\nTemporary-file cleanup failed: {exc}\n"


# ── Saved logs (the archive) ─────────────────────────────────────────────

def logs_dir():
    return config.APP_DIR / "logs"


def save_log(job):
    job["finished"] = time.time()
    logs_dir().mkdir(parents=True, exist_ok=True)
    data = {k: job.get(k) for k in ("id", "kind", "title", "status", "error", "warning", "outputs", "created", "started",
                                    "finished", "log", "result")}
    (logs_dir() / f"{job['id']}.json").write_text(json.dumps(data), encoding="utf-8")


_saved = {}  # saved log file -> its facts without the text (each file is read once)


def saved_logs():
    """Every saved log's facts (not the text), newest first."""
    folder = logs_dir()
    result = []
    for path in folder.glob("*.json") if folder.is_dir() else []:
        if path not in _saved:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            data["has_log"] = bool(data.pop("log", "") or data.get("error"))
            _saved[path] = data
        result.append(_saved[path])
    return sorted(result, key=lambda j: j.get("created") or 0, reverse=True)


def saved_log(job_id):
    if not re.fullmatch(r"[\w-]+", job_id):
        raise ValueError(job_id)
    return json.loads((logs_dir() / f"{job_id}.json").read_text(encoding="utf-8"))


def remove_logs(ids):
    """Deletes these jobs' saved logs (not of a job still queued or running), and
    this session's ones from the Jobs list too; returns how many."""
    removed = 0
    for job_id in ids:
        job = JOBS.get(job_id)
        if job and job["status"] in ("queued", "running") or not re.fullmatch(r"[\w-]+", job_id):
            continue
        path = logs_dir() / f"{job_id}.json"
        if path.exists() or job:
            path.unlink(missing_ok=True)
            _saved.pop(path, None)
            JOBS.pop(job_id, None)
            removed += 1
    return removed


def remove_old_logs(days):
    """Deletes saved logs of jobs that ended more than `days` ago; returns how many."""
    removed = 0
    for path in list(logs_dir().glob("*.json")) if logs_dir().is_dir() else []:
        if time.time() - path.stat().st_mtime > days * 86400 and path.stem not in JOBS:
            path.unlink(missing_ok=True)
            _saved.pop(path, None)
            removed += 1
    return removed


def images():
    """What the tools take on disk: Cladia's Linux distributions [{name, size (bytes), used, old}]."""
    return linux.sizes()


def remove_old_images():
    """Nothing to remove: Cladia's Linux updates in place (kept for Settings' Clean)."""
    return []


def clean_job_folders():
    """At startup: folders of earlier runs' jobs (failed or cancelled ones, kept to
    look into until Cladia is closed). Their logs stay in the archive."""
    shutil.rmtree(config.APP_DIR / "jobs", ignore_errors=True)


def cancel(job_id):
    """Skips a queued job, or kills the container of a running one."""
    job = JOBS[job_id]
    if job["status"] == "queued":
        job["status"] = "cancelled"
        with _queue_lock:
            _schedule()  # its log saved, the next one started
    elif job["status"] == "running":
        job["cancel"] = True
        if job["kind"].startswith("project-"):
            return job  # cooperative local cancellation; no WSL/container to kill
        if container_command():
            _try([*container_command(), "kill", f"cladia-{job_id}"])
        else:
            linux.cancel(f"cladia-{job_id}")
    return job
