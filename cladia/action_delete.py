"""Session-only undo for deletions, backed by Trash and list versions."""
import hashlib
import json
from pathlib import Path

from . import history, library


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _trash_signature(folder):
    return {p.relative_to(folder).as_posix(): _digest(p)
            for p in folder.rglob("*") if p.is_file()}


def _file_signatures(entry):
    return {name: _digest(library.library_dir() / name) if (library.library_dir() / name).is_file() else None
            for name in library._files_of(entry)}


def _trash_dirs():
    root = library.trash_dir()
    return {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()


def perform(ids):
    """Delete while recording enough information to undo it, under library's lock."""
    all_items = library.item_map()
    selected = [all_items[i] for i in ids if i in all_items]
    whole = {item["id"] for item in selected if not item["parent"]}
    by_parent = {}
    for item in selected:
        if item["parent"] and item["parent"] not in whole:
            by_parent.setdefault(item["parent"], set()).add(item["record"])
    partial = {}
    for owner, members in by_parent.items():
        parent = all_items[owner]
        if len(members) < len(library.record_index(parent["path"])):
            history.checkpoint(owner, parent["path"])
            partial[owner] = {"before": history.current_hash(owner)}  # by content: old versions get pruned
    owner_ids = whole | set(by_parent)
    original_entries = {e["id"]: dict(e) for e in library.load() if e["id"] in owner_ids}
    signatures = {i: _file_signatures(e) for i, e in original_entries.items()}
    before_trash = _trash_dirs()
    library.remove(ids, trash=True)
    new_trash = _trash_dirs() - before_trash
    for owner, version in partial.items():
        parent = library.get_item(owner)
        history.checkpoint(owner, parent["path"])
        version["after"] = history.current_hash(owner)
    trash = {}
    for tid in new_trash:
        folder = library.trash_dir() / tid
        meta = json.loads((folder / "item.json").read_text(encoding="utf-8"))
        trash[tid] = {"partial": meta.get("from_id"), "entry": meta["entry"],
                      "signature": _trash_signature(folder)}
    return {"kind": "delete", "ids": list(ids), "entries": original_entries,
            "signatures": signatures, "partial": partial, "trash": trash}


def validate_redo(action):
    entries = {e["id"]: e for e in library.load()}
    for item_id, expected in action["entries"].items():
        if entries.get(item_id) != expected or _file_signatures(expected) != action["signatures"][item_id]:
            raise ValueError("A restored item changed; redo would delete newer work")


def capture(ids):
    """Record current item identities and file contents for conflict checking."""
    entries = {e["id"]: dict(e) for e in library.load() if e["id"] in ids}
    if set(entries) != set(ids):
        raise ValueError("An item is no longer in the library")
    return {"entries": entries, "signatures": {i: _file_signatures(e) for i, e in entries.items()}}


def validate_undo(action):
    """Check every dependency before starting a restore."""
    existing = {e["id"]: e for e in library.load()}
    folders = set(library.folders())
    whole_trash, partial_trash = [], []
    for tid, info in action["trash"].items():
        folder = library.trash_dir() / tid
        if not folder.is_dir() or _trash_signature(folder) != info["signature"]:
            raise ValueError("The item in the trash was removed or changed; the deletion can't be undone")
        if info["partial"]:
            partial_trash.append(tid)
            continue
        entry = info["entry"]
        if entry["id"] in existing or any((library.library_dir() / n).exists() for n in library._files_of(entry)):
            raise ValueError("An item or filename has been reused; the deletion can't be undone safely")
        if entry.get("folder") and entry["folder"] not in folders:
            raise ValueError("The original folder no longer exists")
        if (folder / "history").exists() and history.directory(entry["id"]).exists():
            raise ValueError("The item history has been replaced")
        if (folder / "figure-history").exists() and history.directory(f"{entry["id"]}_figure").exists():
            raise ValueError("The figure history has been replaced")
        whole_trash.append(tid)
    for owner, indices in action["partial"].items():
        entry = existing.get(owner)
        if entry != action["entries"][owner]:
            raise ValueError("The sequence list changed; the deletion can't be undone safely")
        if history.current_hash(owner) != indices["after"]:
            raise ValueError("The sequence list history changed; the deletion can't be undone safely")
        if _digest(library.library_dir() / entry["file"]) != indices["after"]:
            raise ValueError("The sequence list changed; the deletion can't be undone safely")
        before = history.index_of(owner, indices["before"])
        if before is None:
            raise ValueError("The list as it was is no longer in its saved versions (only the latest are kept)")
        history.data(owner, before)  # checked (damaged: refused) before touching any items
    return whole_trash, partial_trash


def undo(action):
    """Restore whole items and list members after validating every dependency."""
    whole_trash, partial_trash = validate_undo(action)
    if whole_trash:
        library.restore(whole_trash)
    for owner, indices in action["partial"].items():
        library.restore_version(library.get_item(owner), history.index_of(owner, indices["before"]), whole=True)
    if partial_trash:
        library.empty_trash(partial_trash)  # The deleted records are back in their original lists.
    return action["ids"]
