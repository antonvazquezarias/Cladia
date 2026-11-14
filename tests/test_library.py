import cladia.request_context as http_request_context
import cladia.request_context as http_request_context
import cladia.execution as owner_execution
import cladia.tool_plugins.mauve as owner_mauve
"""The library (Cladia's own copies) and the API on top of it.

Run with:  python -m pytest tests
"""

import json
import shutil
import time
from io import StringIO
from pathlib import Path

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord
from fastapi.testclient import TestClient

from cladia import config, jobs, library
from cladia.genbank import read_records

DATA = Path(__file__).parent / "data"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Empty data folder, plus a separate folder with the user's own files."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    files = tmp_path / "my files"
    files.mkdir()
    shutil.copy(DATA / "synthetic_mt.gb", files / "synthetic_mt.gb")
    (files / "set.fasta").write_text(">strainA one\nACGTACGT\n>strainB two\nACGTTCGT\n")
    (files / "aln.fasta").write_text(">a\nAC-GT\n>b\nACCGT\n")
    from cladia.server import app
    return files, TestClient(app)


def _import(client, files, *names):
    r = client.post("/api/library/add", json={"paths": [str(files / n) for n in names]}).json()
    assert r["errors"] == []
    return {i["label"]: i for i in client.get("/api/library").json()}


def _snapshot(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir()}


def test_import_makes_copies(setup):
    files, client = setup
    before = _snapshot(files)
    items = _import(client, files, "synthetic_mt.gb", "set.fasta", "aln.fasta")
    assert {label: it["kind"] for label, it in items.items()} == {
        "Mt": "sequence", "set": "list", "set › strainA": "sequence", "set › strainB": "sequence",
        "aln": "alignment"}
    assert items["set"]["count"] == 2
    assert items["set › strainB"]["parent"] == items["set"]["id"]
    assert {Path(it["path"]).parent for it in items.values()} == {library.library_dir()}
    assert Path(items["set"]["path"]).suffix == ".gb"  # FASTA is stored as GenBank
    assert (library.library_dir() / "Mt.gb").read_bytes() == before["synthetic_mt.gb"]
    assert _snapshot(files) == before


def test_integrity_reports_missing_corrupt_and_unreferenced(setup):
    files, client = setup
    _import(client, files, "synthetic_mt.gb")
    bam = files / "read.bam"
    bam.write_bytes(b"bam")
    Path(str(bam) + ".bai").write_bytes(b"index")
    mapping_id = library.add_mapping({"summary": {}}, "mapping", bam)
    mapping = library.get_item(mapping_id)
    (library.library_dir() / (mapping["bam"] + ".bai")).unlink()
    Path(mapping["path"]).write_text("not json", encoding="utf-8")
    orphan = library.library_dir() / "orphan.dat"
    orphan.write_bytes(b"leave me")
    response = client.get("/api/library/check")
    assert response.status_code == 200
    result = response.json()
    assert result["checked"] == 2
    assert {issue["problem"].split(":")[0] for issue in result["issues"]} == {"File is missing", "Can't read the item"}
    assert result["unreferenced"] == ["orphan.dat"] and orphan.read_bytes() == b"leave me"


def test_saved_versions_restore_and_follow_trash(setup):
    files, client = setup
    item = _import(client, files, "synthetic_mt.gb")["Mt"]
    data = client.get(f"/api/sequences/{item['id']}").json()
    original = data["sequence"]
    data["sequence"] += "ACGT"
    assert client.put(f"/api/sequences/{item['id']}", json=data).status_code == 200
    versions = client.get(f"/api/library/{item['id']}/history").json()
    assert len(versions["versions"]) == 2 and versions["current"] == 1
    assert client.post(f"/api/library/{item['id']}/history", json={"index": 0}).status_code == 200
    assert client.get(f"/api/sequences/{item['id']}").json()["sequence"] == original
    assert client.post(f"/api/library/{item['id']}/history", json={"index": 1}).status_code == 200
    client.post("/api/library/remove", json={"ids": [item["id"]]})
    tid = client.get("/api/trash").json()[0]["id"]
    restored = client.post("/api/trash/restore", json={"ids": [tid]}).json()["ids"][0]
    assert len(client.get(f"/api/library/{restored}/history").json()["versions"]) == 2


def test_saved_version_refuses_external_changes(setup):
    files, client = setup
    item = _import(client, files, "synthetic_mt.gb")["Mt"]
    data = client.get(f"/api/sequences/{item['id']}").json()
    data["sequence"] += "ACGT"
    client.put(f"/api/sequences/{item['id']}", json=data)
    path = Path(item["path"])
    path.write_bytes(path.read_bytes() + b"external change")
    before = path.read_bytes()
    response = client.post(f"/api/library/{item['id']}/history", json={"index": 0})
    assert response.status_code == 409
    assert path.read_bytes() == before


def test_version_restore_rolls_back_file_when_index_write_fails(setup, monkeypatch):
    from cladia import history
    files, client = setup
    item = _import(client, files, "synthetic_mt.gb")["Mt"]
    data = client.get(f"/api/sequences/{item['id']}").json()
    data["sequence"] += "ACGT"
    client.put(f"/api/sequences/{item['id']}", json=data)
    path = Path(item["path"])
    before = path.read_bytes()
    monkeypatch.setattr(history, "_save", lambda *args: (_ for _ in ()).throw(OSError("index failed")))
    result = client.post(f"/api/library/{item['id']}/history", json={"index": 0})
    assert result.status_code == 409 and path.read_bytes() == before
    assert history.versions(item["id"])["current"] == 1


def test_tree_saved_versions_survive_reopen(setup):
    files, client = setup
    (files / "test.nwk").write_text("((A:1,B:1):1,C:1);", encoding="utf-8")
    item = _import(client, files, "test.nwk")["test"]
    assert client.put(f"/api/tree/{item['id']}/text", json={"text": "((A:2,B:1):1,C:1);"}).status_code == 200
    timeline = client.get(f"/api/library/{item['id']}/history").json()
    assert len(timeline["versions"]) == 2
    assert client.post(f"/api/library/{item['id']}/history", json={"index": 0}).status_code == 200
    assert "A:1" in Path(library.get_item(item["id"])["path"]).read_text(encoding="utf-8")


def test_figure_versions_restore_and_follow_trash(setup):
    files, client = setup
    (files / "figure.nwk").write_text("((A:1,B:1):1,C:1);", encoding="utf-8")
    item = _import(client, files, "figure.nwk")["figure"]
    url = f"/api/figure/{item['id']}"
    assert client.put(url, json={"font": 10}).status_code == 200
    assert client.put(url, json={"font": 10}).status_code == 200  # no duplicate state
    assert client.put(url, json={"font": 20}).status_code == 200
    timeline = client.get(url + "/history").json()
    assert len(timeline["versions"]) == 3 and timeline["current"] == 2
    request = lambda index, state: {"index": index, "expected_hash": state["versions"][state["current"]]["hash"],
                                    "target_hash": state["versions"][index]["hash"],
                                    "expected_current": state["current"], "expected_count": len(state["versions"])}
    assert client.post(url + "/history", json=request(1, timeline)).status_code == 200
    assert client.get(url).json()["settings"] == {"font": 10}
    # A stale history dialog cannot overwrite a subsequent change.
    assert client.post(url + "/history", json=request(0, timeline)).status_code == 409
    current = client.get(url + "/history").json()
    assert client.post(url + "/history", json=request(2, current)).status_code == 200
    library.remove([item["id"]], trash=True)
    assert not library.history.directory(f"{item['id']}_figure").exists()
    tid = client.get("/api/trash").json()[0]["id"]
    restored = client.post("/api/trash/restore", json={"ids": [tid]}).json()["ids"][0]
    assert len(client.get(f"/api/figure/{restored}/history").json()["versions"]) == 3
    assert client.get(f"/api/figure/{restored}").json()["settings"] == {"font": 20}


def test_comparison_figure_versions(setup, tmp_path):
    _, client = setup
    xmfa = tmp_path / "comparison.xmfa"
    xmfa.write_text("", encoding="utf-8")
    item_id = library.add_comparison({"genomes": [], "blocks": []}, "comparison", xmfa)
    url = f"/api/figure/{item_id}"
    assert client.put(url, json={"scale": 1}).status_code == 200
    assert client.put(url, json={"scale": 2}).status_code == 200
    state = client.get(url + "/history").json()
    request = {"index": 1, "expected_hash": state["versions"][2]["hash"],
               "target_hash": state["versions"][1]["hash"], "expected_current": 2, "expected_count": 3}
    assert client.post(url + "/history", json=request).status_code == 200
    assert client.get(url).json()["settings"] == {"scale": 1}


def test_library_action_undo_rename_and_move(setup):
    files, client = setup
    item = _import(client, files, "synthetic_mt.gb")["Mt"]
    client.headers["X-Cladia-Window"] = "undo-test"
    client.post("/api/folders", json={"name": "A"})
    assert client.post("/api/library/rename", json={"id": item["id"], "name": "Renamed"}).status_code == 200
    assert client.post("/api/library/move", json={"ids": [item["id"]], "folder": "A"}).status_code == 200
    undo = lambda redo=False: client.post("/api/library/action-history", json={"redo": redo})
    assert undo().status_code == 200 and library.get_item(item["id"])["folder"] is None
    assert undo().status_code == 200 and library.get_item(item["id"])["name"] == "Mt"
    assert undo(True).status_code == 200 and library.get_item(item["id"])["name"] == "Renamed"
    assert undo(True).status_code == 200 and library.get_item(item["id"])["folder"] == "A"
    client.headers["X-Cladia-Window"] = "other-window"
    client.post("/api/library/rename", json={"id": item["id"], "name": "Other"})
    client.headers["X-Cladia-Window"] = "undo-test"
    assert undo().status_code == 200  # Undoing the move does not overwrite the newer name.
    assert undo().status_code == 409
    assert library.get_item(item["id"])["name"] == "Other"


def test_unsupported_library_action_clears_session_undo(setup):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta")
    item = items["Mt"]
    client.headers["X-Cladia-Window"] = "undo-clear-test"
    client.post("/api/library/rename", json={"id": item["id"], "name": "Renamed"})
    client.post("/api/library/sort", json={"id": items["set"]["id"], "key": "name"})
    result = client.post("/api/library/action-history", json={"redo": False})
    assert result.status_code == 409


def test_delete_undo_restores_items_and_list_members(setup):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta")
    client.headers["X-Cladia-Window"] = "delete-undo-test"
    selected = [items["Mt"]["id"], items["set › strainA"]["id"]]
    assert client.post("/api/library/remove", json={"ids": selected}).status_code == 200
    assert [r.name for r in library.records(library.get_item(items["set"]["id"]))] == ["strainB"]
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200
    assert library.get_item(items["Mt"]["id"])["label"] == "Mt"
    assert [r.name for r in library.records(library.get_item(items["set"]["id"]))] == ["strainA", "strainB"]
    assert not client.get("/api/trash").json()
    assert client.post("/api/library/action-history", json={"redo": True}).status_code == 200
    assert [r.name for r in library.records(library.get_item(items["set"]["id"]))] == ["strainB"]
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200
    assert [r.name for r in library.records(library.get_item(items["set"]["id"]))] == ["strainA", "strainB"]


def test_delete_undo_refuses_missing_trash_or_changed_list(setup):
    files, client = setup
    items = _import(client, files, "set.fasta")
    client.headers["X-Cladia-Window"] = "delete-conflict-test"
    client.post("/api/library/remove", json={"ids": [items["set › strainA"]["id"]]})
    path = Path(library.get_item(items["set"]["id"])["path"])
    path.write_bytes(path.read_bytes() + b"external edit")
    before = path.read_bytes()
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 409
    assert path.read_bytes() == before
    client.post("/api/trash/delete", json={"ids": None})
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 409


def test_delete_undo_mapping_sidecars(setup, tmp_path):
    _, client = setup
    bam = tmp_path / "mapped.bam"
    bam.write_bytes(b"bam")
    Path(str(bam) + ".bai").write_bytes(b"index")
    reads = tmp_path / "reads.tsv"
    reads.write_bytes(b"reads")
    item_id = library.add_mapping({"example": 1}, "mapping", bam, reads)
    original = library.get_item(item_id)
    client.headers["X-Cladia-Window"] = "mapping-delete-test"
    assert client.post("/api/library/remove", json={"ids": [item_id]}).status_code == 200
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200
    restored = library.get_item(item_id)
    assert (library.library_dir() / restored["bam"]).read_bytes() == b"bam"
    assert (library.library_dir() / (restored["bam"] + ".bai")).read_bytes() == b"index"
    assert (library.library_dir() / restored["reads"]).read_bytes() == b"reads"
    assert restored["name"] == original["name"]
    assert client.post("/api/library/action-history", json={"redo": True}).status_code == 200
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200


def test_group_and_split_undo_redo_keep_item_ids(setup):
    files, client = setup
    items = _import(client, files, "set.fasta")
    client.headers["X-Cladia-Window"] = "group-split-test"
    original = items["set"]["id"]
    split = client.post("/api/library/split", json={"ids": [original]}).json()["ids"]
    assert len(split) == 2
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200
    assert [r.name for r in library.records(library.get_item(original))] == ["strainA", "strainB"]
    assert client.post("/api/library/action-history", json={"redo": True}).status_code == 200
    assert all(library.get_item(i) for i in split)
    grouped = client.post("/api/library/group", json={"ids": split, "name": "grouped"}).json()["id"]
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200
    assert all(library.get_item(i) for i in split)
    assert client.post("/api/library/action-history", json={"redo": True}).status_code == 200
    assert library.get_item(grouped)["id"] == grouped


def test_group_undo_refuses_modified_result(setup):
    files, client = setup
    items = _import(client, files, "set.fasta")
    client.headers["X-Cladia-Window"] = "group-conflict-test"
    children = [items["set › strainA"]["id"], items["set › strainB"]["id"]]
    grouped = client.post("/api/library/group", json={"ids": children, "name": "grouped"}).json()["id"]
    path = Path(library.get_item(grouped)["path"])
    path.write_bytes(path.read_bytes() + b"external change")
    before = path.read_bytes()
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 409
    assert path.read_bytes() == before


def test_group_undo_restores_mixed_items_and_list_members(setup):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta")
    client.headers["X-Cladia-Window"] = "group-mixed-test"
    original = items["Mt"]["id"]
    member = items["set › strainA"]["id"]
    parent = items["set"]["id"]
    result = client.post("/api/library/group", json={"ids": [original, member], "name": "mixed"})
    assert result.status_code == 200
    grouped = result.json()["id"]
    assert [r.name for r in library.records(library.get_item(parent))] == ["strainB"]
    assert client.post("/api/library/action-history", json={"redo": False}).status_code == 200
    assert library.get_item(original)["id"] == original
    assert [r.name for r in library.records(library.get_item(parent))] == ["strainA", "strainB"]
    assert client.post("/api/library/action-history", json={"redo": True}).status_code == 200
    assert library.get_item(grouped)["id"] == grouped
    assert [r.name for r in library.records(library.get_item(parent))] == ["strainB"]


def test_import_twice_and_bad_files(setup):
    files, client = setup
    _import(client, files, "synthetic_mt.gb")
    _import(client, files, "synthetic_mt.gb")
    items = client.get("/api/library").json()
    assert [item["label"] for item in items] == ["Mt", "Mt"]
    assert len({item["path"] for item in items}) == 2  # independent internal copies
    (files / "notes.txt").write_text("hello")
    r = client.post("/api/library/add", json={"paths": [str(files / "notes.txt")]}).json()
    assert r["ids"] == [] and "not a GenBank, GFF3, FASTA, Clustal, Newick or GFA" in r["errors"][0]


def test_saving_changes_only_the_copy(setup):
    files, client = setup
    before = _snapshot(files)
    items = _import(client, files, "synthetic_mt.gb", "set.fasta")

    data = client.get(f"/api/sequences/{items['Mt']['id']}").json()
    data["sequence"] += "AAAA"
    client.put(f"/api/sequences/{items['Mt']['id']}", json=data)
    assert len(read_records(Path(items["Mt"]["path"]), "genbank")[0]) == 15789 + 4

    # One sequence of a list: saved into the list, the other one is kept
    child = items["set › strainB"]
    data = client.get(f"/api/sequences/{child['id']}").json()
    assert data["sequence"] == "ACGTTCGT"
    data["features"] = [{"name": "x", "type": "gene", "start": 0, "end": 3, "strand": 1}]
    response = client.put(f"/api/sequences/{child['id']}", json=data).json()
    assert response["features"] == 1 and response["length"] == 8  # the page updates its item from this
    assert response["mtime"] > 0
    saved = read_records(Path(child["path"]), "genbank")
    assert [r.name for r in saved] == ["strainA", "strainB"]
    assert saved[1].features[0].type == "gene"

    assert _snapshot(files) == before  # originals untouched


def test_library_version_tracks_everything_the_page_shows(setup):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb")
    version = client.get("/api/library/version").json()["version"]
    assert client.get("/api/library").headers["x-cladia-version"] == version
    assert client.get("/api/library/version").json()["version"] == version  # stable

    data = client.get(f"/api/sequences/{items['Mt']['id']}").json()
    data["sequence"] += "A"
    client.put(f"/api/sequences/{items['Mt']['id']}", json=data)
    saved = client.get("/api/library/version").json()["version"]
    assert saved != version  # a saved file changes its mtime

    client.post("/api/folders", json={"name": "Mito"})  # an empty folder is shown too
    assert client.get("/api/library/version").json()["version"] != saved


def test_delete_removes_copies_only(setup):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta")
    client.post("/api/library/remove", json={"ids": [items["Mt"]["id"], items["set › strainA"]["id"]]})
    left = {i["label"]: i for i in client.get("/api/library").json()}
    assert sorted(left) == ["set", "set › strainB"]
    assert left["set"]["count"] == 1
    assert not Path(items["Mt"]["path"]).exists()
    assert (files / "synthetic_mt.gb").exists()

    client.post("/api/library/remove", json={"ids": [left["set"]["id"]]})
    assert client.get("/api/library").json() == []
    assert list(library.library_dir().iterdir()) == []


def test_split_list(setup):
    files, client = setup
    items = _import(client, files, "set.fasta")
    ids = client.post("/api/library/split", json={"ids": [items["set"]["id"]]}).json()["ids"]
    after = {i["label"]: i for i in client.get("/api/library").json()}
    assert sorted(after) == ["strainA", "strainB"]
    assert sorted(i["id"] for i in after.values()) == sorted(ids)
    assert all(i["kind"] == "sequence" and not i["parent"] for i in after.values())


def test_export(setup, tmp_path):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta", "aln.fasta")

    # To a path (app window), GenBank, a sequence plus a whole list
    target = tmp_path / "out" / "export.gb"
    target.parent.mkdir()
    ids = [items["Mt"]["id"], items["set"]["id"], items["set › strainA"]["id"]]
    client.post("/api/export", json={"ids": ids, "format": "genbank", "path": str(target)})
    assert [r.name for r in read_records(target, "genbank")] == ["Mt", "strainA", "strainB"]  # Mt: its LOCUS name

    # As text (browser), FASTA, an alignment keeps its gaps
    text = client.post("/api/export", json={"ids": [items["aln"]["id"]], "format": "fasta"}).json()["text"]
    assert text == ">a\nAC-GT\n>b\nACCGT\n"
    r = client.post("/api/export", json={"ids": [items["aln"]["id"]], "format": "genbank"})
    assert r.status_code == 400

    # The CDSs' proteins; a genetic code picked in the editor is used
    text = client.post("/api/export", json={"ids": [items["Mt"]["id"]], "format": "protein"}).json()["text"]
    proteins = list(SeqIO.parse(StringIO(text), "fasta"))
    assert [p.id for p in proteins][:3] == ["cob", "nad4", "nad5"] and len(proteins) == 8
    assert proteins[0].description == "cob Mt 545-1621(-)"
    assert all(str(p.seq).startswith("M") and "*" not in str(p.seq)[-1:] for p in proteins)
    tga = SeqRecord(Seq("ATGTGATGGAAATAA"), id="tga", name="tga", annotations={"molecule_type": "DNA"},
                    features=[SeqFeature(SimpleLocation(0, 15, 1), type="CDS", qualifiers={"gene": ["g"]})])
    SeqIO.write(tga, files / "tga.gb", "genbank")
    tga_id = client.post("/api/library/add", json={"paths": [str(files / "tga.gb")]}).json()["ids"][0]
    export = lambda codes: client.post("/api/export", json={"ids": [tga_id], "format": "protein", "codes": codes}).json()["text"]
    assert export({}) == ">g tga 1-15\nM*WK\n"  # no /organelle: code 1, TGA is a stop
    assert export({tga_id: 4}) == ">g tga 1-15\nMWWK\n"


def test_upload_in_browser_mode(setup):
    files, client = setup
    r = client.post("/api/library/upload", json={"filename": "up.fasta", "text": ">x\nACGT\n"}).json()
    assert r["errors"] == []
    assert [i["label"] for i in client.get("/api/library").json()] == ["x"]  # named after its sequence
    assert not (config.APP_DIR / "upload" / "up.fasta").exists()


def test_deleted_copy_is_flagged(setup):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb")
    Path(items["Mt"]["path"]).unlink()
    item = client.get("/api/library").json()[0]
    assert item["missing"]
    assert client.get(f"/api/sequences/{item['id']}").status_code == 404


def test_trees_import_and_export(setup):
    files, client = setup
    (files / "t.treefile").write_text("((A:0.1,B:0.2)95:0.3,C:0.4);\n")
    (files / "bad.nwk").write_text("this is not a tree (((")
    r = client.post("/api/library/add", json={"paths": [str(files / "t.treefile"), str(files / "bad.nwk")]}).json()
    assert len(r["ids"]) == 1 and "bad.nwk" in r["errors"][0]
    item = client.get("/api/library").json()[0]
    assert (item["kind"], item["label"], Path(item["path"]).suffix) == ("tree", "t", ".nwk")
    assert client.get(f"/api/tree/{item['id']}").json()["newick"] == "((A:0.1,B:0.2)95:0.3,C:0.4);"
    assert client.post("/api/export", json={"ids": [item["id"]], "format": "newick"}).json()["text"] == "((A:0.1,B:0.2)95:0.3,C:0.4);\n"
    assert client.post("/api/export", json={"ids": [item["id"]], "format": "fasta"}).status_code == 400


def test_alignment_features_and_extract(setup):
    """Rows get their source's features, also a reversed row (MAFFT's _R_), and an
    extracted block keeps them in place."""
    files, client = setup
    rec = SeqIO.read(files / "synthetic_mt.gb", "genbank")
    rc = rec.reverse_complement(id="rc", name="rc", description="", features=True, annotations=True)
    rc.annotations["molecule_type"] = "DNA"
    SeqIO.write(rc, files / "rc.gb", "genbank")
    seq = str(rec.seq)
    (files / "pair.fasta").write_text(f">Mt\n{seq}\n>_R_rc\n{seq[:-1]}-\n>none\n{'A' * len(seq)}\n")
    items = _import(client, files, "synthetic_mt.gb", "rc.gb", "pair.fasta")
    aln = items["pair"]
    assert aln["kind"] == "alignment"

    rows = client.get(f"/api/alignment/{aln['id']}/features").json()["rows"]
    assert set(rows) == {"Mt", "_R_rc"}  # "none" has no source sequence
    cds = lambda name: sorted((f for f in rows[name]["features"] if f["type"] == "CDS"), key=lambda f: f["locations"][0]["start"])
    assert [(f["locations"], f["strand"]) for f in cds("_R_rc")] == [(f["locations"], f["strand"]) for f in cds("Mt")]

    first = cds("Mt")[0]  # complement(545..1621): 0-based 544..1620
    assert first["locations"] == [{"start": 544, "end": 1620}] and first["strand"] == -1
    # what the annotation table lists: the feature's index in its sequence, ends, notes (no translation)
    assert (first["start"], first["end"]) == (544, 1620) and isinstance(first["index"], int)
    assert "translation" not in first["notes"] and first["notes"].get("product")
    new_id = client.post("/api/library/extract", json={"id": aln["id"], "start": 600, "end": 899, "rows": [0, 1]}).json()["id"]
    part = client.get(f"/api/alignment/{new_id}/features").json()["rows"]
    moved = [f for f in part["Mt"]["features"] if f["type"] == "CDS"][0]
    assert moved["locations"] == [{"start": 0, "end": 299}]  # the block 601-900 lies inside that CDS
    assert set(part) == {"Mt", "_R_rc"}


def test_save_alignment_gaps_only(setup):
    """Saving an edited alignment: gaps may move, bases, names and order may not."""
    files, client = setup
    aln = _import(client, files, "aln.fasta")["aln"]
    url = f"/api/alignment/{aln['id']}"
    assert client.put(url, json={"rows": [{"name": "a", "sequence": "ACG-T"}, {"name": "b", "sequence": "ACCGT"}]}).status_code == 200
    rows = lambda: [{k: r[k] for k in ("name", "sequence")} for r in client.get(url).json()]
    assert rows() == [{"name": "a", "sequence": "ACG-T"}, {"name": "b", "sequence": "ACCGT"}]
    changed = client.put(url, json={"rows": [{"name": "a", "sequence": "ACG-A"}, {"name": "b", "sequence": "ACCGT"}]})
    assert changed.status_code == 400 and "only gaps" in changed.json()["detail"]
    renamed = client.put(url, json={"rows": [{"name": "b", "sequence": "ACCGT"}, {"name": "a", "sequence": "ACG-T"}]})
    assert renamed.status_code == 400
    assert client.get(url).json()[0]["sequence"] == "ACG-T"  # unchanged by the refused saves


def test_alignment_remove_rows_and_restore_after_save(setup):
    files, client = setup
    (files / "three.fasta").write_text(">a one\n--AC-GT\n>b two\nTTAC-G-\n>c three\n--ACCGT\n")
    aln = _import(client, files, "three.fasta")["three"]
    url = f"/api/alignment/{aln['id']}"
    original = client.get(url).json()
    rows = [{k: r[k] for k in ("name", "sequence")} for r in original]
    assert client.put(url, json={"rows": [rows[0], rows[2]]}).status_code == 200
    assert [r["name"] for r in client.get(url).json()] == ["a", "c"]
    assert client.put(url, json={"rows": rows}).status_code == 200  # undo after saving
    restored = client.get(url).json()
    assert [r["name"] for r in restored] == ["a", "b", "c"]
    assert [r["description"] for r in restored] == [r["description"] for r in original]
    assert client.put(url, json={"rows": []}).status_code == 400
    unknown = [*rows, {"name": "new", "sequence": "ACGT"}]
    assert client.put(url, json={"rows": unknown}).status_code == 400


def test_copy_alignment_rows_to_alignment_or_sequences(setup):
    files, client = setup
    (files / "three.fasta").write_text(">a one\n--AC-GT\n>b two\nTTAC-G-\n>c three\n--ACCGT\n")
    aln = _import(client, files, "three.fasta")["three"]
    library.add_folder("Research")
    library.move([aln["id"]], "Research")
    rows = client.get(f"/api/alignment/{aln['id']}").json()
    selected = [{k: rows[i][k] for k in ("name", "sequence")} for i in (0, 2)]
    url = f"/api/alignment/{aln['id']}/copy-rows"
    response = client.post(url, json={"rows": selected})
    assert response.status_code == 200
    new = library.get_item(response.json()["ids"][0])
    assert new["kind"] == "alignment"
    assert [str(r.seq) for r in library.records(new)] == ["AC-GT", "ACCGT"]
    result = client.post(url, json={"rows": selected, "as_sequences": True})
    assert result.status_code == 200
    assert len(result.json()["ids"]) == 1
    copied_list = library.get_item(result.json()["ids"][0])
    assert copied_list["kind"] == "list"
    assert copied_list["folder"] == "Research" and len(library.children(copied_list)) == 2
    assert [str(r.seq) for r in library.records(copied_list)] == ["ACGT", "ACCGT"]
    assert [str(r.seq) for r in library.records(library.get_item(aln["id"]))] == ["--AC-GT", "TTAC-G-", "--ACCGT"]
    assert client.post(url, json={"rows": [selected[0]]}).status_code == 400


def test_alignment_sequence_copy_keeps_matching_source_features(setup):
    files, client = setup
    (files / "two.fasta").write_text(">a\n--AC-GT\n>b\nTTAC-G-\n")
    source = SeqRecord(Seq("ACGT"), id="source", name="source", annotations={"molecule_type": "DNA", "organism": "Test species"})
    source.features = [SeqFeature(SimpleLocation(0, 3), type="gene", qualifiers={"gene": ["example"]})]
    source_id = library.add_sequence(source, "source")
    aln = _import(client, files, "two.fasta")["two"]
    entries = library.load()
    next(e for e in entries if e["id"] == aln["id"])["sources"] = {"a": {"item": source_id, "offset": 0}}
    library._save(entries)
    rows = client.get(f"/api/alignment/{aln['id']}").json()
    result = client.post(f"/api/alignment/{aln['id']}/copy-rows", json={"rows": [rows[0]], "as_sequences": True})
    assert result.status_code == 200
    copied_list = library.get_item(result.json()["ids"][0])
    assert copied_list["kind"] == "list"
    copy = library.records(copied_list)[0]
    assert str(copy.seq) == "ACGT" and copy.annotations["organism"] == "Test species"
    assert any(f.type == "gene" and f.qualifiers["gene"] == ["example"] for f in copy.features)


def test_rename(setup):
    """A sequence's record is renamed with it (also inside a list); an alignment only in the list."""
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta", "aln.fasta")
    rename = lambda item_id, name: client.post("/api/library/rename", json={"id": item_id, "name": name})
    assert rename(items["Mt"]["id"], "Chondrus mito").status_code == 200
    renamed = {it["id"]: it for it in client.get("/api/library").json()}[items["Mt"]["id"]]
    assert renamed["label"] == "Chondrus mito"
    record = SeqIO.read(renamed["path"], "genbank")
    assert record.name == "Chondrus_mito" and len(record.features) > 10  # annotations kept

    child = next(it for it in client.get("/api/library").json() if it["parent"] == items["set"]["id"] and it["record"] == 1)
    assert rename(child["id"], "strainB2").status_code == 200
    kids = [it for it in client.get("/api/library").json() if it["parent"] == items["set"]["id"]]
    assert sorted(k["name"] for k in kids) == ["strainA", "strainB2"]
    fasta = client.post("/api/export", json={"ids": [items["set"]["id"]], "format": "fasta"}).json()["text"]
    assert ">strainB2" in fasta

    assert rename(items["aln"]["id"], "my alignment").status_code == 200
    assert client.get(f"/api/alignment/{items['aln']['id']}").json()[0]["name"] == "a"  # rows unchanged
    assert rename(items["aln"]["id"], "  ").status_code == 400


def test_duplicate_and_new_sequence(setup):
    """Duplicates are separate copies; a region becomes a new sequence with the features inside it."""
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta", "aln.fasta")
    mt = items["Mt"]
    child = next(it for it in client.get("/api/library").json() if it["parent"] == items["set"]["id"] and it["record"] == 0)
    new = client.post("/api/library/duplicate", json={"ids": [mt["id"], child["id"], items["aln"]["id"]]}).json()["ids"]
    by_id = {it["id"]: it for it in client.get("/api/library").json()}
    assert [by_id[i]["label"] for i in new] == ["Mt copy", "strainA copy", "aln copy"]
    assert [by_id[i]["kind"] for i in new] == ["sequence", "sequence", "alignment"]
    assert by_id[new[0]]["path"] != mt["path"]
    assert len(SeqIO.read(by_id[new[0]]["path"], "genbank").features) == len(SeqIO.read(mt["path"], "genbank").features)

    data = client.get(f"/api/sequences/{mt['id']}").json()
    cds = next(f for f in data["features"] if f["type"] == "CDS")  # 544..1620
    region = {"sequence": data["sequence"][500:1700], "geneticCode": 4,
              "features": [{**cds, "id": None, "start": cds["start"] - 500, "end": cds["end"] - 500}]}
    client.headers["X-Cladia-Window"] = "paste-test"
    r = client.post("/api/library/new-sequences", json={"sequences": [{"name": "Mt 501-1700", "data": region, "source": mt["id"]}]})
    [new_id] = r.json()["ids"]
    record = SeqIO.read({it["id"]: it for it in client.get("/api/library").json()}[new_id]["path"], "genbank")
    assert len(record) == 1200 and record.name == "Mt_501-1700"
    cds_new = next(f for f in record.features if f.type == "CDS")
    assert int(cds_new.location.start) == 44 and record.annotations.get("organism")  # metadata kept

    # several rows: one list, "<list>_extract"; the paste is one undo step, and comes back on redo
    two = [{"name": "a 1-100", "data": {"sequence": "ACGT" * 25, "features": []}, "source": mt["id"]},
           {"name": "b 1-100", "data": {"sequence": "TTGA" * 25, "features": []}, "source": mt["id"]}]
    [list_id] = client.post("/api/library/new-sequences", json={"sequences": two, "list_name": "set_extract"}).json()["ids"]
    listed = {it["id"]: it for it in client.get("/api/library").json()}
    assert listed[list_id]["kind"] == "list" and listed[list_id]["label"] == "set_extract" and listed[list_id]["count"] == 2
    assert [r.name for r in library.records(listed[list_id])] == ["a_1-100", "b_1-100"]
    assert client.post("/api/library/action-history", json={}).json()["kind"] == "paste"
    assert list_id not in {it["id"] for it in client.get("/api/library").json()}
    assert client.post("/api/library/action-history", json={"redo": True}).status_code == 200
    assert list_id in {it["id"] for it in client.get("/api/library").json()}
    assert client.post("/api/library/action-history", json={}).status_code == 200  # and undone again
    assert client.post("/api/library/action-history", json={}).status_code == 200  # the first paste too
    assert new_id not in {it["id"] for it in client.get("/api/library").json()}


def test_list_records_read_and_written_one_at_a_time(setup):
    """Opening, saving, renaming or removing one sequence of a list touches only its record."""
    files, client = setup
    src = (files / "synthetic_mt.gb").read_text()
    (files / "many.gb").write_text("".join(src.replace("LOCUS       Mt", f"LOCUS       G{i}", 1) for i in range(5)))
    many = _import(client, files, "many.gb")["many"]
    assert many["kind"] == "list" and many["count"] == 5
    kids = lambda: sorted((it for it in client.get("/api/library").json() if it["parent"] == many["id"]), key=lambda it: it["record"])
    path = Path(many["path"])
    before = path.read_bytes()
    index = library.record_index(path)
    assert [name for _, _, name in index] == ["G0", "G1", "G2", "G3", "G4"]

    third = kids()[2]
    assert client.get(f"/api/sequences/{third['id']}").json()["name"] == "G2"
    assert client.post("/api/library/rename", json={"id": third["id"], "name": "Third one"}).status_code == 200
    after = path.read_bytes()
    assert after[: index[2][0]] == before[: index[2][0]] and after.endswith(before[index[3][0]:])  # others untouched
    assert [k["name"] for k in kids()] == ["G0", "G1", "Third_one", "G3", "G4"]

    client.post("/api/library/remove", json={"ids": [kids()[1]["id"], kids()[3]["id"]]})
    assert [k["name"] for k in kids()] == ["G0", "Third_one", "G4"]
    assert [r.name for r in read_records(path, "genbank")] == ["G0", "Third_one", "G4"]


def test_group_sort_and_descriptions(setup):
    files, client = setup
    (files / "short.fasta").write_text(">short\nACGT\n")
    found = _import(client, files, "set.fasta", "short.fasta", "synthetic_mt.gb")
    # FASTA: the description is the header after the name; none at all -> ""
    assert found["set › strainA"]["description"] == "one"
    assert found["short"]["description"] == ""
    assert client.get(f"/api/sequences/{found['short']['id']}").json()["description"] == ""

    # a whole list and two sequences -> one new list, the old items gone
    genome = next(i for i in found.values() if i["kind"] == "sequence" and not i["parent"] and i["label"] != "short")
    ids = [found["set"]["id"], found["short"]["id"], genome["id"]]
    new = client.post("/api/library/group", json={"ids": ids, "name": "All"}).json()["id"]
    labels = [i["label"] for i in client.get("/api/library").json()]
    assert not {"set", "short", genome["label"]} & set(labels)
    names = lambda: [r.name for r in read_records(library.get_item(new)["path"], "genbank")]
    assert names() == ["strainA", "strainB", "short", library.get_item(new + "-3")["name"]]

    client.post("/api/library/sort", json={"id": new, "key": "length"})
    lengths = [len(r) for r in read_records(library.get_item(new)["path"], "genbank")]
    assert lengths == sorted(lengths, reverse=True)
    assert library.get_item(new)["sorted"] == {"key": "length", "descending": True}
    client.post("/api/library/sort", json={"id": new, "key": "length", "descending": False})
    assert [len(r) for r in read_records(library.get_item(new)["path"], "genbank")] == sorted(lengths)
    client.post("/api/library/sort", json={"id": new, "key": "name"})
    assert [n.lower() for n in names()] == sorted(n.lower() for n in names())
    assert client.post("/api/library/group", json={"ids": [new + "-0"], "name": "x"}).status_code == 400


def test_blast_results_item(setup):
    files, client = setup
    results = {"query_length": 100, "hits": [], "query": {"name": "q", "item": None, "start": 1, "end": 100},
               "program": "megablast", "database": "core_nt", "entrez": "", "rid": "R", "date": "2026-10-23 10:00"}
    new = library.add_blast(results, "q BLAST")
    item = next(i for i in client.get("/api/library").json() if i["id"] == new)
    assert (item["kind"], item["label"]) == ("blast", "q BLAST")
    assert client.get(f"/api/blast/{new}").json() == results
    assert client.get(f"/api/info/{new}").json()["hits"] == 0


def test_folders(setup):
    from cladia import jobs
    files, client = setup
    client.post("/api/folders", json={"name": "Mito"})
    assert client.post("/api/folders", json={"name": "mito"}).status_code == 400
    r = client.post("/api/library/add", json={"paths": [str(files / "set.fasta")], "folder": "Mito"}).json()
    (list_id,) = r["ids"]
    found = {i["label"]: i for i in client.get("/api/library").json()}
    assert found["set"]["folder"] == "Mito" and found["set › strainA"]["folder"] == "Mito"
    # made from it: same folder
    dup = client.post("/api/library/duplicate", json={"ids": [list_id + "-0"]}).json()["ids"][0]
    assert library.get_item(dup)["folder"] == "Mito"
    # A job uses an existing chosen destination.
    client.post("/api/folders", json={"name": "Plastid"})
    job = jobs.submit("x", "x", lambda job, workdir: job["outputs"].append({"id": dup}), folder="Plastid")
    while job["status"] in ("queued", "running"):
        time.sleep(0.05)
    assert library.get_item(dup)["folder"] == "Plastid" and "Plastid" in client.get("/api/folders").json()
    # moving a member moves its list; renaming and deleting folders
    client.post("/api/library/move", json={"ids": [list_id + "-1"], "folder": None})
    assert library.get_item(list_id)["folder"] is None
    client.post("/api/folders/rename", json={"name": "Plastid", "new": "Chloroplast"})
    assert library.get_item(dup)["folder"] == "Chloroplast"
    client.post("/api/folders/delete", json={"name": "Chloroplast"})
    assert library.get_item(dup)["folder"] is None and client.get("/api/folders").json() == ["Mito"]


def test_sort_alignment(setup):
    files, client = setup
    (files / "aln3.fasta").write_text(">b\nAC-GT\n>a\nACCGT\n>c\nA---T\n")
    (aln,) = client.post("/api/library/add", json={"paths": [str(files / "aln3.fasta")]}).json()["ids"]
    names = lambda: [r.id for r in read_records(library.get_item(aln)["path"], "fasta")]
    client.post("/api/library/sort", json={"id": aln, "key": "name"})
    assert names() == ["a", "b", "c"]
    client.post("/api/library/sort", json={"id": aln, "key": "name", "descending": True})
    assert names() == ["c", "b", "a"]
    client.post("/api/library/sort", json={"id": aln, "key": "length"})  # bases, gaps not counted
    assert names() == ["a", "b", "c"]


def test_sort_alignment_distance(setup):
    files, client = setup
    (files / "dist.fasta").write_text(
        ">far\nTTTT--\n>unknown\nNNNN--\n>ref\nACGT--\n>near\nACGA--\n>same\nACGTNN\n")
    [aln] = client.post("/api/library/add", json={"paths": [str(files / "dist.fasta")]}).json()["ids"]
    path = Path(library.get_item(aln)["path"])
    original = path.read_bytes()
    for reference in (None, -1, 5):
        assert client.post("/api/library/sort", json={"id": aln, "key": "distance", "reference": reference}).status_code == 400
        assert path.read_bytes() == original
    assert client.post("/api/library/sort", json={"id": aln, "key": "distance", "reference": 2}).status_code == 200
    rows = list(read_records(path, "fasta"))
    assert [r.id for r in rows] == ["ref", "same", "near", "far", "unknown"]
    assert {r.id: str(r.seq) for r in rows} == {"far": "TTTT--", "unknown": "NNNN--", "ref": "ACGT--", "near": "ACGA--", "same": "ACGTNN"}
    assert library.get_item(aln)["sorted"] == {"key": "distance", "descending": False, "reference": "ref"}
    assert client.post("/api/library/sort", json={"id": aln, "key": "distance", "reference": 0, "descending": True}).status_code == 200
    assert [r.id for r in read_records(path, "fasta")] == ["far", "near", "same", "ref", "unknown"]
    items = _import(client, files, "set.fasta")
    assert client.post("/api/library/sort", json={"id": items["set"]["id"], "key": "distance", "reference": 0}).status_code == 400


def test_distance_pairwise_missing_and_protein():
    rows = [{"sequence": s} for s in ("ACGT--", "A---NN", "ACGA--", "NNNN--", "ACG---")]
    assert library._distance_order(rows, 0, False) == [0, 1, 4, 2, 3]
    assert library._distance_order(rows, 0, True) == [2, 1, 4, 0, 3]
    protein = [{"sequence": s} for s in ("MELK--", "MEIK--", "MELX--", "XXXX--")]
    assert library._distance_order(protein, 0, False) == [0, 2, 1, 3]


def test_edit_locks(setup, monkeypatch):
    from cladia import server
    files, client = setup
    found = _import(client, files, "set.fasta", "synthetic_mt.gb")
    member, genome = found["set › strainA"]["id"], next(i for i in found.values() if not i["parent"] and i["kind"] == "sequence")["id"]
    a, b = {"X-Cladia-Window": "A"}, {"X-Cladia-Window": "B"}
    assert client.post("/api/locks", json={"window": "A", "ids": [member]}).status_code == 200
    # the other window: can't take it, nor change it, its list, or delete it
    assert client.post("/api/locks", json={"window": "B", "ids": [member]}).status_code == 409
    assert client.post("/api/library/rename", json={"id": member, "name": "x"}, headers=b).status_code == 409
    list_id = found["set"]["id"]
    assert client.post("/api/library/sort", json={"id": list_id, "key": "name"}, headers=b).status_code == 409
    assert client.post("/api/library/remove", json={"ids": [list_id + "-1"]}, headers=b).status_code == 409
    data = client.get(f"/api/sequences/{member}").json()
    assert client.put(f"/api/sequences/{member}", json=data, headers=b).status_code == 409
    assert client.put(f"/api/sequences/{member}", json=data, headers=a).status_code == 200  # the holder can
    assert client.post("/api/library/rename", json={"id": genome, "name": "free"}, headers=b).status_code == 200
    # released: the other window may
    client.post("/api/locks/release", json={"window": "A"})
    assert client.post("/api/locks", json={"window": "B", "ids": [member]}).status_code == 200
    # a lock not renewed runs out (a closed or crashed window)
    monkeypatch.setattr(http_request_context, "LOCK_SECONDS", -1)
    client.post("/api/locks", json={"window": "B", "ids": [member]})
    assert client.post("/api/locks", json={"window": "A", "ids": [member]}).status_code == 200
    http_request_context.LOCKS.clear()


def test_add_to_list(setup):
    files, client = setup
    (files / "short.fasta").write_text(">short\nACGT\n")
    found = _import(client, files, "set.fasta", "short.fasta")
    list_id = found["set"]["id"]
    r = client.post("/api/library/add-to-list", json={"id": list_id, "ids": [found["short"]["id"], list_id + "-0"]})
    assert r.json() == {"added": 1}  # its own sequence isn't added again
    labels = [i["label"] for i in client.get("/api/library").json()]
    assert "short" not in labels and "set › short" in labels
    assert [r.name for r in read_records(library.get_item(list_id)["path"], "genbank")] == ["strainA", "strainB", "short"]
    assert client.post("/api/library/add-to-list", json={"id": list_id + "-0", "ids": [list_id]}).status_code == 400


def test_restore_mapping_sidecars_with_name_collisions(setup, tmp_path):
    _, client = setup
    bam = tmp_path / "input.bam"
    bam.write_bytes(b"bam data")
    Path(str(bam) + ".bai").write_bytes(b"index data")
    reads = tmp_path / "input.tsv"
    reads.write_bytes(b"reads data")
    item_id = library.add_mapping({"example": 1}, "mapping", bam, reads)
    item = library.get_item(item_id)
    originals = {key: item[key] for key in ("bam", "reads")}
    library.remove([item_id], trash=True)
    trash_id = client.get("/api/trash").json()[0]["id"]
    for name in originals.values():
        (library.library_dir() / name).write_bytes(b"existing")
    response = client.post("/api/trash/restore", json={"ids": [trash_id]})
    assert response.status_code == 200
    restored = library.get_item(response.json()["ids"][0])
    assert restored["bam"] != originals["bam"] and restored["reads"] != originals["reads"]
    assert (library.library_dir() / restored["bam"]).read_bytes() == b"bam data"
    assert (library.library_dir() / (restored["bam"] + ".bai")).read_bytes() == b"index data"
    assert (library.library_dir() / restored["reads"]).read_bytes() == b"reads data"
    assert all((library.library_dir() / name).read_bytes() == b"existing" for name in originals.values())
    assert library.mapping_data(restored) == {"example": 1}


def test_trash_restore_rolls_back_files_if_index_save_fails(setup, monkeypatch):
    files, client = setup
    items = _import(client, files, "synthetic_mt.gb", "set.fasta")
    ids = [items["Mt"]["id"], items["set"]["id"]]
    library.remove(ids, trash=True)
    trash = library.trash_items()
    before = {t["id"]: _snapshot(library.trash_dir() / t["id"]) for t in trash}
    original_save = library._save
    monkeypatch.setattr(library, "_save", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        library.restore([t["id"] for t in trash])
    monkeypatch.setattr(library, "_save", original_save)
    assert all(_snapshot(library.trash_dir() / tid) == contents for tid, contents in before.items())
    assert not ({item["id"] for item in library.items()} & set(ids))

def test_trash(setup):
    """Delete keeps items in the trash: restored to their folder, or deleted for good."""
    files, client = setup
    client.post("/api/folders", json={"name": "Mito"})
    (seq,), _ = library.import_paths([files / "synthetic_mt.gb"], "Mito")
    (lst,), _ = library.import_paths([files / "set.fasta"])
    path = Path(library.get_item(seq)["path"])
    client.post("/api/library/remove", json={"ids": [seq, f"{lst}-1"]})  # a sequence, and one of a list's
    assert not path.exists() and [c["name"] for c in library.children(library.get_item(lst))] == ["strainA"]
    trash = {t["label"]: t for t in client.get("/api/trash").json()}
    assert trash["Mt"]["folder"] == "Mito" and trash["Mt"]["size"] > 0
    assert trash["strainB"]["from_list"] == "set" and trash["strainB"]["kind"] == "sequence"
    space = client.get("/api/settings/space").json()
    assert space["trash_count"] == 2 and space["trash"] > 0
    # restored: in its folder again, the file back; the list's sequence beside the list
    back = client.post("/api/trash/restore", json={"ids": [trash["Mt"]["id"], trash["strainB"]["id"]]}).json()["ids"]
    restored = {library.get_item(i)["label"]: library.get_item(i) for i in back}
    assert restored["Mt"]["folder"] == "Mito" and path.exists() and restored["Mt"]["id"] == seq
    assert str(library.records(restored["strainB"])[0].seq) == "ACGTTCGT"
    assert client.get("/api/trash").json() == []
    # a name taken since: restored beside it; deleted for good
    client.post("/api/library/remove", json={"ids": [seq]})
    (again,), _ = library.import_paths([files / "synthetic_mt.gb"])
    (tid,) = [t["id"] for t in client.get("/api/trash").json()]
    (back,) = client.post("/api/trash/restore", json={"ids": [tid]}).json()["ids"]
    assert library.get_item(back)["path"] != library.get_item(again)["path"]
    client.post("/api/library/remove", json={"ids": [back, again]})
    assert client.post("/api/trash/delete", json={"ids": None}).json() == {"removed": 2}
    assert client.get("/api/trash").json() == [] and not any(library.trash_dir().iterdir())
    # moves that remove what they copied (Group) don't fill the trash
    (a,), _ = library.import_paths([files / "synthetic_mt.gb"])
    client.post("/api/library/group", json={"ids": [a, lst], "name": "both"})
    assert client.get("/api/trash").json() == []


def test_reroot_undo(setup):
    """A reroot sends the tree as it was; writing it back undoes the reroot."""
    files, client = setup
    (files / "t.nwk").write_text("((A:0.1,B:0.2)91:0.05,(C:0.1,D:0.1)77:0.03,E:0.3);\n")
    (tree,), _ = library.import_paths([files / "t.nwk"])
    path = Path(library.get_item(tree)["path"])
    before = path.read_text()
    r = client.post(f"/api/tree/{tree}/reroot", json={"outgroup": ["E"]}).json()
    assert r["previous"] == before and path.read_text() != before
    rerooted = path.read_text()
    assert client.put(f"/api/tree/{tree}/text", json={"text": r["previous"]}).json() == {"previous": rerooted}
    assert path.read_text() == before
    assert client.put(f"/api/tree/{tree}/text", json={"text": "not a tree"}).status_code == 400


def test_nested_folders(setup):
    files, client = setup
    client.post("/api/folders", json={"name": "Mito"})
    assert client.post("/api/folders", json={"name": "Red", "parent": "Mito"}).json() == {"name": "Mito/Red"}
    assert client.post("/api/folders", json={"name": "a/b"}).status_code == 400
    (seq,), _ = library.import_paths([files / "synthetic_mt.gb"], "Mito/Red")
    (lst,), _ = library.import_paths([files / "set.fasta"], "Mito")
    assert client.get("/api/folders").json() == ["Mito", "Mito/Red"]
    # renaming a folder carries what's inside it
    assert client.post("/api/folders/rename", json={"name": "Mito", "new": "Mitochondria"}).json() == {"name": "Mitochondria"}
    assert library.get_item(seq)["folder"] == "Mitochondria/Red" and library.get_item(lst)["folder"] == "Mitochondria"
    # moving one folder into another, not into itself
    client.post("/api/folders", json={"name": "Plastids"})
    assert client.post("/api/folders/move", json={"name": "Mitochondria", "parent": "Mitochondria/Red"}).status_code == 400
    client.post("/api/folders/move", json={"name": "Mitochondria/Red", "parent": "Plastids"})
    assert library.get_item(seq)["folder"] == "Plastids/Red"
    # deleting a folder: what was in it goes up a level
    client.post("/api/folders/delete", json={"name": "Plastids"})
    assert library.get_item(seq)["folder"] == "Red" and client.get("/api/folders").json() == ["Mitochondria", "Red"]
    client.post("/api/folders/delete", json={"name": "Red"})
    assert library.get_item(seq)["folder"] is None


def test_take_out_of_list(setup):
    files, client = setup
    found = _import(client, files, "set.fasta")
    lst = found["set"]["id"]
    client.post("/api/folders", json={"name": "F"})
    new = client.post("/api/library/take-out", json={"ids": [lst + "-1"], "folder": "F"}).json()["ids"]
    item = library.get_item(new[0])
    assert (item["kind"], item["name"], item["folder"]) == ("sequence", "strainB", "F")
    assert [r.name for r in read_records(item["path"], "genbank")] == ["strainB"]
    assert [r.name for r in read_records(library.get_item(lst)["path"], "genbank")] == ["strainA"]
    # the last one out: the list goes
    client.post("/api/library/take-out", json={"ids": [lst + "-0"]})
    assert lst not in [i["id"] for i in client.get("/api/library").json()]


def test_original_file(setup):
    files, client = setup
    found = _import(client, files, "set.fasta", "synthetic_mt.gb")
    lst = found["set"]
    assert lst["original"] == {"path": str((files / "set.fasta").resolve()), "format": "fasta"}
    genome = next(i for i in found.values() if i["kind"] == "sequence" and not i["parent"])
    assert genome["original"]["format"] == "genbank"
    # written back in its own format, the user's changes included
    client.post("/api/library/rename", json={"id": genome["id"], "name": "Renamed"})
    assert client.post(f"/api/library/{genome['id']}/write-back").status_code == 200
    assert read_records(files / "synthetic_mt.gb", "genbank")[0].name == "Renamed"
    client.post("/api/library/sort", json={"id": lst["id"], "key": "name", "descending": True})
    client.post(f"/api/library/{lst['id']}/write-back")
    assert (files / "set.fasta").read_text().startswith(">strainB")
    # a list's sequence can't replace the list's file; uploads have no original
    assert client.post(f"/api/library/{lst['id']}-0/write-back").status_code == 400
    up = client.post("/api/library/upload", json={"filename": "x.fasta", "text": ">x\nACGT\n"}).json()
    item = next(i for i in client.get("/api/library").json() if i["label"] == "x")
    assert item["original"] is None


def test_blast_databases_list(setup, tmp_path):
    files, client = setup
    for ending in ("nsq", "nhr", "nin"):
        (files / f"mine.00.{ending}").write_bytes(b"x" * 10)
    (files / "mine.nal").write_bytes(b"TITLE mine\nDBLIST mine.00\n")
    r = client.post("/api/blastdbs/add", json={"path": str(files / "mine.00.nsq"), "name": "Mine"}).json()
    assert r["database"]["path"] == str(files / "mine") and r["database"]["type"] == "nucl"
    assert client.post("/api/blastdbs/add", json={"path": str(files / "mine.nal"), "name": "mine"}).status_code == 400
    assert client.post("/api/blastdbs/add", json={"path": str(files / "synthetic_mt.gb")}).status_code == 400
    dbs = client.get("/api/blastdbs").json()["databases"]
    assert dbs[0]["size"] == 30 + len("TITLE mine\nDBLIST mine.00\n") and not dbs[0]["own"]
    client.put("/api/settings", json={"blast_databases": []})  # the dialog's Save can't wipe the list
    assert len(client.get("/api/blastdbs").json()["databases"]) == 1
    client.post("/api/blastdbs/remove", json={"name": "Mine"})  # not Cladia's: its files stay
    assert client.get("/api/blastdbs").json()["databases"] == [] and (files / "mine.00.nsq").exists()


def test_blast_on_this_pc(setup, monkeypatch):
    files, client = setup
    from cladia import jobs
    for ending in ("nsq", "nhr", "nin"):
        (files / f"mine.{ending}").write_bytes(b"x")
    client.post("/api/blastdbs/add", json={"path": str(files / "mine.nsq"), "name": "Mine"})
    found = _import(client, files, "set.fasta")
    query = [{"item": found["set › strainA"]["id"]}]
    sent = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **params: sent.append((kind, func, params)) or {})
    body = {"operation": "blast.search", "engine": "local_blast", "inputs": {"queries": query}}
    assert client.post("/api/tool-registry/run", json={**body,
        "options": {"database": "local:Other"}}).status_code == 400
    r = client.post("/api/tool-registry/run", json={**body,
        "options": {"program": "blastx", "database": "local:Mine"}})
    assert r.status_code == 400
    assert client.post("/api/tool-registry/run", json={**body,
        "options": {"database": "local:Mine"}}).status_code == 200
    kind, func, params = sent[0]
    assert (kind, params["db"]["name"], params["program"]) == ("localblast", "Mine", "megablast")


XMFA = """#FormatVersion Mauve1
#Sequence1File	/data/g0.fasta
#Sequence2File	/data/g1.fasta
> 1:1-6 + /data/g0.fasta
ACG-TAC
> 2:3-8 - /data/g1.fasta
ACGAAC-
=
> 1:7-8 + /data/g0.fasta
GT
=
"""


def test_genome_comparison(setup, monkeypatch):
    from cladia import formats, jobs
    files, client = setup
    blocks = formats.read_xmfa(XMFA)
    assert [len(b["parts"]) for b in blocks] == [2, 1]
    assert blocks[0]["parts"][1] == {"genome": 1, "start": 3, "end": 8, "strand": -1, "seq": "ACGAAC-"}
    found = _import(client, files, "set.fasta")  # strainA ACGTACGT, strainB ACGTTCGT
    assert client.post("/api/tool-registry/run", json={"operation": "align.compare", "engine": "mauve",
        "inputs": {"sequences": [found["set › strainA"]["id"]]}}).status_code == 400

    # the job, with progressiveMauve's output given
    def fake(job, image, args, workdir, mounts=None):
        assert "progressiveMauve " in " ".join(args) and (workdir / "g1.fasta").read_text() == ">g1\nACGTTCGT\n"
        (workdir / "out.xmfa").write_text(XMFA)
        return ""
    monkeypatch.setattr(owner_execution, "tool", fake)
    monkeypatch.setattr(owner_execution, "ensure_tools_image", lambda job: None)
    job = {"log": "", "outputs": [], "id": "m1"}
    owner_mauve.run_comparison(job, files, library.children(library.get_item(found["set"]["id"])), "both", line_up=False)
    comp = library.get_item(job["outputs"][0]["id"])
    data = client.get(f"/api/comparison/{comp['id']}").json()
    assert [g["name"] for g in data["genomes"]] == ["strainA", "strainB"]
    assert data["blocks"] == [{"xmfa": 0, "columns": 7, "parts": [{"genome": 0, "start": 1, "end": 6, "strand": 1},
                                                                  {"genome": 1, "start": 3, "end": 8, "strand": -1}]}]
    # a block as an alignment: rows named after the genomes, the reversed one _R_, with sources
    new = library.get_item(client.post(f"/api/comparison/{comp['id']}/extract", json={"block": 0}).json()["id"])
    assert [(r.id, str(r.seq)) for r in read_records(new["path"], "fasta")] == [("strainA", "ACG-TAC"), ("_R_strainB", "ACGAAC-")]
    assert new["sources"]["strainB"]["offset"] == 0 and new["sources"]["strainA"]["offset"] == 0
    # its figure (not an alignment's)
    figure = client.get(f"/api/figure/{comp['id']}").json()
    assert (figure["of"], figure["settings"]) == ("comparison", {})
    assert client.get(f"/api/figure/{new['id']}").status_code == 400
    # a duplicate keeps its XMFA (blocks can be extracted from it)
    dup = client.post("/api/library/duplicate", json={"ids": [comp["id"]]}).json()["ids"][0]
    assert client.post(f"/api/comparison/{dup}/extract", json={"block": 0}).status_code == 200
    # its XMFA goes with it
    xmfa = library.library_dir() / comp["xmfa"]
    assert xmfa.exists()
    client.post("/api/library/remove", json={"ids": [comp["id"]]})
    assert not xmfa.exists()


def test_line_up_genomes(tmp_path, monkeypatch):
    """A genome turned and reverse complemented is turned back by what _line_up finds."""
    import random
    from Bio.SeqFeature import SeqFeature, SimpleLocation
    from Bio.SeqRecord import SeqRecord
    from cladia import jobs
    from cladia.genbank import turned
    random.seed(2)
    first = SeqRecord(Seq("".join(random.choice("ACGT") for _ in range(3000))), id="a", name="a",
                      annotations={"molecule_type": "DNA", "topology": "circular"})
    first.features = [SeqFeature(SimpleLocation(2900, 3000, strand=1), type="gene", qualifiers={"gene": ["x"]})]
    other = turned(first, 1000, flip=True)  # first's base 0 is now its base 999, on the - strand
    assert str(other.seq) == str(first.seq[1000:] + first.seq[:1000]).translate(str.maketrans("ACGT", "TGCA"))[::-1]

    def fake_blast(job, image, args, workdir):  # the best hit: window 0 at 1000, reversed
        (workdir / "line1.tsv").write_text("0\t1\t1000\t501\t900\n375\t1\t625\t126\t300\n")
        return ""
    monkeypatch.setattr(owner_execution, "tool", fake_blast)
    job = {"log": ""}
    turns = owner_mauve._line_up(job, tmp_path, [first, other])
    start, flip = turns[1]
    back = turned(other, start, flip)
    assert flip and str(back.seq) == str(first.seq)
    (gene,) = [f for f in back.features if f.type == "gene"]
    assert (int(gene.location.start), int(gene.location.end), gene.location.strand) == (2900, 3000, 1)
    assert "reverse complemented" in job["log"]


def test_figures(setup):
    import base64
    from io import BytesIO
    from PIL import Image
    files, client = setup
    (files / "t.nwk").write_text("((a:0.1,b:0.2)95:0.05,c:0.3);\n")
    found = _import(client, files, "t.nwk")
    tree = found["t"]
    # a tree's figure, through the source's id (until Figure ▾): none until first saved
    first = client.get(f"/api/figure/{tree['id']}").json()
    assert (first["id"], first["of"], first["settings"]) == (None, "tree", {})
    saved = client.put(f"/api/figure/{tree['id']}", json={"fontSize": 9, "clades": {"a\tb": {"color": "#ff0000"}}}).json()
    figure = library.get_item(saved["id"])
    assert (figure["kind"], figure["source"], figure["name"]) == ("figure", tree["id"], "t figure")
    assert client.get(f"/api/figure/{figure['id']}").json()["settings"]["clades"]["a\tb"]["color"] == "#ff0000"
    assert library.get_item(tree["id"])["figures"] == 1
    # duplicating the tree doesn't copy its figures; deleting it leaves them, marked missing
    copy = client.post("/api/library/duplicate", json={"ids": [tree["id"]]}).json()["ids"][0]
    assert client.get(f"/api/figure/{copy}").json()["id"] is None
    client.post("/api/library/remove", json={"ids": [tree["id"]]})
    gone = client.get(f"/api/figure/{figure['id']}").json()
    assert gone["missing"] and gone["settings"]["fontSize"] == 9
    # exports: PDF from the SVG (vector, standard fonts), PNG and TIFF with their dpi
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="20mm" viewBox="0 0 141.73 56.69">'
           '<text x="5" y="20" font-family="Times New Roman" font-size="9" font-style="italic">Fucus</text></svg>')
    pdf = base64.b64decode(client.post("/api/figure/export", json={"format": "pdf", "svg": svg}).json()["data"])
    assert pdf.startswith(b"%PDF") and b"Times-Italic" in pdf
    raw = BytesIO()
    Image.new("RGBA", (20, 10), (255, 255, 255, 255)).save(raw, "PNG")
    png = base64.b64encode(raw.getvalue()).decode()
    for fmt in ("png", "tiff"):
        data = base64.b64decode(client.post("/api/figure/export", json={"format": fmt, "png": png, "dpi": 600}).json()["data"])
        image = Image.open(BytesIO(data))
        assert image.mode == "RGB" and round(image.info["dpi"][0]) == 600


def test_sequences_in_batches(setup):
    files, client = setup
    found = _import(client, files, "set.fasta")
    ids = [found["set › strainB"]["id"], found["set › strainA"]["id"]]
    rows = client.post("/api/sequences", json={"ids": ids}).json()
    assert [r["sequence"] for r in rows] == ["ACGTTCGT", "ACGTACGT"]  # in the order asked
    assert found["set › strainA"]["length"] == 8  # the viewer's size limit counts these
    assert client.post("/api/sequences", json={"ids": ["nope"]}).status_code == 404


def test_feature_keys_any_case(setup):
    """"cds", "Gene"... read as GenBank spells them (colours, translations, tools know them)."""
    files, client = setup
    text = (DATA / "synthetic_mt.gb").read_text(encoding="utf-8")
    (files / "lower.gb").write_text(text.replace("     CDS   ", "     cds   ").replace("     gene  ", "     Gene  "))
    (item,), _ = library.import_paths([files / "lower.gb"])
    data = client.get(f"/api/sequences/{item}").json()
    assert {"CDS", "gene"} <= {f["type"] for f in data["features"]} and not {"cds", "Gene"} & {f["type"] for f in data["features"]}
    found = {i["id"]: i for i in client.get("/api/library").json()}
    assert found[item]["features"] == len(data["features"])


def test_saved_versions_are_limited(setup):
    """Each timeline keeps its last MAX_VERSIONS saves; the oldest go, with their files."""
    from cladia import history
    files, client = setup
    (seq,), _ = library.import_paths([files / "synthetic_mt.gb"])
    for k in range(history.MAX_VERSIONS + 5):
        record = library.records(library.get_item(seq))[0]
        record.description = f"edit {k}"
        library.save_record(library.get_item(seq), record)
    timeline = history.versions(seq)
    assert len(timeline["versions"]) == history.MAX_VERSIONS and timeline["current"] == history.MAX_VERSIONS - 1
    assert len(list(history.directory(seq).glob("*.gz"))) == history.MAX_VERSIONS
    # the oldest kept can be restored (positions moved with the pruning)
    library.restore_version(library.get_item(seq), 0)
    assert "edit 5" in library.records(library.get_item(seq))[0].description
    # Storage: their size, and deleting them all
    assert client.get("/api/settings/space").json()["history"] > 0
    client.post("/api/settings/clean", json={"what": "history"})
    assert history.total_size() == 0 and library.records(library.get_item(seq))[0].description.startswith("edit 5")


def test_big_list_keeps_versions_of_the_record(setup, monkeypatch):
    """A big list's sequence: versions of its record only, restored exactly."""
    from cladia import history
    files, client = setup
    monkeypatch.setattr(library, "BIG_LIST", 0)  # every list counts as big
    (lst,), _ = library.import_paths([files / "set.fasta"])
    member = library.children(library.get_item(lst))[1]
    other_before = library._record_bytes(library.children(library.get_item(lst))[0])
    record = library.records(member)[0]
    record.description = "changed"
    library.save_record(member, record)
    member = library.get_item(member["id"])
    timeline = library.saved_versions(member)
    assert not timeline["whole_list"] and len(timeline["versions"]) == 2
    assert not history.directory(lst).joinpath("index.json").exists()  # no copy of the whole list
    library.restore_version(member, 0)
    assert library.records(library.get_item(member["id"]))[0].description != "changed"
    assert library._record_bytes(library.children(library.get_item(lst))[0]) == other_before
    library.restore_version(library.get_item(member["id"]), 1)  # and forward again: still recognised
    assert library.records(library.get_item(member["id"]))[0].description == "changed"


def test_rotated_annotation_is_not_applied(setup, tmp_path):
    """A result whose circular sequence was rotated (a de novo annotator may start it at rns) isn't
    applied to the sequence: the job saves it as a copy (the user's choice)."""
    files, client = setup
    (seq,), _ = library.import_paths([files / "synthetic_mt.gb"])
    item = library.get_item(seq)
    before = Path(item["path"]).read_bytes()
    library.annotation_baselines([item])
    rotated = library.records(item)[0]
    rotated = rotated[100:] + rotated[:100]
    rotated.annotations = {"molecule_type": "DNA", "topology": "circular"}
    from cladia.genbank import write_record
    write_record(rotated, tmp_path / "result.gb")
    assert library.apply_annotation_group([(item, tmp_path / "result.gb")], lambda ids: False) is False
    assert Path(item["path"]).read_bytes() == before
