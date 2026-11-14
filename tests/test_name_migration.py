"""Existing libraries and tool installations remain usable after the rename."""
import json

from cladia import config, linux


def test_existing_library_moves_with_settings(tmp_path, monkeypatch):
    old, new = tmp_path / "SeqLab", tmp_path / "Cladia"
    (old / "library").mkdir(parents=True)
    (old / "library" / "sequence.gb").write_text("existing data")
    (old / ".seqlab").mkdir()
    (old / ".seqlab" / "settings.json").write_text("{}")
    monkeypatch.setattr(config, "_override", None)
    monkeypatch.setattr(config, "LEGACY_DATA_DIR", old)
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", new)
    monkeypatch.setattr(config, "DATA_DIR", old)
    monkeypatch.setattr(config, "APP_DIR", old / ".seqlab")

    assert config.migrate_default_data()
    assert config.DATA_DIR == new and config.APP_DIR == new / ".cladia"
    assert (new / "library" / "sequence.gb").read_text() == "existing data"
    assert (new / ".cladia" / "settings.json").read_text() == "{}"
    assert not old.exists()


def test_previous_tools_distro_supplies_new_runtime_metadata(tmp_path, monkeypatch):
    old = tmp_path / "old-linux"
    old.mkdir()
    expected = {"version": linux.DISTROS["tools"]["version"], "env": [], "workdir": "/", "entrypoint": []}
    (old / "SeqLab-tools.json").write_text(json.dumps(expected))
    archive = tmp_path / "tools.tar"
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(linux, "LINUX_DIR", tmp_path / "new-linux")
    monkeypatch.setattr(linux, "LEGACY_LINUX_DIR", old)
    monkeypatch.setattr(linux, "registered", lambda: {"SeqLab-tools"})
    monkeypatch.setattr(linux, "_export", lambda *args: archive)
    monkeypatch.setattr(linux, "_host", lambda *args: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    imports = []
    def wsl(*args, **kwargs):
        imports.append(args)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(linux, "wsl", wsl)

    linux._ensure_runtime(lambda *args: None, lambda *args: None)
    assert any(args[:2] == ("--import", "Cladia") for args in imports)
    assert linux.meta("tools") == expected
    assert json.loads((linux.LINUX_DIR / "runtime.json").read_text())["source"] == "SeqLab-tools"


def test_shared_distro_carries_optional_environment_metadata(tmp_path, monkeypatch):
    old, new = tmp_path / "old-linux", tmp_path / "new-linux"
    (old / "environments").mkdir(parents=True)
    tools = {"version": linux.DISTROS["tools"]["version"], "env": [], "workdir": "/", "entrypoint": []}
    example = {"version": linux.DISTROS["example"]["version"], "env": [], "workdir": "/", "entrypoint": []}
    (old / "runtime.json").write_text(json.dumps({"ready": True, "tools": tools}))
    (old / "environments" / "example.json").write_text(json.dumps(example))
    archive = tmp_path / "shared.tar"
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(linux, "LINUX_DIR", new)
    monkeypatch.setattr(linux, "LEGACY_LINUX_DIR", old)
    monkeypatch.setattr(linux, "registered", lambda: {"SeqLab"})
    monkeypatch.setattr(linux, "_export", lambda *args: archive)
    commands = []
    def result(*args, **kwargs):
        commands.append(args)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(linux, "_host", result)
    monkeypatch.setattr(linux, "wsl", result)

    linux._ensure_runtime(lambda *args: None, lambda *args: None)
    assert linux.meta("example") == example
    assert any("/var/lib/seqlab/volumes" in str(args) for args in commands)
