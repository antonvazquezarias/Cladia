"""Shared window context, edit locks and library action history."""
from .item_inputs import InputError, _item_in, _item, _sequences
import time
from contextvars import ContextVar
from pydantic import BaseModel
from . import action_delete, library


class ResultDestination(BaseModel):
    folder: str | None = None  # omitted: common source folder; null: Library root


def _result_folder(req, items):
    return library.result_folder(items, req.folder if "folder" in req.model_fields_set else library.DEFAULT_FOLDER)


LOCK_SECONDS = 15


LOCKS = {}  # item id -> {"window", "until", "dirty" (it has unsaved changes)}


_window = ContextVar("window", default=None)


LIBRARY_ACTIONS = {}  # window id -> {undo, redo}; session only


def _action_stack():
    window = _window.get()
    return LIBRARY_ACTIONS.setdefault(window, {"undo": [], "redo": []}) if window else None


def _record_action(action):
    stack = _action_stack()
    if stack:
        stack["undo"].append(action)
        stack["redo"].clear()


def _record_created(ids):
    """New items (pasted, extracted, duplicated): undo moves them to the trash, redo brings them back."""
    if ids and _action_stack() is not None:
        _record_action({"kind": "paste", "output": action_delete.capture(ids), "output_ids": list(ids)})
    elif ids:
        _clear_actions()


def _clear_actions():
    stack = _action_stack()
    if stack:
        stack["undo"].clear()
        stack["redo"].clear()


def _held_elsewhere(item_id):
    """The lock on this item, or on a sequence of this list (or this sequence's
    list), held by another window that's still alive."""
    now = time.time()
    for key, lock in list(LOCKS.items()):
        if lock["until"] < now:
            del LOCKS[key]
        elif lock["window"] != _window.get() and (
                key == item_id or key.startswith(f"{item_id}-") or item_id.startswith(f"{key}-")):
            return True
    return False


def _free(*item_ids):
    """Refuses the request if another window is editing any of these items."""
    busy = [i for i in item_ids if i and _held_elsewhere(i)]
    if busy:
        by_id = library.item_map()  # once: a save may name many items
        names = ", ".join(by_id[i]["label"] for i in busy if i in by_id)
        raise InputError(409, f"{names or 'This'} is being edited in another Cladia window")


def unsaved_windows():
    """The windows that have unsaved changes (for closing: __main__.py)."""
    return {lock["window"] for lock in LOCKS.values() if lock["dirty"] and lock["until"] >= time.time()}


class IdsRequest(BaseModel):
    ids: list[str]


AFTER_SETTINGS = []  # called when the settings change (the app window: the text size, __main__)


class MetadataRequest(BaseModel):
    description: str = ""
    organism: str = ""
    accession: str = ""
    strain: str = ""
    isolate: str = ""
    country: str | None = None
    collection_date: str | None = None
    host: str | None = None
    organelle: str | None = None
