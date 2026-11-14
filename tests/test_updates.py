"""A newer installer in the folder the installed copy came from is announced."""
from cladia import updates


def test_newer_installer_found(tmp_path, monkeypatch):
    program, shared = tmp_path / "app", tmp_path / "Ferramentas"
    program.mkdir()
    shared.mkdir()
    monkeypatch.setattr(updates, "PROGRAM", program)

    # a development copy (no VERSION): never told about updates
    assert updates.status() == {"version": None, "folder": None, "newer": None}

    (program / "VERSION").write_text("2026.11.05 (abc1234)\n")
    (program / "UPDATES_FROM").write_text("﻿" + str(shared) + "\n", encoding="utf-8")  # Inno writes a BOM
    for name in ("SeqLab-setup-2026.11.03.exe", "Cladia-setup-2026.11.05.exe", "notes.txt"):
        (shared / name).write_text("")
    assert updates.status()["newer"] is None  # the installed one is the newest

    (shared / "Cladia-setup-2026.11.12.exe").write_text("")
    (shared / "Cladia-setup-2026.11.10.exe").write_text("")
    newer = updates.status()["newer"]
    assert newer["version"] == "2026.11.12" and newer["path"].endswith("Cladia-setup-2026.11.12.exe")

    # the folder gone (OneDrive not synced, a network drive away): no update, no error
    (program / "UPDATES_FROM").write_text(str(tmp_path / "missing"))
    assert updates.status()["newer"] is None
