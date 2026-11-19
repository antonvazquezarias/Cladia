"""Versioned project ZIPs. Stage and validate first; publish the library index last.

Only saved library data travels. Machine settings, credentials, tools, databases,
job workspaces and external reads are never collected. See docs/PORTABILITY_PLAN.md.
"""
import copy
import gzip
import hashlib
import json
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from . import config, history, library

FORMAT = "Cladia project"
VERSION = 1
MAX_FILES = 100000
MAX_BYTES = 50 * 2**30  # uncompressed archive; large kept BAMs are allowed
MAX_HISTORY_BLOB = 512 * 2**20
KINDS = {"sequence", "list", "item_list", "alignment", "tree", "mapping", "comparison", "blast", "primers", "graph", "figure", "variants", "reads"}
LINK_KEYS = {"item", "id", "reference_item", "from_id", "source"}  # source: what a figure draws


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _path(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Invalid project path")
    parts = name.split("/")
    if any(not p or p in (".", "..") or p[-1:] in (".", " ") or
           re.search(r'[<>:"|?*\x00-\x1f]', p) or
           p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)},
                                      *{f"LPT{i}" for i in range(1, 10)}} for p in parts):
        raise ValueError(f"Unsafe project path: {name}")
    return PurePosixPath(name)


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,80}", value):
        raise ValueError("Invalid project item ID")


def _entry(entry):
    if not isinstance(entry, dict) or entry.get("kind") not in KINDS or not isinstance(entry.get("name"), str) or not entry.get("file"):
        raise ValueError("Invalid project item")
    _id(entry.get("id"))
    for name in library._files_of(entry):
        if len(_path(name).parts) != 1 or name.casefold() in ("item.json", "index.json"):
            raise ValueError("Library files must have simple filenames")
    if entry.get("folder"):
        _path(entry["folder"])
    if "group" in entry:
        _id(entry["group"])
    if "group_order" in entry and (type(entry["group_order"]) is not int or entry["group_order"] < 0):
        raise ValueError("Invalid list member order")


def _exported(entries, include_reads):
    """The entries as a ZIP holds them: a reads item without its FASTQ files unless asked (the report goes)."""
    entries = copy.deepcopy(entries)
    for entry in entries:
        if entry["kind"] == "reads" and not include_reads and entry.get("read_files"):
            entry["read_files"], entry["reads_removed"] = [], True
    return entries


@library.writes
def _capture(include_history=False, include_trash=False, include_reads=False):
    """Capture the index and file identities; expensive copying happens outside the lock."""
    entries = _exported(library.load(), include_reads)
    manifest = {"format": FORMAT, "version": VERSION, "created": time.time(),
                "entries": entries, "folders": library.folders(), "trash": [], "files": {},
                "includes_history": include_history, "includes_trash": include_trash,
                "excluded": ["external source files and reads", "settings and credentials", "tools and databases", "job logs"]}
    files = {}

    def collect(path, name):
        _path(name)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe project file: {name}")
        files[name] = path

    def tree(folder, prefix):
        if folder.is_dir():
            for path in folder.rglob("*"):
                if path.is_file():
                    if path.name == "index.json" or re.fullmatch("[0-9a-f]{64}\\.gz", path.name):
                        collect(path, f"{prefix}/{path.relative_to(folder).as_posix()}")

    for entry in entries:
        _entry(entry)
        for name in library._files_of(entry):
            collect(library.library_dir() / name, f"library/{name}")
        if include_history:
            for owner in (entry["id"], entry["id"] + "_figure", entry["id"] + "_variants"):
                tree(history.directory(owner), f"history/{owner}")
    if include_trash:
        for folder in sorted(library.trash_dir().glob("*")):
            if not folder.is_dir():
                continue
            meta = json.loads((folder / "item.json").read_text(encoding="utf-8"))
            _id(meta["id"])
            _entry(meta["entry"])
            manifest["trash"].append(meta)
            for name in library._files_of(meta["entry"]):
                collect(folder / name, f"trash/{meta['id']}/{name}")
            if include_history:
                for part in ("history", "figure-history", "variants-history"):
                    tree(folder / part, f"trash/{meta['id']}/{part}")
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files.values()) > MAX_BYTES:
        raise ValueError("Project exceeds the ZIP size or file-count limit")
    return manifest, files, {name: _signature(path) for name, path in files.items()}


def transfer_root():
    root = config.APP_DIR / "transfers"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _signature(path):
    s = path.stat()
    return s.st_size, s.st_mtime_ns, s.st_ino


def _tick(check):
    if check:
        check()


def _copy(src, dst, check=None):
    with src.open("rb") as source, dst.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            _tick(check)
            target.write(chunk)


def export_zip(target, include_history=False, include_trash=False, check=None, progress=None, include_reads=False):
    """Optimistic saved snapshot: reject concurrent changes rather than block writers."""
    manifest, files, signatures = _capture(include_history, include_trash, include_reads)
    with tempfile.TemporaryDirectory(prefix="export-", dir=transfer_root()) as tmp:
        snapshot = Path(tmp)
        for n, (name, path) in enumerate(files.items()):
            _tick(check)
            if progress:
                progress(0, f"Copying {name}", n / max(1, len(files)))
            dst = snapshot / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                _copy(path, dst, check)
            except FileNotFoundError as exc:
                raise ValueError("The library changed during export; export again") from exc
        with library._writing:
            try:
                changed = _exported(library.load(), include_reads) != manifest["entries"] or library.folders() != manifest["folders"]
                changed |= any(_signature(path) != signatures[name] for name, path in files.items())
            except FileNotFoundError:
                changed = True
            if changed:
                raise ValueError("The library changed during export; export again")
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for n, name in enumerate(files):
                _tick(check)
                if progress:
                    progress(1, f"Compressing {name}", n / max(1, len(files)))
                path = snapshot / name
                if not name.startswith("library/") and path.suffix == ".gz":
                    _verify_blob(path, path.stem, check)
                manifest["files"][name] = {"size": path.stat().st_size, "sha256": _hash(path)}
                with path.open("rb") as src, archive.open(name, "w", force_zip64=True) as dst:
                    while chunk := src.read(1024 * 1024):
                        _tick(check)
                        dst.write(chunk)
            data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            if len(data) > 16 * 2**20:
                raise ValueError("Project manifest exceeds 16 MiB")
            archive.writestr("project.json", data)
    return {"items": len(manifest["entries"]), "files": len(files)}


def validate_zip(source, staging, check=None, progress=None):
    """Validate and stream to a private staging directory, without extractall()."""
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > MAX_FILES + 1 or sum(i.file_size for i in infos) > MAX_BYTES:
                raise ValueError("Project exceeds the ZIP size or file-count limit")
            if len({n.casefold() for n in names}) != len(names):
                raise ValueError("Duplicate project paths")
            for info in infos:
                _path(info.filename)
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise ValueError("Directories, links and encrypted entries are not supported")
            if "project.json" not in names or archive.getinfo("project.json").file_size > 16 * 2**20:
                raise ValueError("Missing or oversized project manifest")
            manifest = json.loads(archive.read("project.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
                raise ValueError("Unsupported Cladia project format/version")
            manifest.setdefault("excluded", ["external source files and reads", "settings and credentials", "tools and databases", "job logs"])
            entries, trash = manifest["entries"], manifest["trash"]
            if not isinstance(entries, list) or not isinstance(trash, list) or not isinstance(manifest["folders"], list):
                raise ValueError("Invalid project manifest")
            ids, required, history_roots = set(), set(), []
            for entry in entries:
                _entry(entry)
                if entry["id"] in ids:
                    raise ValueError("Duplicate project item IDs")
                ids.add(entry["id"])
                required.update(f"library/{n}" for n in library._files_of(entry))
                history_roots.extend([f"history/{entry['id']}/", f"history/{entry['id']}_figure/",
                                      f"history/{entry['id']}_variants/"])
            tids = set()
            for meta in trash:
                _id(meta["id"])
                _entry(meta["entry"])
                if meta["id"] in tids:
                    raise ValueError("Duplicate Trash IDs")
                tids.add(meta["id"])
                required.update(f"trash/{meta['id']}/{n}" for n in library._files_of(meta["entry"]))
                history_roots.extend([f"trash/{meta['id']}/history/", f"trash/{meta['id']}/figure-history/",
                                      f"trash/{meta['id']}/variants-history/"])
            for folder in manifest["folders"]:
                _path(folder)
            expected = manifest["files"]
            if not isinstance(expected, dict) or set(names) != {*expected, "project.json"} or not required <= expected.keys():
                raise ValueError("Project files do not match its manifest")
            for n, (name, spec) in enumerate(expected.items()):
                _tick(check)
                if progress:
                    progress(0, f"Checking {name}", n / max(1, len(expected)))
                if name not in required and not any(name.startswith(root) for root in history_roots):
                    raise ValueError(f"Unregistered project file: {name}")
                if name not in required:
                    root = next(root for root in history_roots if name.startswith(root))
                    relative = name[len(root):]
                    if not re.fullmatch(r"(?:records/[0-9a-f]{16}/)?(?:index\.json|[0-9a-f]{64}\.gz)", relative):
                        raise ValueError("Invalid saved-version path")
                info = archive.getinfo(name)
                if info.file_size != spec["size"]:
                    raise ValueError(f"Incorrect project file size: {name}")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        _tick(check)
                        dst.write(chunk)
                if _hash(target) != spec["sha256"]:
                    raise ValueError(f"Project checksum failed: {name}")
            _validate_histories(staging, check)
            for entry in entries:
                library.check_file_content(entry, staging / "library")
            for meta in trash:
                library.check_file_content(meta["entry"], staging / "trash" / meta["id"])
            return manifest
    except (zipfile.BadZipFile, KeyError, TypeError, UnicodeError, json.JSONDecodeError, RuntimeError, EOFError) as exc:
        raise ValueError(f"Invalid project archive: {exc}") from exc


def _validate_histories(staging, check=None):
    for index in staging.rglob("index.json"):
        if "library" in index.relative_to(staging).parts:
            continue
        data = json.loads(index.read_text(encoding="utf-8"))
        versions, current = data["versions"], data["current"]
        if not isinstance(versions, list) or not isinstance(current, int) or not -1 <= current < len(versions):
            raise ValueError("Invalid saved-version index")
        for version in versions:
            digest = version["hash"]
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("Invalid saved-version hash")
            blob = index.parent / (digest + ".gz")
            _verify_blob(blob, digest, check)


def _verify_blob(blob, digest, check=None):
    with gzip.open(blob, "rb") as stream:
        h, size = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            _tick(check)
            size += len(chunk)
            if size > MAX_HISTORY_BLOB:
                raise ValueError("Saved version exceeds the project size limit; export without saved versions")
            h.update(chunk)
    if h.hexdigest() != digest:
        raise ValueError("Saved-version checksum failed")


def _rewrite_payloads(staging, ids, check=None):
    """Remap links inside result sidecars and history snapshots, rehashing changed blobs."""
    for path in staging.rglob("*.json"):
        _tick(check)
        if path.name in ("index.json", "item.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        mapped = _links(data, ids)
        if mapped != data:
            path.write_text(json.dumps(mapped), encoding="utf-8")
    for index in staging.rglob("index.json"):
        data = json.loads(index.read_text(encoding="utf-8"))
        for version in data["versions"]:
            _tick(check)
            blob = index.parent / (version["hash"] + ".gz")
            # GenBank/FASTA/Newick need no remapping. Inspect a bounded prefix;
            # never allocate their entire uncompressed snapshot just to reject JSON.
            with gzip.open(blob, "rb") as stream:
                prefix = stream.read(4096)
                if prefix.lstrip()[:1] not in (b"{", b"["):
                    continue
                raw = prefix + stream.read(MAX_HISTORY_BLOB)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                continue  # GenBank, FASTA and Newick snapshots have no library links
            mapped = _links(value, ids)
            if mapped != value:
                raw = json.dumps(mapped).encode()
                digest = hashlib.sha256(raw).hexdigest()
                target = index.parent / (digest + ".gz")
                target.write_bytes(gzip.compress(raw, compresslevel=history.LEVEL))
                version.update(hash=digest, size=target.stat().st_size)
        index.write_text(json.dumps(data), encoding="utf-8")


def _links(value, ids, key=None):
    """Remap library references, preserving biological names, labels and accessions."""
    if isinstance(value, dict):
        return {k: _links(v, ids, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_links(v, ids, key) for v in value]
    if isinstance(value, str) and key in LINK_KEYS:
        if value in ids:
            return ids[value]
        base, sep, suffix = value.rpartition("-")
        if sep and base in ids and suffix.isdigit():
            return ids[base] + "-" + suffix
    return value


def import_zip(source, name="Imported project", check=None, progress=None):
    """Import all saved items into a fresh folder. Existing files are never replaced."""
    library.ensure_folders()
    name = name.strip() or "Imported project"
    if len(_path(name).parts) != 1:
        raise ValueError("Choose a single project folder name")
    staging = Path(tempfile.mkdtemp(prefix="import-", dir=transfer_root()))
    try:
        manifest = validate_zip(source, staging, check, progress)
        folder, n = name, 2
        existing = library.folders()
        while folder in existing:
            folder, n = f"{name} {n}", n + 1
        oldids = {e["id"] for e in manifest["entries"]} | {m["entry"]["id"] for m in manifest["trash"]}
        ids = {i: uuid.uuid4().hex[:12] for i in oldids}
        if set(ids.values()) & {e["id"] for e in library.load()}:
            raise ValueError("Project ID collision; retry the import")
        if progress:
            progress(1, "Preparing item links and saved versions", None)
        _rewrite_payloads(staging, ids, check)
        filemap = {p.name: uuid.uuid4().hex[:12] + "_" + p.name[-180:] for p in (staging / "library").glob("*")}
        # A BAM's index must retain the conventional '<bam>.bai' filename.
        for e in manifest["entries"]:
            if e.get("bam"):
                filemap[e["bam"] + ".bai"] = filemap[e["bam"]] + ".bai"
        moves, added, trash_moves = [], [], []

        def entry_copy(original, rename_files=True):
            entry = _links(copy.deepcopy(original), ids)
            group = entry.pop("group", None)
            if group in ids:
                entry["group"] = ids[group]
            else:
                entry.pop("group_order", None)
            entry["folder"] = folder + ("/" + original["folder"] if original.get("folder") else "")
            if rename_files:
                for key in library.FILE_FIELDS:
                    if entry.get(key):
                        entry[key] = filemap[entry[key]]
            # A colleague's original path must never become a write-back destination.
            if "original" in entry:
                entry["imported_original"] = entry.pop("original")
            return entry

        for original in manifest["entries"]:
            entry = entry_copy(original)
            added.append(entry)
            for filename in library._files_of(original):
                src = staging / "library" / filename
                dst = library.library_dir() / filemap[filename]
                if (src, dst) not in moves:
                    moves.append((src, dst))
            for old, new in ((original["id"], entry["id"]), (original["id"] + "_figure", entry["id"] + "_figure"),
                             (original["id"] + "_variants", entry["id"] + "_variants")):
                src = staging / "history" / old
                if src.is_dir():
                    moves.append((src, history.directory(new)))
        for meta in manifest["trash"]:
            newmeta = _links(copy.deepcopy(meta), ids)
            newmeta["id"] = uuid.uuid4().hex[:12]
            newmeta["entry"] = entry_copy(meta["entry"], False)
            src = staging / "trash" / meta["id"]
            (src / "item.json").write_text(json.dumps(newmeta), encoding="utf-8")
            trash_moves.append((src, library.trash_dir() / newmeta["id"]))
        planned = [{"path": dst.relative_to(config.DATA_DIR).as_posix(), "files": _inventory(src, check)}
                   for src, dst in moves + trash_moves]
        _tick(check)
        with library._writing:
            # Other windows may have changed folders/items while validation ran.
            existing = library.folders()
            folder, n = name, 2
            while folder in existing:
                folder, n = f"{name} {n}", n + 1
            for entry, original in zip(added, manifest["entries"]):
                entry["folder"] = folder + ("/" + original["folder"] if original.get("folder") else "")
            for (src, dst), meta in zip(trash_moves, manifest["trash"]):
                itemfile = src / "item.json"
                saved = json.loads(itemfile.read_text(encoding="utf-8"))
                saved["entry"]["folder"] = folder + ("/" + meta["entry"]["folder"] if meta["entry"].get("folder") else "")
                itemfile.write_text(json.dumps(saved), encoding="utf-8")
                planned[len(moves) + trash_moves.index((src, dst))]["files"]["item.json"] = _hash(itemfile)
            if set(ids.values()) & {e["id"] for e in library.load()} or any(dst.exists() for src, dst in moves + trash_moves):
                raise ValueError("Project destination already exists; retry the import")
            journal = {"format": "Cladia import transaction", "ids": [e["id"] for e in added],
                       "folder": folder, "targets": planned}
            _journal(staging, journal)
            published = []
            try:
                if progress:
                    progress(2, "Adding verified items to the library", None)
                for src, dst in moves + trash_moves:
                    _tick(check)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.rename(dst)
                    published.append((src, dst))
                _tick(check)
                folders = sorted({*existing, folder, *(folder + "/" + f for f in manifest["folders"]),
                                  *(e["folder"] for e in added)})
                library._save(library.load() + added, folders)
            except Exception as exc:
                errors = []
                for src, dst in reversed(published):
                    try:
                        dst.rename(src)
                    except OSError as rollback_error:
                        errors.append(f"{dst}: {rollback_error}")
                if errors:
                    raise OSError(f"Project import failed ({exc}); recovery journal retained in {staging}: "
                                  + "; ".join(errors)) from exc
                (staging / "journal.json").unlink(missing_ok=True)
                raise
            # The index is the commit marker; a crash here is recognized at startup.
            try:
                (staging / "journal.json").unlink(missing_ok=True)
            except OSError:
                pass  # index committed; startup will recognize and finish cleanup
        library.migrate_sidecar_figures()  # a project from before figures were items: its figures become items
        return {"ids": [e["id"] for e in added], "folder": folder, "trash": len(trash_moves)}
    finally:
        if not (staging / "journal.json").exists():
            shutil.rmtree(staging, ignore_errors=True)


def _inventory(path, check=None):
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    result = {}
    for p in files:
        _tick(check)
        if p.is_symlink():
            raise ValueError("A transfer file became a symbolic link")
        result[p.relative_to(path).as_posix() if path.is_dir() else ""] = _hash(p)
    return result


def _journal(staging, data):
    tmp = staging / "journal.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(staging / "journal.json")


def _recovery_target(relative):
    parts = _path(relative).parts
    if not (len(parts) == 2 and parts[0] == "library" or
            len(parts) >= 3 and parts[:2] in ((".cladia", "history"), (".cladia", "trash"))):
        raise ValueError("Invalid recovery destination")
    target = config.DATA_DIR.joinpath(*parts)
    if not target.resolve().is_relative_to(config.DATA_DIR.resolve()) or target.is_symlink():
        raise ValueError("Unsafe recovery destination")
    return target


def _recover_export(staging, data):
    target = Path(data["staging"])
    destination = Path(data["destination"])
    if not (target.is_absolute() and re.fullmatch(r"\.cladia-export-[0-9a-f]{32}\.tmp", target.name)
            and target.parent.resolve() == destination.parent.resolve()) or target.is_symlink():
        raise ValueError("Unsafe export recovery destination")
    if target.exists():
        # An interrupted copy must be a byte-for-byte prefix of our own ZIP.
        # Preserve it for inspection if someone modified it after interruption.
        with target.open("rb") as partial, (staging / "project.zip").open("rb") as original:
            while chunk := partial.read(1024 * 1024):
                if original.read(len(chunk)) != chunk:
                    raise ValueError("Export staging file changed; left untouched")
        target.unlink()


@library.writes
def recover_transfers():
    """Called once at startup, before accepting requests or starting jobs."""
    issues, removed = [], 0
    for staging in transfer_root().iterdir():
        if not staging.is_dir() or staging.is_symlink() or not re.fullmatch(r"(?:import|export|preview)-[a-zA-Z0-9_]+", staging.name):
            continue
        journal = staging / "journal.json"
        try:
            if journal.exists():
                data = json.loads(journal.read_text(encoding="utf-8"))
                if data.get("format") == "Cladia export transaction":
                    _recover_export(staging, data)
                    shutil.rmtree(staging)
                    removed += 1
                    continue
                if data.get("format") != "Cladia import transaction":
                    raise ValueError("Unknown recovery journal")
                committed = set(data["ids"]) <= {e["id"] for e in library.load()} and data["folder"] in library.folders()
                if not committed:
                    # Validate every destination before deleting any leftover.
                    targets = [_recovery_target(t["path"]) for t in data["targets"]]
                    registered = {library.library_dir() / f for e in library.load() for f in library._files_of(e)}
                    if any(t in registered for t in targets):
                        raise ValueError("Recovery destination is registered; left untouched")
                    for target, spec in zip(targets, data["targets"]):
                        if target.exists() and _inventory(target) != spec["files"]:
                            raise ValueError(f"Recovery file changed; left untouched: {target}")
                    for target in targets:
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink(missing_ok=True)
            shutil.rmtree(staging)
            removed += 1
        except (ValueError, OSError, KeyError, TypeError) as exc:
            issues.append(f"{staging.name}: {exc}")
    result = {"removed": removed, "issues": issues}
    (config.APP_DIR / "transfer-recovery.json").write_text(json.dumps(result), encoding="utf-8")
    return result
