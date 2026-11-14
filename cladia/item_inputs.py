"""Item resolution and validation errors independent of HTTP."""
from . import library


class InputError(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _item_in(items_by_id, item_id):
    """An item from an already-listed library (one pass for requests that look up many)."""
    if not isinstance(item_id, str):
        raise InputError(400, "Invalid library item ID")
    item = items_by_id.get(item_id)
    if item is None:
        raise InputError(404, f"Not in the list: {item_id}")
    if item["missing"]:
        raise InputError(404, f"Cladia's copy is gone (deleted outside Cladia?): {item['path']}")
    return item

def _item(item_id):
    return _item_in(library.item_map(), item_id)

def _sequences(ids, items_by_id=None):
    """Sequence items for the given ids; a sequence list stands for all its sequences."""
    by_id = library.item_map() if items_by_id is None else items_by_id
    every = by_id.values()
    result, seen = [], set()
    for item_id in ids:
        for child in library.children(_item_in(by_id, item_id), every):
            if child["kind"] == "sequence" and child["id"] not in seen:
                seen.add(child["id"])
                result.append(child)
    return result
