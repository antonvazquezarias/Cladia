"""Window edit-lock HTTP endpoints."""
import time
from fastapi import APIRouter
from pydantic import BaseModel
from . import library, request_context as context
from .request_context import _free

router = APIRouter()

class LockRequest(BaseModel):
    window: str
    ids: list[str] = []
    dirty: bool = False

@router.post("/api/locks")
def take_locks(req: LockRequest):
    """Takes (or renews) the locks on these items for a window, all or none."""
    context._window.set(req.window)
    now = time.time()
    if req.ids and all(context.LOCKS.get(i, {}).get("window") == req.window and context.LOCKS[i]["until"] >= now for i in req.ids):
        # a renewal (every 5 s): no need to wait for a slow library write, the items are this window's already
        for item_id in req.ids:
            context.LOCKS[item_id] = {"window": req.window, "until": now + context.LOCK_SECONDS, "dirty": req.dirty}
        return {"ok": True}
    with library._writing:  # taking them: not in the middle of a change to them
        _free(*req.ids)
        for item_id in req.ids:
            context.LOCKS[item_id] = {"window": req.window, "until": time.time() + context.LOCK_SECONDS, "dirty": req.dirty}
    return {"ok": True}

@router.post("/api/locks/release")
def release_locks(req: LockRequest):
    """A window's locks go (these, or all of them when none are named)."""
    for key, lock in list(context.LOCKS.items()):
        if lock["window"] == req.window and (not req.ids or key in req.ids):
            del context.LOCKS[key]
    return {"ok": True}
