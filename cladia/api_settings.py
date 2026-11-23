"""Settings, storage, submitters and BLAST database routes."""
from .request_context import AFTER_SETTINGS, LIBRARY_ACTIONS
import os
from pathlib import Path
import webbrowser
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from . import blastdb, config, help, history, jobs, library, submitter, updates

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/updates")
def get_updates():
    """The installed version and a newer installer in the folder it came from, if any."""
    return updates.status()


@router.post("/api/updates/reveal")
def reveal_update():
    try:
        updates.reveal()
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {}


@router.get("/guide", response_class=HTMLResponse, include_in_schema=False)
@router.get("/guide/{name}", response_class=HTMLResponse, include_in_schema=False)
def guide(name: str = "guide"):
    """The user guide (or /guide/installation) as a page."""
    page = help.page(name)
    if page is None:
        raise HTTPException(404, "No such guide")
    return page


@router.post("/api/help/guide")
def open_guide(request: Request):
    """Opens the user guide in the default browser, from this server."""
    webbrowser.open(f"{request.base_url}guide")
    return {}


@router.post("/api/help/logs")
def show_logs():
    """Explorer on the folder with cladia.log and the job logs (to send with a problem report)."""
    return {"path": help.show_logs()}


@router.get("/api/settings")
def get_settings():
    return {**config.settings(), "cpus": os.cpu_count() or 1, "default_cores": jobs.CORES}


@router.put("/api/settings")
def put_settings(changes: dict):
    changes.pop("blast_databases", None)  # changed only through /api/blastdbs
    if "library_view" in changes:
        view = changes["library_view"]
        if (not isinstance(view, dict) or set(view) - {"folder", "expanded", "sorts"} or
                ("sorts" in view and (not isinstance(view["sorts"], dict) or
                                      config.library_sorts(view["sorts"]) != view["sorts"])) or
                ("folder" in view and view["folder"] is not None and not isinstance(view["folder"], str)) or
                ("expanded" in view and (not isinstance(view["expanded"], list) or
                                        any(not isinstance(p, str) for p in view["expanded"])))):
            raise HTTPException(400, "Library view needs a folder path, a list of expanded paths and each folder's sort")
    if "tree_layout" in changes:
        layout = changes["tree_layout"]
        if (not isinstance(layout, dict) or
                any(k not in config.TREE_LAYOUT_DEFAULTS or type(v) is not bool
                    for k, v in layout.items())):
            raise HTTPException(400, "Tree view choices must be boolean display options")
    for key, default, low, high in (("cores", 0, 0, os.cpu_count() or 1),
                                   ("text_size", 100, 80, 200)):
        if key not in changes:
            continue
        try:
            value = changes[key]
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError()
            changes[key] = max(low, min(int(value if value not in (None, "") else default), high))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"{key} must be a whole number") from None
    for key in ("ncbi_email", "ncbi_api_key"):
        if key in changes:
            changes[key] = str(changes[key]).strip()
    if "submitters" in changes:
        try:
            changes["submitters"] = submitter.check_presets(changes["submitters"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    saved = config.save_settings(changes)
    if changes.keys() - {"tree_layout", "library_view"}:
        for call in AFTER_SETTINGS:
            call()
    return saved


class SubmitterText(BaseModel):
    text: str = ""
    preset: dict = {}


@router.post("/api/submitters/parse")
def parse_submitter(req: SubmitterText):
    """A submission template (.sbt) read into a submitter preset (the dialog adds it to its list)."""
    try:
        return submitter.read_sbt(req.text)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/submitters/sbt")
def submitter_sbt(req: SubmitterText):
    """The submission template (.sbt text) for a preset."""
    try:
        return {"text": submitter.write_sbt(submitter.clean(req.preset))}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _size(path):
    path = Path(path)
    try:
        if path.is_file():
            return path.stat().st_size
    except FileNotFoundError:
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except FileNotFoundError:
            pass  # a background job may finish cleanup while Storage is measuring
    return total


@router.get("/api/settings/space")
def space():
    """What Cladia's files take: the library, logs, job folders, tool images."""
    logs = list(jobs.logs_dir().glob("*.json")) if jobs.logs_dir().is_dir() else []
    result = {"library": _size(library.library_dir()), "logs": sum(_size(p) for p in logs),
              "log_count": len(logs), "jobs": _size(config.APP_DIR / "jobs"), "images": [], "image_disk": None,
              "trash": _size(library.trash_dir()), "trash_count": len(library.trash_items()),
              "history": history.total_size(), "transfers": _size(config.APP_DIR / "transfers")}
    try:
        images = jobs.images()
    except Exception as e:  # no containers here
        result["images_error"] = str(e)
        images = []
    result["images"] = images
    disk = Path(os.environ.get("LOCALAPPDATA", "")) / "wslc"  # before Cladia's own Linux: wslc's images
    disks = list(disk.glob("sessions/*/storage.vhdx")) if disk.is_dir() else []
    if disks:
        result["image_disk"] = {"path": str(disks[0]), "size": disks[0].stat().st_size}
    return result


class CleanRequest(BaseModel):
    what: str  # "logs": saved logs older than 30 days; "images": tool images Cladia no longer uses;


@router.post("/api/settings/clean")
def clean(req: CleanRequest):
    if req.what == "logs":
        return {"removed": jobs.remove_old_logs(30)}
    if req.what == "images":
        return {"removed": jobs.remove_old_images()}
    if req.what == "history":
        with library._writing:
            size = history.total_size()
            history.clear_all()
            LIBRARY_ACTIONS.clear()  # undoing deletes and moves needs the versions just deleted
        return {"removed": size}
    raise HTTPException(400, "logs, images or history")


@router.get("/api/blastdbs")
def blast_databases():
    return {"folder": str(blastdb.folder()), "databases": blastdb.databases()}


class BlastDbRequest(BaseModel):
    name: str = ""
    path: str = ""   # add: a file of a BLAST database, or a FASTA file
    query: str = ""  # download: a GenBank (nucleotide) search


@router.post("/api/blastdbs/add")
def add_blast_database(req: BlastDbRequest):
    """A database you have is listed where it is; a FASTA file is made into one (a job)."""
    try:
        kind, path, _ = blastdb.kind_of(req.path)
        name = req.name.strip() or Path(path).name.split(".")[0]
        if kind == "database":
            return {"database": blastdb.add_existing(req.path, name)}
        blastdb._check_name(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job": jobs.submit("blastdb", f"BLAST database {name}", blastdb.build_job, path=str(path), name=name)}


@router.post("/api/blastdbs/remove")
def remove_blast_database(req: BlastDbRequest):
    try:
        blastdb.remove(req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {}


@router.get("/api/blastdbs/estimate")
def estimate_blast_database(query: str):
    try:
        return blastdb.estimate(query)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/blastdbs/download")
def download_blast_database(req: BlastDbRequest):
    if not req.query.strip():
        raise HTTPException(400, "Type a GenBank search")
    try:
        name = blastdb._check_name(req.name or req.query)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job": jobs.submit("blastdb", f"BLAST database {name}", blastdb.download_job,
                               query=req.query.strip(), name=name)}
