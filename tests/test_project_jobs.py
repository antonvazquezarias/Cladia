import cladia.request_context as http_request_context
import cladia.execution as owner_execution
"""Project jobs, cancellation and normal-close protection; all data is temporary."""
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cladia import config, jobs, library, projects


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    monkeypatch.setattr(jobs, "JOBS", {})
    monkeypatch.setattr(jobs, "_queue", [])
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    client.post("/api/library/upload", json={"filename": "sample.fa", "text": ">sample\nACGT\n"})
    yield client
    for job in list(jobs.JOBS.values()):
        if job["status"] in ("queued", "running"):
            jobs.cancel(job["id"])
    for job in list(jobs.JOBS.values()):
        assert job["_finished"].wait(10)


def wait(client, response):
    assert response.status_code == 200, response.text
    job = jobs.JOBS[response.json()["job"]]
    assert job["_finished"].wait(10), job
    result = client.get(f"/api/logs/{job['id']}")
    assert result.status_code == 200
    return result.json()


def test_job_roundtrip_preserves_provenance(client):
    source = library.load()[0]["id"]
    provenance = {"kind": "original analysis", "inputs": [{"id": source, "label": "sample"}]}
    library.set_provenance([source], provenance)
    export = wait(client, client.post("/api/project/export", json={"history": True}))
    assert export["status"] == "done" and export["kind"] == "project-export"
    raw = client.get(f"/api/project/download/{export['result']['download']}").content
    inspect = wait(client, client.post("/api/project/upload", content=raw))
    assert inspect["status"] == "done" and inspect["result"]["items"] == 1
    imported = wait(client, client.post("/api/project/import", json={"token": inspect["result"]["token"], "name": "Shared"}))
    assert imported["status"] == "done" and len(imported["outputs"]) == 1
    new = library.get_item(imported["result"]["ids"][0])
    assert new["provenance"]["kind"] == "original analysis"
    assert new["provenance"]["inputs"][0]["id"] == new["id"]
    assert not any(k.startswith("_") for k in client.get("/api/jobs").json()[0])


def test_export_cancellation_keeps_existing_destination(client, tmp_path, monkeypatch):
    target = tmp_path / "existing.zip"
    target.write_bytes(b"keep existing export")
    entered = threading.Event()
    original = projects._copy
    def slow(src, dst, check=None):
        entered.set()
        while True:
            check()
            time.sleep(.01)
    monkeypatch.setattr(projects, "_copy", slow)
    response = client.post("/api/project/export", json={"path": str(target)})
    job = jobs.JOBS[response.json()["job"]]
    assert entered.wait(5)
    monkeypatch.setattr(owner_execution, "container_command", lambda: pytest.fail("local cancellation called a container"))
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert job["_finished"].wait(5)
    assert job["status"] == "cancelled" and target.read_bytes() == b"keep existing export"
    assert not list(projects.transfer_root().glob("*"))


def test_queued_inspect_cancellation_cleans_upload(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocker(job, workdir):
        entered.set()
        assert release.wait(10)
    first = jobs.submit("project-inspect", "Hold transfer lane", blocker)
    assert entered.wait(5)
    try:
        response = client.post("/api/project/upload", content=b"not yet inspected")
        second = jobs.JOBS[response.json()["job"]]
        assert second["status"] == "queued"
        client.post(f"/api/jobs/{second['id']}/cancel")
        assert second["_finished"].wait(5) and second["status"] == "cancelled"
        assert not list(projects.transfer_root().glob("preview-*"))
    finally:
        release.set()
        assert first["_finished"].wait(5)


def test_close_popup_protects_transfer_and_waits_for_cancellation(client, monkeypatch):
    from cladia import server
    from cladia.window import ask_before_closing
    monkeypatch.setattr(http_request_context, "unsaved_windows", lambda: set())
    entered = threading.Event()
    def running(job, workdir):
        entered.set()
        while not job.get("cancel"):
            time.sleep(.01)
        time.sleep(.05)  # simulate rolling back a partly prepared transfer
        raise RuntimeError("cancelled")
    job = jobs.submit("project-export", "Export project ZIP", running)
    assert entered.wait(5)
    class Window:
        answer = False
        prompts = []
        def create_confirmation_dialog(self, title, text):
            self.prompts.append((title, text))
            return self.answer
    view = Window()
    assert ask_before_closing(view, {"main"}, "This window has", main=True) is False
    assert job["status"] == "running" and not job.get("cancel")
    assert view.prompts[-1][0] == "Jobs still running" and "Export project ZIP" in view.prompts[-1][1]
    view.answer = True
    assert ask_before_closing(view, {"main"}, "This window has", main=True) is True
    assert job["_finished"].is_set() and job["status"] == "cancelled"


def test_bad_archive_is_a_failed_job(client):
    result = wait(client, client.post("/api/project/upload", content=b"not a zip"))
    assert result["status"] == "failed" and "Invalid project archive" in result["error"]
    assert not list(projects.transfer_root().glob("preview-*"))


def test_workspace_creation_failure_finishes_job_and_cleans_transfer(client, monkeypatch):
    original = Path.mkdir
    def fail_workspace(path, *args, **kwargs):
        if path.parent == config.APP_DIR / "jobs":
            raise OSError("no room for job workspace")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_workspace)
    result = wait(client, client.post("/api/project/export", json={}))
    assert result["status"] == "failed" and "no room" in result["error"]
    assert not list(projects.transfer_root().glob("preview-*"))


def test_native_export_job(client, tmp_path):
    path = tmp_path / "shared.zip"
    export = wait(client, client.post("/api/project/export", json={"path": str(path)}))
    assert export["status"] == "done" and path.is_file()
    inspected = wait(client, client.post("/api/project/inspect", json={"path": str(path)}))
    assert inspected["status"] == "done" and inspected["result"]["items"] == 1
    client.delete(f"/api/project/preview/{inspected['result']['token']}")


def test_forced_exit_during_native_export_recovers_external_temp(client, tmp_path):
    import os
    import subprocess
    import sys
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"original ZIP stays")
    code = """
import os, sys
from cladia import projects
from cladia.project_api import ExportRequest, export_project
copy = projects._copy
def crash(src, dst, check=None):
    if dst.name.startswith('.cladia-export-'):
        dst.write_bytes(src.read_bytes()[:64])
        os._exit(77)
    return copy(src, dst, check)
projects._copy = crash
export_project(ExportRequest(path=sys.argv[1], background=False))
"""
    done = subprocess.run([sys.executable, "-c", code, str(destination)],
                          env={**os.environ, "CLADIA_DATA": str(config.DATA_DIR)}, timeout=15)
    assert done.returncode == 77
    assert list(tmp_path.glob(".cladia-export-*.tmp"))
    report = projects.recover_transfers()
    assert report["removed"] >= 1 and not report["issues"]
    assert not list(tmp_path.glob(".cladia-export-*.tmp"))
    assert destination.read_bytes() == b"original ZIP stays"
