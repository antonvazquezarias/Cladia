"""Linux PCs: Cladia's Linux as a Podman container (no live Podman here)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cladia import config, linux


def result(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


@pytest.fixture
def podman(tmp_path, monkeypatch):
    """PODMAN on, every podman call recorded (answers: inspect -> Debian's config)."""
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return result(out=json.dumps({"Env": ["PATH=/usr/bin:/bin"], "WorkingDir": ""}))
        if args[:2] == ("container", "inspect"):
            return result(out="1\n")
        return result()

    monkeypatch.setattr(linux, "PODMAN", True)
    monkeypatch.setattr(linux, "podman", run)
    monkeypatch.setattr(linux, "LINUX_DIR", tmp_path)
    monkeypatch.setattr(linux, "_ready", set())
    return calls


def test_jobs_run_through_podman_exec(podman, monkeypatch):
    linux._save(linux.LINUX_DIR / "environments" / "tools.json", {"env": [], "workdir": "/data", "entrypoint": []})
    monkeypatch.setattr(linux, "linux_path", lambda p: "/host/home/u/" + str(p))
    cmd = linux.command(config.TOOLS_IMAGE, ["mafft", "in.fa"], "job", "cladia-job")
    assert cmd[:5] == ["podman", "exec", "--user", "root", "cladia"]
    assert "wsl" not in " ".join(cmd).lower()
    assert cmd[-2:] == ["mafft", "in.fa"] and "unshare" in cmd


def test_host_paths_are_seen_under_host(monkeypatch, tmp_path):
    monkeypatch.setattr(linux, "PODMAN", True)
    assert linux.linux_path(tmp_path) == "/host" + Path(tmp_path).resolve().as_posix()


def test_registered_is_the_container(podman, monkeypatch):
    assert linux.registered() == {"Cladia"}
    assert podman[-1] == ("container", "exists", "cladia")
    monkeypatch.setattr(linux, "podman", lambda *a, **k: result(1))
    assert linux.registered() == set()


def test_runtime_is_a_privileged_container_with_the_pc_at_host(podman, monkeypatch):
    monkeypatch.setattr(linux, "registered", lambda **k: set())
    shell = []
    monkeypatch.setattr(linux, "_host", lambda *a, **k: shell.append(a) or result())
    linux._ensure_runtime(lambda text: None, lambda text, fraction: None)
    create = next(c for c in podman if c[0] == "create")
    assert "--privileged" in create and "/:/host:rslave" in create and "cladia.owned=1" in create
    assert ("start", "cladia") in podman
    assert json.loads((linux.LINUX_DIR / "runtime.json").read_text())["ready"] is True
    assert linux.meta("tools")["env"] == ["PATH=/usr/bin:/bin"]
    assert not any("wsl.conf" in " ".join(a) for a in shell)  # (WSL's own settings: not here)


def test_a_container_cladia_did_not_make_is_left_alone(podman, monkeypatch):
    monkeypatch.setattr(linux, "podman", lambda *a, **k: result(out="\n"))
    with pytest.raises(RuntimeError, match="not made by Cladia"):
        linux._owned("Cladia")


def test_size_is_the_containers(podman, monkeypatch):
    monkeypatch.setattr(linux, "podman", lambda *a, **k: result(out="123456\n"))
    assert linux.sizes() == [{"name": "Cladia (Podman container)", "size": 123456, "used": True, "old": False}]


def test_missing_podman_says_how_to_install_it(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("podman")
    monkeypatch.setattr(linux.subprocess, "run", missing)
    with pytest.raises(linux.WslPrerequisiteError, match="apt install podman"):
        linux.podman("info")
