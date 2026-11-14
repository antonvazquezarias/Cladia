"""Folder mutations reject stale targets and implicit merges before writing."""
import pytest
from fastapi.testclient import TestClient
from cladia import config, library
from cladia.server import app


@pytest.fixture
def folder_library(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    library.ensure_folders()
    for name in ["Parent", "Other"]:
        library.add_folder(name)
    library.add_folder("Child", "Parent")
    return TestClient(app)


def test_new_folder_rejects_parent_renamed_in_another_window(folder_library):
    client = folder_library
    client.post("/api/folders/rename", json={"name": "Parent", "new": "Renamed"})
    before = library._items_file().read_bytes()
    response = client.post("/api/folders", json={"name": "New", "parent": "Parent"})
    assert response.status_code == 400 and "parent folder no longer exists" in response.json()["detail"]
    assert library._items_file().read_bytes() == before
    assert library.folders() == ["Other", "Renamed", "Renamed/Child"]


@pytest.mark.parametrize("existing", ["Child", "child"])
def test_delete_folder_rejects_child_name_collision_without_writing(folder_library, existing):
    client = folder_library
    library.add_folder(existing)
    for name, folder in [("nested", "Parent/Child"), ("existing", existing)]:
        library.import_text(name + ".fa", f">{name}\nACGT\n", folder)
    before = library._items_file().read_bytes()
    response = client.post("/api/folders/delete", json={"name": "Parent"})
    assert response.status_code == 400 and "would merge" in response.json()["detail"]
    assert library._items_file().read_bytes() == before
    assert len(library.load()) == 2


def test_delete_parent_preserves_nested_paths_and_items(folder_library):
    client = folder_library
    library.add_folder("Deep", "Parent/Child")
    ids, _ = library.import_text("nested.fa", ">nested\nACGT\n", "Parent/Child/Deep")
    assert client.post("/api/folders/delete", json={"name": "Parent"}).status_code == 200
    assert library.folders() == ["Child", "Child/Deep", "Other"]
    assert library.get_item(ids[0])["folder"] == "Child/Deep"


@pytest.mark.parametrize("route,body", [
    ("rename", {"name": "Gone", "new": "Recreated"}),
    ("move", {"name": "Gone", "parent": "Other"}),
    ("move", {"name": "Parent", "parent": "Gone"}),
    ("delete", {"name": "Gone"}),
])
def test_stale_folder_mutations_do_not_change_library(folder_library, route, body):
    before = library._items_file().read_bytes()
    response = folder_library.post(f"/api/folders/{route}", json=body)
    assert response.status_code == 400 and "no longer exists" in response.json()["detail"]
    assert library._items_file().read_bytes() == before


def test_item_move_rejects_missing_destination(folder_library):
    ids, _ = library.import_text("item.fa", ">item\nACGT\n", "Parent")
    before = library._items_file().read_bytes()
    response = folder_library.post("/api/library/move", json={"ids": ids, "folder": "Gone"})
    assert response.status_code == 400
    assert library._items_file().read_bytes() == before
