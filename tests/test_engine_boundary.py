"""The engine/host contract is public, explicit and independent of HTTP/scheduling."""
import ast
import inspect
import subprocess
import sys
from pathlib import Path

from cladia import engine_api
from cladia.tool_registry import PLUGIN_DIR


def test_plugin_import_boundary():
    violations = []
    paths = [*Path(PLUGIN_DIR).rglob("*.py"), *(Path(__file__).resolve().parents[1] / "plugins").rglob("*.py")]
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    violations.append(f"{path.name}:{node.lineno}: relative import")
                elif node.module == "cladia":
                    for alias in node.names:
                        if alias.name != "engine_api": violations.append(f"{path.name}: {alias.name}")
                        else: aliases.add(alias.asname or alias.name)
                elif node.module == "cladia.engine_api":
                    for alias in node.names:
                        assert alias.name in engine_api.__all__, f"Unpublished service {alias.name}"
                elif node.module and node.module.startswith("cladia."):
                    violations.append(f"{path.name}:{node.lineno}: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "cladia" or alias.name.startswith("cladia.") and alias.name != "cladia.engine_api":
                        violations.append(f"{path.name}:{node.lineno}: {alias.name}")
                    elif alias.name == "cladia.engine_api": aliases.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                assert node.attr in engine_api.__all__, f"{path.name}:{node.lineno}: private/unpublished {node.attr}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases:
                assert isinstance(node.args[1], ast.Constant) and node.args[1].value in engine_api.__all__
    assert not violations, ", ".join(violations)


def test_explicit_public_host_services():
    definitions = [node.name for node in ast.parse(Path(engine_api.__file__).read_text()).body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    assert len(definitions) == len(set(definitions)), "A host API definition is shadowed"
    assert len(engine_api.__all__) == len(set(engine_api.__all__))
    assert not hasattr(engine_api, "_forward")
    assert not any(name.endswith("_job") for name in engine_api.__all__)
    for name in engine_api.__all__:
        assert not name.startswith("_") and hasattr(engine_api, name)
        value = getattr(engine_api, name)
        assert not inspect.ismodule(value), f"Module escape hatch: {name}"
    assert not any(hasattr(engine_api, name) for name in ("jobs", "config", "mapping", "library"))
    for name in ("run_container", "add_output", "watch_log", "tool_setting"):
        args = list(inspect.signature(getattr(engine_api, name)).parameters.values())
        assert args and args[0].kind != inspect.Parameter.VAR_POSITIONAL


def test_engine_imports_do_not_load_scheduler_or_http():
    modules = [f"cladia.tool_plugins.{path.stem}" for path in Path(PLUGIN_DIR).glob("*.py") if not path.name.startswith("_")]
    code = "import importlib,sys; " + "; ".join(f"importlib.import_module({name!r})" for name in modules)
    code += "; assert 'cladia.server' not in sys.modules; assert 'cladia.jobs' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_checked_execution_contract(monkeypatch, tmp_path):
    from cladia import execution
    monkeypatch.setattr(execution, "ensure_image", lambda *args: None)
    monkeypatch.setattr(execution, "run_container", lambda *args, **kwargs: (0, "result", "diagnostic", ["program"]))
    job = {"id": "contract", "log": ""}
    assert engine_api.run_container(job, "image", ["program"], tmp_path) == "result"
    assert "diagnostic" in job["log"]
    monkeypatch.setattr(execution, "run_container", lambda *args, **kwargs: (2, "", "failed", ["program"]))
    import pytest
    with pytest.raises(RuntimeError, match="exited with code 2"):
        engine_api.run_container(job, "image", ["program"], tmp_path)


def test_settings_access_is_published(monkeypatch):
    from cladia import tool_registry
    monkeypatch.setattr(tool_registry, "tool_setting", lambda engine, key: "configured")
    assert engine_api.tool_setting("example", "credential") == "configured"


def test_new_engine_can_publish_without_scheduler_changes(tmp_path, monkeypatch):
    from cladia import config, jobs, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    namespace = {}
    exec('''from cladia import engine_api as api
def run(job, workdir):
    api.steps(job, "Publish")
    path = workdir / "result.fa"
    path.write_text(">example\\nACGT\\n")
    api.watch_log(job, workdir / "live.log", lambda text, progress: None)
    api.add_output(job, path, "example")
''', namespace)
    job = {"id": "plugin-contract", "kind": "example", "title": "Example", "log": "", "outputs": []}
    assert not hasattr(jobs, "example_job")
    jobs._run(job, namespace["run"], {})
    assert job["status"] == "done", job.get("error")
    assert str(library.records(library.get_item(job["outputs"][0]["id"]))[0].seq) == "ACGT"
    from cladia.api_tools import _facts
    import json
    assert "_progress_reader" not in _facts(job)
    assert "_progress_reader" not in jobs.saved_log(job["id"])
    json.dumps(_facts(job))
