"""Settings persistence, validation, recovery and storage measurement."""
import json
import pytest
from fastapi import HTTPException
from cladia import config, api_settings
from fastapi.testclient import TestClient
from cladia.server import app


def test_tree_preferences_merge_and_survive_unrelated_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    config.save_settings({"tree_layout": {"ladderize": True}})
    config.save_settings({"tree_layout": {"align": True}})
    config.save_settings({"text_size": 110})
    assert config.setting("tree_layout") == dict(align=True, support=True, lengths=False, ladderize=True)
    config.save_settings({"tree_layout": {"ladderize": False}})
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["tree_layout"]["ladderize"] is False
    assert saved["tree_layout"]["align"] is True
    assert saved["text_size"] == 110


@pytest.mark.parametrize("value", [None, [], {"ladderize": "false"}, {"unknown": True}])
def test_invalid_tree_preferences_rejected(value):
    with pytest.raises(HTTPException) as error:
        api_settings.put_settings({"tree_layout": value})
    assert error.value.status_code == 400


@pytest.mark.parametrize("saved", ["null", "[]", '"text"', "{broken"])
def test_invalid_settings_file_uses_independent_defaults(tmp_path, monkeypatch, saved):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    (tmp_path / "settings.json").write_text(saved)
    assert config.settings() == config.SETTINGS
    config.settings()["submitters"].append({"name": "temporary"})
    assert config.settings()["submitters"] == []
    config.save_settings({"tree_layout": {"ladderize": True}})
    assert config.setting("tree_layout")["ladderize"] is True


@pytest.mark.parametrize("layout", [[], "bad", {"align": True, "ladderize": "false", "extra": True}])
def test_saved_tree_choices_keep_only_known_booleans(tmp_path, monkeypatch, layout):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"tree_layout": layout, "ncbi_email": "me@example.org"}))
    saved = config.settings()
    assert saved["ncbi_email"] == "me@example.org"
    assert saved["tree_layout"] == {**config.TREE_LAYOUT_DEFAULTS, "align": isinstance(layout, dict)}


def test_settings_replace_failure_keeps_previous_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    config.save_settings({"tree_layout": {"ladderize": True}})
    original = (tmp_path / "settings.json").read_bytes()
    def fail_replace(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(type(tmp_path), "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        config.save_settings({"tree_layout": {"ladderize": False}})
    assert (tmp_path / "settings.json").read_bytes() == original
    assert not (tmp_path / "settings.json.tmp").exists()


@pytest.mark.parametrize("value", ["abc", [], {}, True, 1.5])
@pytest.mark.parametrize("key", ["cores", "text_size"])
def test_invalid_numeric_settings_return_client_error_without_saving(tmp_path, monkeypatch, key, value):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    config.save_settings({"ncbi_email": "keep@example.org"})
    original = (tmp_path / "settings.json").read_bytes()
    response = TestClient(app).put("/api/settings", json={key: value, "ncbi_email": "changed@example.org"})
    assert response.status_code == 400
    assert "whole number" in response.json()["detail"]
    assert (tmp_path / "settings.json").read_bytes() == original


def test_storage_tolerates_log_deleted_during_measurement(tmp_path, monkeypatch):
    from pathlib import Path
    from cladia import jobs
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    logs = jobs.logs_dir()
    logs.mkdir(parents=True)
    log = logs / "finished.json"
    log.write_text("{}")
    stat = Path.stat
    def disappearing(path, *args, **kwargs):
        if path == log:
            raise FileNotFoundError(path)
        return stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", disappearing)
    monkeypatch.setattr(jobs, "images", lambda: [])
    assert api_settings.space()["logs"] == 0


def test_library_view_merges_and_does_not_reapply_window_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(api_settings, "AFTER_SETTINGS", [lambda: calls.append(True)])
    client = TestClient(app)
    assert client.put("/api/settings", json={"library_view": {"folder": "Parent/Child"}}).status_code == 200
    assert client.put("/api/settings", json={"library_view": {"expanded": ["Parent"]}}).status_code == 200
    sorts = {"": {"key": "name", "descending": False}, "Parent": {"key": "size", "descending": True}}
    assert client.put("/api/settings", json={"library_view": {"sorts": sorts}}).status_code == 200
    assert client.get("/api/settings").json()["library_view"] == {"folder": "Parent/Child", "expanded": ["Parent"],
                                                                  "sorts": sorts}
    assert calls == []
    client.put("/api/settings", json={"text_size": 110})
    assert calls == [True]


@pytest.mark.parametrize("view", [None, [], {"folder": 7}, {"expanded": "Parent"}, {"expanded": [None]}, {"bad": True},
                                  {"sorts": []}, {"sorts": {"A": {"key": "colour", "descending": False}}},
                                  {"sorts": {"A": {"key": "name"}}}])
def test_invalid_library_view_rejected_without_saving(tmp_path, monkeypatch, view):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    response = TestClient(app).put("/api/settings", json={"library_view": view})
    assert response.status_code == 400
    assert not (tmp_path / "settings.json").exists()
