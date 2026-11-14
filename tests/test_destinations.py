import cladia.job_outputs as owner_job_outputs
"""Destination rules and folder edits during background work (isolated libraries)."""
import threading
import time

import pytest
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from fastapi.testclient import TestClient

from cladia import config, jobs, library
from cladia.genbank import write_record
from cladia.server import app


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    monkeypatch.setattr(jobs, "JOBS", {})
    library.ensure_folders()
    for folder in ["A", "B", "Reference", "Results"]:
        library.add_folder(folder)
    def sequence(name, folder):
        ids, errors = library.import_text(name + ".fa", f">{name}\n" + "ACGT" * 100 + "\n", folder)
        assert not errors
        return library.get_item(ids[0])
    a, b, ref = sequence("a", "A"), sequence("b", "B"), sequence("ref", "Reference")
    return tmp_path, TestClient(app), a, b, ref


def test_combined_jobs_require_destination(storage, monkeypatch):
    _, client, a, b, _ = storage
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda *args, **kwargs: submitted.append(kwargs) or {"id": "test"})
    bodies = [
        {"operation": "align.new", "engine": "mafft", "inputs": {"sequences": [a["id"], b["id"]]}},
        {"operation": "align.compare", "engine": "mauve", "inputs": {"sequences": [a["id"], b["id"]]}},
        {"operation": "assemble.one", "engine": "sanger", "inputs": {"reads": [a["id"], b["id"]]}},
        {"operation": "primers.test", "engine": "primer_test",
         "inputs": {"sequences": [a["id"], b["id"]], "forward": ["ACGTACGTACGT"], "reverse": ["ACGTACGTACGT"]}},
    ]
    for body in bodies:
        response = client.post("/api/tool-registry/run", json=body)
        assert response.status_code == 400, response.text
        assert "different folders" in response.json()["detail"]
        assert not submitted
        for folder in ["Results", None]:
            response = client.post("/api/tool-registry/run", json={**body, "folder": folder})
            assert response.status_code == 200, response.text
            assert submitted.pop()["folder"] == folder
        response = client.post("/api/tool-registry/run", json={**body, "folder": "Gone"})
        assert response.status_code == 400
        assert not submitted


def test_reference_does_not_choose_destination(storage, monkeypatch):
    _, client, a, _, ref = storage
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda *args, **kwargs: submitted.append(kwargs) or {"id": "test"})
    response = client.post("/api/tool-registry/run", json={"operation": "align.add", "engine": "mafft",
        "inputs": {"sequences": [a["id"]], "add_to": [ref["id"]]}})
    assert response.status_code == 200, response.text
    assert submitted[-1]["folder"] == "A"
    reads = storage[0] / "reads.fastq"
    reads.write_text("@r\nACGT\n+\nIIII\n")
    response = client.post("/api/tool-registry/run", json={"operation": "map.reads", "engine": "minimap2",
        "inputs": {"reference": [ref["id"]], "read_files": [str(reads)]}, "folder": "Results"})
    assert response.status_code == 200, response.text
    assert submitted[-1]["folder"] == "Results"


def test_mixed_group_and_graph_validate_before_creation(storage):
    _, client, a, b, _ = storage
    for route, body in [("/api/library/group", {"name": "group"}), ("/api/graph/from-contigs", {"name": "graph"})]:
        before = {it["id"] for it in library.items()}
        response = client.post(route, json={**body, "ids": [a["id"], b["id"]]})
        assert response.status_code == 400, response.text
        assert {it["id"] for it in library.items()} == before
    response = client.post("/api/library/group", json={"ids": [a["id"], b["id"]], "name": "group", "folder": "Results"})
    assert response.status_code == 200, response.text
    assert library.folder_of(library.get_item(response.json()["id"])) == "Results"


@pytest.mark.parametrize("fallback", [False, True])
def test_annotation_copies_keep_each_source_folder(storage, monkeypatch, fallback):
    path, _, a, b, _ = storage
    results = []
    for item in [a, b]:
        target = path / (item["name"] + ".gb")
        write_record(library.records(item)[0], target)
        results.append((item, target))
    monkeypatch.setattr(library, "apply_annotation_group", lambda *args: False)
    job = {"folder": "A", "outputs": [], "log": ""}
    owner_job_outputs.annotated(job, path, results, "_copy", None, apply=fallback)
    assert [library.folder_of(library.get_item(o["id"])) for o in job["outputs"]] == ["A", "B"]
    library.move([b["id"]], "Results")
    jobs._place_outputs(job, job["outputs"])
    assert [library.folder_of(library.get_item(o["id"])) for o in job["outputs"]] == ["A", "Results"]


@pytest.mark.parametrize("explicit_root", [False, True])
def test_take_out_preserves_sources_unless_destination_explicit(storage, explicit_root):
    _, client, _, _, _ = storage
    members = []
    for folder in ["A", "B"]:
        records = [SeqRecord(Seq("ACGT"), id=folder + str(i), name=folder + str(i), annotations={"molecule_type": "DNA"}) for i in range(2)]
        item_id = library.add_list(records, folder + " list")
        library.move([item_id], folder)
        members.append(library.children(library.get_item(item_id))[0]["id"])
    body = {"ids": members, **({"folder": None} if explicit_root else {})}
    response = client.post("/api/library/take-out", json=body)
    assert response.status_code == 200, response.text
    assert [library.folder_of(library.get_item(i)) for i in response.json()["ids"]] == ([None, None] if explicit_root else ["A", "B"])


@pytest.mark.parametrize("edit", ["rename", "move", "delete"])
def test_running_job_destination_follows_folder_edits(storage, edit):
    path, _, _, _, _ = storage
    library.add_folder("Child", "A")
    job = {"id": "folder-test", "kind": "test", "title": "test", "status": "running", "created": time.time(),
           "log": "", "error": None, "outputs": [], "folder": "A/Child"}
    jobs.JOBS[job["id"]] = job
    ready, release = threading.Event(), threading.Event()
    def work(job, workdir):
        ready.set()
        assert release.wait(10)
        output = workdir / "out.fa"; output.write_text(">result\nACGT\n")
        owner_job_outputs.add_output(job, output, "result")
    thread = threading.Thread(target=jobs._run, args=(job, work, {}));thread.start()
    try:
        assert ready.wait(10)
        if edit == "rename":
            library.rename_folder("A", "Renamed"); expected = "Renamed/Child"
        elif edit == "move":
            library.move_folder("A", "B"); expected = "B/A/Child"
        else:
            library.delete_folder("A"); expected = "Child"
        assert job["folder"] == expected
    finally:
        release.set(); thread.join(10)
    assert not thread.is_alive()
    assert job["status"] == "done", job.get("error")
    assert library.folder_of(library.get_item(job["outputs"][0]["id"])) == expected
    assert "A/Child" not in library.folders()


def test_completed_function_still_tracks_destination_until_publication(storage):
    _, _, _, _, _ = storage
    job = {"status": "done", "_destination_pending": True, "folder": "A", "outputs": [{"folder": "A"}]}
    jobs.JOBS["publishing"] = job
    library.rename_folder("A", "Changed")
    assert job["folder"] == "Changed" and job["outputs"][0]["folder"] == "Changed"


def test_pasted_results_preserve_individual_sources_or_choose_combined(storage):
    _, client, a, b, _ = storage
    sequences = [{"source": it["id"], "name": it["name"] + "_extract", "data": {"sequence": "ACGT", "features": []}} for it in [a,b]]
    response = client.post("/api/library/new-sequences", json={"sequences": sequences})
    assert response.status_code == 200, response.text
    assert [library.folder_of(library.get_item(i)) for i in response.json()["ids"]] == ["A", "B"]
    before = {it["id"] for it in library.items()}
    response = client.post("/api/library/new-sequences", json={"sequences": sequences, "list_name": "combined"})
    assert response.status_code == 400
    assert {it["id"] for it in library.items()} == before
    response = client.post("/api/library/new-sequences", json={"sequences": sequences, "list_name": "combined", "folder": None})
    assert response.status_code == 200, response.text
    assert library.folder_of(library.get_item(response.json()["ids"][0])) is None


def test_queued_and_root_destinations_follow_folder_removal(storage, monkeypatch):
    _, _, _, _, _ = storage
    monkeypatch.setattr(jobs, "_schedule", lambda: None)
    # Avoid leaving fake queued entries for unrelated tests.
    monkeypatch.setattr(jobs, "_queue", [])
    job = jobs.submit("test", "queued", lambda *args: None, folder="A")
    root = jobs.submit("test", "root", lambda *args: None)
    library.delete_folder("A")
    assert job["folder"] is None and root["folder"] is None
    library.add_folder("A")  # reusing the name must not capture an older job
    assert job["folder"] is None


def test_submission_folder_disappearing_returns_clear_error(storage, monkeypatch):
    _, client, a, _, ref = storage
    submit = jobs.submit
    def racing(*args, **kwargs):
        library.delete_folder(kwargs["folder"])
        return submit(*args, **kwargs)
    monkeypatch.setattr(jobs, "submit", racing)
    response = client.post("/api/tool-registry/run", json={"operation": "align.add", "engine": "mafft",
        "inputs": {"sequences": [a["id"]], "add_to": [ref["id"]]}, "folder": "Results"})
    assert response.status_code == 400, response.text
    assert "no longer exists" in response.json()["detail"]
    assert not jobs.JOBS
