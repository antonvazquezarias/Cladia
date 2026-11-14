"""Persistent snapshots of saved library files.

The caller serializes access with library's write lock. A timeline is keyed by an
owner: a library item (its file), an item's figure settings, or one record of a
big list (`record_owner`). Versions are numbered by their position in the
timeline; the oldest go first when a timeline passes MAX_VERSIONS or MAX_BYTES,
so callers that keep a version for later remember its hash, not its number.
"""
import gzip
import hashlib
import json
import shutil
import time
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config
from .genbank import replace_file

MAX_VERSIONS = 20          # versions kept per timeline (the oldest go first)
MAX_BYTES = 300 * 2 ** 20  # and their compressed size at most, per timeline
LEVEL = 1                  # gzip level: level 9 took ~5x as long for a slightly smaller file
_transactions = threading.local()


def root():
    return config.APP_DIR / "history"


def directory(owner):
    return root() / owner


def record_owner(list_id, name):
    """The timeline of one record of a list (saved on its own: big lists). Inside the
    list's own folder, so it moves with it to the trash and back."""
    return f"{list_id}/records/{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}"


def _manifest(folder):
    path = folder / "index.json"
    if not path.exists():
        return {"versions": [], "current": -1}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(folder, data):
    path = folder / "index.json"
    tmp = folder / "index.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def checkpoint(owner, path):
    """Record a file's current bytes if they changed, truncating redo only for a new state."""
    checkpoint_bytes(owner, Path(path).read_bytes())


def checkpoint_bytes(owner, data):
    """Record a state from bytes (a record, or a state without a file yet, such as default settings)."""
    digest = _digest(data)
    folder = directory(owner)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(folder)
    versions, current = manifest["versions"], manifest["current"]
    if current >= 0 and versions[current]["hash"] == digest:
        return
    blob = folder / (digest + ".gz")
    if not blob.exists():
        tmp = folder / (digest + ".tmp")
        tmp.write_bytes(gzip.compress(data, compresslevel=LEVEL))
        tmp.replace(blob)
    manifest["versions"] = versions[:current + 1] + [{"hash": digest, "time": time.time(), "size": blob.stat().st_size}]
    manifest["current"] = len(manifest["versions"]) - 1
    _prune(folder, manifest)
    _save(folder, manifest)
    if not getattr(_transactions, "depth", 0):
        _collect(folder)


def _prune(folder, manifest):
    """The oldest versions out, past MAX_VERSIONS or MAX_BYTES (never the current one),
    and blobs no version uses any more."""
    versions = manifest["versions"]
    size = lambda: sum(v.get("size", 0) for v in {v["hash"]: v for v in versions}.values())
    while manifest["current"] > 0 and (len(versions) > MAX_VERSIONS or size() > MAX_BYTES):
        versions.pop(0)
        manifest["current"] -= 1
def _collect(folder):
    """Only delete obsolete blobs after the index and document are committed."""
    try:
        used = {v["hash"] for v in _manifest(folder)["versions"]}
    except OSError:
        return
    for blob in folder.glob("*.gz"):
        if blob.stem not in used:
            try:
                blob.unlink(missing_ok=True)
            except OSError:
                pass  # stale blobs can be collected on a later successful save


@contextmanager
def protect(path, owners):
    """Roll back a document and its timeline indices if any part of a save fails.

    Caller holds library's write lock and has already checkpointed the baseline.
    Backups are streamed to the same disk; pruning is deferred through nested saves.
    """
    path = Path(path)
    owners = list(dict.fromkeys(owners))
    indices = {owner: (directory(owner) / "index.json").read_bytes() for owner in owners}
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="edit-", dir=config.APP_DIR))
    backup = folder / "document"
    existed = path.exists()
    rollback_failed = False
    try:
        for n, raw in enumerate(indices.values()):
            (folder / f"index-{n}.json").write_bytes(raw)
        if existed:
            shutil.copyfile(path, backup)
        if not getattr(_transactions, "depth", 0):
            _transactions.owners = set()
        _transactions.owners.update(owners)
        _transactions.depth = getattr(_transactions, "depth", 0) + 1
        try:
            yield
        except BaseException as exc:
            errors = []
            try:
                if existed:
                    replace_file(backup, path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as err:
                errors.append(str(err))
            for owner, raw in indices.items():
                try:
                    target = directory(owner) / "index.json"
                    staged = target.with_suffix(".rollback")
                    staged.write_bytes(raw)
                    replace_file(staged, target)
                except OSError as err:
                    errors.append(str(err))
            if errors:
                rollback_failed = True
                raise OSError(f"Save failed ({exc}); rollback failed: {'; '.join(errors)}. "
                              f"Recovery files retained in {folder}") from exc
            raise
        finally:
            _transactions.depth -= 1
            if not _transactions.depth and not rollback_failed:
                for owner in _transactions.owners:
                    _collect(directory(owner))
    except BaseException:
        # Retain an unused backup only when restoring the document itself failed.
        if not rollback_failed:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    else:
        shutil.rmtree(folder, ignore_errors=True)


def versions(owner):
    manifest = _manifest(directory(owner))
    return {**manifest, "versions": [{**v, "index": i} for i, v in enumerate(manifest["versions"])]}


def index_of(owner, digest):
    """The number of the latest version with these bytes, or None (pruned since)."""
    found = [i for i, v in enumerate(_manifest(directory(owner))["versions"]) if v["hash"] == digest]
    return found[-1] if found else None


def current_hash(owner):
    manifest = _manifest(directory(owner))
    return manifest["versions"][manifest["current"]]["hash"] if manifest["current"] >= 0 else None


def data(owner, index):
    """A version's bytes, checked against its hash."""
    entry = _manifest(directory(owner))["versions"][index]
    raw = gzip.decompress((directory(owner) / (entry["hash"] + ".gz")).read_bytes())
    if _digest(raw) != entry["hash"]:
        raise ValueError("The saved version is damaged")
    return raw


def move_to(owner, index):
    """Mark a version as the current one (after the caller put its bytes back)."""
    folder = directory(owner)
    manifest = _manifest(folder)
    manifest["current"] = index
    _save(folder, manifest)


def restore(owner, path, index):
    """Restore a timeline entry, refusing to overwrite an unrecorded external edit."""
    folder = directory(owner)
    manifest = _manifest(folder)
    current = manifest["current"]
    entries = manifest["versions"]
    if current < 0 or not 0 <= index < len(entries):
        raise ValueError("No such saved version")
    path = Path(path)
    original = path.read_bytes()
    if _digest(original) != entries[current]["hash"]:
        raise ValueError("The file changed outside its history; reload before restoring")
    saved = data(owner, index)
    staging = path.with_name(path.name + ".history-saving")
    staging.write_bytes(saved)
    replace_file(staging, path)
    manifest["current"] = index
    try:
        _save(folder, manifest)
    except Exception as exc:
        try:
            staging.write_bytes(original)
            replace_file(staging, path)
        except OSError as rollback_error:
            raise OSError(f"Could not save version index ({exc}); file rollback failed: {rollback_error}") from exc
        raise
    return versions(owner)


def total_size():
    """Bytes all saved versions take (Settings > Storage)."""
    folder = root()
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0


def clear_all():
    """Every saved version deleted (Settings > Storage); the files stay as they are."""
    shutil.rmtree(root(), ignore_errors=True)
