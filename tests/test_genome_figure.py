"""Figure items: genome maps, their history, list members as sources, and the migration of
figures that were kept beside their source."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cladia import config, library


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    return TestClient(app)


def test_genome_figure_settings_duplicate_trash_and_history(client):
    item_id = client.post("/api/library/upload", json={"filename": "genome.fa", "text": ">genome\nACGTACGT\n"}).json()["ids"][0]
    figure = client.post("/api/figures", json={"source": item_id}).json()["id"]
    data = client.get(f"/api/figure/{figure}").json()
    assert (data["of"], data["source"], data["settings"], data["missing"]) == ("genome", item_id, {}, False)
    settings = {"shape": "linear", "gc": {"show": True}, "featureEdits": {"CDS|cox1|10|100": {"color": "#123456"}}}
    original = Path(library.get_item(item_id)["path"]).read_bytes()
    assert client.put(f"/api/figure/{figure}", json=settings).status_code == 200
    assert Path(library.get_item(item_id)["path"]).read_bytes() == original  # the sequence itself is never touched
    assert client.get(f"/api/figure/{figure}").json()["settings"] == settings
    assert len(client.get(f"/api/figure/{figure}/history").json()["versions"]) == 2
    copy = library.duplicate(library.get_item(figure))  # a second figure of the same genome
    assert client.get(f"/api/figure/{copy}").json()["settings"] == settings
    assert library.get_item(copy)["source"] == item_id and library.get_item(item_id)["figures"] == 2
    library.remove([figure], trash=True)
    restored = library.restore([library.trash_items()[0]["id"]])[0]
    assert client.get(f"/api/figure/{restored}").json()["settings"] == settings
    timeline = client.get(f"/api/figure/{restored}/history").json()
    request = {"index": 0, "expected_hash": timeline["versions"][timeline["current"]]["hash"],
               "target_hash": timeline["versions"][0]["hash"], "expected_current": timeline["current"], "expected_count": 2}
    assert client.post(f"/api/figure/{restored}/history", json=request).status_code == 200
    assert client.get(f"/api/figure/{restored}").json()["settings"] == {}
    assert library.check_integrity()["issues"] == []


def test_figure_of_a_selection_keeps_its_part_and_first_settings(client):
    item_id = client.post("/api/library/upload", json={"filename": "g.fa", "text": ">g\n" + "ACGT" * 50 + "\n"}).json()["ids"][0]
    figure = client.post("/api/figures", json={"source": item_id, "part": {"start": 11, "end": 60}, "name": "g 11–60",
                                               "settings": {"shape": "linear", "range": {"from": 11, "to": 60}}}).json()["id"]
    data = client.get(f"/api/figure/{figure}").json()
    assert data["part"] == {"start": 11, "end": 60} and data["settings"]["range"] == {"from": 11, "to": 60}
    assert library.get_item(figure)["name"] == "g 11–60"
    client.put(f"/api/figure/{figure}", json={"shape": "linear", "range": {"from": 20, "to": 60}})
    assert client.get(f"/api/figure/{figure}").json()["part"] == {"start": 11, "end": 60}  # saving keeps what it was made of


def test_list_members_have_figures_proteins_and_traces_do_not(client):
    lst = client.post("/api/library/upload", json={"filename": "list.fa", "text": ">a\nACGT\n>b\nACGA\n"}).json()["ids"][0]
    figure = client.post("/api/figures", json={"source": f"{lst}-1"}).json()["id"]
    entry = library.get_item(figure)
    assert (entry["source"], entry["source_record"]) == (lst, "b")  # the list and the record's name, not the member id
    assert client.get(f"/api/figure/{figure}").json()["source"] == f"{lst}-1"
    library.remove([f"{lst}-0"])  # the members are renumbered: b is now -0
    assert client.get(f"/api/figure/{figure}").json()["source"] == f"{lst}-0"
    library.remove([f"{lst}-0"])
    assert client.get(f"/api/figure/{figure}").json()["missing"]
    protein = client.post("/api/library/upload", json={"filename": "protein.fa", "text": ">protein\nMEKWQILP\n"}).json()["ids"][0]
    assert client.post("/api/figures", json={"source": protein}).status_code == 400
    entries = library.load()
    sequence = next(e for e in entries if e["kind"] == "sequence")
    sequence["trace"] = "test.trace.json"
    library._save(entries)
    assert client.get(f"/api/figure/{sequence['id']}").status_code == 400


def test_sidecar_figures_become_items_with_their_history(client):
    tree_file = library.library_dir() / "t.nwk"
    tree_file.write_text("((a:1,b:1):1,c:1);")
    tree = library._register(tree_file, "tree", {"name": "t"})
    side = library.library_dir() / "t.figure.json"
    owner = f"{tree}_figure"  # how figures beside their source kept their versions
    for settings in ({}, {"fontSize": 9}):
        side.write_text(json.dumps(settings))
        library.history.checkpoint(owner, side)
    entries = library.load()
    entries[0]["figure"] = side.name
    library._save(entries)
    library.migrate_sidecar_figures()
    (figure,) = [it for it in library.items() if it["kind"] == "figure"]
    assert (figure["name"], figure["source"]) == ("t figure", tree)
    assert library.figure_data(figure)["settings"] == {"fontSize": 9}
    assert not side.exists() and not library.get_item(tree).get("figure")
    versions = library.figure_versions(figure)["versions"]
    assert len(versions) == 3  # the two old ones, then the item's own file
    library.history.restore(figure["id"], Path(figure["path"]), 0)  # an old version (settings alone) still reads
    assert library.figure_data(library.get_item(figure["id"]))["settings"] == {}
    library.migrate_sidecar_figures()  # again: nothing more to do
    assert len([it for it in library.items() if it["kind"] == "figure"]) == 1