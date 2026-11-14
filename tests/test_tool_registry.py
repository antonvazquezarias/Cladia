import cladia.execution as owner_execution
"""Generated tool contracts, engine discovery and optional tools."""
from fastapi.testclient import TestClient
import pytest

from cladia import config, jobs, library
from cladia.server import app
from cladia.tool_registry import build_registry


@pytest.mark.parametrize("name", ["mafft", "annotate-from-reference", "orfs", "primers",
                                "blast", "map", "mauve", "assemble", "tree", "download"])
def test_superseded_tool_route_is_removed(name):
    assert not any(getattr(route, "path", None) == f"/api/jobs/{name}" and "POST" in getattr(route, "methods", ())
                   for route in app.routes)
    assert TestClient(app).post(f"/api/jobs/{name}", json={}).status_code in {404, 405}


def test_align_registry_and_invalid_plugin(tmp_path, caplog):
    bad = tmp_path / "bad.py"
    bad.write_text("ENGINE = {'id': 'bad', 'label': 'Bad', 'blurb': '', 'supports': ['missing'], 'options': [],"
                   "'lane': 'compute', 'cores': 1}\n"
                   "def run(*args, **kwargs): pass\n")
    registry = build_registry(tmp_path)
    assert not registry.engines
    assert "Unknown operation ids: missing" in caplog.text
    registry = build_registry(installed={})
    assert [o["id"] for o in registry.operationsFor("align")] == ["align.new", "align.add", "align.compare"]  # (Trim columns once trimAl or ClipKIT is installed)
    assert [e["id"] for e in registry.enginesFor("align.new")] == ["mafft"]  # (Clustal Omega once installed)
    assert [e["id"] for e in registry.enginesFor("tree.build")] == ["iqtree", "fasttree", "distance_tree"]
    assert [e["id"] for e in registry.enginesFor("map.reads")] == ["minimap2"]
    assert [e["id"] for e in registry.enginesFor("annotate.orfs")] == ["orfs"]
    assert [e["id"] for e in registry.enginesFor("variants.alignment_snps")] == ["alignment_snps"]  # built in
    assert [o["group"] for o in registry.operationsFor("assemble")] == ["Sanger reads", "Sanger reads"]
    assert "crispr" not in [category["id"] for category in registry.public()["categories"]]
    public = registry.public()
    assert [o["id"] for o in public["operations"]] == ["annotate.denovo", "annotate.reference", "annotate.orfs",
        "align.new", "align.add", "align.compare", "tree.build", "map.reads", "assemble.one", "assemble.batch",
        "variants.alignment_snps", "primers.design", "primers.test", "blast.search", "download.accessions", "download.search"]
    assert all("run" not in e and "install" not in e and "uninstall" not in e for e in public["engines"])


def test_one_file_engine_appears_and_disappears(tmp_path):
    plugin = tmp_path / "example.py"
    plugin.write_text("from cladia import engine_api as api\n"
                      "ENGINE = {'id':'example','label':'Example','blurb':'',"
                      "'supports':['align.new'],'options':[],'lane':'compute','cores':1}\n"
                      "def run(job, workdir, operation, items, output, add_to=None):\n"
                      "    api.steps(job, 'Saving result')\n"
                      "    path = workdir / 'result.fasta'\n"
                      "    path.write_text('>A\\nACGT\\n>B\\nACGT\\n')\n"
                      "    api.add_output(job, path, output, kind='alignment')\n")
    registry = build_registry(tmp_path)
    assert [e["id"] for e in registry.enginesFor("align.new")] == ["example"]
    from cladia import engine_api
    captured = []
    original = engine_api.add_output
    engine_api.add_output = lambda job, path, name, **kw: captured.append((path.read_text(), name, kw))
    try:
        registry.engines["example"]["run"]({"progress": {}}, tmp_path, "align.new", [], "Example alignment")
    finally:
        engine_api.add_output = original
    assert captured == [(">A\nACGT\n>B\nACGT\n", "Example alignment", {"kind": "alignment"})]
    plugin.unlink()
    assert build_registry(tmp_path).enginesFor("align.new") == []


def test_default_registry_is_cached_until_a_plugin_changes():
    from cladia.tool_registry import invalidate_registry
    invalidate_registry()
    first = build_registry()
    assert build_registry() is first
    invalidate_registry()
    assert build_registry() is not first


def test_optional_tool_settings_are_validated_and_secrets_are_masked(tmp_path, monkeypatch):
    from cladia import tool_registry
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    plugin = tmp_path / "example.py"
    plugin.write_text("ENGINE = {'id':'example','label':'Example','blurb':'',"
                      "'supports':['align.new'],'options':[],'lane':'compute','cores':1,"
                      "'optional':True,'settings':[{'key':'api_key','label':'API key','type':'secret'},"
                      "{'key':'endpoint','label':'Endpoint','type':'text'},"
                      "{'key':'enabled','label':'Enabled','type':'bool'}]}\n"
                      "def run(*args, **kwargs): pass\n"
                      "def install(*args, **kwargs): pass\n"
                      "def uninstall(*args, **kwargs): pass\n")
    registry = build_registry(tmp_path, installed={"example": []})
    monkeypatch.setattr(tool_registry, "build_registry", lambda: registry)
    client = TestClient(app)
    url = "/api/tool-engines/example/settings"
    assert client.get(url).json() == {"api_key": False, "endpoint": "", "enabled": False}
    assert client.put(url, json={"api_key": "secret-value", "endpoint": "local", "enabled": True}).status_code == 200
    assert client.get(url).json() == {"api_key": True, "endpoint": "local", "enabled": True}
    assert tool_registry.tool_setting("example", "api_key") == "secret-value"
    assert client.put(url, json={"unexpected": "x"}).status_code == 400
    assert client.put(url, json={"enabled": "yes"}).status_code == 400
    assert client.put(url, json={"api_key": None}).status_code == 200
    assert tool_registry.tool_setting("example", "api_key") is None


def test_an_optional_engine_needs_its_uninstall(tmp_path, caplog):
    (tmp_path / "half.py").write_text("ENGINE = {'id':'half','label':'Half','blurb':'','supports':['align.new'],"
                                      "'options':[],'lane':'compute','cores':1,'optional':True}\n"
                                      "def run(*a, **k): pass\ndef install(*a, **k): pass\n")
    assert not build_registry(tmp_path).engines
    assert "install and uninstall" in caplog.text


def test_blast_and_download_dispatch_through_registered_engines(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "query.fasta"
    source.write_text(">query\nACGTACGTACGTACGT\n")
    ids, errors = library.import_paths([source])
    assert not errors
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, kw)) or {"id": "job"})
    client = TestClient(app)
    blast = {"operation": "blast.search", "engine": "ncbi_blast", "inputs": {"queries": ids},
             "options": {"program": "megablast", "database": "core_nt", "max_hits": 20}}
    response = client.post("/api/tool-registry/run", json=blast)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "blast"
    assert client.post("/api/tool-registry/run", json={**blast, "engine": "local_blast"}).status_code == 400
    assert client.post("/api/tool-registry/run", json={**blast, "options":
        {"program": "blastx", "database": "core_nt", "max_hits": 20}}).status_code == 400
    download = {"operation": "download.accessions", "engine": "genbank_download",
                "inputs": {"accessions": ["NC_001677", "MK000001.1"]},
                "options": {"as_list": False}, "output": "Records"}
    response = client.post("/api/tool-registry/run", json=download)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "download"
    assert captured[-1][1]["as_list"] is False
    assert captured[-1][1]["output"] == "Records"
    assert client.post("/api/tool-registry/run", json={**download,
        "operation": "download.search", "inputs": {"accessions": ["bad query"]}}).status_code == 400


def test_optional_engine_install_job_controls_visibility(tmp_path, monkeypatch):
    from cladia import engine_api, tool_registry
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    tool_registry.invalidate_registry()
    assert not build_registry().enginesFor("assemble.organelle")
    client = TestClient(app)
    catalog = client.get("/api/tool-engines").json()
    row = next(e for e in catalog if e["id"] == "getorganelle")
    assert not row["installed"] and not row["builtin"] and row["type"] == "Assemble"
    assert row["packages"] == "getorganelle=1.7.7.1" and not row["outdated"]
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, func, kw)) or {"id": "install"})
    response = client.post("/api/tool-engines/install", json={"engines": [{"id": "getorganelle", "choices": ["other_pt"]}]})
    assert response.status_code == 200, response.text
    assert captured[0][0] == "tool-install"
    assert not build_registry().enginesFor("assemble.organelle")  # only once the job succeeds
    calls = []
    monkeypatch.setattr(engine_api, "conda_install", lambda job, workdir, engine: calls.append(("conda", engine["id"])))
    monkeypatch.setattr(engine_api, "volume_create", lambda *a: (True, ""))
    monkeypatch.setattr(engine_api, "run_container", lambda job, image, args, *a, **kw: calls.append(("run", args[-4:])))
    captured[0][1]({"log": "", "progress": {"at": 0}}, tmp_path)
    assert calls[0] == ("conda", "getorganelle") and calls[1] == ("run", ["--add", "other_pt", "--config-dir", "/organelle-db"])
    registry = build_registry()
    assert [e["id"] for e in registry.enginesFor("assemble.organelle")] == ["getorganelle"]
    assert [o["group"] for o in registry.operationsFor("assemble")] == ["Sanger reads", "Sanger reads", "Organelles"]
    assert next(e for e in registry.public()["engines"] if e["id"] == "getorganelle")["installedChoices"] == ["other_pt"]
    assert not registry.outdated(registry.engines["getorganelle"])
    more = client.post("/api/tool-engines/install", json={"engines": [{"id": "getorganelle", "choices": ["embplant_mt"]}]})
    assert more.status_code == 200, more.text
    captured[1][1]({"log": "", "progress": {"at": 0}}, tmp_path)
    assert tool_registry.installed_engines()["getorganelle"] == ["other_pt", "embplant_mt"]
    # an app update with another lock: the tool shows as outdated until updated
    monkeypatch.setattr("cladia.conda.lock_hash", lambda engine_id, version: "new")
    tool_registry.invalidate_registry()
    assert build_registry().outdated(build_registry().engines["getorganelle"])
    removed = []
    monkeypatch.setattr(engine_api, "conda_uninstall", lambda job, workdir, engine: removed.append(engine["id"]))
    monkeypatch.setattr(engine_api, "volume_remove", lambda name: removed.append(name))
    response = client.post("/api/tool-engines/getorganelle/uninstall")
    assert response.status_code == 200, response.text
    assert captured[2][0] == "tool-uninstall"
    captured[2][1]({"log": ""}, tmp_path)
    assert removed[0] == "getorganelle" and removed[1].startswith("cladia-getorganelle-databases-")
    assert not build_registry().enginesFor("assemble.organelle")
    assert "getorganelle" not in tool_registry.installed_engines()
    assert client.post("/api/tool-engines/getorganelle/uninstall").status_code == 400  # (not installed any more)


def test_optional_engine_install_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    client = TestClient(app)
    assert client.post("/api/tool-engines/install", json={"engines": [{"id": "getorganelle", "choices": ["bogus"]}]}).status_code == 400
    assert client.post("/api/tool-engines/install", json={"engines": [{"id": "mafft", "choices": []}]}).status_code == 400
    assert client.post("/api/tool-registry/run", json={"operation": "assemble.organelle", "engine": "getorganelle",
        "inputs": {"read_files": ["missing.fastq"]}, "options": {}, "output": "test"}).status_code == 400


def test_failed_optional_install_stays_hidden(tmp_path, monkeypatch):
    from cladia import engine_api, tool_registry
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    tool_registry.invalidate_registry()
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append(func) or {"id": "install"})
    response = TestClient(app).post("/api/tool-engines/install", json={"engines": [{"id": "clustal_omega", "choices": []}]})
    assert response.status_code == 200, response.text
    monkeypatch.setattr(engine_api, "conda_install", lambda *a: (_ for _ in ()).throw(RuntimeError("download failed")))
    with pytest.raises(RuntimeError, match="download failed"):
        captured[0]({"log": "", "progress": {"at": 0}}, tmp_path)
    assert [e["id"] for e in build_registry().enginesFor("align.new")] == ["mafft"]


def test_getorganelle_runner_creates_library_outputs(tmp_path, monkeypatch):
    from cladia.tool_plugins import getorganelle
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    reads = tmp_path / "sample_R1.fastq"
    reads.write_text("@r1\nACGT\n+\nIIII\n")
    workdir = tmp_path / "work"
    workdir.mkdir()

    def fake_tool(job, image, args, directory, mounts=None):
        assert args[0] == "env" and args[2] == "get_organelle_from_reads.py"
        assert mounts["volume:test-getorganelle-volume"] == "/organelle-db"
        result = directory / "assembly"
        result.mkdir()
        (result / "get_org.log.txt").write_text(
            "2026-11-03 19:47:58,358 - INFO: Tasting 100000+100000 reads ...\n")
        (result / "embplant_pt.complete.graph1.path_sequence.fasta").write_text(">plastid\nACGT\n")

    monkeypatch.setattr(owner_execution, "tool", fake_tool)
    monkeypatch.setattr(getorganelle, "_volume", lambda: "test-getorganelle-volume")
    job = {"id": "test", "log": "", "outputs": [], "cores": 2}
    getorganelle.run(job, workdir, "assemble.organelle", [], "sample plastid",
                     groups=[{"name": "sample", "files": [str(reads)]}], organelle_type="other_pt")
    assert job["live_log"] == str(workdir / "assembly" / "get_org.log.txt")
    job["status"] = "running"
    job["progress"]["at"] = 0
    from cladia import job_progress
    assert job_progress.current_progress(job)["detail"] == "Tasting 100000+100000 reads ..."
    jobs.JOBS[job["id"]] = job
    try:
        response = TestClient(app).get(f"/api/logs/{job['id']}")
        assert response.status_code == 200
        assert "Tasting 100000+100000 reads" in response.json()["log"]
        assert next(row for row in TestClient(app).get("/api/jobs").json()
                    if row["id"] == job["id"])["has_log"]
    finally:
        jobs.JOBS.pop(job["id"], None)
    assert len(job["outputs"]) == 1
    assert library.get_item(job["outputs"][0]["id"])["kind"] == "sequence"


def test_registered_getorganelle_rejects_uninstalled_database(tmp_path, monkeypatch):
    from cladia import tool_registry
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    tool_registry.save_installed_engine("getorganelle", ["other_pt"])
    tool_registry.invalidate_registry()
    reads = tmp_path / "sample.fastq"
    reads.write_text("@r1\nACGT\n+\nIIII\n")
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, kw)) or {"id": "assembly"})
    body = {"operation": "assemble.organelle", "engine": "getorganelle",
            "inputs": {"read_files": [str(reads)]}, "options": {"organelle_type": "other_pt"}, "output": "plastid"}
    client = TestClient(app)
    response = client.post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "getorganelle" and captured[-1][1]["groups"]
    body["options"]["organelle_type"] = "animal_mt"
    assert client.post("/api/tool-registry/run", json=body).status_code == 400
    body["options"]["organelle_type"] = "other_pt"
    body["inputs"]["read_files"] = [str(reads.with_suffix(".fasta"))]
    assert client.post("/api/tool-registry/run", json=body).status_code == 400


def test_conda_locks_and_scripts():
    from cladia import conda
    for engine, version in (("clustal_omega", "1.2.4"), ("krait", "1.5.0"), ("getorganelle", "1.7.7.1")):
        text = conda.lock_text(engine, version)  # (checked: explicit, conda-forge / bioconda only, checksums)
        assert "repo.anaconda.com" not in text and "/main/" not in text
    assert conda.check_lock("@EXPLICIT\nhttps://repo.anaconda.com/pkgs/main/linux-64/x-1.conda#" + "0" * 32)
    assert conda.check_lock("@EXPLICIT\nhttps://conda.anaconda.org/bioconda/linux-64/x-1.conda")  # no checksum
    command = conda.command("krait", "1.5.0", ["pytrf", "--version"])
    assert command[0] == "env" and command[1].startswith("PATH=/opt/cladia-conda/envs/krait-1.5.0-") and command[2:] == ["pytrf", "--version"]
    script = conda.install_script("krait", "1.5.0", "abc")
    assert conda.MICROMAMBA_SHA256 in script and "--override-channels -c conda-forge -c bioconda" in script
    assert script.index("micromamba create") < script.index("echo abc >")  # complete only once .cladia-lock is written


def test_registered_align_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "pair.fasta"
    source.write_text(">one\nACGT\n>two\nACGA\n")
    ids, errors = library.import_paths([source])
    assert not errors
    leaves = [it["id"] for it in library.items() if it.get("parent") == ids[0]]
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, title, func, kw)) or {"id": "test"})
    client = TestClient(app)
    body = {"operation": "align.new", "engine": "mafft", "inputs": {"sequences": leaves},
            "options": {"strategy": "auto", "adjust_direction": True}, "output": "test"}
    response = client.post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert captured[0][0] == "mafft" and captured[0][3]["output"] == "test"
    assert captured[0][3]["resources"] == ("compute", None)
    assert captured[0][3]["items"][0]["id"] in leaves
    whole_list = {**body, "inputs": {"sequences": ids}}
    assert client.post("/api/tool-registry/run", json=whole_list).status_code == 200
    bad = {**body, "options": {"strategy": "not-a-strategy"}}
    assert client.post("/api/tool-registry/run", json=bad).status_code == 400


def test_registered_orf_dispatch_keeps_annotation_job_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "gene.fasta"
    source.write_text(">gene\nATGAAATAG\n")
    ids, errors = library.import_paths([source])
    assert not errors
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, kw)) or {"id": "orfs"})
    body = {"operation": "annotate.orfs", "engine": "orfs", "inputs": {"sequences": ids},
            "options": {"code": 0, "min_codons": 2}, "save_copy": True}
    response = TestClient(app).post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert captured[0][0] == "orfs" and captured[0][1]["apply"] is False
    assert captured[0][1]["min_codons"] == 2


def test_registered_primer_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "template.fasta"
    source.write_text(">template\n" + "ACGT" * 100 + "\n")
    ids, errors = library.import_paths([source])
    assert not errors
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, kw)) or {"id": "primer"})
    client = TestClient(app)
    design = {"operation": "primers.design", "engine": "primer3", "inputs": {"template": ids},
              "options": {"product_min": 100, "product_max": 300}}
    response = client.post("/api/tool-registry/run", json=design)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "primers" and captured[-1][1]["product_min"] == 100
    test = {"operation": "primers.test", "engine": "primer_test",
            "inputs": {"sequences": ids, "forward": ["ACGTACGTACGT"], "reverse": ["ACGTACGTACGT"]},
            "options": {"three_prime": None}}
    response = client.post("/api/tool-registry/run", json=test)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "pcr" and captured[-1][1]["three_prime"] is None


def test_registered_tree_engines_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "alignment.fasta"
    source.write_text(">one\nACG-T\n>two\nACGAT\n>three\nACGGT\n")
    ids, errors = library.import_paths([source])
    assert not errors
    item = library.get_item(ids[0])
    assert item["kind"] == "alignment"
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, func, kw)) or {"id": "tree"})
    client = TestClient(app)
    body = {"operation": "tree.build", "engine": "fasttree", "inputs": {"alignment": ids},
            "options": {}, "output": "a tree"}
    response = client.post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert captured[0][0] == "tree" and captured[0][2]["items"][0]["id"] == ids[0]
    assert captured[0][2]["resources"] == ("compute", None)
    body["engine"] = "iqtree"
    body["options"] = {"outgroup": "one"}
    assert client.post("/api/tool-registry/run", json=body).status_code == 200
    body["options"] = {"outgroup": "absent"}
    assert client.post("/api/tool-registry/run", json=body).status_code == 400


def test_registered_mapping_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    reference = tmp_path / "reference.fasta"
    reference.write_text(">reference\nACGTACGT\n")
    ids, errors = library.import_paths([reference])
    assert not errors
    reads = tmp_path / "reads.fastq"
    reads.write_text("@r1\nACGT\n+\nIIII\n")
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, title, func, kw)) or {"id": "map"})
    body = {"operation": "map.reads", "engine": "minimap2",
            "inputs": {"reference": ids, "read_files": [str(reads)]},
            "options": {"read_type": "ont_hq", "min_depth": 3}, "output": "mapping"}
    response = TestClient(app).post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert captured[0][0] == "map" and captured[0][3]["groups"]
    assert captured[0][3]["resources"] == ("compute", None)


def test_registered_sanger_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    library.ensure_folders()
    reads = tmp_path / "reads.fasta"
    reads.write_text(">sample_F\nACGT\n>sample_R\nACGA\n")
    ids, errors = library.import_paths([reads])
    assert not errors
    leaves = [it["id"] for it in library.items() if it.get("parent") == ids[0]]
    captured = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: captured.append((kind, title, func, kw)) or {"id": "test"})
    client = TestClient(app)
    body = {"operation": "assemble.one", "engine": "sanger", "inputs": {"reads": leaves},
            "options": {}, "output": "sample"}
    response = client.post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert captured[-1][0] == "assemble" and captured[-1][3]["items"][0]["id"] in leaves
    body["operation"] = "assemble.batch"
    response = client.post("/api/tool-registry/run", json=body)
    assert response.status_code == 200, response.text
    assert len(captured[-1][3]["samples"]) == 1
