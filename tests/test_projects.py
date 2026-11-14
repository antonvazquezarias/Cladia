"""Project ZIPs use scratch libraries only; no installed tools or user data."""
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cladia import config, history, library, projects


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    return TestClient(app)


def sequence(client, name="sample"):
    return client.post("/api/library/upload", json={"filename": name + ".fasta", "text": f">{name}\nACGTACGT\n"}).json()["ids"][0]


def archive_bytes(client, **options):
    result = client.post("/api/project/export", json={"background": False, **options})
    assert result.status_code == 200, result.text
    return result.content


def rewrite(raw, mutate):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(files.pop("project.json"))
    mutate(manifest, files)
    files["project.json"] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return out.getvalue()


def upload(client, raw):
    return client.post("/api/project/upload?background=false", content=raw, headers={"Content-Type": "application/zip"})


def snapshot():
    return {p.relative_to(config.DATA_DIR).as_posix(): p.read_bytes()
            for p in config.DATA_DIR.rglob("*") if p.is_file()}


def test_project_roundtrip_preserves_links_sidecars_and_history(client, tmp_path):
    source = sequence(client)
    record = library.records(library.get_item(source))[0]
    record.description = "edited"
    library.save_record(library.get_item(source), record)
    library.add_folder("Study")
    library.add_folder("Empty", "Study")
    library.move([source], "Study")
    aln = library.library_dir() / "alignment.fasta"
    aln.write_text(">sample\nACGTACGT\n>other\nACGTACGA\n")
    alignment = library._register(aln, "alignment", {"sources": {"sample": {"item": source, "offset": 0}}})
    bam = tmp_path / "reads.bam"
    bam.write_bytes(b"bam content")
    Path(str(bam) + ".bai").write_bytes(b"index content")
    reads = tmp_path / "reads.tsv"
    reads.write_text("example reads")
    mapping = library.add_mapping({"reference_item": source, "summary": {}}, "mapped", bam, reads)
    mapping_item = library.get_item(mapping)
    history.checkpoint(mapping, mapping_item["path"])
    data = {"reference_item": source, "summary": {"edited": True}}
    Path(mapping_item["path"]).write_text(json.dumps(data))
    history.checkpoint(mapping, mapping_item["path"])
    nwk = library.library_dir() / "tree.nwk"
    nwk.write_text("(a:1,b:2);")
    tree = library._register(nwk, "tree")
    figure = library.add_figure(library.get_item(tree))  # a figure item drawing the tree
    library.save_figure_settings(library.get_item(figure), {"width": 600})
    library.set_provenance([mapping], {"inputs": [{"id": source, "label": "sample"}]})
    before = snapshot()
    raw = archive_bytes(client, history=True)
    assert snapshot() == before
    preview = upload(client, raw)
    assert preview.status_code == 200, preview.text
    assert preview.json()["items"] == 5 and preview.json()["history"]
    result = client.post("/api/project/import", json={"background": False, "token": preview.json()["token"], "name": "Shared"})
    assert result.status_code == 200, result.text
    ids = result.json()["ids"]
    assert len(ids) == 5 and not set(ids) & {source, alignment, mapping, tree, figure}
    imported = {library.get_item(i)["kind"]: library.get_item(i) for i in ids}
    newsource = imported["sequence"]["id"]
    assert imported["alignment"]["sources"]["sample"]["item"] == newsource
    assert imported["mapping"]["provenance"]["inputs"][0]["id"] == newsource
    assert json.loads(Path(imported["mapping"]["path"]).read_text())["reference_item"] == newsource
    assert (library.library_dir() / imported["mapping"]["bam"]).read_bytes() == bam.read_bytes()
    assert (library.library_dir() / (imported["mapping"]["bam"] + ".bai")).read_bytes() == b"index content"
    assert "Shared/Study/Empty" in library.folders()
    library.restore_version(imported["sequence"], 0)
    assert library.records(library.get_item(newsource))[0].description != "edited"
    library.restore_version(imported["mapping"], 0)
    assert json.loads(Path(imported["mapping"]["path"]).read_text())["reference_item"] == newsource
    assert imported["figure"]["source"] == imported["tree"]["id"]  # still draws the imported tree
    assert len(library.figure_versions(imported["figure"])["versions"]) == 2
    # Existing files stay byte-for-byte identical; only the index gains entries/folders.
    after = snapshot()
    assert all(after[p] == content for p, content in before.items() if p != ".cladia/items.json")
    assert library.check_integrity()["issues"] == []
    preview2 = upload(client, raw).json()
    again = client.post("/api/project/import", json={"background": False, "token": preview2["token"], "name": "Shared"}).json()
    assert again["folder"] == "Shared 2" and not set(again["ids"]) & set(ids)


def test_trash_roundtrip_and_optional_exclusions(client):
    source = sequence(client)
    record = library.records(library.get_item(source))[0]
    record.description = "changed"
    library.save_record(library.get_item(source), record)
    library.remove([source], trash=True)
    raw = archive_bytes(client)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        manifest = json.loads(z.read("project.json"))
        assert not manifest["trash"] and not manifest["files"]
    raw = archive_bytes(client, trash=True, history=True)
    token = upload(client, raw).json()["token"]
    result = client.post("/api/project/import", json={"background": False, "token": token, "name": "Deleted"})
    assert result.status_code == 200, result.text
    tid = next(t["id"] for t in library.trash_items() if t["folder"] == "Deleted")
    restored = library.restore([tid])[0]
    assert restored != source
    assert len(library.saved_versions(library.get_item(restored))["versions"]) == 2
    library.restore_version(library.get_item(restored), 0)
    assert library.records(library.get_item(restored))[0].description != "changed"


@pytest.mark.parametrize("problem", ["version", "traversal", "backslash", "missing", "corrupt", "duplicate_ids", "malformed", "folder"])
def test_bad_archives_do_not_change_library(client, problem):
    sequence(client)
    raw = archive_bytes(client)
    before = snapshot()

    def mutate(m, files):
        key = next(iter(files))
        if problem == "version": m["version"] = 999
        elif problem in ("traversal", "backslash"):
            path = "../escape" if problem == "traversal" else "library\\escape"
            files[path] = b"bad"
            m["files"][path] = {"size": 3, "sha256": hashlib.sha256(b"bad").hexdigest()}
        elif problem == "missing":
            del files[key]
            del m["files"][key]
        elif problem == "corrupt": files[key] = b"x" * len(files[key])
        elif problem == "duplicate_ids": m["entries"].append(m["entries"][0])
        elif problem == "folder": m["folders"] = ["../outside"]
        elif problem == "malformed":
            files[key] = b"not GenBank"
            m["files"][key] = {"size": len(files[key]), "sha256": hashlib.sha256(files[key]).hexdigest()}
    response = upload(client, rewrite(raw, mutate))
    assert response.status_code == 400, response.text
    assert snapshot() == before


def test_reject_duplicate_paths_links_and_limits(client, monkeypatch):
    sequence(client)
    raw = archive_bytes(client)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist(): target.writestr(info.filename, source.read(info))
        target.writestr("PROJECT.JSON", b"{}")
    assert upload(client, out.getvalue()).status_code == 400
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        info = zipfile.ZipInfo("symlink")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "elsewhere")
    assert upload(client, out.getvalue()).status_code == 400
    monkeypatch.setattr(projects, "MAX_BYTES", 1)
    assert upload(client, raw).status_code == 400


def test_failed_publication_rolls_back(client, tmp_path, monkeypatch):
    sequence(client)
    raw = archive_bytes(client, history=True)
    path = tmp_path / "project.zip"
    path.write_bytes(raw)
    before = snapshot()
    def fail(*args, **kwargs): raise OSError("index failure")
    monkeypatch.setattr(library, "_save", fail)
    with pytest.raises(OSError, match="index failure"):
        projects.import_zip(path)
    assert snapshot() == before


def test_failed_file_publication_rolls_back(client, tmp_path, monkeypatch):
    sequence(client, "one")
    sequence(client, "two")
    path = tmp_path / "project.zip"
    path.write_bytes(archive_bytes(client))
    before = snapshot()
    rename = Path.rename
    count = 0
    def fail_second(self, target):
        nonlocal count
        if Path(target).parent == library.library_dir():
            count += 1
            if count == 2:
                raise OSError("publication failure")
        return rename(self, target)
    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError, match="publication failure"):
        projects.import_zip(path)
    assert snapshot() == before


def test_native_export_inspect_and_one_use_token(client, tmp_path):
    sequence(client)
    path = tmp_path / "share.zip"
    result = client.post("/api/project/export", json={"background": False, "path": str(path)})
    assert result.status_code == 200 and path.is_file()
    preview = client.post("/api/project/inspect", json={"background": False, "path": str(path)}).json()
    payload = {"background": False, "token": preview["token"], "name": "Native"}
    assert client.post("/api/project/import", json=payload).status_code == 200
    assert client.post("/api/project/import", json=payload).status_code == 409
    assert client.post("/api/project/export", json={"background": False, "path": str(library.library_dir() / "bad.zip")}).status_code == 400


def test_large_list_history_and_child_links(client, tmp_path, monkeypatch):
    monkeypatch.setattr(library, "BIG_LIST", 1)
    parent = client.post("/api/library/upload", json={"filename": "list.fa", "text": ">a\nACGT\n>b\nACGA\n"}).json()["ids"][0]
    child = library.get_item(parent + "-0")
    library.edit_metadata(child, {"host": "Alga"})
    p = library.library_dir() / "primers.primers.json"
    p.write_text(json.dumps({"template": {"id": child["id"]}}))
    library._register(p, "primers")
    raw = archive_bytes(client, history=True)
    token = upload(client, raw).json()["token"]
    result = client.post("/api/project/import", json={"background": False, "token": token}).json()
    members = [library.get_item(i) for i in result["ids"]]
    newparent = next(m for m in members if m["kind"] == "list")
    primer = next(m for m in members if m["kind"] == "primers")
    assert json.loads(Path(primer["path"]).read_text())["template"]["id"] == newparent["id"] + "-0"
    newchild = library.get_item(newparent["id"] + "-0")
    library.restore_version(newchild, 0)
    assert library.record_metadata(library.get_item(newchild["id"]))["host"] == ""


def test_other_item_kinds_and_attachments(client, tmp_path):
    source = sequence(client)
    xmfa = tmp_path / "genomes.xmfa"
    xmfa.write_text("# example XMFA\n")
    comparison = library.add_comparison({"genomes": [{"item": source}], "blocks": []}, "comparison", xmfa)
    graph = library.add_graph("H\tVN:Z:1.0\nS\tnode\tACGT\n", "graph")
    for kind, data in (("blast", {"query": {"item": source}}), ("primers", {"template": {"id": source}})):
        target = library.library_dir() / f"result.{kind}.json"
        target.write_text(json.dumps(data))
        library._register(target, kind)
    entries = library.load()
    e = next(e for e in entries if e["id"] == graph)
    e["hits"] = "graph.hits.json"
    (library.library_dir() / e["hits"]).write_text(json.dumps({"queries": [{"item": source}]}))
    e = next(e for e in entries if e["id"] == source)
    e["trace"] = "sample.trace.json"
    (library.library_dir() / e["trace"]).write_text(json.dumps({"quality": [30] * 8}))
    e["original"] = {"path": "D:/colleague/original.gb", "format": "genbank"}
    library._save(entries)
    raw = archive_bytes(client)
    token = upload(client, raw).json()["token"]
    result = client.post("/api/project/import", json={"background": False, "token": token})
    assert result.status_code == 200, result.text
    imported = {library.get_item(i)["kind"]: library.get_item(i) for i in result.json()["ids"]}
    newsource = imported["sequence"]["id"]
    assert not imported["sequence"]["original"]
    assert json.loads((library.library_dir() / imported["graph"]["hits"]).read_text())["queries"][0]["item"] == newsource
    assert json.loads(Path(imported["comparison"]["path"]).read_text())["genomes"][0]["item"] == newsource
    assert (library.library_dir() / imported["comparison"]["xmfa"]).read_bytes() == xmfa.read_bytes()
    assert library.check_integrity()["issues"] == []
    # Graph hit sidecars also survive the ordinary Trash restore path.
    library.remove([imported["graph"]["id"]], trash=True)
    tid = library.trash_items()[0]["id"]
    restored = library.get_item(library.restore([tid])[0])
    assert (library.library_dir() / restored["hits"]).is_file()


def test_history_checksum_and_missing_sidecar_rejected(client):
    source = sequence(client)
    library.edit_metadata(library.get_item(source), {"host": "Alga"})
    raw = archive_bytes(client, history=True)
    def corrupt(m, files):
        blob = next(n for n in files if n.endswith(".gz"))
        import gzip
        files[blob] = gzip.compress(b"wrong saved content")
        m["files"][blob] = {"size": len(files[blob]), "sha256": hashlib.sha256(files[blob]).hexdigest()}
    assert upload(client, rewrite(raw, corrupt)).status_code == 400
    def missing(m, files): m["entries"][0]["trace"] = "missing.trace.json"
    assert upload(client, rewrite(raw, missing)).status_code == 400


def test_cancelled_preview_is_discarded(client):
    sequence(client)
    token = upload(client, archive_bytes(client)).json()["token"]
    assert client.delete(f"/api/project/preview/{token}").json()["discarded"]
    assert client.post("/api/project/import", json={"background": False, "token": token}).status_code == 409


def test_folder_created_during_import_is_preserved(client, tmp_path, monkeypatch):
    import threading
    sequence(client)
    path = tmp_path / "project.zip"
    path.write_bytes(archive_bytes(client))
    entered, release = threading.Event(), threading.Event()
    original = library._save
    errors = []
    def gated_save(*args, **kwargs):
        if threading.current_thread() is importer:
            entered.set()
            if not release.wait(10): raise RuntimeError("probe timed out")
        return original(*args, **kwargs)
    def run():
        try: projects.import_zip(path)
        except Exception as exc: errors.append(exc)
    importer = threading.Thread(target=run)
    monkeypatch.setattr(library, "_save", gated_save)
    importer.start()
    try:
        assert entered.wait(10)
        creator = threading.Thread(target=lambda: library.add_folder("Created during import"))
        creator.start()
    finally:
        release.set()
        importer.join(10)
    creator.join(10)
    assert not importer.is_alive() and not creator.is_alive() and not errors
    assert "Created during import" in library.folders()


@pytest.mark.parametrize("committed", [False, True])
def test_startup_recovery_after_forced_process_exit(client, tmp_path, committed):
    import os
    import subprocess
    import sys
    source = sequence(client, "survivor")
    old = Path(library.get_item(source)["path"]).read_bytes()
    path = tmp_path / "crash.zip"
    path.write_bytes(archive_bytes(client, history=True))
    code = """
import os, sys
from cladia import library, projects
save = library._save
def crash(*args, **kwargs):
    if sys.argv[2] == 'True': save(*args, **kwargs)
    os._exit(77)
library._save = crash
projects.import_zip(sys.argv[1], 'Crash project')
"""
    done = subprocess.run([sys.executable, "-c", code, str(path), str(committed)],
                          env={**os.environ, "CLADIA_DATA": str(config.DATA_DIR)}, timeout=15)
    assert done.returncode == 77
    assert list(projects.transfer_root().glob("import-*"))
    report = projects.recover_transfers()
    assert report["removed"] >= 1 and not report["issues"]
    assert len(library.load()) == (2 if committed else 1)
    assert Path(library.get_item(source)["path"]).read_bytes() == old
    assert not library.check_integrity()["unreferenced"]
    assert not list(projects.transfer_root().glob("import-*"))


def test_recovery_preserves_modified_or_registered_files(client, tmp_path):
    source = sequence(client)
    path = Path(library.get_item(source)["path"])
    stage = projects.transfer_root() / "import-recoverytest"
    stage.mkdir()
    projects._journal(stage, {"format": "Cladia import transaction", "ids": ["notpresent"], "folder": "Absent",
                              "targets": [{"path": path.relative_to(config.DATA_DIR).as_posix(),
                                           "files": {"": projects._hash(path)}}]})
    before = path.read_bytes()
    result = projects.recover_transfers()
    assert result["issues"] and path.read_bytes() == before and stage.exists()
    orphan = library.library_dir() / "orphan.gb"
    orphan.write_bytes(b"modified by somebody else")
    projects._journal(stage, {"format": "Cladia import transaction", "ids": ["notpresent"], "folder": "Absent",
                              "targets": [{"path": "library/orphan.gb", "files": {"": "wrong hash"}}]})
    assert projects.recover_transfers()["issues"] and orphan.read_bytes() == b"modified by somebody else"


def test_export_copy_does_not_block_writes_and_rejects_changed_snapshot(client, tmp_path, monkeypatch):
    import threading
    sequence(client)
    entered, release = threading.Event(), threading.Event()
    original, errors = projects._copy, []
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(projects, "_copy", paused)
    def run():
        try: projects.export_zip(tmp_path / "snapshot.zip")
        except Exception as exc: errors.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        # This completes while copying is deliberately paused; it cannot wait
        # for an export-wide write lock.
        library.add_folder("New folder")
    finally:
        release.set()
        thread.join(5)
    assert errors and "changed during export" in errors[0]
    assert "New folder" in library.folders()


def test_import_validation_does_not_block_writes_and_merges_current_folders(client, tmp_path, monkeypatch):
    import threading
    sequence(client)
    path = tmp_path / "snapshot.zip"
    path.write_bytes(archive_bytes(client))
    entered, release = threading.Event(), threading.Event()
    original, errors, results = projects.validate_zip, [], []
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(projects, "validate_zip", paused)
    def run():
        try: results.append(projects.import_zip(path, "Shared"))
        except Exception as exc: errors.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        library.add_folder("Shared")
    finally:
        release.set()
        thread.join(5)
    assert not errors and results[0]["folder"] == "Shared 2"
    assert {"Shared", "Shared 2"} <= set(library.folders())
