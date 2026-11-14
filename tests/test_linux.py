"""Shared WSL migration, dependency roots and command construction (no live WSL)."""
import json
from types import SimpleNamespace

import pytest

from cladia import config, linux
from conftest import EXAMPLE_IMAGE


def test_setup_output_is_delivered_before_process_finishes(tmp_path):
    import sys
    acknowledgement = tmp_path / 'received'
    script = (
        'import pathlib,time,sys; '
        'p=pathlib.Path(sys.argv[1]); '
        'print("Downloading packages", flush=True); '
        'deadline=time.monotonic()+5; '
        '\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(0.01)'
        '\nprint("Finished", file=sys.stderr, flush=True)'
        '\nsys.exit(0 if p.exists() else 1)'
    )
    messages = []
    def output(line):
        messages.append(line.strip())
        acknowledgement.write_text('received')
    result = linux._stream_command([sys.executable, '-u', '-c', script, str(acknowledgement)], output)
    assert result.returncode == 0
    assert messages == ['Downloading packages', 'Finished']
    assert 'Finished' in result.stdout


def result(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, "LINUX_DIR", tmp_path)
    monkeypatch.setattr(linux, "LEGACY_LINUX_DIR", tmp_path / "legacy")
    monkeypatch.setattr(linux, "_ready", set())
    return tmp_path


def legacy_metadata(*names):
    """What an older SeqLab install left beside its distributions."""
    linux.LEGACY_LINUX_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        (linux.LEGACY_LINUX_DIR / f"{name}.json").write_text(json.dumps(info("example")), encoding="utf-8")


def info(kind):
    return {"version": linux.DISTROS[kind]["version"], "env": ["PATH=/usr/bin:/bin", "SPECIAL=a b"],
            "workdir": "/data", "entrypoint": []}


def test_all_images_share_one_distribution():
    assert {d["name"] for d in linux.DISTROS.values()} == {"Cladia"}
    assert linux.kind_of(EXAMPLE_IMAGE) == "example"
    assert linux.kind_of(config.TOOLS_IMAGE) == "tools"
    assert linux.DISTROS["tools"]["root"] == "/"
    assert linux.DISTROS["example"]["root"] == "/var/lib/cladia/environments/example"
    # (optional tools: conda environments in the tools root; images: one root per engine)


@pytest.mark.parametrize("kind,spec", [("tools", {"name": "a/b", "check": "true", "version": "1"}),
                                       ("../x", {"name": "a/b", "check": "true", "version": "1"}),
                                       ("ok", {"name": "a/b", "version": "1"})])
def test_engine_images_are_checked(kind, spec):
    with pytest.raises(ValueError):
        linux.add_environment(kind, spec)


def test_a_new_image_version_unpacks_again(runtime, monkeypatch):
    linux._save(runtime / "environments" / "example.json", {**info("example"), "version": "0"})
    calls, made = [], []
    monkeypatch.setattr(linux, "_host", lambda *a, **k: calls.append(a) or result())
    monkeypatch.setattr(linux, "_make_environment", lambda kind, *a: made.append(kind) or {**info(kind), "version": None})
    monkeypatch.setattr(linux, "_environment_exec", lambda *a, **k: result())
    linux._ensure_environment("example", lambda *a: None, lambda *a: None)
    assert ("rm", "-rf", "/var/lib/cladia/environments/example") in calls and made == ["example"]
    assert linux.meta("example")["version"] == "1"


def test_removed_runtime_invalidates_session_cache(runtime, monkeypatch):
    linux._ready.update({"tools", "example", "migration"})
    monkeypatch.setattr(linux, "registered", lambda: set())
    events = []
    monkeypatch.setattr(linux, "_ensure_runtime", lambda *args: events.append("runtime"))
    def prepare(kind, *args):
        events.append(kind)
        linux._ready.add(kind)
    monkeypatch.setattr(linux, "_ensure_environment", prepare)
    monkeypatch.setattr(linux, "_retire_old_optional_tools", lambda *args: None)
    messages = []
    linux.ensure("tools", messages.append)
    assert events == ["runtime", "tools"]
    assert linux._ready == {"tools", "migration"}
    assert "removed" in "".join(messages)


@pytest.mark.parametrize("kind", ["tools", "example"])
def test_commands_keep_environment_and_bind_mounts_separate(runtime, monkeypatch, kind):
    data = info(kind)
    data["entrypoint"] = ["python3", "run.py"]
    linux._save(runtime / "environments" / f"{kind}.json", data)
    monkeypatch.setattr(linux, "linux_path", lambda p: "/mnt/c/" + str(p))
    image = {"tools": config.TOOLS_IMAGE, "example": EXAMPLE_IMAGE}[kind]
    cmd = linux.command(image, ["--name", "a'b"], "job with spaces", "cladia-job",
                        {"volume:db": "/db", "reads": "/reads/0"}, entrypoint="bash")
    assert cmd[cmd.index("-d") + 1] == "Cladia"
    assert cmd[-3:] == ["bash", "--name", "a'b"]
    script = cmd[-5]
    assert "--propagation" in cmd and "private" in cmd
    assert "SPECIAL=a b" in script
    root = linux.DISTROS[kind]["root"].rstrip("/")
    assert f"{root}/db" in script and f"{root}/reads/0" in script
    assert "remount,bind,ro" in script
    assert "mkdir -p /var/lib/cladia/volumes/db" in script
    if kind != "tools":
        assert f"exec chroot {root}" in script
        assert f"mount -t proc proc {root}/proc" in script
        assert f"mount --rbind /dev {root}/dev" in script
    else:
        assert "chroot" not in script


@pytest.mark.parametrize("name", ["../bad", "bad;echo", "x/y", ""])
def test_rejects_unsafe_job_and_volume_names(runtime, name):
    with pytest.raises(ValueError):
        linux.command(config.TOOLS_IMAGE, ["true"], runtime, name)
    with pytest.raises(ValueError):
        linux.command(config.TOOLS_IMAGE, ["true"], runtime, "safe", {"volume:" + name: "/db"})


def test_migration_checks_all_replacements_before_retiring(runtime, monkeypatch):
    monkeypatch.setattr(linux, '_retire_old_optional_tools', lambda *args: None)
    names = {"Cladia", "SeqLab-tools", "SeqLab-Example", "Ubuntu"}
    legacy_metadata("SeqLab-tools", "SeqLab-Example")
    events = []
    monkeypatch.setattr(linux, "registered", lambda: names)
    monkeypatch.setattr(linux, "_ensure_runtime", lambda *a: events.append("runtime"))
    def prepare(kind, *args):
        events.append("check:" + kind)
        linux._ready.add(kind)
    monkeypatch.setattr(linux, "_ensure_environment", prepare)
    monkeypatch.setattr(linux, "_retire_legacy", lambda kind, *a: events.append("retire:" + kind))
    linux.ensure("tools")
    assert events == ["runtime", "check:tools", "check:example", "retire:tools", "retire:example"]
    linux.ensure("example")
    assert len(events) == 5


def test_failed_replacement_never_unregisters_old_environments(runtime, monkeypatch):
    monkeypatch.setattr(linux, "registered", lambda: {"SeqLab-tools", "SeqLab-Example"})
    legacy_metadata("SeqLab-tools", "SeqLab-Example")
    monkeypatch.setattr(linux, "_ensure_runtime", lambda *a: None)
    retired = []
    monkeypatch.setattr(linux, "_retire_legacy", lambda *a: retired.append(a))
    def fail(kind, *args):
        if kind == "example":
            raise RuntimeError("validation failed")
        linux._ready.add(kind)
    monkeypatch.setattr(linux, "_ensure_environment", fail)
    with pytest.raises(RuntimeError, match="validation failed"):
        linux.ensure("tools")
    assert not retired and "migration" not in linux._ready


def test_export_refuses_running_or_unowned_distros(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(linux, "wsl", lambda *a, **k: calls.append(a) or result(out="SeqLab-tools"))
    monkeypatch.setattr(linux, "_owned", lambda name: None)
    with pytest.raises(RuntimeError, match="Close jobs"):
        linux._export("SeqLab-tools", lambda *a: None)
    assert not any("--export" in args for args in calls)
    monkeypatch.setattr(linux, "_owned", lambda name: (_ for _ in ()).throw(RuntimeError("not owned")))
    with pytest.raises(RuntimeError, match="not owned"):
        linux._export("SeqLab-tools", lambda *a: None)


def test_shared_disk_counted_once_and_legacy_marked(runtime):
    for name in ["Cladia", "SeqLab-Example"]:
        root = runtime if name == "Cladia" else linux.LEGACY_LINUX_DIR
        disk = root / name / "ext4.vhdx"
        disk.parent.mkdir(parents=True)
        disk.write_bytes(b"disk")
    assert linux.sizes() == [{"name":"Cladia","size":4,"used":True,"old":False},
                             {"name":"SeqLab-Example","size":4,"used":False,"old":True}]


def test_cancel_and_volume_removal_use_shared_host(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(linux, "registered", lambda: {"Cladia"})
    monkeypatch.setattr(linux, "_host", lambda *a, **k: calls.append(a) or result())
    linux.cancel("cladia-test")
    linux.remove_volume("db")
    assert len(calls) == 2 and "/run/cladia-test.pid" in calls[0][-1]
    assert calls[1] == ("rm", "-rf", "/var/lib/cladia/volumes/db")


def test_root_publication_recovers_metadata_after_interruption(runtime, monkeypatch):
    monkeypatch.setattr(linux, "_host", lambda *a: result(out=json.dumps(info("example"))))
    observed = linux._make_environment("example", lambda *a: None, lambda *a: None)
    assert observed == info("example") and linux.meta("example") == observed


def test_existing_shared_distro_without_metadata_is_not_adopted(runtime, monkeypatch):
    monkeypatch.setattr(linux, "registered", lambda: {"Cladia"})
    monkeypatch.setattr(linux, "_owned", lambda name: None)
    with pytest.raises(RuntimeError, match="without migration metadata"):
        linux._ensure_runtime(lambda *a: None, lambda *a: None)


def test_wsl_listing_errors_do_not_look_like_missing_installations(runtime, monkeypatch):
    monkeypatch.setattr(linux, "wsl", lambda *a, **k: result(1, err="WSL unavailable"))
    with pytest.raises(RuntimeError, match="WSL unavailable"):
        linux.registered()


def test_fresh_bootstrap_uses_one_distro_and_records_metadata(runtime, monkeypatch):
    archive = runtime / "base.tar.gz"
    archive.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(linux, "registered", lambda: set())
    monkeypatch.setattr(linux, "fetch_image", lambda *a: ([archive], {"Env":["PATH=/bin"],"WorkingDir":"/"}))
    monkeypatch.setattr(linux, "wsl", lambda *a, **k: calls.append(a) or result())
    linux._ensure_runtime(lambda *a: None, lambda *a: None)
    imports = [a for a in calls if a[0] == "--import"]
    assert len(imports) == 1 and imports[0][1] == "Cladia"
    assert linux.meta("tools")["version"] is None
    assert json.loads((runtime / "runtime.json").read_text())["ready"]
    assert not any("--unregister" in a for a in calls)


def test_old_seqlab_without_metadata_is_left_alone(runtime, monkeypatch):
    # A clean install removed %LOCALAPPDATA%\SeqLab but its WSL registrations remained.
    archive = runtime / "base.tar.gz"
    archive.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(linux, "LEGACY_LINUX_DIR", runtime / "gone")
    monkeypatch.setattr(linux, "registered", lambda: {"SeqLab", "SeqLab-Example"})
    monkeypatch.setattr(linux, "fetch_image", lambda *a: ([archive], {"Env": ["PATH=/bin"], "WorkingDir": "/"}))
    monkeypatch.setattr(linux, "wsl", lambda *a, **k: calls.append(a) or result())
    linux._ensure_runtime(lambda *a: None, lambda *a: None)
    assert [a[1] for a in calls if a[0] == "--import"] == ["Cladia"]
    assert linux._legacy_name("tools", {"SeqLab"}) is None and linux._legacy_name("example", {"SeqLab-Example"}) is None
    linux._retire_legacy("example", lambda *a: None)
    assert not any(a[0] in ("--unregister", "--export") for a in calls)


def test_retirement_failure_keeps_export_for_retry(runtime, monkeypatch):
    legacy = "SeqLab-Example"
    backup = runtime / "download" / f"{legacy}.tar"
    backup.parent.mkdir()
    backup.write_bytes(b"backup")
    legacy_metadata(legacy)
    monkeypatch.setattr(linux, "registered", lambda: {legacy})
    monkeypatch.setattr(linux, "_owned", lambda *a: None)
    monkeypatch.setattr(linux, "_legacy_idle", lambda *a: None)
    monkeypatch.setattr(linux, "_host", lambda *a: result())
    monkeypatch.setattr(linux, "wsl", lambda *a: result(1, err="in use"))
    with pytest.raises(RuntimeError, match="in use"):
        linux._retire_legacy("example", lambda *a: None)
    assert backup.exists()
