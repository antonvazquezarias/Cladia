"""Tool footprints never install runtimes and keep shared dependencies explicit."""
from types import SimpleNamespace
import subprocess
import pytest
from fastapi.testclient import TestClient
from cladia import config, linux, tool_storage
from cladia.server import app
from cladia.tool_registry import build_registry


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, "LINUX_DIR", tmp_path)
    monkeypatch.setattr(config, "CONTAINER_CMD", "")
    monkeypatch.setattr(tool_storage, "_cache", {})
    return tmp_path


def engines():
    return {"local_blast":{"label":"BLAST+","storage":{"kind":"linux","environment":"tools","paths":["/usr/bin/blast*"],"estimateBytes":1000}},
            "transfer":{"storage":{"kind":"shared","uses":"local_blast"}},
            "orfs":{"storage":{"kind":"builtin"}}, "online":{"storage":{"kind":"remote"}},
            "example":{"storage":{"kind":"linux","environment":"example","paths":["/"],"estimateBytes":2000}}}


def test_no_runtime_means_estimates_without_starting_wsl(storage, monkeypatch):
    monkeypatch.setattr(linux, "wsl", lambda *a, **k: pytest.fail("Must not start WSL"))
    data = tool_storage.catalog(engines())
    assert data["local_blast"]["bytes"] is None
    assert data["local_blast"]["estimateBytes"] == 1000
    assert data["transfer"]["estimateBytes"] == 1000
    assert data["orfs"]["kind"] == "builtin" and data["online"]["kind"] == "remote"


def test_one_measurement_is_cached_and_shared_tools_refer_to_same_files(storage, monkeypatch):
    linux._save(storage / "environments/tools.json", {"version":"test"})
    calls=[]
    def wsl(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="local_blast\t1200\nexample\t2200\n", stderr="")
    monkeypatch.setattr(linux,"wsl",wsl)
    data=tool_storage.catalog(engines())
    assert data["local_blast"]["bytes"] == data["transfer"]["bytes"] == 1200
    assert data["transfer"]["sharedWith"] == "BLAST+"
    assert data["example"]["bytes"] == 2200
    script=calls[0][-1]
    assert "/var/lib/cladia/environments/example/" in script
    assert "/usr/bin/blast*" in script and "--import" not in script
    tool_storage.catalog(engines()); assert len(calls)==1
    linux._save(storage / "environments/example.json", {"version":"changed"})
    tool_storage.catalog(engines()); assert len(calls)==2


@pytest.mark.parametrize("path",["/tmp/../etc", "/tmp/x;touch", "/tmp/$(id)","/tmp/space here","/"])
def test_storage_patterns_cannot_inject_commands_or_measure_the_entire_host(path):
    with pytest.raises(ValueError):
        tool_storage.validate({"kind":"linux","environment":"tools","paths":[path]})


def test_measurement_failure_preserves_catalog_and_estimates(storage,monkeypatch):
    linux._save(storage / "environments/tools.json", {"version":"test"})
    def fail(*a, **k):
        raise subprocess.TimeoutExpired("wsl",25)
    monkeypatch.setattr(linux,"wsl",fail)
    data=tool_storage.catalog(engines())
    assert data["local_blast"]["bytes"] is None and data["local_blast"]["estimateBytes"]==1000
    assert data["local_blast"]["measurementUnavailable"]


def test_tools_endpoint_includes_builtins_without_measuring_disk(storage, monkeypatch):
    monkeypatch.setattr(tool_storage, "_measure", lambda *a: pytest.fail("Tool list should not measure WSL storage"))
    registry=build_registry()
    rows=TestClient(app).get("/api/tool-engines").json()
    assert {e["id"] for e in rows} == set(registry.engines)
    assert all(e["builtin"] == (not registry.engines[e["id"]].get("optional")) for e in rows)
    assert all("storage" in e for e in rows)
    assert {e["id"] for e in registry.installable()} == {"clustal_omega", "getorganelle", "krait", "muscle", "macse", "raxml_ng", "mrbayes", "trnascan", "aragorn", "trimal", "clipkit"}
    assert all(e["packages"] and e["version"] for e in registry.installable())


def test_package_footprint_is_separate_from_dependencies(storage):
    (storage/"bin").mkdir();(storage/"bin/tool").write_bytes(b"12345")
    (storage/"engine.py").write_bytes(b"12")
    assert tool_storage.package_size(storage)==7
