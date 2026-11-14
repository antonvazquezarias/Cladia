"""Generic progress state and engine-owned log readers."""
from pathlib import Path


def watch_log(job, path, reader, offset=0):
    if not callable(reader):
        raise TypeError('A live-log reader must be callable')
    job.update(live_log=str(path), watch='engine', watch_from=offset)
    job['_progress_reader'] = reader
    job.pop('_progress_cache', None)


def steps(job, *names):
    job["progress"] = {"steps": list(names), "at": 0, "fraction": None, "detail": ""}

def step(job, at, fraction=None, detail=""):
    job["progress"].update(at=at, fraction=fraction, detail=detail)

def current_progress(job):
    """What the Jobs list shows for a running job (its live log read now)."""
    progress = job.get("progress")
    watcher = job.get("_progress_reader") if job.get("watch") else None
    if not progress or not watcher or not job.get("live_log"):
        return progress
    live = Path(job["live_log"])
    try:
        stat = live.stat()
        # Keep only the parsed summary. Unchanged logs need neither another read
        # nor another full parse; a host stage/metadata change invalidates it too.
        signature = (str(live), stat.st_mtime_ns, stat.st_size, job.get("watch_from", 0),
                     repr(progress), watcher)
        cached = job.get("_progress_cache")
        if cached and cached[0] == signature:
            return cached[1]
        text = live.read_text(encoding="utf-8", errors="replace")[job.get("watch_from", 0):]
    except OSError:  # a job can replace/remove its log while the list is polled
        return progress
    found = watcher(text[:max(text.rfind("\n"), text.rfind("\r")) + 1], progress)  # whole lines only
    if found and (found["at"] >= progress["at"] or "steps" in found):  # MAFFT --auto discovers its stages
        progress = {**progress, **found}
    job["_progress_cache"] = (signature, progress)
    return progress
